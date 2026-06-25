from __future__ import annotations

import json
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

from .image_io import image_to_data_url
from .logging_utils import emit_event
from .models import RunSettings
from .token_usage import openai_token_usage_message

T = TypeVar("T", bound=BaseModel)


class OpenAIChartClient:
    def __init__(self, api_key: str | None, mock_mode: bool, run_dir: Path | None = None, run_id: str | None = None) -> None:
        self.api_key = api_key
        self.mock_mode = mock_mode
        self.run_dir = run_dir
        self.run_id = run_id
        self._client = None

    @property
    def client(self) -> Any:
        if self.mock_mode:
            raise RuntimeError("mock mode does not create an OpenAI client")
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY is required unless OPENAI_MOCK_MODE=true")
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI(api_key=self.api_key)
        return self._client

    def _reasoning(self, settings: RunSettings) -> dict[str, str] | None:
        if settings.reasoning_effort:
            return {"effort": settings.reasoning_effort}
        return None

    def _input(
        self,
        user_prompt: str,
        image_path: Path,
        detail: str,
        prefix_items: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        items = list(prefix_items or [])
        items.append(
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": user_prompt},
                    {"type": "input_image", "image_url": image_to_data_url(image_path), "detail": detail},
                ],
            }
        )
        return items

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
        emit_event(self.run_dir, "API", f"Calling {settings.model_id} for crop", run_id=self.run_id, stage="crop")
        prefix_items: list[dict[str, Any]] = []
        if previous_tool_call_id:
            prefix_items.append(
                {
                    "type": "function_call_output",
                    "call_id": previous_tool_call_id,
                    "output": json.dumps(previous_tool_output or {}, ensure_ascii=False),
                }
            )
        request: dict[str, Any] = {
            "model": settings.model_id,
            "instructions": system_prompt,
            "input": self._input(user_prompt, image_path, settings.image_detail, prefix_items=prefix_items),
            "store": True,
            "tools": [
                {
                    "type": "function",
                    "name": "propose_crop",
                    "description": "Return the crop-stage decision and, when needed, a complete chart crop bounding box in full_image_norm.",
                    "parameters": schema,
                    "strict": True,
                }
            ],
            "tool_choice": {"type": "function", "name": "propose_crop"},
        }
        if previous_response_id:
            request["previous_response_id"] = previous_response_id
        if prompt_cache_key:
            request["prompt_cache_key"] = prompt_cache_key
        reasoning = self._reasoning(settings)
        if reasoning:
            request["reasoning"] = reasoning
        response = self.client.responses.create(**request)
        response_dict = response.model_dump(mode="json") if hasattr(response, "model_dump") else response.to_dict()
        emit_event(self.run_dir, "API", openai_token_usage_message(response_dict), run_id=self.run_id, stage="crop")
        call = self._extract_function_call(response_dict)
        args = json.loads(call.get("arguments", "{}"))
        return request, {"raw": response_dict, "arguments": args, "response_id": response_dict.get("id"), "function_call_id": call.get("call_id")}

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
        request: dict[str, Any] = {
            "model": settings.model_id,
            "instructions": system_prompt,
            "input": self._input(user_prompt, image_path, settings.image_detail),
            "store": True,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": schema_name,
                    "schema": schema,
                    "strict": True,
                }
            },
        }
        if previous_response_id:
            request["previous_response_id"] = previous_response_id
        if prompt_cache_key:
            request["prompt_cache_key"] = prompt_cache_key
        reasoning = self._reasoning(settings)
        if reasoning:
            request["reasoning"] = reasoning
        response = self.client.responses.create(**request)
        response_dict = response.model_dump(mode="json") if hasattr(response, "model_dump") else response.to_dict()
        emit_event(self.run_dir, "API", openai_token_usage_message(response_dict), run_id=self.run_id)
        text = getattr(response, "output_text", None) or self._extract_output_text(response_dict)
        return request, {"raw": response_dict, "text": text, "json": json.loads(text), "response_id": response_dict.get("id")}

    def _extract_function_arguments(self, response: dict[str, Any]) -> dict[str, Any]:
        return json.loads(self._extract_function_call(response).get("arguments", "{}"))

    def _extract_function_call(self, response: dict[str, Any]) -> dict[str, Any]:
        for item in response.get("output", []):
            if item.get("type") == "function_call":
                return item
        raise RuntimeError("OpenAI response did not contain a function call")

    def _extract_output_text(self, response: dict[str, Any]) -> str:
        fragments: list[str] = []
        for item in response.get("output", []):
            for content in item.get("content", []):
                if content.get("type") in {"output_text", "text"}:
                    fragments.append(content.get("text", ""))
        if not fragments:
            raise RuntimeError("OpenAI response did not contain text output")
        return "".join(fragments)
