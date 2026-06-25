from __future__ import annotations

import shutil
import tempfile
import threading
from pathlib import Path
from typing import Callable

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from . import coordinates
from .artifacts import ensure_run_dirs, load_state, new_run_id, relative_path, run_dir, safe_run_file, save_state
from .config import api_key_for_model, get_config, load_models, missing_api_key_message, validate_run_settings
from .exports import create_run_archive, csv_bytes, xlsx_bytes
from .image_io import SUPPORTED_UPLOAD_SUFFIXES, asset_for_path, crop_image, rasterize_pdf
from .logging_utils import emit_event
from .models import (
    Asset,
    CalibratedAxis,
    CalibrationPoint,
    CalibrationState,
    CropState,
    NormBBox,
    PixelBBox,
    PromptMetadata,
    RunSettings,
    RunState,
    SeriesState,
)
from .llm_client import ChartLLMClient, create_chart_client
from .prompts import PromptPack, load_prompt_pack
from .stages.calibration import approve_all_axes, approve_axis, run_axis_identification_stage, run_axis_stage
from .stages.crop import approve_crop, run_crop_stage
from .stages.series import cancel_series_selection, retry_series_digitization, run_selected_series_stage, run_series_stage


app = FastAPI(title="PlotLift")
ROOT = Path(__file__).resolve().parents[1]
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
templates = Jinja2Templates(directory=ROOT / "templates")


class CropApprovalRequest(NormBBox):
    pass


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "index.html", {"initial_run_id": None})


@app.get("/runs/{run_id}", response_class=HTMLResponse)
def run_page(request: Request, run_id: str) -> HTMLResponse:
    return templates.TemplateResponse(request, "index.html", {"initial_run_id": run_id})


@app.get("/api/config")
def api_config() -> dict:
    cfg = get_config()
    models_file = load_models()
    default = models_file.default_model()
    return {
        "models": [model.model_dump(mode="json") for model in models_file.enabled_models()],
        "default_model_id": default.id,
        "mock_mode": cfg.openai_mock_mode,
        "pdf_dpi": cfg.pdf_dpi,
        "series_min_data_points": cfg.series_min_data_points,
        "series_max_data_points": cfg.series_max_data_points,
        "show_debug_info": cfg.show_debug_info,
    }


@app.get("/api/runs")
def list_runs() -> dict:
    cfg = get_config()
    runs: list[dict] = []
    if not cfg.runs_dir.exists():
        return {"runs": runs}
    for state_path in cfg.runs_dir.glob("*/state.json"):
        try:
            state = RunState.model_validate_json(state_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        runs.append(
            {
                "run_id": state.run_id,
                "created_at": state.created_at,
                "updated_at": state.updated_at,
                "upload_filename": state.upload_filename,
                "stage": state.stage,
                "model_id": state.settings.model_id,
                "series_count": len(state.series),
                "crop_approved": bool(state.crop and state.crop.approved),
                "calibration_approved": state.calibration.all_usable_axes_approved(),
            }
        )
    runs.sort(key=lambda item: item["updated_at"], reverse=True)
    return {"runs": runs}


@app.post("/api/runs")
async def create_run(
    file: UploadFile = File(...),
    model_id: str = Form(...),
    image_detail: str = Form("high"),
    reasoning_effort: str = Form("medium"),
) -> dict:
    cfg = get_config()
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in SUPPORTED_UPLOAD_SUFFIXES:
        raise HTTPException(status_code=400, detail=f"Unsupported file type {suffix!r}")
    settings = validate_run_settings(
        RunSettings(
            model_id=model_id,
            image_detail=image_detail,  # type: ignore[arg-type]
            reasoning_effort=reasoning_effort,  # type: ignore[arg-type]
            mock_mode=cfg.openai_mock_mode,
        )
    )
    if not settings.mock_mode and not api_key_for_model(settings):
        raise HTTPException(status_code=400, detail=missing_api_key_message(settings))

    prompt_pack = load_prompt_pack()
    run_id = new_run_id()
    root = ensure_run_dirs(run_id)
    original_path = root / "uploads" / f"original{suffix}"
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = Path(tmp.name)
    shutil.move(str(tmp_path), original_path)

    emit_event(root, "USER", f"Created run from {file.filename}", run_id=run_id, artifact_path=relative_path(original_path, root))

    prompt_meta = PromptMetadata(prompt_version=prompt_pack.version, prompt_hash=prompt_pack.hash)
    state = RunState(
        run_id=run_id,
        settings=settings,
        prompt_metadata=prompt_meta,
        upload_filename=file.filename or "upload",
        original_path=relative_path(original_path, root),
    )

    if suffix == ".pdf":
        state.pages = rasterize_pdf(original_path, root / "pages", cfg.pdf_dpi, run_id)
        if len(state.pages) == 1:
            state.selected_page_index = 0
            state.canonical_image = state.pages[0]
            state.crop = _default_crop_for_asset(state.canonical_image)
            state.stage = "crop_review"
            emit_event(root, "STAGE", "Single-page PDF uploaded; default full-page crop ready for user review", run_id=run_id, stage="crop")
        else:
            state.stage = "uploaded"
            emit_event(root, "STAGE", "PDF uploaded; waiting for page selection", run_id=run_id)
    else:
        page_path = root / "pages" / "page_001.png"
        from PIL import Image, ImageOps

        with Image.open(original_path) as img:
            canonical = ImageOps.exif_transpose(img).convert("RGBA")
            canonical.save(page_path)
        asset = asset_for_path(page_path, root, label="Image")
        state.pages = [asset]
        state.selected_page_index = 0
        state.canonical_image = asset
        state.crop = _default_crop_for_asset(asset)
        state.stage = "crop_review"
        emit_event(root, "ARTIFACT", "Created canonical image", run_id=run_id, artifact_path=asset.path)
        emit_event(root, "STAGE", "Default full-image crop ready for user review", run_id=run_id, stage="crop")

    save_state(state)
    return {"run_id": run_id, "state": state.model_dump(mode="json")}


@app.post("/api/runs/{run_id}/select-page/{page_index}")
def select_page(run_id: str, page_index: int) -> dict:
    state = load_state(run_id)
    root = run_dir(run_id)
    if page_index < 0 or page_index >= len(state.pages):
        raise HTTPException(status_code=400, detail="Invalid page index")
    if state.selected_page_index == page_index and state.canonical_image:
        return {"state": state.model_dump(mode="json")}
    state.selected_page_index = page_index
    state.canonical_image = state.pages[page_index]
    state.crop = _default_crop_for_asset(state.canonical_image)
    state.calibration = CalibrationState()
    state.pending_series = []
    state.series = []
    state.stage = "crop_review"
    save_state(state)
    emit_event(root, "USER", f"Selected page {page_index + 1}", run_id=run_id)
    emit_event(root, "STAGE", "Default full-image crop ready for user review", run_id=run_id, stage="crop")
    return {"state": state.model_dump(mode="json")}


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict:
    return {"state": load_state(run_id).model_dump(mode="json")}


@app.delete("/api/runs/{run_id}")
def delete_run(run_id: str) -> dict:
    cfg = get_config()
    runs_root = cfg.runs_dir.resolve()
    root = (cfg.runs_dir / run_id).resolve()
    try:
        root.relative_to(runs_root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid run id") from exc
    if root == runs_root:
        raise HTTPException(status_code=400, detail="Invalid run id")
    if not root.exists():
        raise HTTPException(status_code=404, detail="Run not found")
    state_path = root / "state.json"
    if state_path.exists():
        state = RunState.model_validate_json(state_path.read_text(encoding="utf-8"))
        if state.active_job:
            raise HTTPException(status_code=409, detail=f"Run has active job {state.active_job}")
    shutil.rmtree(root)
    return {"ok": True}


@app.get("/api/runs/{run_id}/events")
def get_events(run_id: str) -> dict:
    events_path = run_dir(run_id) / "events.jsonl"
    if not events_path.exists():
        return {"events": []}
    events = [line for line in events_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return {"events": [__import__("json").loads(line) for line in events]}


@app.get("/api/runs/{run_id}/events/stream")
def stream_events(run_id: str) -> StreamingResponse:
    import asyncio
    import json

    async def generator():
        events_path = run_dir(run_id) / "events.jsonl"
        position = 0
        while True:
            if events_path.exists():
                with events_path.open("r", encoding="utf-8") as handle:
                    handle.seek(position)
                    for line in handle:
                        yield f"data: {line.strip()}\n\n"
                    position = handle.tell()
            await asyncio.sleep(1)

    return StreamingResponse(generator(), media_type="text/event-stream")


@app.post("/api/runs/{run_id}/jobs/crop")
def start_crop_job(run_id: str) -> dict:
    _start_job(run_id, "crop", lambda state, prompts, client: run_crop_stage(state, prompts, client), initial_status="Finding crop...")
    return {"ok": True}


@app.post("/api/runs/{run_id}/jobs/calibration")
def start_axis_calibration_job(run_id: str) -> dict:
    _start_job(run_id, "axis_calibration", _run_axis_calibration_stages, initial_status="Identifying axes...")
    return {"ok": True}


@app.post("/api/runs/{run_id}/jobs/calibration/{axis}")
def start_axis_job(run_id: str, axis: str) -> dict:
    _start_job(run_id, f"{axis}_axis", lambda state, prompts, client: run_axis_stage(state, axis, prompts, client), initial_status="Calibrating axis 1 of 1...")
    return {"ok": True}


@app.post("/api/runs/{run_id}/jobs/series")
def start_series_job(run_id: str) -> dict:
    _start_job(run_id, "series", lambda state, prompts, client: run_series_stage(state, prompts, client), initial_status="Identifying series...")
    return {"ok": True}


@app.post("/api/runs/{run_id}/jobs/series-selection")
async def start_selected_series_job(run_id: str, request: Request) -> dict:
    data = await request.json()
    selected_indexes = [int(index) for index in data.get("selected_indexes", [])]
    selected_count = len(selected_indexes)
    initial_status = f"Digitising series 1 of {selected_count}..." if selected_count else None
    _start_job(run_id, "series", lambda state, prompts, client: run_selected_series_stage(state, prompts, client, selected_indexes), initial_status=initial_status)
    return {"ok": True}


@app.post("/api/runs/{run_id}/jobs/series/{series_id}")
def retry_series_job(run_id: str, series_id: str) -> dict:
    _start_job(run_id, "series", lambda state, prompts, client: retry_series_digitization(state, prompts, client, series_id), initial_status="Digitising series 1 of 1...")
    return {"ok": True}


@app.post("/api/runs/{run_id}/crop")
async def update_crop(run_id: str, request: Request) -> dict:
    data = await request.json()
    bbox = NormBBox.model_validate(data.get("bbox", data))
    state = approve_crop(load_state(run_id), bbox)
    return {"state": state.model_dump(mode="json")}


@app.post("/api/runs/{run_id}/crop/edit")
def edit_crop(run_id: str) -> dict:
    state = load_state(run_id)
    root = run_dir(run_id)
    if not state.crop:
        raise HTTPException(status_code=400, detail="Crop is required before editing")
    state.crop.approved = False
    state.calibration = CalibrationState()
    state.pending_series = []
    state.series = []
    state.stage = "crop_review"
    save_state(state)
    emit_event(root, "USER", "User reopened crop editing", run_id=run_id, stage="crop")
    return {"state": state.model_dump(mode="json")}


@app.post("/api/runs/{run_id}/crop-draft")
async def update_crop_draft(run_id: str, request: Request) -> dict:
    data = await request.json()
    bbox = NormBBox.model_validate(data.get("bbox", data))
    state = load_state(run_id)
    root = run_dir(run_id)
    if not state.canonical_image:
        raise HTTPException(status_code=400, detail="Canonical image is required before crop edits")
    bbox_px = coordinates.norm_to_px_bbox(bbox, state.canonical_image.width, state.canonical_image.height)
    state.crop = CropState(bbox_full_norm=bbox, bbox_full_px=bbox_px, approved=False, warnings=state.crop.warnings if state.crop else [])
    state.calibration = CalibrationState()
    state.pending_series = []
    state.series = []
    state.stage = "crop_review"
    save_state(state)
    emit_event(root, "USER", "User adjusted crop window", run_id=run_id, stage="crop")
    return {"state": state.model_dump(mode="json")}


@app.post("/api/runs/{run_id}/calibration")
async def update_axis_calibration(run_id: str, request: Request) -> dict:
    data = await request.json()
    state = load_state(run_id)
    root = run_dir(run_id)
    if not state.crop or not state.crop.image:
        raise HTTPException(status_code=400, detail="Approved crop is required before calibration approval")
    try:
        if "axes" in data:
            axes = [CalibratedAxis.model_validate(item) for item in data.get("axes", [])]
            state = approve_all_axes(state, axes)
            return {"state": state.model_dump(mode="json")}
        x_points = [CalibrationPoint.model_validate(item) for item in data.get("x_points", [])]
        y_points = [CalibrationPoint.model_validate(item) for item in data.get("y_points", [])]
        if len(x_points) != 2 or len(y_points) != 2:
            raise ValueError("Axis calibration requires two x points and two y points")
        x_axis = CalibratedAxis(
            axis_id=state.calibration.default_axis_id("x") or "x",
            direction="x",
            name="X axis",
            location_description="Legacy x-axis calibration",
            points=x_points,
        )
        y_axis = CalibratedAxis(
            axis_id=state.calibration.default_axis_id("y") or "y",
            direction="y",
            name="Y axis",
            location_description="Legacy y-axis calibration",
            points=y_points,
        )
        state = approve_all_axes(state, [x_axis, y_axis])
        return {"state": state.model_dump(mode="json")}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/runs/{run_id}/calibration/edit")
def edit_axis_calibration(run_id: str) -> dict:
    state = load_state(run_id)
    root = run_dir(run_id)
    if not state.calibration.calibrated_axes:
        raise HTTPException(status_code=400, detail="Axis calibration points are required before editing")
    for axis in state.calibration.calibrated_axes:
        axis.approved = False
    state.calibration.approved_x = False
    state.calibration.approved_y = False
    state.pending_series = []
    state.series = []
    state.stage = "calibration_ready"
    save_state(state)
    emit_event(root, "USER", "User reopened axis calibration editing", run_id=run_id, stage="axis_calibration")
    return {"state": state.model_dump(mode="json")}


@app.post("/api/runs/{run_id}/calibration/{axis}")
async def update_calibration(run_id: str, axis: str, request: Request) -> dict:
    data = await request.json()
    points = [CalibrationPoint.model_validate(item) for item in data.get("points", [])]
    state = approve_axis(load_state(run_id), axis, points)
    return {"state": state.model_dump(mode="json")}


@app.post("/api/runs/{run_id}/calibration/{axis}/draft")
async def update_calibration_draft(run_id: str, axis: str, request: Request) -> dict:
    data = await request.json()
    state = load_state(run_id)
    root = run_dir(run_id)
    if not state.crop or not state.crop.image:
        raise HTTPException(status_code=400, detail="Approved crop is required before calibration edits")
    axis_state = state.calibration.axis_by_id(axis)
    if axis_state is None and axis in {"x", "y"}:
        axis_state = next((item for item in state.calibration.calibrated_axes if item.direction == axis), None)
    if axis_state is None:
        raise HTTPException(status_code=400, detail=f"Unknown axis {axis!r}")
    points = [CalibrationPoint.model_validate(item) for item in data.get("points", [])]
    points = coordinates.populate_calibration_pixels(points, state.crop.image.width, state.crop.image.height)
    try:
        coordinates.validate_axis_geometry(points, axis_state.direction)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    axis_state.points = points
    axis_state.approved = False
    for index, item in enumerate(state.calibration.calibrated_axes):
        if item.axis_id == axis_state.axis_id:
            state.calibration.calibrated_axes[index] = axis_state
            break
    if axis_state.direction == "x":
        state.stage = "x_calibration_review"
    elif axis_state.direction == "y":
        state.stage = "y_calibration_review"
    first_x = next((item for item in state.calibration.calibrated_axes if item.direction == "x"), None)
    first_y = next((item for item in state.calibration.calibrated_axes if item.direction == "y"), None)
    state.calibration.x_points = first_x.points if first_x else []
    state.calibration.y_points = first_y.points if first_y else []
    state.calibration.approved_x = bool(first_x and first_x.approved)
    state.calibration.approved_y = bool(first_y and first_y.approved)
    state.pending_series = []
    state.series = []
    save_state(state)
    emit_event(root, "USER", f"User adjusted calibration for {axis_state.display_name}", run_id=run_id, stage=f"axis_{axis_state.axis_id}")
    return {"state": state.model_dump(mode="json")}


@app.post("/api/runs/{run_id}/series")
async def update_series(run_id: str, request: Request) -> dict:
    data = await request.json()
    source = data.get("source", "image")
    mark_complete = bool(data.get("mark_complete", False))
    state = load_state(run_id)
    root = run_dir(run_id)
    if not state.crop or not state.crop.image:
        raise HTTPException(status_code=400, detail="Crop is required before series edits")
    series_items = [SeriesState.model_validate(item) for item in data.get("series", [])]
    for series in series_items:
        series.x_axis_id = series.x_axis_id or state.calibration.default_axis_id("x")
        series.y_axis_id = series.y_axis_id or state.calibration.default_axis_id("y")
        x_axis = state.calibration.axis_by_id(series.x_axis_id)
        y_axis = state.calibration.axis_by_id(series.y_axis_id)
        if not x_axis or not y_axis:
            raise HTTPException(status_code=400, detail=f"Series {series.name!r} does not have valid axes")
        for index, point in enumerate(series.points):
            if source == "chart" and point.chart_x and point.chart_y:
                px = coordinates.chart_space_to_image_point(
                    point.chart_x,
                    point.chart_y,
                    x_axis.points,
                    y_axis.points,
                )
                point.crop_image_px = px
                point.crop_image_norm = coordinates.crop_px_to_crop_norm(px, state.crop.image.width, state.crop.image.height, clamp=True)
            else:
                series.points[index] = coordinates.populate_series_point(
                    point,
                    state.crop.image.width,
                    state.crop.image.height,
                    x_axis.points,
                    y_axis.points,
                )
    state.series = series_items
    state.pending_series = []
    if mark_complete and series_items:
        state.stage = "complete"
    elif series_items:
        state.stage = "series_review"
    elif state.calibration.has_approved_direction("x") and state.calibration.has_approved_direction("y"):
        state.stage = "series_ready"
    save_state(state)
    message = "User saved series data" if mark_complete else "User edited series data"
    emit_event(root, "USER", message, run_id=run_id, stage="series")
    return {"state": state.model_dump(mode="json")}


@app.post("/api/runs/{run_id}/series-selection/cancel")
def cancel_pending_series_selection(run_id: str) -> dict:
    state = cancel_series_selection(load_state(run_id))
    return {"state": state.model_dump(mode="json")}


@app.post("/api/runs/{run_id}/series/edit")
def edit_series(run_id: str) -> dict:
    state = load_state(run_id)
    root = run_dir(run_id)
    if not state.series:
        raise HTTPException(status_code=400, detail="Series are required before editing")
    state.stage = "series_review"
    state.pending_series = []
    save_state(state)
    emit_event(root, "USER", "User reopened series editing", run_id=run_id, stage="series")
    return {"state": state.model_dump(mode="json")}


@app.get("/api/runs/{run_id}/files/{path:path}")
def run_file(run_id: str, path: str) -> FileResponse:
    target = safe_run_file(run_id, path)
    return FileResponse(target)


@app.get("/api/runs/{run_id}/export.csv")
def export_csv(run_id: str, debug: bool = False) -> Response:
    state = load_state(run_id)
    data = csv_bytes(state, include_debug=debug)
    return Response(
        content=data,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{run_id}_chart_data.csv"'},
    )


@app.get("/api/runs/{run_id}/export.xlsx")
def export_xlsx(run_id: str, debug: bool = False) -> Response:
    state = load_state(run_id)
    data = xlsx_bytes(state, include_debug=debug)
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{run_id}_chart_data.xlsx"'},
    )


@app.get("/api/runs/{run_id}/archive.zip")
def export_archive(run_id: str) -> FileResponse:
    root = run_dir(run_id)
    target = root / "exports" / f"{run_id}_archive.zip"
    create_run_archive(root, target)
    return FileResponse(target, media_type="application/zip", filename=f"{run_id}_archive.zip")


def _start_job(run_id: str, name: str, fn: Callable[[RunState, object, ChartLLMClient], RunState], *, initial_status: str | None = None) -> None:
    state = load_state(run_id)
    root = run_dir(run_id)
    if state.active_job:
        raise HTTPException(status_code=409, detail=f"Run already has active job {state.active_job}")
    state.active_job = name
    state.active_step_status = initial_status
    save_state(state)
    emit_event(root, "STAGE", f"Started job {name}", run_id=run_id, stage=name)

    def worker() -> None:
        prompts = load_prompt_pack()
        try:
            fresh = load_state(run_id)
            client = create_chart_client(fresh.settings, run_dir=root, run_id=run_id)
            result = fn(fresh, prompts, client)
            result.active_job = None
            result.active_step_status = None
            save_state(result)
            emit_event(root, "STAGE", f"Finished job {name}", run_id=run_id, stage=name)
        except Exception as exc:  # noqa: BLE001
            failed = load_state(run_id)
            failed.active_job = None
            failed.active_step_status = None
            failed.stage = "error"
            failed.warnings.append(str(exc))
            save_state(failed)
            emit_event(root, "ERROR", f"Job {name} failed: {exc}", run_id=run_id, stage=name)

    threading.Thread(target=worker, daemon=True).start()


def _run_axis_calibration_stages(state: RunState, prompts: PromptPack, client: ChartLLMClient) -> RunState:
    if not state.crop or not state.crop.approved:
        raise ValueError("approved crop is required before axis calibration")
    state.calibration = CalibrationState()
    state.pending_series = []
    state.series = []
    state.active_step_status = "Identifying axes..."
    save_state(state)
    state = run_axis_identification_stage(state, prompts, client)
    if not state.calibration.usable_axes():
        raise ValueError("axis identification did not find any usable axes")
    if not any(axis.direction == "x" for axis in state.calibration.usable_axes()):
        raise ValueError("axis identification did not find a usable x-axis")
    if not any(axis.direction == "y" for axis in state.calibration.usable_axes()):
        raise ValueError("axis identification did not find a usable y-axis")
    usable_axes = list(state.calibration.usable_axes())
    for index, axis in enumerate(usable_axes, start=1):
        state.active_step_status = f"Calibrating axis {index} of {len(usable_axes)}..."
        save_state(state)
        state = run_axis_stage(state, axis, prompts, client)
    state.stage = "calibration_ready"
    return state


def _default_crop_for_asset(asset: Asset) -> CropState:
    bbox = NormBBox(left=0, top=0, right=999, bottom=999)
    return CropState(
        bbox_full_norm=bbox,
        bbox_full_px=PixelBBox(left=0, top=0, right=asset.width, bottom=asset.height),
        approved=False,
        warnings=[],
    )
