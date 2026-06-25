from __future__ import annotations

from typing import Any


def openai_token_usage_message(response: dict[str, Any]) -> str:
    usage = response.get("usage") or {}
    input_details = usage.get("input_tokens_details") or {}
    output_details = usage.get("output_tokens_details") or {}
    return _usage_message(
        input_tokens=usage.get("input_tokens"),
        cache_input_tokens=input_details.get("cached_tokens"),
        output_tokens=usage.get("output_tokens"),
        reasoning_tokens=output_details.get("reasoning_tokens"),
        thinking_label="reasoning",
    )


def gemini_token_usage_message(response: dict[str, Any]) -> str:
    usage = response.get("usage_metadata") or response.get("usageMetadata") or {}
    output_tokens = _first_present(usage, "candidates_token_count", "candidatesTokenCount")
    thinking_tokens = _first_present(usage, "thoughts_token_count", "thoughtsTokenCount")
    return _usage_message(
        input_tokens=_first_present(usage, "prompt_token_count", "promptTokenCount"),
        cache_input_tokens=_first_present(usage, "cached_content_token_count", "cachedContentTokenCount"),
        output_tokens=_sum_present(output_tokens, thinking_tokens),
        reasoning_tokens=thinking_tokens,
        thinking_label="thinking",
    )


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


def _first_present(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping:
            return mapping[key]
    return None


def _sum_present(*values: Any) -> Any:
    present = [value for value in values if value is not None]
    if not present:
        return None
    if all(isinstance(value, int | float) for value in present):
        return sum(present)
    return present[0]
