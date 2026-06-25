from __future__ import annotations

from typing import Any

from openai.lib._pydantic import to_strict_json_schema
from pydantic import BaseModel

from .series_point_limits import series_data_point_limits


def strict_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Return an OpenAI strict-compatible JSON schema for a Pydantic model."""
    schema = to_strict_json_schema(model)
    _strip_nonessential_keywords(schema)
    _apply_series_point_limits(schema)
    return schema


def _strip_nonessential_keywords(node: Any) -> None:
    if isinstance(node, dict):
        node.pop("default", None)
        for value in node.values():
            _strip_nonessential_keywords(value)
    elif isinstance(node, list):
        for item in node:
            _strip_nonessential_keywords(item)


def _apply_series_point_limits(schema: dict[str, Any]) -> None:
    targets: list[dict[str, Any]] = []
    if schema.get("title") == "SeriesDigitizationOutput":
        targets.append(schema)
    defs = schema.get("$defs", {})
    if isinstance(defs, dict) and isinstance(defs.get("SeriesDigitizationOutput"), dict):
        targets.append(defs["SeriesDigitizationOutput"])

    if not targets:
        return

    min_points, max_points = series_data_point_limits()
    for target in targets:
        points_schema = target.get("properties", {}).get("points")
        if isinstance(points_schema, dict):
            points_schema["minItems"] = min_points
            points_schema["maxItems"] = max_points
