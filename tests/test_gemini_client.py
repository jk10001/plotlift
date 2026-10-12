from __future__ import annotations

import base64
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config import load_models, validate_run_settings
from app.gemini_client import GeminiChartClient, _gemini_coordinate_prompt, _thinking_config, gemini_schema, gemini_to_internal_coordinates
from app.models import (
    CropConversationResponse,
    RunSettings,
    ScatterSeriesDigitizationConversationResponse,
    SeriesDigitizationConversationResponse,
)
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


def test_gemini_schema_relaxes_series_point_array_bounds_at_complexity_limit(monkeypatch) -> None:
    monkeypatch.setenv("LINE_SERIES_MIN_DATA_POINTS", "5")

    for max_points, should_relax in ((19, False), (20, True), (30, True)):
        monkeypatch.setenv("LINE_SERIES_MAX_DATA_POINTS", str(max_points))
        openai_schema = strict_json_schema(SeriesDigitizationConversationResponse)
        gemini_adapted = gemini_schema(openai_schema)
        openai_points = openai_schema["$defs"]["SeriesDigitizationOutput"]["properties"]["points"]
        gemini_points = gemini_adapted["$defs"]["SeriesDigitizationOutput"]["properties"]["points"]

        assert openai_points["maxItems"] == max_points
        assert gemini_points["minItems"] == 5
        assert ("maxItems" not in gemini_points) is should_relax


def test_gemini_schema_relaxes_scatter_max_but_keeps_minimum(monkeypatch) -> None:
    monkeypatch.setenv("SCATTER_SERIES_MAX_DATA_POINTS", "30")
    openai_schema = strict_json_schema(ScatterSeriesDigitizationConversationResponse)
    gemini_adapted = gemini_schema(openai_schema)
    openai_points = openai_schema["$defs"]["ScatterSeriesDigitizationOutput"]["properties"]["points"]
    gemini_points = gemini_adapted["$defs"]["ScatterSeriesDigitizationOutput"]["properties"]["points"]

    assert openai_points["maxItems"] == 30
    assert gemini_points["minItems"] == 1
    assert "maxItems" not in gemini_points


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


def test_gemini_38_flash_is_enabled_with_expected_options() -> None:
    model = load_models().get_enabled("gemini-3.8-flash")

    assert model.label == "Gemini 3.8 Flash"
    assert model.family == "gemini-3.8"
    assert model.provider == "gemini"
    assert model.reasoning_efforts == ["low", "medium", "high"]
    assert model.default_reasoning_effort == "medium"
    assert model.image_detail_options == ["auto", "low", "medium", "high", "ultra_high"]
    assert model.default_image_detail == "high"
    validate_run_settings(RunSettings(model_id="gemini-3.8-flash", image_detail="ultra_high", reasoning_effort="medium"))
    with pytest.raises(ValueError, match="reasoning effort"):
        validate_run_settings(RunSettings(model_id="gemini-3.8-flash", image_detail="high", reasoning_effort="minimal"))


@pytest.mark.parametrize("model", [m for m in load_models().enabled_models() if m.provider == "gemini"])
def test_interactions_options_validate_with_installed_sdk(model, tmp_path):
    from google.genai.interactions import CreateModelInteraction

    path = tmp_path / "chart.png"
    path.write_bytes(b"image")
    client = GeminiChartClient(api_key=None, mock_mode=True)
    for effort in model.reasoning_efforts:
        for detail in model.image_detail_options:
            settings = RunSettings(model_id=model.id, image_detail=detail, reasoning_effort=effort)
            config = client._config("0 to 999 inclusive", strict_json_schema(CropConversationResponse), settings)
            payload = dict(model=model.id, input=client._input("inspect", [path], detail), store=True, **config)
            CreateModelInteraction.model_validate(payload)
            assert config["generation_config"] == {"thinking_level": effort}
            assert "0 to 1000 inclusive" in config["system_instruction"]
            assert payload["input"][0].get("resolution") == (None if detail == "auto" else detail)
            assert not {"temperature", "top_p", "top_k", "candidate_count", "thinking_budget"}.intersection(config)


class FakeInteractions:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def create(self, **payload):
        self.calls.append(copy.deepcopy(payload))
        raw = next(self.responses)
        return SimpleNamespace(model_dump=lambda **kwargs: copy.deepcopy(raw), output_text=None)


def interaction(text='{"x": 1000, "y": 500}', **updates):
    raw = {
        "id": "interaction-1", "status": "completed",
        "steps": [
            {"type": "thought", "signature": "private-signature", "summary": "not JSON"},
            {"type": "model_output", "content": [{"type": "text", "text": text}]},
        ],
        "usage": {"total_input_tokens": 1000, "total_cached_tokens": 100,
                  "total_output_tokens": 200, "total_thought_tokens": 50},
    }
    raw.update(updates)
    return raw


def test_interactions_chaining_images_and_artifacts(tmp_path):
    from app.artifacts import save_json, load_json

    images = [tmp_path / "first.png", tmp_path / "second.jpg"]
    for path in images:
        path.write_bytes(path.name.encode())
    fake = FakeInteractions([interaction(), interaction(id="interaction-2"), interaction(id="new-chain")])
    client = GeminiChartClient(api_key="test-key", mock_mode=False)
    client._client = SimpleNamespace(interactions=fake)
    kwargs = dict(settings=RunSettings(model_id="gemini-3.8-flash", image_detail="high"),
                  system_prompt="system", schema_name="points", schema={"type": "object"})
    request, response = client.call_structured(**kwargs, user_prompt="inspect", image_paths=images)
    assert response["json"] == {"x": 999, "y": 500}
    assert response["provider_json"] == {"x": 1000, "y": 500}
    assert [base64.b64decode(i["data"]) for i in fake.calls[0]["input"][:-1]] == [p.name.encode() for p in images]
    assert [i["mime_type"] for i in fake.calls[0]["input"][:-1]] == ["image/png", "image/jpeg"]
    next_request, next_response = client.call_structured(
        **kwargs, user_prompt="review", image_path=images[1], previous_response_id=response["response_id"],
        conversation_history=[{"image_path": "must-not-read", "model_text": "old"}], prompt_cache_key="local-only")
    assert fake.calls[1]["previous_interaction_id"] == "interaction-1"
    assert len(fake.calls[1]["input"]) == 2
    assert fake.calls[1]["input"][-1] == {"type": "text", "text": "review"}
    assert "prompt_cache_key" not in fake.calls[1]
    for key in ("system_instruction", "response_format", "generation_config", "store"):
        assert fake.calls[0][key] == fake.calls[1][key]
    client.call_structured(**kwargs, user_prompt="restart", image_path=images[0])
    assert "previous_interaction_id" not in fake.calls[2]
    artifact = tmp_path / "artifact.json"
    save_json(artifact, {"request": next_request, "response": next_response, "wire": fake.calls[1]})
    saved = json.dumps(load_json(artifact))
    assert "interaction-1" not in saved and "interaction-2" not in saved
    assert "private-signature" not in saved
    assert base64.b64encode(images[1].name.encode()).decode() not in saved
    assert response["raw"]["steps"][0]["signature"] == "private-signature"
    assert "omitted" in request["input"][0]["data"]


@pytest.mark.parametrize("raw, error", [
    (interaction(status="failed"), "did not complete"),
    (interaction(status="cancelled"), "did not complete"),
    (interaction(status="in_progress"), "did not complete"),
    (interaction(status="requires_action"), "did not complete"),
    (interaction(id=None), "ID"),
    (interaction(text=""), "text output"),
    (interaction(steps=[]), "text output"),
    (interaction(text="not json"), "Expecting value"),
])
def test_interactions_invalid_responses_fail(raw, error, tmp_path):
    path = tmp_path / "chart.png"
    path.write_bytes(b"image")
    client = GeminiChartClient(api_key="test-key", mock_mode=False)
    fake = FakeInteractions([raw])
    client._client = SimpleNamespace(interactions=fake)
    with pytest.raises((RuntimeError, ValueError), match=error):
        client.call_structured(settings=RunSettings(model_id="gemini-3.8-flash"), system_prompt="system",
                               user_prompt="inspect", image_path=path, schema_name="test", schema={"type": "object"})
    assert len(fake.calls) == 1


def test_interactions_crop_preserves_shared_contract(tmp_path):
    path = tmp_path / "chart.png"
    path.write_bytes(b"image")
    raw = interaction(text='{"response_kind":"accept_previous","revision_reason":null,"proposal":null}')
    client = GeminiChartClient(api_key="test-key", mock_mode=False)
    fake = FakeInteractions([raw])
    client._client = SimpleNamespace(interactions=fake)
    _, response = client.call_crop_tool(settings=RunSettings(model_id="gemini-3.8-flash"),
        system_prompt="system", user_prompt="review", image_path=path,
        schema=strict_json_schema(CropConversationResponse), previous_response_id="prior")
    CropConversationResponse.model_validate(response["arguments"])
    assert response["response_id"] == "interaction-1"
    assert response["function_call_id"] is None
    assert "tools" not in fake.calls[0]
    assert fake.calls[0]["previous_interaction_id"] == "prior"


def test_empty_user_prompt_rejected(tmp_path):
    client = GeminiChartClient(api_key=None, mock_mode=True)
    with pytest.raises(ValueError, match="non-empty"):
        client._input(" ", [tmp_path / "not-read.png"], "high")
