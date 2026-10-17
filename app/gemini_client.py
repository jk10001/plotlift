from __future__ import annotations

import base64
import copy
import json
from pathlib import Path
from typing import Any

from .config import load_models
from .logging_utils import emit_event
from .models import RunSettings
from .token_usage import RunCostTracker

GEMINI_COORDINATE_MAX = 1000
INTERNAL_COORDINATE_MAX = 999
GEMINI_MAX_BOUNDED_ARRAY_ITEMS = 20
GEMINI_ARGON_TIMEOUT_SECONDS = 15 * 60


class GeminiChartClient:
    def __init__(self, api_key: str | None, mock_mode: bool, run_dir: Path | None = None, run_id: str | None = None) -> None:
        self.api_key = api_key
        self.mock_mode = mock_mode
        self.run_dir = run_dir
        self.run_id = run_id
        self._client = None
        self._cost_tracker = RunCostTracker(run_dir)

    @property
    def client(self) -> Any:
        if self.mock_mode:
            raise RuntimeError("mock mode does not create a Gemini client")
        if not self.api_key:
            raise RuntimeError("GEMINI_API_KEY is required unless OPENAI_MOCK_MODE=true")
        if self._client is None:
            from google import genai

            self._client = genai.Client(api_key=self.api_key)
        return self._client

    def call_crop_tool(
        self,
        *,
        settings: RunSettings,
        system_prompt: str,
        user_prompt: str,
        image_path: Path,
        schema: dict[str, Any],
        previous_response_id: str | None = None,
        previous_tool_call_id: str | None = None,
        previous_tool_output: dict[str, Any] | None = None,
        prompt_cache_key: str | None = None,
        conversation_history: list[dict[str, Any]] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        request, response = self.call_structured(
            settings=settings,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            image_path=image_path,
            schema_name="CropDecision",
            schema=schema,
            previous_response_id=previous_response_id,
            prompt_cache_key=prompt_cache_key,
            conversation_history=conversation_history,
        )
        request["previous_tool_call_id"] = previous_tool_call_id
        request["previous_tool_output"] = previous_tool_output
        return request, {
            "raw": response["raw"],
            "text": response.get("text"),
            "arguments": response["json"],
            "provider_arguments": response.get("provider_json"),
            "response_id": response.get("response_id"),
            "function_call_id": None,
        }

    def call_structured(
        self,
        *,
        settings: RunSettings,
        system_prompt: str,
        user_prompt: str,
        image_path: Path | None = None,
        image_paths: list[Path] | None = None,
        schema_name: str,
        schema: dict[str, Any],
        previous_response_id: str | None = None,
        prompt_cache_key: str | None = None,
        conversation_history: list[dict[str, Any]] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        emit_event(self.run_dir, "API", f"Calling {settings.model_id} for {schema_name}", run_id=self.run_id)
        paths = _ordered_image_paths(image_path, image_paths)
        # Interactions retains the full history and thought signatures server-side.
        # Only send the new turn, even when the shared stage interface supplies history.
        inputs = self._input(user_prompt, paths, settings.image_detail)
        payload = {
            "model": settings.model_id,
            "input": inputs,
            "store": True,
            **self._config(system_prompt, schema, settings),
        }
        if previous_response_id:
            payload["previous_interaction_id"] = previous_response_id
        logged_payload = copy.deepcopy(payload)
        for item in logged_payload["input"]:
            if item["type"] == "image":
                item["data"] = "<base64 omitted; see attempt image artifacts>"
        request: dict[str, Any] = {
            "provider": "gemini",
            "api": "interactions",
            "coordinate_scale": f"0-{GEMINI_COORDINATE_MAX}",
            **logged_payload,
        }
        if prompt_cache_key:
            request["prompt_cache_key"] = prompt_cache_key

        # Argon can spend several minutes thinking. The Interactions request
        # timeout uses seconds and is a transport option, separate from the body.
        request_options = {"timeout": GEMINI_ARGON_TIMEOUT_SECONDS} if settings.model_id == "gemini-4-argon" else {}
        response = self.client.interactions.create(**payload, **request_options)
        response_dict = response.model_dump(mode="json", exclude_none=True)
        model = load_models().get_enabled(settings.model_id)
        cost = self._cost_tracker.record_gemini(response_dict, model)
        emit_event(
            self.run_dir,
            "API",
            cost.message,
            run_id=self.run_id,
            call_cost_usd=cost.call_cost_usd,
            run_cost_usd=cost.run_cost_usd,
        )
        if response_dict.get("status") != "completed":
            raise RuntimeError(f"Gemini interaction did not complete (status={response_dict.get('status', 'missing')})")
        if not response_dict.get("id"):
            raise RuntimeError("Gemini interaction did not return an ID")
        text = getattr(response, "output_text", None) or self._extract_text(response_dict)
        if not text.strip():
            raise RuntimeError("Gemini interaction did not contain text output")
        provider_json = json.loads(text)
        internal_json = gemini_to_internal_coordinates(provider_json)
        return request, {
            "raw": response_dict,
            "text": text,
            "json": internal_json,
            "provider_json": provider_json,
            "response_id": response_dict["id"],
        }

    def _input(self, user_prompt: str, image_paths: list[Path], detail: str) -> list[dict[str, Any]]:
        if not user_prompt.strip():
            raise ValueError("Gemini requires a non-empty user prompt")
        images = [
            {
                "type": "image",
                "data": base64.b64encode(path.read_bytes()).decode("ascii"),
                "mime_type": _mime_type(path),
                **_media_resolution_kwargs(detail),
            }
            for path in image_paths
        ]
        return [*images, {"type": "text", "text": user_prompt}]

    def _config(self, system_prompt: str, schema: dict[str, Any], settings: RunSettings) -> dict[str, Any]:
        config: dict[str, Any] = {
            "system_instruction": _gemini_coordinate_prompt(system_prompt),
            "response_format": [{
                "type": "text",
                "mime_type": "application/json",
                "schema": gemini_schema(schema),
            }],
        }
        thinking_config = _thinking_config(settings)
        if thinking_config:
            config["generation_config"] = thinking_config
        return config

    def _extract_text(self, response: dict[str, Any]) -> str:
        # Match output_text: only the trailing model output, never thought summaries.
        fragments: list[str] = []
        for step in reversed(response.get("steps") or []):
            if step.get("type") != "model_output":
                break
            for content in reversed(step.get("content") or []):
                if content.get("type") != "text":
                    return "".join(reversed(fragments))
                fragments.append(content.get("text") or "")
        if not fragments:
            raise RuntimeError("Gemini interaction did not contain text output")
        return "".join(reversed(fragments))


def gemini_schema(schema: dict[str, Any]) -> dict[str, Any]:
    adapted = copy.deepcopy(schema)
    _adapt_schema_node(adapted)
    return adapted


def gemini_to_internal_coordinates(value: Any) -> Any:
    copied = copy.deepcopy(value)
    return _convert_coordinate_node(copied)


def _adapt_schema_node(node: Any) -> None:
    if isinstance(node, dict):
        node.pop("additionalProperties", None)
        max_items = node.get("maxItems")
        if isinstance(max_items, int) and max_items >= GEMINI_MAX_BOUNDED_ARRAY_ITEMS:
            # Gemini can reject high-cardinality nested array schemas as too complex.
            # The app still enforces configured point limits after parsing the response.
            node.pop("maxItems", None)
        title = node.get("title")
        if title == "NormPoint":
            for axis in ("x", "y"):
                prop = node.get("properties", {}).get(axis)
                if isinstance(prop, dict) and prop.get("maximum") == INTERNAL_COORDINATE_MAX:
                    prop["maximum"] = GEMINI_COORDINATE_MAX
        elif title == "NormBBox":
            for edge in ("left", "top", "right", "bottom"):
                prop = node.get("properties", {}).get(edge)
                if isinstance(prop, dict) and prop.get("maximum") == INTERNAL_COORDINATE_MAX:
                    prop["maximum"] = GEMINI_COORDINATE_MAX
        for value in node.values():
            _adapt_schema_node(value)
    elif isinstance(node, list):
        for item in node:
            _adapt_schema_node(item)


def _convert_coordinate_node(node: Any) -> Any:
    if isinstance(node, dict):
        if {"x", "y"}.issubset(node) and _is_coordinate(node["x"]) and _is_coordinate(node["y"]):
            node["x"] = _gemini_coord_to_internal(node["x"])
            node["y"] = _gemini_coord_to_internal(node["y"])
        if {"left", "top", "right", "bottom"}.issubset(node) and all(_is_coordinate(node[key]) for key in ("left", "top", "right", "bottom")):
            for key in ("left", "top", "right", "bottom"):
                node[key] = _gemini_coord_to_internal(node[key])
        for key, value in list(node.items()):
            node[key] = _convert_coordinate_node(value)
    elif isinstance(node, list):
        for index, item in enumerate(node):
            node[index] = _convert_coordinate_node(item)
    return node


def _is_coordinate(value: Any) -> bool:
    return isinstance(value, int) and 0 <= value <= GEMINI_COORDINATE_MAX


def _gemini_coord_to_internal(value: int) -> int:
    return round(value / GEMINI_COORDINATE_MAX * INTERNAL_COORDINATE_MAX)


def _gemini_coordinate_prompt(prompt: str) -> str:
    return (
        prompt.replace("0 to 999 inclusive", "0 to 1000 inclusive")
        .replace("0..999", "0..1000")
        .replace("0-999", "0-1000")
    )


def _media_resolution_kwargs(detail: str) -> dict[str, Any]:
    if detail == "auto":
        return {}
    if detail not in {"low", "medium", "high", "ultra_high"}:
        raise ValueError(f"Unsupported Gemini image detail: {detail}")
    return {"resolution": detail}


def _thinking_config(settings: RunSettings) -> dict[str, Any] | None:
    effort = settings.reasoning_effort
    if not effort or effort == "none" or settings.model_id.startswith("gemma-"):
        return None
    level = {"minimal": "minimal", "low": "low", "medium": "medium", "high": "high", "xhigh": "high"}.get(effort)
    if not level:
        return None
    return {"thinking_level": level}


def _mime_type(path: Path) -> str:
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }.get(path.suffix.lower(), "image/png")


def _ordered_image_paths(image_path: Path | None, image_paths: list[Path] | None) -> list[Path]:
    if image_paths is not None:
        if not image_paths:
            raise ValueError("at least one image path is required")
        return [Path(path) for path in image_paths]
    if image_path is None:
        raise ValueError("image_path or image_paths is required")
    return [Path(image_path)]
