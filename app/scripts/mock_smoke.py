from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from PIL import Image, ImageOps

from app.artifacts import ensure_run_dirs, new_run_id, relative_path, save_state
from app.config import get_config
from app.exports import csv_bytes, xlsx_bytes
from app.image_io import asset_for_path
from app.logging_utils import emit_event
from app.models import PromptMetadata, RunSettings, RunState
from app.openai_client import OpenAIChartClient
from app.prompts import load_prompt_pack
from app.stages.calibration import approve_all_axes, run_axis_identification_stage, run_axis_stage
from app.stages.crop import approve_crop, run_crop_stage
from app.stages.series import run_selected_series_stage, run_series_stage


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the PlotLift pipeline in mock mode.")
    parser.add_argument("image", nargs="?", default="example_charts/pump_curve_01.png", help="Image file to process")
    parser.add_argument("--keep-run", action="store_true", help="Keep the generated run directory after a successful smoke test")
    args = parser.parse_args()
    image_path = Path(args.image).resolve()
    if not image_path.exists():
        raise SystemExit(f"Image not found: {image_path}")

    cfg = get_config()
    prompt_pack = load_prompt_pack()
    run_id = new_run_id()
    root = ensure_run_dirs(run_id)

    original_path = root / "uploads" / image_path.name
    shutil.copyfile(image_path, original_path)
    page_path = root / "pages" / "page_001.png"
    with Image.open(original_path) as img:
        ImageOps.exif_transpose(img).convert("RGBA").save(page_path)

    settings = RunSettings(model_id="gpt-5.4-mini", image_detail="high", reasoning_effort="medium", mock_mode=True)
    prompt_meta = PromptMetadata(prompt_version=prompt_pack.version, prompt_hash=prompt_pack.hash)
    state = RunState(
        run_id=run_id,
        settings=settings,
        prompt_metadata=prompt_meta,
        upload_filename=image_path.name,
        original_path=relative_path(original_path, root),
        pages=[asset_for_path(page_path, root, label="Image")],
        selected_page_index=0,
        canonical_image=asset_for_path(page_path, root, label="Image"),
        stage="page_selected",
    )
    save_state(state)
    emit_event(root, "SYSTEM", "Mock smoke run created", run_id=run_id)

    client = OpenAIChartClient(api_key=None, mock_mode=True, run_dir=root, run_id=run_id)
    state = run_crop_stage(state, prompt_pack, client)
    if not state.crop:
        raise RuntimeError("mock crop did not produce a crop")
    state = approve_crop(state, state.crop.bbox_full_norm)
    state = run_axis_identification_stage(state, prompt_pack, client)
    for axis in list(state.calibration.usable_axes()):
        state = run_axis_stage(state, axis, prompt_pack, client)
    state = approve_all_axes(state, state.calibration.calibrated_axes)
    state = run_series_stage(state, prompt_pack, client)
    state = run_selected_series_stage(state, prompt_pack, client, list(range(len(state.pending_series))))

    csv_path = root / "exports" / "mock_smoke.csv"
    xlsx_path = root / "exports" / "mock_smoke.xlsx"
    csv_path.write_bytes(csv_bytes(state, include_debug=True))
    xlsx_path.write_bytes(xlsx_bytes(state, include_debug=True))
    emit_event(root, "ARTIFACT", "Saved mock smoke exports", run_id=run_id, artifact_path="exports/")

    if args.keep_run:
        print(f"Mock smoke run complete: {root}")
        print(f"Open in the app: http://127.0.0.1:8000/runs/{run_id}")
        return

    shutil.rmtree(root)
    print(f"Mock smoke test passed; removed temporary run: {root}")


if __name__ == "__main__":
    main()
