from __future__ import annotations

import os
import warnings
from pathlib import Path

from dotenv import load_dotenv


ROOT_DIR = Path(__file__).resolve().parents[1]

LINE_SERIES_MIN_DATA_POINTS_ENV = "LINE_SERIES_MIN_DATA_POINTS"
LINE_SERIES_MAX_DATA_POINTS_ENV = "LINE_SERIES_MAX_DATA_POINTS"
SCATTER_SERIES_MAX_DATA_POINTS_ENV = "SCATTER_SERIES_MAX_DATA_POINTS"
LEGACY_SERIES_MIN_DATA_POINTS_ENV = "SERIES_MIN_DATA_POINTS"
LEGACY_SERIES_MAX_DATA_POINTS_ENV = "SERIES_MAX_DATA_POINTS"

DEFAULT_LINE_SERIES_MIN_DATA_POINTS = 5
DEFAULT_LINE_SERIES_MAX_DATA_POINTS = 10
DEFAULT_SCATTER_SERIES_MAX_DATA_POINTS = 30


def line_series_data_point_limits() -> tuple[int, int]:
    load_dotenv(ROOT_DIR / ".env")
    min_points = _env_int_with_legacy_fallback(
        LINE_SERIES_MIN_DATA_POINTS_ENV,
        LEGACY_SERIES_MIN_DATA_POINTS_ENV,
        DEFAULT_LINE_SERIES_MIN_DATA_POINTS,
    )
    max_points = _env_int_with_legacy_fallback(
        LINE_SERIES_MAX_DATA_POINTS_ENV,
        LEGACY_SERIES_MAX_DATA_POINTS_ENV,
        DEFAULT_LINE_SERIES_MAX_DATA_POINTS,
    )
    if min_points < 1:
        raise ValueError(f"{LINE_SERIES_MIN_DATA_POINTS_ENV} must be at least 1")
    if max_points < min_points:
        raise ValueError(
            f"{LINE_SERIES_MAX_DATA_POINTS_ENV} must be greater than or equal to "
            f"{LINE_SERIES_MIN_DATA_POINTS_ENV}"
        )
    return min_points, max_points


def scatter_series_max_data_points() -> int:
    load_dotenv(ROOT_DIR / ".env")
    max_points = _env_int(SCATTER_SERIES_MAX_DATA_POINTS_ENV, DEFAULT_SCATTER_SERIES_MAX_DATA_POINTS)
    if max_points < 1:
        raise ValueError(f"{SCATTER_SERIES_MAX_DATA_POINTS_ENV} must be at least 1")
    return max_points


def series_data_point_prompt_context() -> dict[str, str]:
    line_min_points, line_max_points = line_series_data_point_limits()
    scatter_max_points = scatter_series_max_data_points()
    return {
        "line_series_min_data_points": str(line_min_points),
        "line_series_max_data_points": str(line_max_points),
        "line_series_data_point_range": f"{line_min_points} to {line_max_points}",
        "scatter_series_max_data_points": str(scatter_max_points),
    }


def point_limit_configuration_warnings() -> list[str]:
    load_dotenv(ROOT_DIR / ".env")
    messages: list[str] = []
    for new_name, legacy_name in (
        (LINE_SERIES_MIN_DATA_POINTS_ENV, LEGACY_SERIES_MIN_DATA_POINTS_ENV),
        (LINE_SERIES_MAX_DATA_POINTS_ENV, LEGACY_SERIES_MAX_DATA_POINTS_ENV),
    ):
        if not _has_env_value(new_name) and _has_env_value(legacy_name):
            messages.append(f"{legacy_name} is deprecated; use {new_name} instead")
    return messages


def series_data_point_limits() -> tuple[int, int]:
    """Deprecated compatibility alias for line_series_data_point_limits."""
    return line_series_data_point_limits()


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


def _env_int_with_legacy_fallback(name: str, legacy_name: str, default: int) -> int:
    if _has_env_value(name):
        return _env_int(name, default)
    if _has_env_value(legacy_name):
        warnings.warn(
            f"{legacy_name} is deprecated; use {name} instead",
            FutureWarning,
            stacklevel=2,
        )
        return _env_int(legacy_name, default)
    return default


def _has_env_value(name: str) -> bool:
    raw = os.getenv(name)
    return raw is not None and raw.strip() != ""
