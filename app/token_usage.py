from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from .models import ModelOption


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: Any
    cache_input_tokens: Any
    output_tokens: Any
    reasoning_tokens: Any
    thinking_label: str

    def message(self) -> str:
        return _usage_message(
            input_tokens=self.input_tokens,
            cache_input_tokens=self.cache_input_tokens,
            output_tokens=self.output_tokens,
            reasoning_tokens=self.reasoning_tokens,
            thinking_label=self.thinking_label,
        )


@dataclass(frozen=True)
class CallCostRecord:
    message: str
    call_cost_usd: float | None
    run_cost_usd: float


class RunCostTracker:
    def __init__(self, run_dir: Path | None) -> None:
        self._total_cost_usd = _load_existing_run_cost(run_dir)

    def record_openai(self, response: dict[str, Any], model: ModelOption) -> CallCostRecord:
        return self._record(openai_token_usage(response), model)

    def record_gemini(self, response: dict[str, Any], model: ModelOption) -> CallCostRecord:
        return self._record(gemini_token_usage(response), model)

    def _record(self, usage: TokenUsage, model: ModelOption) -> CallCostRecord:
        call_cost = _call_cost_usd(usage, model)
        if call_cost is not None:
            self._total_cost_usd += call_cost
        cost_text = "n/a" if call_cost is None else _format_usd(call_cost)
        message = f"{usage.message()}, cost={cost_text}, run total={_format_usd(self._total_cost_usd)}"
        return CallCostRecord(
            message=message,
            call_cost_usd=float(call_cost) if call_cost is not None else None,
            run_cost_usd=float(self._total_cost_usd),
        )


def openai_token_usage(response: dict[str, Any]) -> TokenUsage:
    usage = response.get("usage") or {}
    input_details = usage.get("input_tokens_details") or {}
    output_details = usage.get("output_tokens_details") or {}
    return TokenUsage(
        input_tokens=usage.get("input_tokens"),
        cache_input_tokens=input_details.get("cached_tokens"),
        output_tokens=usage.get("output_tokens"),
        reasoning_tokens=output_details.get("reasoning_tokens"),
        thinking_label="reasoning",
    )


def gemini_token_usage(response: dict[str, Any]) -> TokenUsage:
    usage = response.get("usage_metadata") or response.get("usageMetadata") or {}
    output_tokens = _first_present(usage, "candidates_token_count", "candidatesTokenCount")
    thinking_tokens = _first_present(usage, "thoughts_token_count", "thoughtsTokenCount")
    return TokenUsage(
        input_tokens=_first_present(usage, "prompt_token_count", "promptTokenCount"),
        cache_input_tokens=_first_present(usage, "cached_content_token_count", "cachedContentTokenCount"),
        output_tokens=_sum_present(output_tokens, thinking_tokens),
        reasoning_tokens=thinking_tokens,
        thinking_label="thinking",
    )


def openai_token_usage_message(response: dict[str, Any]) -> str:
    return openai_token_usage(response).message()


def gemini_token_usage_message(response: dict[str, Any]) -> str:
    return gemini_token_usage(response).message()


def _call_cost_usd(usage: TokenUsage, model: ModelOption) -> Decimal | None:
    input_tokens = _token_decimal(usage.input_tokens)
    cached_tokens = _token_decimal(usage.cache_input_tokens)
    output_tokens = _token_decimal(usage.output_tokens)
    if input_tokens is None and output_tokens is None:
        return None

    input_tokens = input_tokens or Decimal(0)
    cached_tokens = min(cached_tokens or Decimal(0), input_tokens)
    output_tokens = output_tokens or Decimal(0)
    uncached_tokens = input_tokens - cached_tokens
    input_rate = model.input_cost_per_million_tokens_usd
    cached_input_rate = model.cached_input_cost_per_million_tokens_usd
    output_rate = model.output_cost_per_million_tokens_usd
    if model.long_context_threshold_tokens is not None and input_tokens > model.long_context_threshold_tokens:
        if model.long_context_input_cost_per_million_tokens_usd is not None:
            input_rate = model.long_context_input_cost_per_million_tokens_usd
        if model.long_context_cached_input_cost_per_million_tokens_usd is not None:
            cached_input_rate = model.long_context_cached_input_cost_per_million_tokens_usd
        if model.long_context_output_cost_per_million_tokens_usd is not None:
            output_rate = model.long_context_output_cost_per_million_tokens_usd
    per_million = Decimal(1_000_000)
    return (
        uncached_tokens * Decimal(str(input_rate))
        + cached_tokens * Decimal(str(cached_input_rate))
        + output_tokens * Decimal(str(output_rate))
    ) / per_million


def _load_existing_run_cost(run_dir: Path | None) -> Decimal:
    if run_dir is None:
        return Decimal(0)
    events_path = run_dir / "events.jsonl"
    if not events_path.exists():
        return Decimal(0)

    latest_total: Decimal | None = None
    summed_calls = Decimal(0)
    try:
        with events_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    event = json.loads(line)
                except (json.JSONDecodeError, TypeError):
                    continue
                call_cost = _decimal_or_none(event.get("call_cost_usd"))
                run_cost = _decimal_or_none(event.get("run_cost_usd"))
                if call_cost is not None:
                    summed_calls += call_cost
                if run_cost is not None:
                    latest_total = run_cost
    except OSError:
        return Decimal(0)
    return latest_total if latest_total is not None else summed_calls


def _usage_message(
    *,
    input_tokens: Any,
    cache_input_tokens: Any,
    output_tokens: Any,
    reasoning_tokens: Any,
    thinking_label: str,
) -> str:
    return (
        "API token usage: "
        f"input={_format_token_count(input_tokens)}, "
        f"cache input={_format_token_count(cache_input_tokens)}, "
        f"total output inc. thinking={_format_token_count(output_tokens)}, "
        f"{thinking_label}={_format_token_count(reasoning_tokens)}"
    )


def _format_token_count(value: Any) -> str:
    if value is None:
        return "n/a"
    return str(value)


def _format_usd(value: Decimal) -> str:
    precision = 8 if value != 0 and abs(value) < Decimal("0.000001") else 6
    return f"${value:.{precision}f}"


def _token_decimal(value: Any) -> Decimal | None:
    result = _decimal_or_none(value)
    if result is None or result < 0:
        return None
    return result


def _decimal_or_none(value: Any) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, int | float | Decimal):
        return None
    try:
        return Decimal(str(value))
    except ArithmeticError:
        return None


def _first_present(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping:
            return mapping[key]
    return None


def _sum_present(*values: Any) -> Any:
    present = [value for value in values if value is not None]
    if not present:
        return None
    if all(isinstance(value, int | float) and not isinstance(value, bool) for value in present):
        return sum(present)
    return present[0]
