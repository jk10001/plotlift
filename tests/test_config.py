from __future__ import annotations

import pytest

from app import config as config_module
from app.models import RunSettings
from app.series_point_limits import line_series_data_point_limits, scatter_series_max_data_points


def test_debug_info_visibility_env_flag(monkeypatch) -> None:
    monkeypatch.setenv("APP_SHOW_DEBUG_INFO", "false")
    config_module.get_config.cache_clear()
    try:
        assert config_module.get_config().show_debug_info is False
    finally:
        config_module.get_config.cache_clear()


def test_new_point_limit_names_take_precedence_and_are_exposed(monkeypatch) -> None:
    monkeypatch.setenv("LINE_SERIES_MIN_DATA_POINTS", "3")
    monkeypatch.setenv("LINE_SERIES_MAX_DATA_POINTS", "8")
    monkeypatch.setenv("SCATTER_SERIES_MAX_DATA_POINTS", "17")
    monkeypatch.setenv("SERIES_MIN_DATA_POINTS", "6")
    monkeypatch.setenv("SERIES_MAX_DATA_POINTS", "12")
    config_module.get_config.cache_clear()
    try:
        config = config_module.get_config()
        assert (config.line_series_min_data_points, config.line_series_max_data_points) == (3, 8)
        assert config.scatter_series_max_data_points == 17
        assert config.configuration_warnings == []
    finally:
        config_module.get_config.cache_clear()


def test_deprecated_line_point_limit_names_are_fallbacks(monkeypatch) -> None:
    monkeypatch.setenv("LINE_SERIES_MIN_DATA_POINTS", "")
    monkeypatch.setenv("LINE_SERIES_MAX_DATA_POINTS", "")
    monkeypatch.setenv("SERIES_MIN_DATA_POINTS", "2")
    monkeypatch.setenv("SERIES_MAX_DATA_POINTS", "9")

    with pytest.warns(FutureWarning):
        assert line_series_data_point_limits() == (2, 9)


def test_scatter_point_limit_must_be_positive(monkeypatch) -> None:
    monkeypatch.setenv("SCATTER_SERIES_MAX_DATA_POINTS", "0")
    with pytest.raises(ValueError, match="SCATTER_SERIES_MAX_DATA_POINTS must be at least 1"):
        scatter_series_max_data_points()


def test_api_config_exposes_explicit_and_deprecated_point_limit_keys(monkeypatch) -> None:
    from app.main import api_config

    monkeypatch.setenv("LINE_SERIES_MIN_DATA_POINTS", "4")
    monkeypatch.setenv("LINE_SERIES_MAX_DATA_POINTS", "11")
    monkeypatch.setenv("SCATTER_SERIES_MAX_DATA_POINTS", "23")
    config_module.get_config.cache_clear()
    try:
        payload = api_config()
        assert payload["line_series_min_data_points"] == payload["series_min_data_points"] == 4
        assert payload["line_series_max_data_points"] == payload["series_max_data_points"] == 11
        assert payload["scatter_series_max_data_points"] == 23
        assert payload["configuration_warnings"] == []
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
