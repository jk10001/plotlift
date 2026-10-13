from __future__ import annotations

import json
import multiprocessing
import os
import threading
import time
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.artifacts import save_json
from app.jobs import FileLease, JobManager
from app.models import Asset, PromptMetadata, RunSettings, RunState, SeriesState


def blocking_worker(runs_root, run_id, operation, parameters, job_path, control):
    """A real spawned process, with deterministic barriers instead of API calls."""
    root = Path(runs_root) / run_id
    with FileLease(Path(job_path) / "worker.lock"):
        control.recv()
        state = json.loads((root / "state.json").read_text())
        state["stage"] = "series_review"
        state["warnings"] = ["uncommitted"]
        state["series"] = []
        save_json(root / "state.json", state)
        (root / "old.txt").write_text("overwritten")
        (root / "new.txt").write_text("new artifact")
        with (root / "events.jsonl").open("a") as handle:
            handle.write(json.dumps({"message": "usage", "call_cost_usd": 0.25, "run_cost_usd": 0.25}) + "\n")
        (Path(job_path) / "ready").touch()
        if operation == "crash":
            os._exit(3)
        while not (Path(job_path) / "release").exists():
            time.sleep(0.02)
        save_json(Path(job_path) / "result.json", {"ok": True})


def watchdog_worker(*args):
    from app import job_worker
    from app.config import get_config
    def blocked_operation(state, *_):
        (get_config().runs_dir / state.run_id / "request-blocked").touch()
        while True:
            time.sleep(0.05)
    job_worker.run_operation = blocked_operation
    job_worker.worker_main(*args)


def doomed_supervisor(root, control):
    manager = JobManager(Path(root), worker_target=watchdog_worker)
    manager.open()
    job_id = manager.start("test-run", "crop", "crop")
    control.send(job_id)
    control.recv()
    os._exit(0)  # Simulate abrupt server death, bypassing shutdown handlers.


def wait_until(predicate, timeout=15):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("Timed out waiting for worker")
        time.sleep(0.02)


def make_run(root: Path, run_id="test-run"):
    state = RunState(run_id=run_id, settings=RunSettings(model_id="gpt-5.6-luna", image_detail="high", mock_mode=True),
                     prompt_metadata=PromptMetadata(prompt_version="test", prompt_hash="test"),
                     upload_filename="chart.png", original_path="uploads/chart.png", stage="crop_review", warnings=["baseline"])
    state.series = [SeriesState(id="existing", name="Previously digitised series")]
    save_json(root / run_id / "state.json", state.model_dump(mode="json"))
    (root / run_id / "old.txt").write_text("original")
    (root / run_id / "events.jsonl").write_text('{"message":"baseline"}\n')
    return state


@pytest.fixture
def manager(tmp_path):
    make_run(tmp_path)
    instance = JobManager(tmp_path, worker_target=blocking_worker)
    instance.open()
    try:
        yield instance
    finally:
        instance.close()


@pytest.mark.parametrize("operation", ["crop", "calibration", "axis", "series", "series_selection", "series_retry"])
def test_cancel_stops_worker_restores_files_and_keeps_costs(manager, operation):
    baseline = manager.read_state("test-run").model_dump()
    job_id = manager.start("test-run", "test", operation)
    job = manager._jobs["test-run"]
    wait_until(lambda: (job.path / "ready").exists())
    result = manager.cancel("test-run", job_id)
    assert not job.process.is_alive()
    assert result["outcome"] == "cancelled"
    restored = manager.read_state("test-run").model_dump()
    restored.pop("updated_at")
    baseline.pop("updated_at")
    assert restored == baseline
    root = manager.run_path("test-run")
    assert (root / "old.txt").read_text() == "original"
    assert not (root / "new.txt").exists()
    events = [json.loads(line) for line in (root / "events.jsonl").read_text().splitlines()]
    assert events[0]["message"] == "baseline"
    assert any(event.get("call_cost_usd") == 0.25 for event in events)
    assert events[-1]["message"] == "Operation cancelled. Previous results restored."
    assert manager.cancel("test-run", job_id)["outcome"] == "cancelled"
    second_id = manager.start("test-run", "test", operation)
    with pytest.raises(HTTPException) as exc:
        manager.cancel("test-run", job_id)
    assert exc.value.status_code == 409
    manager.cancel("test-run", second_id)


def test_completion_wins_and_failure_preserves_existing_behavior(manager):
    job_id = manager.start("test-run", "test", "crop")
    job = manager._jobs["test-run"]
    wait_until(lambda: (job.path / "ready").exists())
    (job.path / "release").touch()
    wait_until(lambda: "test-run" not in manager._jobs)
    assert manager.cancel("test-run", job_id)["outcome"] == "already_finished"
    assert (manager.run_path("test-run") / "old.txt").read_text() == "overwritten"
    manager.start("test-run", "test", "crash")
    wait_until(lambda: "test-run" not in manager._jobs)
    state = manager.read_state("test-run")
    assert state.stage == "error"
    assert state.active_job_id is None
    assert "Worker exited unexpectedly" in state.warnings


def test_cancel_wins_before_completion_is_committed(manager):
    job_id = manager.start("test-run", "test", "crop")
    job = manager._jobs["test-run"]
    wait_until(lambda: (job.path / "ready").exists())
    with manager.lock("test-run"):
        (job.path / "release").touch()
        job.process.join(timeout=10)
        assert not job.process.is_alive()
        assert manager.cancel("test-run", job_id)["outcome"] == "cancelled"
    assert (manager.run_path("test-run") / "old.txt").read_text() == "original"


def test_active_job_guards_edits_and_other_runs_are_independent(manager):
    make_run(manager.root, "other-run")
    job_id = manager.start("test-run", "test", "crop")
    with pytest.raises(HTTPException):
        manager.start("test-run", "test", "series")
    with pytest.raises(HTTPException):
        with manager.edit("test-run"):
            pytest.fail("Edit must be rejected")
    with manager.edit("other-run"):
        assert manager.read_state("other-run").active_job is None
    manager.cancel("test-run", job_id)


def test_snapshot_failure_does_not_start_or_change_state(manager, monkeypatch):
    before = (manager.run_path("test-run") / "state.json").read_bytes()
    def fail(*args, **kwargs):
        raise OSError("disk full")
    monkeypatch.setattr("app.jobs.shutil.copytree", fail)
    with pytest.raises(OSError, match="disk full"):
        manager.start("test-run", "test", "crop")
    assert not manager._jobs
    assert (manager.run_path("test-run") / "state.json").read_bytes() == before


def test_recovery_restores_an_interrupted_rollback(manager):
    job_id = manager.start("test-run", "test", "crop")
    job = manager._jobs["test-run"]
    wait_until(lambda: (job.path / "ready").exists())
    with manager.lock("test-run"):
        manager._jobs.pop("test-run")  # Simulate loss of the server's in-memory registry.
        manager._stop(job)
        job.control.close()
    job.monitor.join(timeout=5)
    with (manager.run_path("test-run") / "events.jsonl").open("ab") as handle:
        handle.write(b'{"incomplete":')
    manager.recover()
    assert manager.read_state("test-run").stage == "crop_review"
    assert (manager.run_path("test-run") / "old.txt").read_text() == "original"
    events = [json.loads(line) for line in (manager.run_path("test-run") / "events.jsonl").read_text().splitlines()]
    assert events[-1]["message"].startswith("Interrupted operation recovered")
    assert manager.cancel("test-run", job_id)["outcome"] == "cancelled"


def test_server_shutdown_restores_active_run(manager):
    manager.start("test-run", "test", "crop")
    job = manager._jobs["test-run"]
    wait_until(lambda: (job.path / "ready").exists())
    manager.close()
    assert not job.process.is_alive()
    assert manager.read_state("test-run").stage == "crop_review"


def test_cancel_endpoint_and_mutation_guards(manager, monkeypatch):
    from app import main
    monkeypatch.setattr(main, "job_manager", manager)
    client = TestClient(main.app)
    response = client.post("/api/runs/test-run/jobs/crop")
    assert response.status_code == 200
    job_id = response.json()["job_id"]
    assert client.get("/api/runs/test-run").json()["state"]["active_job_id"] == job_id
    for path in ["crop/edit", "calibration/edit", "series/edit", "series-selection/cancel", "select-page/0"]:
        assert client.post(f"/api/runs/test-run/{path}").status_code == 409
    assert client.delete("/api/runs/test-run").status_code == 409
    assert client.post("/api/runs/test-run/jobs/cancel", json={"job_id": "bad"}).status_code == 422
    response = client.post("/api/runs/test-run/jobs/cancel", json={"job_id": job_id})
    assert response.status_code == 200
    assert response.json()["state"]["stage"] == "crop_review"


def test_real_worker_completes_mock_crop(tmp_path):
    from PIL import Image
    state = make_run(tmp_path)
    Image.new("RGB", (160, 120), "white").save(tmp_path / state.run_id / "chart.png")
    state.canonical_image = Asset(path="chart.png", width=160, height=120)
    save_json(tmp_path / state.run_id / "state.json", state.model_dump(mode="json"))
    manager = JobManager(tmp_path)
    manager.open()
    try:
        manager.start(state.run_id, "crop", "crop")
        wait_until(lambda: state.run_id not in manager._jobs)
        result = manager.read_state(state.run_id)
        assert result.stage != "error", result.warnings
        assert result.crop is not None
        assert result.attempts
        assert result.active_job_id is None
    finally:
        manager.close()


def test_failed_termination_retains_lock_until_retry(manager, monkeypatch):
    job_id = manager.start("test-run", "test", "crop")
    job = manager._jobs["test-run"]
    wait_until(lambda: (job.path / "ready").exists())
    stop = manager._stop
    monkeypatch.setattr(manager, "_stop", lambda job: (_ for _ in ()).throw(HTTPException(503, "stop failed")))
    with pytest.raises(HTTPException) as exc:
        manager.cancel("test-run", job_id)
    assert exc.value.status_code == 503
    assert job.process.is_alive()
    assert manager.read_state("test-run").active_job_id == job_id
    with pytest.raises(HTTPException):
        with manager.edit("test-run"):
            pytest.fail("Still locked")
    monkeypatch.setattr(manager, "_stop", stop)
    assert manager.cancel("test-run", job_id)["outcome"] == "cancelled"


def test_concurrent_starts_only_launch_one_worker(manager):
    barrier = threading.Barrier(2)
    results = []
    def start():
        barrier.wait()
        try:
            results.append(manager.start("test-run", "test", "crop"))
        except HTTPException as exc:
            results.append(exc.status_code)
    threads = [threading.Thread(target=start) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert len(results) == 2
    assert results.count(409) == 1


def test_recovery_does_not_restore_under_a_live_worker(manager):
    job_id = manager.start("test-run", "test", "crop")
    job = manager._jobs["test-run"]
    wait_until(lambda: (job.path / "ready").exists())
    with pytest.raises(OSError):
        with FileLease(job.path / "worker.lock"):
            pytest.fail("Live worker owns this lease")
    manager.cancel("test-run", job_id)


def test_incomplete_event_record_is_not_returned(tmp_path):
    from app.logging_utils import read_events
    path = tmp_path / "events.jsonl"
    path.write_bytes(b'{"message":"complete"}\n{"message":"partial')
    assert read_events(path) == [{"message": "complete"}]


def test_parent_death_stops_blocked_worker_and_allows_startup_recovery(tmp_path):
    make_run(tmp_path)
    ctx = multiprocessing.get_context("spawn")
    parent, child = ctx.Pipe()
    supervisor = ctx.Process(target=doomed_supervisor, args=(str(tmp_path), child))
    supervisor.start()
    child.close()
    try:
        assert parent.poll(15)
        job_id = parent.recv()
        wait_until(lambda: (tmp_path / "test-run" / "request-blocked").exists())
        with pytest.raises(OSError):
            with FileLease(tmp_path / ".jobs" / job_id / "worker.lock"):
                pytest.fail("Blocked worker still owns its files")
        parent.send("crash")
        supervisor.join(timeout=10)
        assert supervisor.exitcode == 0
        recovered = JobManager(tmp_path)
        recovered.open()
        try:
            assert recovered.read_state("test-run").active_job is None
            assert not (tmp_path / "test-run" / "request-blocked").exists()
        finally:
            recovered.close()
    finally:
        if supervisor.is_alive():
            supervisor.terminate()
            supervisor.join(timeout=5)
        parent.close()


@pytest.mark.parametrize("job_id", [None, "a" * 32])
def test_startup_recovers_busy_run_without_a_job_record(tmp_path, job_id):
    state = make_run(tmp_path)
    state.stage = "y_calibration_review"
    state.active_job = "axis_calibration"
    state.active_job_id = job_id
    state.active_step_status = "Calibrating axis 2 of 2..."
    save_json(tmp_path / state.run_id / "state.json", state.model_dump(mode="json"))
    before = state.model_dump(exclude={"updated_at", "active_job", "active_job_id", "active_step_status"})
    manager = JobManager(tmp_path)
    manager.open()
    try:
        loaded = manager.read_state(state.run_id)
        assert loaded.active_job is loaded.active_job_id is loaded.active_step_status is None
        assert loaded.model_dump(exclude={"updated_at", "active_job", "active_job_id", "active_step_status"}) == before
        assert (tmp_path / state.run_id / "old.txt").read_text() == "original"
        with manager.edit(state.run_id):
            pass  # The restored run is editable, not merely visually unlocked.
        events_path = tmp_path / state.run_id / "events.jsonl"
        events = events_path.read_text()
        manager.read_state(state.run_id)
        manager.recover()
        assert events_path.read_text() == events  # Recovery is idempotent.
    finally:
        manager.close()


def test_loading_legacy_run_after_startup_clears_busy_flags(manager, monkeypatch):
    from app import main
    state = make_run(manager.root, "legacy")
    state.active_job = "crop"
    state.active_step_status = "Finding crop..."
    save_json(manager.root / state.run_id / "state.json", state.model_dump(mode="json"))
    monkeypatch.setattr(main, "job_manager", manager)
    response = TestClient(main.app).get("/api/runs/legacy")
    assert response.status_code == 200
    assert response.json()["state"]["active_job"] is None
    assert response.json()["state"]["active_step_status"] is None


def test_non_owner_does_not_clear_an_unknown_busy_run(tmp_path):
    state = make_run(tmp_path)
    state.active_job = "crop"
    save_json(tmp_path / state.run_id / "state.json", state.model_dump(mode="json"))
    assert JobManager(tmp_path).read_state(state.run_id).active_job == "crop"
