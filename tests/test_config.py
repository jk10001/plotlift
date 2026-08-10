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
