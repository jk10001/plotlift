from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


ROOT_DIR = Path(__file__).resolve().parents[1]

SERIES_MIN_DATA_POINTS_ENV = "SERIES_MIN_DATA_POINTS"
SERIES_MAX_DATA_POINTS_ENV = "SERIES_MAX_DATA_POINTS"

DEFAULT_SERIES_MIN_DATA_POINTS = 5
DEFAULT_SERIES_MAX_DATA_POINTS = 10


def series_data_point_limits() -> tuple[int, int]:
    load_dotenv(ROOT_DIR / ".env")
    min_points = _env_int(SERIES_MIN_DATA_POINTS_ENV, DEFAULT_SERIES_MIN_DATA_POINTS)
    max_points = _env_int(SERIES_MAX_DATA_POINTS_ENV, DEFAULT_SERIES_MAX_DATA_POINTS)
    if min_points < 1:
        raise ValueError(f"{SERIES_MIN_DATA_POINTS_ENV} must be at least 1")
    if max_points < min_points:
        raise ValueError(f"{SERIES_MAX_DATA_POINTS_ENV} must be greater than or equal to {SERIES_MIN_DATA_POINTS_ENV}")
    return min_points, max_points


def series_data_point_prompt_context() -> dict[str, str]:
    min_points, max_points = series_data_point_limits()
    return {
        "series_min_data_points": str(min_points),
        "series_max_data_points": str(max_points),
        "series_data_point_range": f"{min_points} to {max_points}",
    }


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
