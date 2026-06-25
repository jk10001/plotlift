from __future__ import annotations

from pathlib import Path
from typing import Any

from .artifacts import relative_path, save_json


def prompt_cache_key(run_id: str, stage: str, series_id: str | None = None) -> str:
    suffix = f":{series_id}" if series_id else ""
    return f"plotlift:{run_id}:{stage}{suffix}"


def response_id(response: dict[str, Any]) -> str | None:
    raw = response.get("raw")
    if isinstance(raw, dict):
        return response.get("response_id") or raw.get("id")
    return response.get("response_id")


def save_conversation_entry(
    *,
    root: Path,
    run_id: str,
    stage: str,
    entry: dict[str, Any],
    entries: list[dict[str, Any]],
    series_id: str | None = None,
) -> str:
    entries.append(entry)
    if stage == "series" and series_id:
        path = root / "series" / series_id / "conversation.json"
    else:
        path = root / stage / "conversation.json"
    save_json(path, {"run_id": run_id, "stage": stage, "series_id": series_id, "attempts": entries})
    return relative_path(path, root)
