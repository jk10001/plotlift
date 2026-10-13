"""Importable process entry point; no web-server objects or clients cross processes."""
from __future__ import annotations

import os
import json
import threading
from pathlib import Path

from .artifacts import load_state, save_json, save_state
from .config import get_config
from .llm_client import create_chart_client
from .models import CalibrationState
from .prompts import load_prompt_pack
from .stages.calibration import run_axis_identification_stage, run_axis_stage
from .stages.crop import run_crop_stage
from .stages.series import retry_series_digitization, run_selected_series_stage, run_series_stage


def run_operation(state, prompts, client, operation: str, parameters: dict):
    if operation == "crop":
        return run_crop_stage(state, prompts, client)
    if operation == "axis":
        return run_axis_stage(state, parameters["axis"], prompts, client)
    if operation == "series":
        return run_series_stage(state, prompts, client)
    if operation == "series_selection":
        return run_selected_series_stage(state, prompts, client, parameters["selected_indexes"])
    if operation == "series_retry":
        return retry_series_digitization(state, prompts, client, parameters["series_id"], mode=parameters["mode"])
    if operation != "calibration":
        raise ValueError(f"Unknown job operation: {operation}")
    if not state.crop or not state.crop.approved:
        raise ValueError("approved crop is required before axis calibration")
    state.calibration = CalibrationState()
    state.pending_series = []
    state.series = []
    state.active_step_status = "Identifying axes..."
    save_state(state)
    state = run_axis_identification_stage(state, prompts, client)
    axes = list(state.calibration.usable_axes())
    if not axes:
        raise ValueError("axis identification did not find any usable axes")
    for direction in ("x", "y"):
        if not any(axis.direction == direction for axis in axes):
            raise ValueError(f"axis identification did not find a usable {direction}-axis")
    for index, axis in enumerate(axes, start=1):
        state.active_step_status = f"Calibrating axis {index} of {len(axes)}..."
        save_state(state)
        state = run_axis_stage(state, axis, prompts, client)
    state.stage = "calibration_ready"
    return state


def worker_main(runs_root: str, run_id: str, operation: str, parameters: dict, job_path: str, control) -> None:
    from .jobs import FileLease

    # A restarted server waits for this lease before recovering the snapshot.
    with FileLease(Path(job_path) / "worker.lock"):
        if json.loads((Path(job_path) / "job.json").read_text())["phase"] != "running":
            return
        try:
            if control.recv() != "start":
                return
        except EOFError:
            return

        def watch_parent() -> None:
            try:
                control.recv()
            except (EOFError, OSError):
                pass
            # Parent death must stop even an SDK request blocked in native I/O.
            os._exit(2)

        threading.Thread(target=watch_parent, daemon=True).start()
        get_config().runs_dir = Path(runs_root)
        try:
            state = load_state(run_id)
            client = create_chart_client(state.settings, run_dir=Path(runs_root) / run_id, run_id=run_id)
            result = run_operation(state, load_prompt_pack(), client, operation, parameters)
            save_state(result)
            save_json(Path(job_path) / "result.json", {"ok": True})
        except Exception as exc:  # noqa: BLE001
            save_json(Path(job_path) / "result.json", {"ok": False, "error": str(exc)})
