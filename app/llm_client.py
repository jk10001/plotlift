from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from .config import api_key_for_model, load_models
from .models import RunSettings


class ChartLLMClient(Protocol):
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
        ...

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
        ...


def create_chart_client(settings: RunSettings, *, run_dir: Path | None = None, run_id: str | None = None) -> ChartLLMClient:
    model = load_models().get_enabled(settings.model_id)
    api_key = api_key_for_model(settings)
    if model.provider == "gemini":
        from .gemini_client import GeminiChartClient

        return GeminiChartClient(api_key=api_key, mock_mode=settings.mock_mode, run_dir=run_dir, run_id=run_id)

    from .openai_client import OpenAIChartClient

    return OpenAIChartClient(api_key=api_key, mock_mode=settings.mock_mode, run_dir=run_dir, run_id=run_id)
