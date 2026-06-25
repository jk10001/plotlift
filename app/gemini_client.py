from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from .logging_utils import emit_event
from .models import RunSettings
from .token_usage import gemini_token_usage_message

GEMINI_COORDINATE_MAX = 1000
INTERNAL_COORDINATE_MAX = 999
GEMINI_MAX_BOUNDED_ARRAY_ITEMS = 20


class GeminiChartClient:
    def __init__(self, api_key: str | None, mock_mode: bool, run_dir: Path | None = None, run_id: str | None = None) -> None:
        self.api_key = api_key
        self.mock_mode = mock_mode
        self.run_dir = run_dir
        self.run_id = run_id
        self._client = None

    @property
    def client(self) -> Any:
        if self.mock_mode:
            raise RuntimeError("mock mode does not create a Gemini client")
        if not self.api_key:
            raise RuntimeError("GEMINI_API_KEY is required unless OPENAI_MOCK_MODE=true")
        if self._client is None:
            from google import genai

            self._client = genai.Client(api_key=self.api_key, http_options={"api_version": "v1alpha"})
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
        image_path: Path,
        schema_name: str,
        schema: dict[str, Any],
        previous_response_id: str | None = None,
        prompt_cache_key: str | None = None,
        conversation_history: list[dict[str, Any]] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        emit_event(self.run_dir, "API", f"Calling {settings.model_id} for {schema_name}", run_id=self.run_id)
        contents, request_contents = self._contents(user_prompt, image_path, settings.image_detail, history=conversation_history)
        config = self._config(system_prompt, schema, settings)
        request: dict[str, Any] = {
            "provider": "gemini",
            "model": settings.model_id,
            "coordinate_scale": f"0-{GEMINI_COORDINATE_MAX}",
            "contents": request_contents,
            "config": self._scrub_config(config),
        }
        if previous_response_id:
            request["previous_response_id"] = previous_response_id
        if prompt_cache_key:
            request["prompt_cache_key"] = prompt_cache_key

        response = self.client.models.generate_content(
            model=settings.model_id,
            contents=contents,
            config=config,
        )
        response_dict = response.model_dump(mode="json") if hasattr(response, "model_dump") else {}
        emit_event(self.run_dir, "API", gemini_token_usage_message(response_dict), run_id=self.run_id)
        text = getattr(response, "text", None) or self._extract_text(response_dict)
        provider_json = json.loads(text)
        internal_json = gemini_to_internal_coordinates(provider_json)
        return request, {
            "raw": response_dict,
            "text": text,
            "json": internal_json,
            "provider_json": provider_json,
            "response_id": response_dict.get("id") or response_dict.get("response_id"),
        }

    def _contents(
        self,
        user_prompt: str,
        image_path: Path,
        detail: str,
        *,
        history: list[dict[str, Any]] | None = None,
    ) -> tuple[list[Any], list[dict[str, Any]]]:
        from google.genai import types

        contents: list[Any] = []
        request_contents: list[dict[str, Any]] = []
        for turn in history or []:
            user_content, request_user_content = self._user_content(types, turn["user_prompt"], Path(turn["image_path"]), detail)
            contents.append(user_content)
            request_contents.append(request_user_content)
            contents.append(types.Content(role="model", parts=[types.Part(text=turn["model_text"])]))
            request_contents.append({"role": "model", "parts": [{"text": turn["model_text"]}]})
        user_content, request_user_content = self._user_content(types, user_prompt, image_path, detail)
        contents.append(user_content)
        request_contents.append(request_user_content)
        return contents, request_contents

    def _user_content(self, types: Any, user_prompt: str, image_path: Path, detail: str) -> tuple[Any, dict[str, Any]]:
        mime_type = _mime_type(image_path)
        image_part = types.Part(
            inline_data=types.Blob(mime_type=mime_type, data=image_path.read_bytes()),
            **_media_resolution_kwargs(detail),
        )
        content = types.Content(role="user", parts=[image_part, types.Part(text=user_prompt)])
        request_content = {
            "role": "user",
            "parts": [
                {
                    "inline_data": {"mime_type": mime_type, "data": "<bytes omitted; see attempt image artifacts>"},
                    **_media_resolution_kwargs(detail),
                },
                {"text": user_prompt},
            ],
        }
        return content, request_content

    def _config(self, system_prompt: str, schema: dict[str, Any], settings: RunSettings) -> dict[str, Any]:
        config: dict[str, Any] = {
            "system_instruction": _gemini_coordinate_prompt(system_prompt),
            "response_mime_type": "application/json",
            "response_json_schema": gemini_schema(schema),
        }
        thinking_config = _thinking_config(settings)
        if thinking_config:
            config["thinking_config"] = thinking_config
        return config

    def _scrub_config(self, config: dict[str, Any]) -> dict[str, Any]:
        scrubbed = copy.deepcopy(config)
        return scrubbed

    def _extract_text(self, response: dict[str, Any]) -> str:
        candidates = response.get("candidates") or []
        fragments: list[str] = []
        for candidate in candidates:
            content = candidate.get("content") or {}
            for part in content.get("parts") or []:
                text = part.get("text")
                if text:
                    fragments.append(text)
        if not fragments:
            raise RuntimeError("Gemini response did not contain text output")
        return "".join(fragments)


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
        if isinstance(max_items, int) and max_items > GEMINI_MAX_BOUNDED_ARRAY_ITEMS:
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
    level = {
        "low": "media_resolution_low",
        "medium": "media_resolution_medium",
        "high": "media_resolution_high",
        "ultra_high": "media_resolution_ultra_high",
    }.get(detail)
    if not level:
        return {}
    return {"media_resolution": {"level": level}}


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
