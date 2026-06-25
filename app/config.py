from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

from .models import ModelsFile, RunSettings
from .series_point_limits import series_data_point_limits


ROOT_DIR = Path(__file__).resolve().parents[1]
MODELS_PATH = ROOT_DIR / "models.json"
PROMPTS_PATH = ROOT_DIR / "prompts.yaml"


def _env_flag(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).lower() in {"1", "true", "yes", "y"}


class AppConfig:
    def __init__(self) -> None:
        load_dotenv(ROOT_DIR / ".env")
        self.root_dir = ROOT_DIR
        self.runs_dir = Path(os.getenv("APP_RUNS_DIR", "runs"))
        if not self.runs_dir.is_absolute():
            self.runs_dir = ROOT_DIR / self.runs_dir
        self.pdf_dpi = int(os.getenv("PDF_DPI", "200"))
        self.openai_api_key = os.getenv("OPENAI_API_KEY")
        self.gemini_api_key = os.getenv("GEMINI_API_KEY")
        self.openai_mock_mode = _env_flag("OPENAI_MOCK_MODE")
        self.show_debug_info = _env_flag("APP_SHOW_DEBUG_INFO", "true")
        self.max_crop_attempts = 3
        self.max_axis_attempts = 3
        self.max_series_attempts = 3
        self.series_min_data_points, self.series_max_data_points = series_data_point_limits()


@lru_cache
def get_config() -> AppConfig:
    config = AppConfig()
    config.runs_dir.mkdir(parents=True, exist_ok=True)
    return config


@lru_cache
def load_models() -> ModelsFile:
    with MODELS_PATH.open("r", encoding="utf-8") as handle:
        return ModelsFile.model_validate(json.load(handle))


def validate_run_settings(settings: RunSettings) -> RunSettings:
    model = load_models().get_enabled(settings.model_id)
    if settings.image_detail not in model.image_detail_options:
        raise ValueError(f"image detail {settings.image_detail!r} is not valid for {settings.model_id}")
    if settings.reasoning_effort and settings.reasoning_effort not in model.reasoning_efforts:
        raise ValueError(f"reasoning effort {settings.reasoning_effort!r} is not valid for {settings.model_id}")
    return settings


def api_key_for_model(settings: RunSettings) -> str | None:
    cfg = get_config()
    model = load_models().get_enabled(settings.model_id)
    if model.provider == "gemini":
        return cfg.gemini_api_key
    return cfg.openai_api_key


def missing_api_key_message(settings: RunSettings) -> str:
    model = load_models().get_enabled(settings.model_id)
    env_name = "GEMINI_API_KEY" if model.provider == "gemini" else "OPENAI_API_KEY"
    return f"{env_name} is required unless OPENAI_MOCK_MODE=true"
