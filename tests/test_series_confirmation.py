from __future__ import annotations

from pathlib import Path

from PIL import Image

from app import artifacts
from app.models import (
    Asset,
    CalibrationPoint,
    ChartValue,
    CropState,
    NormBBox,
    NormPoint,
    PixelBBox,
    PixelPoint,
    PromptMetadata,
    RunSettings,
    RunState,
    SeriesIdentification,
    SeriesIdentificationOutput,
    SeriesState,
)
from app.openai_client import OpenAIChartClient
from app.prompts import load_prompt_pack
from app.stages import series as series_stage


class DummyConfig:
    def __init__(self, runs_dir: Path) -> None:
        self.runs_dir = runs_dir
        self.max_series_attempts = 3


def test_series_stage_sends_overlay_back_for_confirmation(tmp_path: Path, monkeypatch) -> None:
    cfg = DummyConfig(tmp_path)
    monkeypatch.setattr(artifacts, "get_config", lambda: cfg)
    monkeypatch.setattr(series_stage, "get_config", lambda: cfg)

    run_id = "seriesconfirm"
    root = tmp_path / run_id
    crop_path = root / "crop" / "approved_crop.png"
    crop_path.parent.mkdir(parents=True)
    Image.new("RGB", (100, 100), "white").save(crop_path)

    state = _ready_state(run_id)
    saved_snapshots: list[RunState] = []
    original_save_state = series_stage.save_state

    def capture_save_state(state: RunState) -> None:
        original_save_state(state)
        saved_snapshots.append(RunState.model_validate(state.model_dump(mode="json")))

    monkeypatch.setattr(series_stage, "save_state", capture_save_state)

    client = OpenAIChartClient(api_key=None, mock_mode=True, run_dir=root, run_id=run_id)
    identified = series_stage.run_series_stage(state, load_prompt_pack(), client)
    result = series_stage.run_selected_series_stage(identified, load_prompt_pack(), client, [0])

    assert len(result.series) == 1
    identification_attempt = result.attempts[0]
    first_attempt = result.attempts[1]
    second_attempt = result.attempts[2]
    assert identification_attempt.stage == "series_identification"
    assert identification_attempt.status == "accepted"
    assert first_attempt.status == "needs_review"
    assert second_attempt.status == "accepted"
    assert any(
        snapshot.series
        and snapshot.series[0].id == first_attempt.id.split("-")[1]
        and any(attempt.id == first_attempt.id and attempt.status == "needs_review" for attempt in snapshot.attempts)
        and not any(attempt.stage == "series" and attempt.status == "accepted" for attempt in snapshot.attempts)
        for snapshot in saved_snapshots
    )

    second_request = artifacts.load_json(root / second_attempt.request_path)
    first_response = artifacts.load_json(root / first_attempt.response_path)
    second_parsed = artifacts.load_json(root / second_attempt.parsed_path)
    assert second_request["source_image_path"] == first_attempt.overlay_path
    assert second_request["source_image_path"].endswith("attempt_01/overlay.png")
    assert "previous_response_id" not in second_request
    assert "response_id" not in first_response
    if "raw" in first_response:
        assert "id" not in first_response["raw"]
    assert second_parsed["response_kind"] == "accept_previous"
    assert second_parsed["proposal"] is None

    series_id = first_attempt.id.split("-")[1]
    conversation = artifacts.load_json(root / "series" / series_id / "conversation.json")
    assert [item["response_kind"] for item in conversation["attempts"][:2]] == ["proposal", "accept_previous"]
    assert "response_id" not in conversation["attempts"][0]
    assert "previous_response_id" not in conversation["attempts"][1]


def test_series_stage_digitizes_every_identified_series_even_past_old_cap(tmp_path: Path, monkeypatch) -> None:
    cfg = DummyConfig(tmp_path)
    cfg.max_auto_series = 2
    monkeypatch.setattr(artifacts, "get_config", lambda: cfg)
    monkeypatch.setattr(series_stage, "get_config", lambda: cfg)

    run_id = "seriesmany"
    root = tmp_path / run_id
    crop_path = root / "crop" / "approved_crop.png"
    crop_path.parent.mkdir(parents=True)
    Image.new("RGB", (100, 100), "white").save(crop_path)
    state = _ready_state(run_id)

    identified = [
        SeriesIdentification(
            series_name=f"Series {index}",
            visual_description=f"Description {index}",
            line_color="blue",
            line_style="solid",
        )
        for index in range(6)
    ]
    digitized_names: list[str] = []

    monkeypatch.setattr(
        series_stage,
        "_identify_all_series",
        lambda *args, **kwargs: SeriesIdentificationOutput(series=identified),
    )

    def fake_digitize_identified_series(**kwargs) -> None:
        series_description = kwargs["series_description"]
        digitized_names.append(series_description.series_name)
        kwargs["state"].series.append(
            SeriesState(id=f"mock-{len(digitized_names)}", name=series_description.series_name)
        )

    monkeypatch.setattr(series_stage, "_digitize_identified_series", fake_digitize_identified_series)

    client = OpenAIChartClient(api_key=None, mock_mode=True, run_dir=root, run_id=run_id)
    identified_result = series_stage.run_series_stage(state, load_prompt_pack(), client)
    result = series_stage.run_selected_series_stage(identified_result, load_prompt_pack(), client, list(range(len(identified))))

    assert digitized_names == [item.series_name for item in identified]
    assert len(result.series) == 6


def test_series_stage_rerun_replaces_previous_auto_series_but_keeps_manual(tmp_path: Path, monkeypatch) -> None:
    cfg = DummyConfig(tmp_path)
    monkeypatch.setattr(artifacts, "get_config", lambda: cfg)
    monkeypatch.setattr(series_stage, "get_config", lambda: cfg)

    run_id = "seriesrerun"
    root = tmp_path / run_id
    crop_path = root / "crop" / "approved_crop.png"
    crop_path.parent.mkdir(parents=True)
    Image.new("RGB", (100, 100), "white").save(crop_path)
    state = _ready_state(run_id)
    state.series = [
        SeriesState(id="old-auto", name="Old auto", source="llm"),
        SeriesState(id="manual", name="Manual", source="manual"),
    ]

    monkeypatch.setattr(
        series_stage,
        "_identify_all_series",
        lambda *args, **kwargs: SeriesIdentificationOutput(
            series=[
                SeriesIdentification(
                    series_name="Replacement auto",
                    visual_description="Replacement series",
                    line_color="blue",
                    line_style="solid",
                )
            ]
        ),
    )

    def fake_digitize_identified_series(**kwargs) -> None:
        kwargs["state"].series.append(SeriesState(id="new-auto", name="Replacement auto", source="llm"))

    monkeypatch.setattr(series_stage, "_digitize_identified_series", fake_digitize_identified_series)

    client = OpenAIChartClient(api_key=None, mock_mode=True, run_dir=root, run_id=run_id)
    identified_result = series_stage.run_series_stage(state, load_prompt_pack(), client)
    result = series_stage.run_selected_series_stage(identified_result, load_prompt_pack(), client, [0])

    assert [series.id for series in result.series] == ["manual", "new-auto"]


def test_series_selection_cancel_digitizes_none(tmp_path: Path, monkeypatch) -> None:
    cfg = DummyConfig(tmp_path)
    monkeypatch.setattr(artifacts, "get_config", lambda: cfg)
    monkeypatch.setattr(series_stage, "get_config", lambda: cfg)

    run_id = "seriescancel"
    root = tmp_path / run_id
    crop_path = root / "crop" / "approved_crop.png"
    crop_path.parent.mkdir(parents=True)
    Image.new("RGB", (100, 100), "white").save(crop_path)
    state = _ready_state(run_id)

    client = OpenAIChartClient(api_key=None, mock_mode=True, run_dir=root, run_id=run_id)
    identified = series_stage.run_series_stage(state, load_prompt_pack(), client)
    assert len(identified.pending_series) == 1

    result = series_stage.cancel_series_selection(identified)

    assert result.pending_series == []
    assert result.series == []
    assert result.stage == "series_ready"


def test_retry_series_digitization_replaces_only_target_series(tmp_path: Path, monkeypatch) -> None:
    cfg = DummyConfig(tmp_path)
    monkeypatch.setattr(artifacts, "get_config", lambda: cfg)
    monkeypatch.setattr(series_stage, "get_config", lambda: cfg)

    run_id = "seriesretryone"
    root = tmp_path / run_id
    crop_path = root / "crop" / "approved_crop.png"
    crop_path.parent.mkdir(parents=True)
    Image.new("RGB", (100, 100), "white").save(crop_path)
    state = _ready_state(run_id)
    state.series = [
        SeriesState(id="keep-auto", name="Keep auto", source="llm"),
        SeriesState(id="retry-auto", name="Retry auto", source="llm", line_color="#0891b2"),
        SeriesState(id="manual", name="Manual", source="manual"),
    ]

    captured: dict[str, object] = {}

    def fake_digitize_identified_series(**kwargs) -> SeriesState:
        captured["series_description"] = kwargs["series_description"]
        captured["append_to_state"] = kwargs["append_to_state"]
        return SeriesState(id="replacement", name="Retry auto", source="llm")

    monkeypatch.setattr(series_stage, "_digitize_identified_series", fake_digitize_identified_series)

    client = OpenAIChartClient(api_key=None, mock_mode=True, run_dir=root, run_id=run_id)
    result = series_stage.retry_series_digitization(state, load_prompt_pack(), client, "retry-auto")

    assert [series.id for series in result.series] == ["keep-auto", "replacement", "manual"]
    assert captured["append_to_state"] is False
    assert captured["series_description"].series_name == "Retry auto"


def _ready_state(run_id: str) -> RunState:
    state = RunState(
        run_id=run_id,
        settings=RunSettings(model_id="gpt-5.4-mini", mock_mode=True),
        prompt_metadata=PromptMetadata(prompt_version="test", prompt_hash="hash"),
        upload_filename="chart.png",
        original_path="uploads/original.png",
        crop=CropState(
            bbox_full_norm=NormBBox(left=0, top=0, right=999, bottom=999),
            bbox_full_px=PixelBBox(left=0, top=0, right=99, bottom=99),
            image=Asset(path="crop/approved_crop.png", width=100, height=100),
            approved=True,
        ),
    )
    state.calibration.x_points = [
        _calibration_point("x1", 0, 90, 0),
        _calibration_point("x2", 90, 90, 100),
    ]
    state.calibration.y_points = [
        _calibration_point("y1", 0, 90, 0),
        _calibration_point("y2", 0, 0, 100),
    ]
    state.calibration.approved_x = True
    state.calibration.approved_y = True
    return state


def _calibration_point(label: str, x: float, y: float, value: float) -> CalibrationPoint:
    return CalibrationPoint(
        label=label,  # type: ignore[arg-type]
        crop_image_norm=NormPoint(x=round(x / 99 * 999), y=round(y / 99 * 999)),
        crop_image_px=PixelPoint(x=x, y=y),
        chart_value=ChartValue(value_raw=str(value), value_type="number", parsed_value=value),
    )
