from __future__ import annotations

from app.config import load_models, validate_run_settings
from app.gemini_client import GeminiChartClient, _gemini_coordinate_prompt, _thinking_config, gemini_schema, gemini_to_internal_coordinates
from app.models import CropConversationResponse, RunSettings, SeriesDigitizationConversationResponse
from app.openai_schema import strict_json_schema


def test_gemini_response_coordinates_are_converted_to_internal_scale() -> None:
    provider_payload = {
        "proposal": {
            "bbox": {"left": 0, "top": 500, "right": 1000, "bottom": 1000},
            "points": [{"crop_image_norm": {"x": 1000, "y": 250}}],
        }
    }

    converted = gemini_to_internal_coordinates(provider_payload)

    assert converted["proposal"]["bbox"] == {"left": 0, "top": 500, "right": 999, "bottom": 999}
    assert converted["proposal"]["points"][0]["crop_image_norm"] == {"x": 999, "y": 250}


def test_gemini_schema_uses_provider_coordinate_scale() -> None:
    schema = gemini_schema(strict_json_schema(CropConversationResponse))

    assert schema["$defs"]["NormBBox"]["properties"]["left"]["maximum"] == 1000
    assert "additionalProperties" not in schema["$defs"]["NormBBox"]


def test_gemini_schema_relaxes_large_series_point_array_bounds(monkeypatch) -> None:
    monkeypatch.setenv("SERIES_MIN_DATA_POINTS", "5")
    monkeypatch.setenv("SERIES_MAX_DATA_POINTS", "30")

    openai_schema = strict_json_schema(SeriesDigitizationConversationResponse)
    gemini_adapted = gemini_schema(openai_schema)
    points_schema = gemini_adapted["$defs"]["SeriesDigitizationOutput"]["properties"]["points"]

    assert openai_schema["$defs"]["SeriesDigitizationOutput"]["properties"]["points"]["maxItems"] == 30
    assert points_schema["minItems"] == 5
    assert "maxItems" not in points_schema


def test_gemini_prompt_coordinate_contract_uses_provider_scale() -> None:
    prompt = "Normalized coordinates are integer coordinates from 0 to 999 inclusive."

    assert "0 to 1000 inclusive" in _gemini_coordinate_prompt(prompt)


def test_gemini_thinking_config_maps_reasoning_effort() -> None:
    config = _thinking_config(RunSettings(model_id="gemini-3.5-flash", image_detail="high", reasoning_effort="medium"))

    assert config == {"thinking_level": "medium"}
    assert _thinking_config(RunSettings(model_id="gemma-4-31b-it", image_detail="auto", reasoning_effort="none")) is None


def test_gemini_35_flash_is_enabled_with_expected_options() -> None:
    model = load_models().get_enabled("gemini-3.5-flash")

    assert model.label == "Gemini 3.5 Flash"
    assert model.provider == "gemini"
    assert model.reasoning_efforts == ["minimal", "low", "medium", "high"]
    assert model.default_reasoning_effort == "medium"
    assert model.image_detail_options == ["auto", "low", "medium", "high", "ultra_high"]
    assert model.default_image_detail == "high"
    validate_run_settings(RunSettings(model_id="gemini-3.5-flash", image_detail="high", reasoning_effort="medium"))


def test_gemini_generate_content_config_uses_sdk_schema_fields() -> None:
    client = GeminiChartClient(api_key="test-key", mock_mode=False)
    config = client._config(
        "Normalized coordinates are integer coordinates from 0 to 999 inclusive.",
        strict_json_schema(CropConversationResponse),
        RunSettings(model_id="gemini-3.5-flash", image_detail="high", reasoning_effort="medium"),
    )

    assert config["response_mime_type"] == "application/json"
    assert "response_json_schema" in config
    assert "response_format" not in config
