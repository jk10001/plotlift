from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path
from typing import Any

from .config import get_config
from .logging_utils import emit_event
from .models import RunState

REDACTED_JSON_KEYS = {"response_id", "previous_response_id", "thought_signature", "thoughtSignature"}


def new_run_id() -> str:
    return uuid.uuid4().hex[:10]


def run_dir(run_id: str) -> Path:
    return get_config().runs_dir / run_id


def relative_path(path: Path, base: Path) -> str:
    return path.relative_to(base).as_posix()


def ensure_run_dirs(run_id: str) -> Path:
    root = run_dir(run_id)
    for name in ["uploads", "pages", "crop", "x_axis", "y_axis", "series", "exports"]:
        (root / name).mkdir(parents=True, exist_ok=True)
    return root


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(redact_saved_json(data), handle, indent=2, ensure_ascii=False)


def redact_saved_json(data: Any) -> Any:
    return _redact_json_node(data, ())


def _redact_json_node(data: Any, path: tuple[str, ...]) -> Any:
    if isinstance(data, dict):
        redacted: dict[str, Any] = {}
        for key, value in data.items():
            if key in REDACTED_JSON_KEYS:
                continue
            if key == "id" and path and path[-1] == "raw":
                continue
            redacted[key] = _redact_json_node(value, (*path, key))
        return redacted
    if isinstance(data, list):
        return [_redact_json_node(item, path) for item in data]
    return data


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def save_state(state: RunState) -> None:
    state.touch()
    root = ensure_run_dirs(state.run_id)
    save_json(root / "state.json", state.model_dump(mode="json"))


def load_state(run_id: str) -> RunState:
    return RunState.model_validate(load_json(run_dir(run_id) / "state.json"))


def copy_upload(run_id: str, source_path: Path, original_filename: str) -> str:
    root = ensure_run_dirs(run_id)
    suffix = Path(original_filename).suffix.lower()
    target = root / "uploads" / f"original{suffix}"
    shutil.copyfile(source_path, target)
    emit_event(root, "ARTIFACT", "Saved original upload", run_id=run_id, artifact_path=relative_path(target, root))
    return relative_path(target, root)


def attempt_dir(run_id: str, stage: str, attempt_number: int, series_id: str | None = None) -> Path:
    root = ensure_run_dirs(run_id)
    if stage == "series" and series_id:
        path = root / "series" / series_id / f"attempt_{attempt_number:02d}"
    else:
        stage_dir = {"x": "x_axis", "y": "y_axis"}.get(stage, stage)
        path = root / stage_dir / f"attempt_{attempt_number:02d}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_attempt_json(
    run_id: str,
    stage: str,
    attempt_number: int,
    name: str,
    data: Any,
    *,
    series_id: str | None = None,
) -> str:
    root = ensure_run_dirs(run_id)
    path = attempt_dir(run_id, stage, attempt_number, series_id=series_id) / name
    save_json(path, data)
    emit_event(root, "ARTIFACT", f"Saved {stage} attempt {attempt_number} {name}", run_id=run_id, stage=stage, attempt=attempt_number, artifact_path=relative_path(path, root))
    return relative_path(path, root)


def safe_run_file(run_id: str, rel_path: str) -> Path:
    root = run_dir(run_id).resolve()
    target = (root / rel_path).resolve()
    if not str(target).startswith(str(root)):
        raise ValueError("requested path escapes run directory")
    if not target.exists():
        raise FileNotFoundError(rel_path)
    return target
