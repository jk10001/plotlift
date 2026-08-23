from __future__ import annotations

from pathlib import Path

import pytest

from app.config import load_models
from app.logging_utils import emit_event
from app.token_usage import RunCostTracker


def test_openai_call_cost_uses_discounted_cached_input() -> None:
    model = load_models().get_enabled("gpt-5.4-mini")
    response = {
        "usage": {
            "input_tokens": 1_000,
            "input_tokens_details": {"cached_tokens": 400},
            "output_tokens": 200,
            "output_tokens_details": {"reasoning_tokens": 50},
        }
    }

    cost = RunCostTracker(None).record_openai(response, model)

    assert cost.call_cost_usd == pytest.approx(0.00138)
    assert cost.run_cost_usd == pytest.approx(0.00138)
    assert "cost=$0.001380" in cost.message
    assert "run total=$0.001380" in cost.message


def test_gemini_call_cost_includes_thinking_tokens() -> None:
    model = load_models().get_enabled("gemini-3.7-flash")
    response = {
        "usageMetadata": {
            "promptTokenCount": 1_000,
            "cachedContentTokenCount": 100,
            "candidatesTokenCount": 200,
            "thoughtsTokenCount": 50,
        }
    }

    cost = RunCostTracker(None).record_gemini(response, model)

    assert cost.call_cost_usd == pytest.approx(0.00162)
    assert "total output inc. thinking=250" in cost.message
    assert "thinking=50" in cost.message


def test_long_context_call_uses_the_models_higher_pricing_tier() -> None:
    model = load_models().get_enabled("gemini-3.1-pro-preview")
    response = {"usageMetadata": {"promptTokenCount": 200_001, "candidatesTokenCount": 1_000}}

    cost = RunCostTracker(None).record_gemini(response, model)

    assert cost.call_cost_usd == pytest.approx(0.818004)


def test_run_cost_resumes_from_the_event_log(tmp_path: Path) -> None:
    model = load_models().get_enabled("gpt-5.6-luna")
    response = {"usage": {"input_tokens": 1_000, "output_tokens": 1_000}}
    first = RunCostTracker(tmp_path).record_openai(response, model)
    emit_event(
        tmp_path,
        "API",
        first.message,
        call_cost_usd=first.call_cost_usd,
        run_cost_usd=first.run_cost_usd,
    )

    second = RunCostTracker(tmp_path).record_openai(response, model)

    assert first.call_cost_usd == pytest.approx(0.0014)
    assert second.run_cost_usd == pytest.approx(0.0028)
    assert "run total=$0.002800" in second.message
