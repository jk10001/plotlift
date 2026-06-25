from __future__ import annotations

from pathlib import Path

import fitz
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import artifacts
from app import main as main_module
from app.stages import crop as crop_stage
from app.models import (
    AxisIdentificationOutput,
    CalibratedAxis,
    CalibrationPoint,
    ChartValue,
    CropConversationResponse,
    CropProposal,
    IdentifiedAxis,
    NormPoint,
    PixelPoint,
    PromptMetadata,
    RunSettings,
    RunState,
    SeriesDigitizationConversationResponse,
    SeriesDigitizationOutput,
    SeriesIdentificationOutput,
    SeriesPoint,
    SeriesPointProposal,
    SeriesState,
)
from app.openai_schema import strict_json_schema
from app.prompts import load_prompt_pack


def test_norm_point_rejects_out_of_range() -> None:
    with pytest.raises(ValueError):
        NormPoint(x=1000, y=0)


def test_series_uses_configured_point_limits(monkeypatch) -> None:
    monkeypatch.setenv("SERIES_MIN_DATA_POINTS", "3")
    monkeypatch.setenv("SERIES_MAX_DATA_POINTS", "4")

    points = [_series_point_proposal(index) for index in range(2)]
    with pytest.raises(ValueError):
        SeriesDigitizationOutput(points=points)

    accepted = [_series_point_proposal(index) for index in range(3)]
    assert len(SeriesDigitizationOutput(points=accepted).points) == 3

    too_many = [_series_point_proposal(index) for index in range(5)]
    with pytest.raises(ValueError):
        SeriesDigitizationOutput(points=too_many)


def test_series_schema_uses_configured_point_limits(monkeypatch) -> None:
    monkeypatch.setenv("SERIES_MIN_DATA_POINTS", "3")
    monkeypatch.setenv("SERIES_MAX_DATA_POINTS", "4")

    schema = strict_json_schema(SeriesDigitizationConversationResponse)
    points_schema = schema["$defs"]["SeriesDigitizationOutput"]["properties"]["points"]
    assert points_schema["minItems"] == 3
    assert points_schema["maxItems"] == 4


def test_axis_and_series_identification_schemas_include_axis_metadata() -> None:
    axis_schema_text = str(strict_json_schema(AxisIdentificationOutput))
    series_schema_text = str(strict_json_schema(SeriesIdentificationOutput))

    for field in ["axis_id", "direction", "quantity", "location_description", "is_primary_for_digitization"]:
        assert field in axis_schema_text
    for field in ["x_axis_id", "y_axis_id", "axis_selection_reason"]:
        assert field in series_schema_text


def test_calibration_state_tracks_usable_axes_and_ignored_duplicates() -> None:
    state = RunState(
        run_id="axes",
        settings=RunSettings(model_id="gpt-5.4-mini", mock_mode=True),
        prompt_metadata=PromptMetadata(prompt_version="test", prompt_hash="hash"),
        upload_filename="chart.png",
        original_path="uploads/original.png",
    )
    state.calibration.identified_axes = [
        IdentifiedAxis(
            axis_id="x_lpm",
            direction="x",
            name="Flow",
            unit="L/min",
            quantity="flow",
            location_description="Bottom metric axis closest to plot",
            is_primary_for_digitization=True,
        ),
        IdentifiedAxis(
            axis_id="x_gpm",
            direction="x",
            name="Flow",
            unit="GPM",
            quantity="flow",
            location_description="Outer bottom imperial axis",
            is_primary_for_digitization=False,
            ignore_reason="Duplicate non-metric flow axis",
        ),
    ]
    state.calibration.calibrated_axes = [
        CalibratedAxis(
            axis_id="x_lpm",
            direction="x",
            name="Flow",
            unit="L/min",
            quantity="flow",
            location_description="Bottom metric axis closest to plot",
        ),
        CalibratedAxis(
            axis_id="x_gpm",
            direction="x",
            name="Flow",
            unit="GPM",
            quantity="flow",
            location_description="Outer bottom imperial axis",
        ),
    ]

    assert [axis.axis_id for axis in state.calibration.usable_axes()] == ["x_lpm"]


def test_series_prompt_uses_configured_point_limits(monkeypatch) -> None:
    monkeypatch.setenv("SERIES_MIN_DATA_POINTS", "3")
    monkeypatch.setenv("SERIES_MAX_DATA_POINTS", "4")

    prompt = load_prompt_pack().render("series.digitization_system")
    assert "Use 3 to 4 representative polyline control points" in prompt


def test_series_identification_prompt_requires_pillow_or_hex_color() -> None:
    prompt = load_prompt_pack().render("series.identification_system")
    assert "Pillow named colour accepted by PIL.ImageColor" in prompt
    assert "hex colour" in prompt
    assert "Do not use informal colour descriptions" in prompt


def test_state_save_load_roundtrip(tmp_path, monkeypatch) -> None:
    class DummyConfig:
        runs_dir = tmp_path

    monkeypatch.setattr(artifacts, "get_config", lambda: DummyConfig())
    state = RunState(
        run_id="abc123",
        settings=RunSettings(model_id="gpt-5.4-mini", mock_mode=True),
        prompt_metadata=PromptMetadata(prompt_version="test", prompt_hash="hash"),
        upload_filename="chart.png",
        original_path="uploads/original.png",
    )
    artifacts.save_state(state)
    loaded = artifacts.load_state("abc123")
    assert loaded.run_id == "abc123"
    assert loaded.settings.model_id == "gpt-5.4-mini"


def test_save_json_redacts_response_ids_and_thought_signatures(tmp_path: Path) -> None:
    path = tmp_path / "attempt" / "response.json"
    artifacts.save_json(
        path,
        {
            "response_id": "resp_outer",
            "previous_response_id": "resp_previous",
            "raw": {
                "id": "resp_raw",
                "output": [{"thought_signature": "secret"}, {"thoughtSignature": "secret-camel"}],
            },
            "series": {"id": "series-id"},
        },
    )

    saved = artifacts.load_json(path)

    assert "response_id" not in saved
    assert "previous_response_id" not in saved
    assert "id" not in saved["raw"]
    assert "thought_signature" not in saved["raw"]["output"][0]
    assert "thoughtSignature" not in saved["raw"]["output"][1]
    assert saved["series"]["id"] == "series-id"


def test_create_run_defaults_to_full_image_crop_and_lists_run(tmp_path: Path, monkeypatch) -> None:
    class DummyConfig:
        runs_dir = tmp_path
        openai_mock_mode = True
        openai_api_key = None
        pdf_dpi = 144

    cfg = DummyConfig()
    monkeypatch.setattr(artifacts, "get_config", lambda: cfg)
    monkeypatch.setattr(main_module, "get_config", lambda: cfg)
    monkeypatch.setattr(crop_stage, "get_config", lambda: cfg)

    image_path = tmp_path / "chart.png"
    Image.new("RGB", (120, 80), "white").save(image_path)

    client = TestClient(main_module.app)
    with image_path.open("rb") as handle:
        response = client.post(
            "/api/runs",
            data={"model_id": "gpt-5.4-mini", "image_detail": "high", "reasoning_effort": "medium"},
            files={"file": ("chart.png", handle, "image/png")},
        )
    assert response.status_code == 200
    payload = response.json()
    state = payload["state"]
    assert state["stage"] == "crop_review"
    assert state["crop"]["approved"] is False
    assert state["crop"]["bbox_full_norm"] == {"left": 0, "top": 0, "right": 999, "bottom": 999}
    assert state["crop"]["bbox_full_px"]["right"] == 120
    assert state["crop"]["bbox_full_px"]["bottom"] == 80

    approve_response = client.post(f"/api/runs/{payload['run_id']}/crop", json={"bbox": state["crop"]["bbox_full_norm"]})
    assert approve_response.status_code == 200
    approved_crop = approve_response.json()["state"]["crop"]
    assert approved_crop["approved"] is True
    assert approved_crop["image"]["width"] == 120
    assert approved_crop["image"]["height"] == 80

    runs_response = client.get("/api/runs")
    assert runs_response.status_code == 200
    runs = runs_response.json()["runs"]
    assert runs[0]["run_id"] == payload["run_id"]
    assert runs[0]["upload_filename"] == "chart.png"


def test_delete_run_removes_run_folder_and_listing(tmp_path: Path, monkeypatch) -> None:
    class DummyConfig:
        runs_dir = tmp_path
        openai_mock_mode = True
        openai_api_key = None
        pdf_dpi = 144

    cfg = DummyConfig()
    monkeypatch.setattr(artifacts, "get_config", lambda: cfg)
    monkeypatch.setattr(main_module, "get_config", lambda: cfg)
    monkeypatch.setattr(crop_stage, "get_config", lambda: cfg)

    image_path = tmp_path / "chart.png"
    Image.new("RGB", (120, 80), "white").save(image_path)

    client = TestClient(main_module.app)
    with image_path.open("rb") as handle:
        payload = client.post(
            "/api/runs",
            data={"model_id": "gpt-5.4-mini", "image_detail": "high", "reasoning_effort": "medium"},
            files={"file": ("chart.png", handle, "image/png")},
        ).json()

    run_id = payload["run_id"]
    assert (tmp_path / run_id).exists()

    delete_response = client.delete(f"/api/runs/{run_id}")
    assert delete_response.status_code == 200
    assert delete_response.json() == {"ok": True}
    assert not (tmp_path / run_id).exists()

    runs_response = client.get("/api/runs")
    assert runs_response.status_code == 200
    assert runs_response.json()["runs"] == []


def test_single_page_pdf_upload_defaults_to_page_crop(tmp_path: Path, monkeypatch) -> None:
    class DummyConfig:
        runs_dir = tmp_path
        openai_mock_mode = True
        openai_api_key = None
        pdf_dpi = 72

    cfg = DummyConfig()
    monkeypatch.setattr(artifacts, "get_config", lambda: cfg)
    monkeypatch.setattr(main_module, "get_config", lambda: cfg)
    monkeypatch.setattr(crop_stage, "get_config", lambda: cfg)

    pdf_path = tmp_path / "chart.pdf"
    doc = fitz.open()
    page = doc.new_page(width=200, height=100)
    page.insert_text((20, 50), "Chart")
    doc.save(pdf_path)
    doc.close()

    client = TestClient(main_module.app)
    with pdf_path.open("rb") as handle:
        response = client.post(
            "/api/runs",
            data={"model_id": "gpt-5.4-mini", "image_detail": "high", "reasoning_effort": "medium"},
            files={"file": ("chart.pdf", handle, "application/pdf")},
        )
    assert response.status_code == 200
    state = response.json()["state"]
    assert state["stage"] == "crop_review"
    assert state["selected_page_index"] == 0
    assert state["canonical_image"]["path"] == "pages/page_001.png"
    assert state["crop"]["bbox_full_norm"] == {"left": 0, "top": 0, "right": 999, "bottom": 999}


def test_reselecting_current_pdf_page_does_not_reset_work(tmp_path: Path, monkeypatch) -> None:
    class DummyConfig:
        runs_dir = tmp_path
        openai_mock_mode = True
        openai_api_key = None
        pdf_dpi = 72

    cfg = DummyConfig()
    monkeypatch.setattr(artifacts, "get_config", lambda: cfg)
    monkeypatch.setattr(main_module, "get_config", lambda: cfg)
    monkeypatch.setattr(crop_stage, "get_config", lambda: cfg)

    pdf_path = tmp_path / "multipage.pdf"
    doc = fitz.open()
    for index in range(2):
        page = doc.new_page(width=200, height=100)
        page.insert_text((20, 50), f"Chart {index + 1}")
    doc.save(pdf_path)
    doc.close()

    client = TestClient(main_module.app)
    with pdf_path.open("rb") as handle:
        payload = client.post(
            "/api/runs",
            data={"model_id": "gpt-5.4-mini", "image_detail": "high", "reasoning_effort": "medium"},
            files={"file": ("multipage.pdf", handle, "application/pdf")},
        ).json()
    run_id = payload["run_id"]
    selected = client.post(f"/api/runs/{run_id}/select-page/1").json()["state"]
    approved = client.post(f"/api/runs/{run_id}/crop", json={"bbox": selected["crop"]["bbox_full_norm"]}).json()["state"]

    reselected = client.post(f"/api/runs/{run_id}/select-page/1").json()["state"]

    assert reselected["selected_page_index"] == 1
    assert reselected["crop"]["approved"] is True
    assert reselected["crop"]["image"] == approved["crop"]["image"]


def test_combined_axis_confirmation_and_edit_preserve_current_points(tmp_path: Path, monkeypatch) -> None:
    class DummyConfig:
        runs_dir = tmp_path
        openai_mock_mode = True
        openai_api_key = None
        pdf_dpi = 144

    cfg = DummyConfig()
    monkeypatch.setattr(artifacts, "get_config", lambda: cfg)
    monkeypatch.setattr(main_module, "get_config", lambda: cfg)
    monkeypatch.setattr(crop_stage, "get_config", lambda: cfg)

    image_path = tmp_path / "chart.png"
    Image.new("RGB", (100, 100), "white").save(image_path)

    client = TestClient(main_module.app)
    with image_path.open("rb") as handle:
        payload = client.post(
            "/api/runs",
            data={"model_id": "gpt-5.4-mini", "image_detail": "high", "reasoning_effort": "medium"},
            files={"file": ("chart.png", handle, "image/png")},
        ).json()
    run_id = payload["run_id"]
    crop_bbox = payload["state"]["crop"]["bbox_full_norm"]
    crop_state = client.post(f"/api/runs/{run_id}/crop", json={"bbox": crop_bbox}).json()["state"]

    x_points = [_point("x1", 10, 90, 0), _point("x2", 90, 90, 10)]
    y_points = [_point("y1", 10, 90, 0), _point("y2", 10, 10, 100)]
    confirmed = client.post(
        f"/api/runs/{run_id}/calibration",
        json={"x_points": x_points, "y_points": y_points},
    ).json()["state"]
    assert confirmed["stage"] == "series_ready"
    assert confirmed["calibration"]["approved_x"] is True
    assert confirmed["calibration"]["approved_y"] is True

    state = artifacts.load_state(run_id)
    state.series = [
        SeriesState(
            id="manual",
            name="Manual",
            source="manual",
            points=[
                SeriesPoint(
                    point_index=index,
                    crop_image_norm=NormPoint(x=100 + index * 10, y=500),
                    crop_image_px=PixelPoint(x=10 + index, y=50),
                    chart_x=ChartValue(value_raw=str(index), value_type="number", parsed_value=index),
                    chart_y=ChartValue(value_raw=str(index), value_type="number", parsed_value=index),
                )
                for index in range(5)
            ],
        )
    ]
    artifacts.save_state(state)

    edited_axes = client.post(f"/api/runs/{run_id}/calibration/edit").json()["state"]
    assert edited_axes["calibration"]["approved_x"] is False
    assert edited_axes["calibration"]["approved_y"] is False
    assert len(edited_axes["calibration"]["x_points"]) == 2
    assert len(edited_axes["calibration"]["y_points"]) == 2
    assert edited_axes["series"] == []

    reapproved_crop = client.post(f"/api/runs/{run_id}/crop", json={"bbox": crop_state["crop"]["bbox_full_norm"]}).json()["state"]
    edited_crop = client.post(f"/api/runs/{run_id}/crop/edit").json()["state"]
    assert edited_crop["crop"]["approved"] is False
    assert edited_crop["crop"]["bbox_full_norm"] == reapproved_crop["crop"]["bbox_full_norm"]
    assert edited_crop["crop"]["image"] == reapproved_crop["crop"]["image"]
    assert edited_crop["calibration"]["x_points"] == []
    assert edited_crop["calibration"]["y_points"] == []


def test_series_requires_confirm_before_complete_and_can_reopen_for_edit(tmp_path: Path, monkeypatch) -> None:
    class DummyConfig:
        runs_dir = tmp_path
        openai_mock_mode = True
        openai_api_key = None
        pdf_dpi = 144

    cfg = DummyConfig()
    monkeypatch.setattr(artifacts, "get_config", lambda: cfg)
    monkeypatch.setattr(main_module, "get_config", lambda: cfg)
    monkeypatch.setattr(crop_stage, "get_config", lambda: cfg)

    image_path = tmp_path / "chart.png"
    Image.new("RGB", (100, 100), "white").save(image_path)

    client = TestClient(main_module.app)
    with image_path.open("rb") as handle:
        payload = client.post(
            "/api/runs",
            data={"model_id": "gpt-5.4-mini", "image_detail": "high", "reasoning_effort": "medium"},
            files={"file": ("chart.png", handle, "image/png")},
        ).json()
    run_id = payload["run_id"]
    crop_bbox = payload["state"]["crop"]["bbox_full_norm"]
    client.post(f"/api/runs/{run_id}/crop", json={"bbox": crop_bbox})
    client.post(
        f"/api/runs/{run_id}/calibration",
        json={
            "x_points": [_point("x1", 10, 90, 0), _point("x2", 90, 90, 10)],
            "y_points": [_point("y1", 10, 90, 0), _point("y2", 10, 10, 100)],
        },
    )

    series_payload = [
        SeriesState(
            id="manual",
            name="Manual",
            source="manual",
            points=[
                SeriesPoint(
                    point_index=index,
                    crop_image_norm=NormPoint(x=100 + index * 100, y=500),
                    chart_x=ChartValue(value_raw=str(index), value_type="number", parsed_value=index),
                    chart_y=ChartValue(value_raw=str(index), value_type="number", parsed_value=index),
                )
                for index in range(5)
            ],
        ).model_dump(mode="json")
    ]

    review = client.post(f"/api/runs/{run_id}/series", json={"source": "image", "series": series_payload}).json()["state"]
    assert review["stage"] == "series_review"
    assert len(review["series"][0]["points"]) == 5

    confirmed = client.post(
        f"/api/runs/{run_id}/series",
        json={"source": "image", "series": review["series"], "mark_complete": True},
    ).json()["state"]
    assert confirmed["stage"] == "complete"

    reopened = client.post(f"/api/runs/{run_id}/series/edit").json()["state"]
    assert reopened["stage"] == "series_review"
    assert reopened["series"] == confirmed["series"]


def test_openai_strict_schema_sets_additional_properties_false() -> None:
    schema = strict_json_schema(CropProposal)
    assert schema["additionalProperties"] is False
    assert schema["$defs"]["NormBBox"]["additionalProperties"] is False
    assert "default" not in str(schema)


def test_conversation_schema_has_full_nullable_proposal_without_old_acceptance_flag() -> None:
    schema = strict_json_schema(CropConversationResponse)
    schema_text = str(schema)
    assert schema["additionalProperties"] is False
    assert "proposal" in schema["properties"]
    assert "CropProposal" in schema_text
    assert "bbox" in schema_text
    assert "accepted" not in schema_text


def test_series_schemas_split_identification_from_digitization() -> None:
    identification_schema = strict_json_schema(SeriesIdentificationOutput)
    identification_schema_text = str(identification_schema)
    digitization_schema_text = str(strict_json_schema(SeriesDigitizationConversationResponse))

    for field in ["series_name", "visual_description", "line_color", "line_style"]:
        assert field in identification_schema_text
        assert field not in digitization_schema_text
    assert "no_more_series" not in digitization_schema_text
    assert "points" in digitization_schema_text

    line_color_schema = identification_schema["$defs"]["SeriesIdentification"]["properties"]["line_color"]
    assert "enum" not in line_color_schema
    assert line_color_schema["anyOf"] == [{"type": "string"}, {"type": "null"}]
    assert "Pillow named colour" in line_color_schema["description"]


def _point(label: str, x: int, y: int, value: float) -> dict:
    return CalibrationPoint(
        label=label,  # type: ignore[arg-type]
        crop_image_norm=NormPoint(x=x * 999 // 99, y=y * 999 // 99),
        chart_value=ChartValue(value_raw=str(value), value_type="number", parsed_value=value),
    ).model_dump(mode="json")


def _series_point_proposal(index: int) -> SeriesPointProposal:
    return SeriesPointProposal(
        point_index=index,
        chart_x=ChartValue(value_raw=str(index), value_type="number", parsed_value=index),
        chart_y=ChartValue(value_raw=str(index), value_type="number", parsed_value=index),
    )
