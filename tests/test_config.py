from __future__ import annotations

from app import config as config_module
from app.models import RunSettings


def test_debug_info_visibility_env_flag(monkeypatch) -> None:
    monkeypatch.setenv("APP_SHOW_DEBUG_INFO", "false")
    config_module.get_config.cache_clear()
    try:
        assert config_module.get_config().show_debug_info is False
    finally:
        config_module.get_config.cache_clear()


def test_gpt_56_family_is_enabled_with_documented_options() -> None:
    models = config_module.load_models()

    for model_id, label in (
        ("gpt-5.6-sol", "GPT-5.6 Sol"),
        ("gpt-5.6-terra", "GPT-5.6 Terra"),
        ("gpt-5.6-luna", "GPT-5.6 Luna"),
    ):
        model = models.get_enabled(model_id)
        assert model.label == label
        assert model.family == "gpt-5.6"
        assert model.provider == "openai"
        assert model.reasoning_efforts == ["none", "low", "medium", "high", "xhigh", "max"]
        assert model.default_reasoning_effort == "medium"
        assert model.image_detail_options == ["high", "auto", "low", "original"]
        assert model.default_image_detail == "high"
        config_module.validate_run_settings(
            RunSettings(model_id=model_id, image_detail="original", reasoning_effort="max")
        )


def test_all_models_have_non_negative_token_prices() -> None:
    for model in config_module.load_models().models:
        assert model.input_cost_per_million_tokens_usd >= 0
        assert model.cached_input_cost_per_million_tokens_usd >= 0
        assert model.output_cost_per_million_tokens_usd >= 0
        assert model.cached_input_cost_per_million_tokens_usd <= model.input_cost_per_million_tokens_usd
        long_context_prices = (
            model.long_context_input_cost_per_million_tokens_usd,
            model.long_context_cached_input_cost_per_million_tokens_usd,
            model.long_context_output_cost_per_million_tokens_usd,
        )
        if model.long_context_threshold_tokens is None:
            assert long_context_prices == (None, None, None)
        else:
            assert all(price is not None for price in long_context_prices)
