from __future__ import annotations

from app import config as config_module


def test_debug_info_visibility_env_flag(monkeypatch) -> None:
    monkeypatch.setenv("APP_SHOW_DEBUG_INFO", "false")
    config_module.get_config.cache_clear()
    try:
        assert config_module.get_config().show_debug_info is False
    finally:
        config_module.get_config.cache_clear()
