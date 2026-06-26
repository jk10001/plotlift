# PlotLift

***Chart Digitiser***

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

PlotLift converts line charts in images and PDF pages into structured numerical data. Its current focus is line charts, particularly pump curves. Support for other chart types is planned as the project develops.

PlotLift combines vision-capable OpenAI or Gemini models with an interactive review interface. Digitisation is iterative: the model proposes a crop, calibration, or series trace, reviews an overlay of its result, and improves the proposal when needed. The user remains in control and can inspect, edit, and confirm every stage.

Each run keeps the source material, model requests and responses, validation results, overlays, user edits, event logs, and exports. This makes the result easier to review than a one-shot extraction.

![PlotLift digitisation workflow](docs/assets/digitisation_example.gif)

## Quickstart

### 1. Clone And Install

```bash
git clone https://github.com/jk10001/plotlift.git
cd plotlift
python -m venv .venv
```

Activate the virtual environment:

```bash
# macOS or Linux
source .venv/bin/activate

# Windows PowerShell
.\.venv\Scripts\Activate.ps1
```

Install the dependencies:

```bash
python -m pip install -r requirements.txt
```

### 2. Configure

Copy the example environment file:

```bash
# macOS or Linux
cp .env.example .env

# Windows PowerShell
Copy-Item .env.example .env
```

Add the API key for the provider you intend to use:

```text
OPENAI_API_KEY=
GEMINI_API_KEY=
OPENAI_MOCK_MODE=false
APP_RUNS_DIR=runs
APP_SHOW_DEBUG_INFO=true
PDF_DPI=200
SERIES_MIN_DATA_POINTS=5
SERIES_MAX_DATA_POINTS=10
```

Only the selected provider's key is required. Keep `.env` private; it is excluded by `.gitignore`.

### 3. Start PlotLift

```bash
python -m uvicorn app.main:app --reload
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000).

Runs are stored under `APP_RUNS_DIR`, which defaults to `runs/`. Previous runs can be reopened from the interface.

## What PlotLift Can Do

PlotLift currently supports:

- PNG, JPEG, WebP, and PDF uploads.
- Page selection for multi-page PDFs.
- Single-panel and multi-panel line charts.
- Pump curves with multiple performance lines and axes.
- Multiple visible line series.
- Multiple x-axes and y-axes, including secondary axes.
- Numeric and date/time axes with linear scales.
- Automatic crop detection, axis identification, axis calibration, and series tracing.
- Manual correction of crops, axis calibration points, series points, names, and series membership.
- CSV and XLSX data exports.
- OpenAI and Gemini models configured through `models.json`.
- Mock mode for local development without API calls.

PlotLift does not currently support logarithmic, categorical, or broken axes. 

## How A Digitisation Run Works

A run passes through five reviewable stages.

### 1. Upload And Page Selection

Create a run by uploading an image or PDF and choosing a model, image-detail setting, and reasoning effort. For best results, use `gpt-5.5`.

Images are converted to a canonical PNG. PDF pages are rasterised at the configured DPI. For a multi-page PDF, select the page containing the chart to digitise.

Each run processes one uploaded image or one selected PDF page. That image may contain several plot panels.

### 2. Crop

The initial crop covers the full selected image or page. You can:

- Drag the crop boundaries manually.
- Ask the model to find the complete chart region with **Auto Crop**.
- Confirm the crop before continuing.

For a multi-panel chart, the crop should include all relevant panels and any shared title, legend, labels, or annotations needed to interpret them.

For an automatic crop, PlotLift renders the proposed boundary as an overlay. The model reviews that overlay and either accepts its previous proposal or returns an improved crop.

### 3. Axis Identification And Calibration

**Auto Calibration** first identifies every visible x-axis and y-axis. It records each axis's:

- Stable axis ID.
- Direction.
- Name and unit.
- Measured quantity.
- Location in the chart or panel.
- Whether it is a primary axis for digitisation.

This allows PlotLift to distinguish panel-specific axes, secondary axes, and duplicate axes showing the same quantity in different units.

Each usable axis is calibrated from two locations. A calibration point combines an image position with its real chart value. PlotLift uses each pair to convert between image coordinates and chart-space values.

PlotLift renders calibration markers for review. The model can revise inaccurate placements, and you can drag markers or edit their values before confirming the calibration.

### 4. Series Identification And Digitisation

**Auto Digitise Series** works in two parts:

1. The model identifies all visible line series, describes their appearance, and assigns the appropriate x-axis and y-axis IDs.
2. You choose which identified series to digitise.

For each selected series, the model returns representative points as real `chart_x` and `chart_y` values. PlotLift projects those values through the selected axis calibrations and renders the resulting trace over the source chart.

The model reviews the overlay and either accepts the trace or returns a complete improved proposal. Up to three attempts are made for each series.

Before confirming the result, you can:

- Drag series points on the chart.
- Edit point values in chart space.
- Add or delete points.
- Add a manual series.
- Rename or delete a series.
- Retry automatic digitisation for an individual series.

Dragging a point updates its chart-space values. Editing chart-space values updates its image position.

### 5. Export

After confirming the series, download the digitised data as CSV or XLSX.

The standard table contains one row per digitised point:

```text
series_name, x, y, x_value_type, y_value_type, x_unit, y_unit, x_axis_name, y_axis_name
```

Enable **Debug Data** to add run, series, axis, and image-coordinate fields:

```text
run_id, llm_series_name, segment_index, point_index, x_axis_id, y_axis_id,
x_axis_unit, y_axis_unit, crop_image_norm_x, crop_image_norm_y,
crop_image_px_x, crop_image_px_y, full_image_px_x, full_image_px_y
```

A debug XLSX export also contains `metadata`, `series_summary`, and `axes` worksheets.

The interface also has an **Archive** download. This is a diagnostic snapshot of the run directory rather than a normal data export. It can include the uploaded source, raw provider responses, prompts, overlays, logs, and state, so review it before sharing.

## Multi-Panel Charts

Multiple plot panels can be handled in one run. PlotLift keeps panel-specific axes distinct through axis IDs and location descriptions. Each identified series is linked to the axes for its panel.

The crop and image-coordinate systems span the complete approved chart region. Each series then uses its assigned calibrated axes for chart-space conversion, allowing different panels to use different scales within the same image.

## Coordinate Systems

PlotLift deliberately separates five coordinate frames:

- `full_image_px`: real pixel coordinates on the canonical uploaded image or rasterised PDF page.
- `full_image_norm`: integer normalized `0..999` coordinates on the canonical full image.
- `crop_image_px`: real pixel coordinates inside the approved crop.
- `crop_image_norm`: integer normalized `0..999` coordinates inside the crop.
- `chart_space`: real x/y values represented by the calibrated chart axes.

The stages use those coordinate frames differently:

- Crop detection sees the complete canonical image and returns a box in `full_image_norm`.
- Axis identification returns metadata only; it does not return calibration coordinates.
- Axis calibration returns two `crop_image_norm` positions and their chart-space values for each usable axis.
- Series identification returns series metadata and the axis IDs assigned to each series.
- Series digitisation returns `chart_x` and `chart_y`. The backend projects these values through the selected calibrated axes to derive `crop_image_px` and `crop_image_norm`.

All image coordinate systems use a top-left origin: x increases to the right and y increases downward. Chart-space direction comes from the calibration values, so PlotLift can represent ascending, descending, and visually reversed axes.

The backend performs the conversions between full-image, crop-image, and chart-space coordinates. OpenAI image-coordinate schemas use the internal `0..999` scale. Gemini uses `0..1000` at the provider boundary, and PlotLift converts those coordinates to `0..999` before validation and storage. Series digitisation uses chart-space values for both providers.

## Model Review Loops

Crop, axis-calibration, and series-digitisation conversations use a consistent decision format:

- The first attempt returns `response_kind: "proposal"` with a complete proposal.
- A review returns `accept_previous` with no replacement, or `revise_previous` with a complete replacement.
- Partial patches are not accepted.

OpenAI calls use the Responses API with structured output, `store: true`, response chaining, and stable prompt-cache keys. Gemini calls use native multimodal requests and preserve the conversation history required for review attempts.

## Mock Mode And Smoke Test

### Mock Mode

Set `OPENAI_MOCK_MODE=true` in `.env`, then start the web app normally:

```bash
python -m uvicorn app.main:app --reload
```

The interface and normal workflow remain available, but PlotLift uses deterministic local mock proposals instead of making OpenAI or Gemini API calls. Mock mode is useful for UI and workflow development; it does not measure real model accuracy.

### Mock Smoke Test

The smoke-test command runs the backend pipeline end to end without starting the web interface:

```bash
python -m app.scripts.mock_smoke example_charts/pump_curve_01.png
```

It creates a temporary mock run, processes every stage, writes debug CSV and XLSX files, and deletes the run after the test succeeds. If the test fails, the run is retained for debugging.

To preserve a successful smoke-test run and inspect it in the web interface:

```bash
python -m app.scripts.mock_smoke example_charts/pump_curve_01.png --keep-run
```

## Tests

```bash
python -m pytest
```

The test suite covers coordinate transforms, crop conversations, provider adapters, schemas, state transitions, exports, configuration, and manual editing behavior.

## Project Structure

```text
app/
  main.py                FastAPI routes and background jobs
  models.py              State and structured-response models
  coordinates.py         Image and chart-space transformations
  artifacts.py           Run directories and artifact persistence
  image_io.py            Image handling and PDF rasterisation
  overlay.py             Crop, calibration, and series overlays
  openai_client.py       OpenAI Responses API integration
  gemini_client.py       Gemini integration and coordinate adaptation
  llm_client.py          Provider-neutral client selection
  prompts.py             Prompt-pack loading and hashing
  exports.py             CSV, XLSX, and diagnostic archive generation
  stages/
    crop.py              Iterative crop workflow
    calibration.py       Axis identification and calibration
    series.py            Series identification and digitisation
  scripts/
    mock_smoke.py        End-to-end mock pipeline check
static/
  app.js                 Browser workflow and interactive editing
  styles.css             Interface styling
templates/
  index.html             Main application page
tests/                   Automated tests
models.json              Available model configuration
prompts.yaml             Versioned model instructions
.env.example             Runtime configuration template
requirements.txt         Python dependencies
```

## Run Artifacts

A typical run directory contains:

```text
runs/<run_id>/
  state.json
  events.jsonl
  uploads/
  pages/
  crop/
  axis_identification/
  axis_<axis_id>/
  series_identification/
  series/
    <series_id>/
  exports/
```

Attempt directories contain the resolved request, raw response, parsed result, validation status, and any generated overlay. Conversation files record response IDs, source images, prompt-cache keys, and review decisions.

## Configuration

- `models.json` controls the model dropdown and each model's detail and reasoning options.
- `prompts.yaml` contains the versioned prompts for crop, axes, and series stages.
- `.env` contains local API keys and runtime settings.
- `requirements.txt` contains Python dependencies.

The default enabled model is currently `gpt-5.5`; change the `default` flag in `models.json` to select another default.
