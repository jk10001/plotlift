"""Single-server job supervision, interruptible workers and durable rollback."""
from __future__ import annotations

import json
import logging
import multiprocessing
import os
import re
import shutil
import threading
import time
import uuid
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from fastapi import HTTPException
from pydantic import ValidationError

from .artifacts import save_json
from .config import get_config
from .logging_utils import emit_event
from .models import RunState


logger = logging.getLogger(__name__)


class FileLease:
    """OS-released lease: startup never restores files under a surviving worker."""

    def __init__(self, path: Path):
        self.path = path
        self.handle = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+b")
        if self.path.stat().st_size == 0:
            self.handle.write(b"0")
            self.handle.flush()
        self.handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.handle.close()
            raise
        return self

    def __exit__(self, *_):
        if os.name == "nt":
            import msvcrt
            self.handle.seek(0)
            msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
        self.handle.close()


@dataclass
class Job:
    id: str
    name: str
    path: Path
    process: Any
    control: Any
    monitor: threading.Thread | None = None


class JobManager:
    def __init__(self, runs_root: Path | Callable[[], Path] | None = None, *, worker_target=None):
        self._runs_root = runs_root
        self._locks: dict[str, threading.RLock] = {}
        self._registry_lock = threading.Lock()
        self._jobs: dict[str, Job] = {}
        self._cancelled_jobs: deque[tuple[str, str]] = deque(maxlen=256)
        self._closing = False
        self._owner = None
        self._worker_target = worker_target

    @property
    def root(self) -> Path:
        configured = self._runs_root() if callable(self._runs_root) else self._runs_root
        return (configured or get_config().runs_dir).resolve()

    def run_path(self, run_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", run_id):
            raise HTTPException(400, "Invalid run id")
        path = (self.root / run_id).resolve()
        if path.parent != self.root:
            raise HTTPException(400, "Invalid run id")
        return path

    def lock(self, run_id: str):
        self.run_path(run_id)
        with self._registry_lock:
            return self._locks.setdefault(run_id, threading.RLock())

    def read_state(self, run_id: str) -> RunState:
        with self.lock(run_id):
            try:
                state = RunState.model_validate_json((self.run_path(run_id) / "state.json").read_text(encoding="utf-8"))
            except FileNotFoundError as exc:
                raise HTTPException(404, "Run not found") from exc
            # Only the server holding the exclusive owner lease may decide that a
            # persisted busy flag has no corresponding live worker. This also
            # handles old runs copied into the runs directory after startup.
            return self._recover_orphaned_state(state) if self._owner else state

    def _recover_orphaned_state(self, state: RunState) -> RunState:
        if state.run_id in self._jobs or not (state.active_job or state.active_job_id or state.active_step_status):
            return state
        if state.active_job_id and re.fullmatch(r"[0-9a-f]{32}", state.active_job_id):
            journal = self.root / ".jobs" / state.active_job_id / "job.json"
            if journal.exists():
                data = json.loads(journal.read_text())
                if data.get("run_id") == state.run_id and data.get("phase") in {"running", "cancelling"}:
                    # Durable jobs must use snapshot recovery after acquiring the
                    # worker lease; never silently unlock one that may still write.
                    return state
        # Legacy jobs have no pre-operation snapshot. Keep their last saved
        # results and review stage instead of inventing a rollback or deleting data.
        state.active_job = state.active_job_id = state.active_step_status = None
        self._write_state(state)
        root = self.run_path(state.run_id)
        self._repair_log(root)
        emit_event(root, "WARN", "Interrupted operation cleared; last saved results retained for review or retry.", run_id=state.run_id)
        return state

    def _write_state(self, state: RunState) -> None:
        state.touch()
        save_json(self.run_path(state.run_id) / "state.json", state.model_dump(mode="json"))

    @contextmanager
    def edit(self, run_id: str):
        with self.lock(run_id):
            state = self.read_state(run_id)
            if state.active_job or run_id in self._jobs:
                raise HTTPException(409, "Wait for the active operation to finish or cancel it first")
            yield

    def start(self, run_id: str, name: str, operation: str, parameters: dict | None = None, *, initial_status=None) -> str:
        from .job_worker import worker_main

        with self.edit(run_id):
            if self._closing:
                raise HTTPException(503, "Server is shutting down")
            root = self.run_path(run_id)
            job_id = uuid.uuid4().hex
            path = self.root / ".jobs" / job_id
            state = self.read_state(run_id)
            # Snapshot is complete before the journal or live state is changed.
            try:
                shutil.copytree(root, path / "snapshot", ignore=lambda directory, names: ["events.jsonl"] if Path(directory) == root else [])
                save_json(path / "job.json", {"run_id": run_id, "job_id": job_id, "name": name, "phase": "running"})
            except Exception:
                if path.exists():
                    shutil.rmtree(path)
                raise
            ctx = multiprocessing.get_context("spawn")
            child_control, parent_control = ctx.Pipe(duplex=False)
            process = ctx.Process(target=self._worker_target or worker_main, args=(str(self.root), run_id, operation, parameters or {}, str(path), child_control), daemon=True)
            job = Job(job_id, name, path, process, parent_control)
            self._jobs[run_id] = job
            state.active_job = name
            state.active_job_id = job_id
            state.active_step_status = initial_status
            try:
                self._write_state(state)
                emit_event(root, "STAGE", f"Started job {name}", run_id=run_id, stage=name)
                process.start()
                child_control.close()
                parent_control.send("start")
            except Exception:
                child_control.close()
                self._stop(job)
                self._restore(run_id, path, "Operation could not start. Previous results restored.")
                self._jobs.pop(run_id, None)
                parent_control.close()
                self._cleanup_job(path)
                raise
            job.monitor = threading.Thread(target=self._monitor, args=(run_id, job), daemon=True)
            job.monitor.start()
            return job_id

    def _stop(self, job: Job) -> None:
        if job.process.pid is None:
            return
        if job.process.is_alive():
            job.process.terminate()
        job.process.join(timeout=2)
        if job.process.is_alive():
            job.process.kill()
            job.process.join(timeout=2)
        if job.process.is_alive():
            raise HTTPException(503, "Worker could not be stopped; run remains locked. Retry cancellation.")

    def cancel(self, run_id: str, job_id: str) -> dict:
        with self.lock(run_id):
            state = self.read_state(run_id)
            job = self._jobs.get(run_id)
            if job and job.id != job_id:
                raise HTTPException(409, "This cancellation refers to an older operation")
            if not job:
                if state.active_job:
                    raise HTTPException(409, "Operation requires recovery before it can be cancelled")
                journal = self.root / ".jobs" / job_id / "job.json" if re.fullmatch(r"[0-9a-f]{32}", job_id) else None
                previous = json.loads(journal.read_text()) if journal and journal.exists() else {}
                with self._registry_lock:
                    cancelled = (run_id, job_id) in self._cancelled_jobs
                outcome = "cancelled" if cancelled or (previous.get("run_id") == run_id and previous.get("phase") == "cancelled") else "already_finished"
                return {"ok": True, "outcome": outcome, "state": state.model_dump(mode="json")}
            self._set_phase(job.path, "cancelling")
            self._stop(job)
            # No writer can survive restoration, even when cancel races completion.
            self._restore(run_id, job.path, "Operation cancelled. Previous results restored.")
            job.control.close()
            self._cleanup_job(job.path)
            self._jobs.pop(run_id, None)
            return {"ok": True, "outcome": "cancelled", "state": self.read_state(run_id).model_dump(mode="json")}

    def _monitor(self, run_id: str, job: Job) -> None:
        job.process.join()
        with self.lock(run_id):
            if self._jobs.get(run_id) is not job:
                return
            try:
                journal = json.loads((job.path / "job.json").read_text())
                if journal["phase"] == "cancelling":
                    self._restore(run_id, job.path, "Operation cancelled. Previous results restored.")
                else:
                    result_file = job.path / "result.json"
                    result = json.loads(result_file.read_text()) if result_file.exists() else {"ok": False, "error": "Worker exited unexpectedly"}
                    state = self.read_state(run_id)
                    state.active_job = state.active_job_id = state.active_step_status = None
                    if not result["ok"]:
                        state.stage = "error"
                        state.warnings.append(result["error"])
                    self._write_state(state)
                    self._set_phase(job.path, "finished")
                    message = f"Finished job {job.name}" if result["ok"] else f"Job {job.name} failed: {result['error']}"
                    self._repair_log(self.run_path(run_id))
                    emit_event(self.run_path(run_id), "STAGE" if result["ok"] else "ERROR", message, run_id=run_id, stage=job.name)
            except Exception as exc:  # noqa: BLE001
                # Keep ownership and snapshot available for a retry/recovery.
                emit_event(self.run_path(run_id), "ERROR", f"Job finalization requires recovery: {exc}", run_id=run_id)
                return
            job.control.close()
            self._cleanup_job(job.path)
            self._jobs.pop(run_id, None)

    def _set_phase(self, path: Path, phase: str) -> None:
        data = json.loads((path / "job.json").read_text())
        data["phase"] = phase
        save_json(path / "job.json", data)

    def _restore(self, run_id: str, path: Path, message: str) -> None:
        root = self.run_path(run_id)
        snapshot = path / "snapshot"
        baseline = RunState.model_validate_json((snapshot / "state.json").read_text(encoding="utf-8"))
        if baseline.run_id != run_id:
            raise RuntimeError("Rollback snapshot does not match the run")
        # Keep state.json until the last write so readers never see a missing state.
        for item in root.iterdir():
            if item.name in {"events.jsonl", "state.json"}:
                continue
            if item.is_dir() and not item.is_symlink():
                shutil.rmtree(item)
            else:
                item.unlink()
        for item in snapshot.iterdir():
            if item.name == "state.json":
                continue
            if item.is_dir():
                shutil.copytree(item, root / item.name)
            else:
                shutil.copy2(item, root / item.name)
        baseline.active_job = baseline.active_job_id = baseline.active_step_status = None
        self._write_state(baseline)
        self._repair_log(root)
        emit_event(root, "USER", message, run_id=run_id)
        self._set_phase(path, "cancelled")
        self._remember_cancelled(run_id, path.name)

    def _remember_cancelled(self, run_id: str, job_id: str) -> None:
        # Keep repeated cancellation requests idempotent without retaining folders.
        with self._registry_lock:
            self._cancelled_jobs.append((run_id, job_id))

    def _cleanup_job(self, path: Path) -> None:
        jobs_root = self.root / ".jobs"
        if path.resolve().parent != jobs_root or not re.fullmatch(r"[0-9a-f]{32}", path.name):
            raise ValueError("Invalid job cleanup path")
        try:
            journal = path / "job.json"
            data = json.loads(journal.read_text())
            if data["phase"] not in {"finished", "cancelled"}:
                return
            # Delete the journal last, so an interrupted/blocked cleanup can be
            # retried at startup without treating committed results as unfinished.
            for item in path.iterdir():
                if item == journal:
                    continue
                if item.is_dir() and not item.is_symlink():
                    shutil.rmtree(item)
                else:
                    item.unlink()
            journal.unlink()
            path.rmdir()
        except OSError:
            # Cleanup must never undo a committed outcome or lock the run.
            logger.warning("Job folder cleanup deferred until startup: %s", path, exc_info=True)

    @staticmethod
    def _repair_log(root: Path) -> None:
        # A terminated writer can leave an incomplete final record. Keep all complete records.
        path = root / "events.jsonl"
        if path.exists():
            data = path.read_bytes()
            if data and not data.endswith(b"\n"):
                with path.open("r+b") as handle:
                    handle.truncate(data.rfind(b"\n") + 1)

    def recover(self) -> None:
        for journal in sorted((self.root / ".jobs").glob("*/job.json")):
            data = json.loads(journal.read_text())
            if data["run_id"] in self._jobs:
                continue
            if data["phase"] in {"finished", "cancelled"}:
                if data["phase"] == "cancelled":
                    self._remember_cancelled(data["run_id"], journal.parent.name)
                self._cleanup_job(journal.parent)
                continue
            # On parent death the worker's watchdog exits and releases its lease.
            deadline = time.monotonic() + 5
            while True:
                try:
                    with FileLease(journal.parent / "worker.lock"):
                        self._restore(data["run_id"], journal.parent, "Interrupted operation recovered. Previous results restored.")
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise RuntimeError("An old worker is still active; refusing unsafe rollback")
                    time.sleep(0.1)
            # Release worker.lock before deleting it (required on Windows).
            self._cleanup_job(journal.parent)
        # Older thread-based jobs never created a journal. Missing/terminal job
        # records can also leave busy metadata behind; recover these separately.
        for state_path in sorted(self.root.glob("*/state.json")):
            try:
                state = RunState.model_validate_json(state_path.read_text(encoding="utf-8"))
            except (OSError, ValidationError):
                continue
            if state_path.parent != self.run_path(state.run_id):
                continue
            with self.lock(state.run_id):
                self._recover_orphaned_state(state)

    def open(self) -> None:
        self._closing = False
        self._owner = FileLease(self.root / ".jobs" / "server.lock")
        self._owner.__enter__()
        try:
            # A crash between deleting job.json and removing its directory can
            # leave an empty folder. Only attempt this before accepting jobs.
            for path in (self.root / ".jobs").iterdir():
                if re.fullmatch(r"[0-9a-f]{32}", path.name) and path.resolve().parent == self.root / ".jobs":
                    try:
                        path.rmdir()  # Nonempty job folders are handled by recovery.
                    except OSError:
                        pass
            self.recover()
        except Exception:
            self._owner.__exit__()
            self._owner = None
            raise

    def close(self) -> None:
        self._closing = True
        try:
            for run_id, job in list(self._jobs.items()):
                self.cancel(run_id, job.id)
        finally:
            if self._owner:
                self._owner.__exit__()
                self._owner = None
