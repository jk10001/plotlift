from __future__ import annotations

import json
from pathlib import Path

from colorama import Fore, Style, just_fix_windows_console

from .models import EventCategory, EventRecord


just_fix_windows_console()

COLORS: dict[str, str] = {
    "API": Fore.BLUE,
    "USER": Fore.GREEN,
    "ARTIFACT": Fore.YELLOW,
    "WARN": Fore.YELLOW,
    "ERROR": Fore.RED,
    "STAGE": Fore.CYAN,
    "SYSTEM": Fore.MAGENTA,
}


def emit_event(
    run_dir: Path | None,
    category: EventCategory,
    message: str,
    *,
    run_id: str | None = None,
    stage: str | None = None,
    attempt: int | None = None,
    artifact_path: str | None = None,
) -> EventRecord:
    event = EventRecord(
        category=category,
        message=message,
        run_id=run_id,
        stage=stage,
        attempt=attempt,
        artifact_path=artifact_path,
    )
    prefix = f"[{event.ts}][{category}]"
    if run_id:
        prefix += f"[RUN {run_id}]"
    if stage:
        prefix += f"[{stage}]"
    if attempt:
        prefix += f"[attempt {attempt}]"
    suffix = f" -> {artifact_path}" if artifact_path else ""
    color = COLORS.get(category, "")
    print(f"{color}{prefix} {message}{suffix}{Style.RESET_ALL}", flush=True)
    if run_dir:
        run_dir.mkdir(parents=True, exist_ok=True)
        with (run_dir / "events.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event.model_dump(), ensure_ascii=False) + "\n")
    return event
