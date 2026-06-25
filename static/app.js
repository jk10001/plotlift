const stateRef = {
  config: null,
  state: null,
  runId: window.INITIAL_RUN_ID || null,
  eventsTimer: null,
  stateTimer: null,
  drag: null,
  pan: null,
  renderFrame: null,
  localSaveCount: 0,
  chartZoom: 1,
  chartAssetKey: null,
  foregroundCalibrationAxisId: null,
  previousRuns: [],
  newRunOpen: false,
  previousRunMenuOpen: false,
  sidebarOpen: {
    calibration: null,
    series: null,
  },
  seriesChoiceKey: null,
  seriesChoiceSelected: [],
};

const DEFAULT_SERIES_COLORS = ["#0891b2", "#7c3aed", "#16a34a", "#ea580c", "#db2777"];
const AXIS_COLORS = ["#0072b2", "#d55e00", "#009e73", "#cc79a7", "#111827", "#56b4e9", "#8b5cf6", "#64748b"];
const SERIES_MARKER_KINDS = ["x", "circle", "square", "diamond", "triangle"];

const els = {
  runBar: document.querySelector("#runBar"),
  newRunToggleBtn: document.querySelector("#newRunToggleBtn"),
  uploadForm: document.querySelector("#uploadForm"),
  fileInput: document.querySelector("#fileInput"),
  modelSelect: document.querySelector("#modelSelect"),
  detailSelect: document.querySelector("#detailSelect"),
  reasoningSelect: document.querySelector("#reasoningSelect"),
  initiateRunBtn: document.querySelector("#initiateRunBtn"),
  loadPreviousRunBtn: document.querySelector("#loadPreviousRunBtn"),
  previousRunMenu: document.querySelector("#previousRunMenu"),
  runInfoBar: document.querySelector("#runInfoBar"),
  runInfoName: document.querySelector("#runInfoName"),
  runInfoFile: document.querySelector("#runInfoFile"),
  runInfoModel: document.querySelector("#runInfoModel"),
  runInfoDetail: document.querySelector("#runInfoDetail"),
  runInfoReasoning: document.querySelector("#runInfoReasoning"),
  runInfoModified: document.querySelector("#runInfoModified"),
  deleteRunBtn: document.querySelector("#deleteRunBtn"),
  modeBadge: document.querySelector("#modeBadge"),
  stageBadge: document.querySelector("#stageBadge"),
  stageControls: document.querySelector("#stageControls"),
  workflowArea: document.querySelector("#workflowArea"),
  cropStep: document.querySelector("#cropStep"),
  axisStep: document.querySelector("#axisStep"),
  seriesStep: document.querySelector("#seriesStep"),
  exportStep: document.querySelector("#exportStep"),
  cropStepStatus: document.querySelector("#cropStepStatus"),
  axisStepStatus: document.querySelector("#axisStepStatus"),
  seriesStepStatus: document.querySelector("#seriesStepStatus"),
  exportStepStatus: document.querySelector("#exportStepStatus"),
  pageChooser: document.querySelector("#pageChooser"),
  imageStage: document.querySelector("#imageStage"),
  chartImage: document.querySelector("#chartImage"),
  imageWrap: document.querySelector("#imageWrap"),
  overlaySvg: document.querySelector("#overlaySvg"),
  imageBusyOverlay: document.querySelector("#imageBusyOverlay"),
  imageBusyStatus: document.querySelector("#imageBusyStatus"),
  pointTooltip: document.querySelector("#pointTooltip"),
  dragLoupe: document.querySelector("#dragLoupe"),
  zoomOutBtn: document.querySelector("#zoomOutBtn"),
  zoomInBtn: document.querySelector("#zoomInBtn"),
  zoomLevel: document.querySelector("#zoomLevel"),
  eventLog: document.querySelector("#eventLog"),
  artifactList: document.querySelector("#artifactList"),
  debugPanel: document.querySelector("#debugPanel"),
  statusGrid: document.querySelector("#statusGrid"),
  calibrationDetails: document.querySelector("#calibrationDetails"),
  calibrationEditor: document.querySelector("#calibrationEditor"),
  calibrationStatus: document.querySelector("#calibrationStatus"),
  seriesDetails: document.querySelector("#seriesDetails"),
  seriesStatus: document.querySelector("#seriesStatus"),
  seriesEditor: document.querySelector("#seriesEditor"),
  debugExport: document.querySelector("#debugExport"),
  csvLink: document.querySelector("#csvLink"),
  xlsxLink: document.querySelector("#xlsxLink"),
  archiveLink: document.querySelector("#archiveLink"),
  cropJobBtn: document.querySelector("#cropJobBtn"),
  approveCropBtn: document.querySelector("#approveCropBtn"),
  axisJobBtn: document.querySelector("#axisJobBtn"),
  approveAxisBtn: document.querySelector("#approveAxisBtn"),
  seriesJobBtn: document.querySelector("#seriesJobBtn"),
  confirmSeriesBtn: document.querySelector("#confirmSeriesBtn"),
  addSeriesBtn: document.querySelector("#addSeriesBtn"),
  seriesChoiceModal: document.querySelector("#seriesChoiceModal"),
  seriesChoiceList: document.querySelector("#seriesChoiceList"),
  seriesChoiceCancelBtn: document.querySelector("#seriesChoiceCancelBtn"),
  seriesChoiceConfirmBtn: document.querySelector("#seriesChoiceConfirmBtn"),
};

init();

async function init() {
  stateRef.config = await api("/api/config");
  populateModelSelectors();
  wireEvents();
  await loadPreviousRuns();
  if (stateRef.runId) {
    await loadRun(stateRef.runId);
  } else {
    render();
  }
  startPolling();
}

function populateModelSelectors() {
  els.modelSelect.innerHTML = "";
  for (const model of stateRef.config.models) {
    const option = document.createElement("option");
    option.value = model.id;
    option.textContent = model.label;
    if (model.id === stateRef.config.default_model_id) option.selected = true;
    els.modelSelect.append(option);
  }
  updateOptionSelectors();
  els.modeBadge.textContent = stateRef.config.mock_mode ? "Mock Mode" : "Real API";
}

function updateOptionSelectors() {
  const model = currentModel();
  fillSelect(els.detailSelect, model.image_detail_options, model.default_image_detail || "high");
  fillSelect(els.reasoningSelect, model.reasoning_efforts, model.default_reasoning_effort || "medium");
}

function fillSelect(select, values, selectedValue) {
  select.innerHTML = "";
  for (const value of values) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = value;
    if (value === selectedValue) option.selected = true;
    select.append(option);
  }
}

function currentModel() {
  return stateRef.config.models.find((model) => model.id === els.modelSelect.value) || stateRef.config.models[0];
}

function wireEvents() {
  els.modelSelect.addEventListener("change", updateOptionSelectors);
  els.uploadForm.addEventListener("submit", onUpload);
  els.newRunToggleBtn.addEventListener("click", () => setNewRunOpen(!stateRef.newRunOpen));
  els.loadPreviousRunBtn.addEventListener("click", (event) => {
    event.stopPropagation();
    setNewRunOpen(false);
    setPreviousRunMenuOpen(!stateRef.previousRunMenuOpen);
  });
  els.previousRunMenu.addEventListener("click", (event) => event.stopPropagation());
  els.deleteRunBtn.addEventListener("click", deleteCurrentRun);
  document.addEventListener("click", (event) => {
    if (!stateRef.previousRunMenuOpen) return;
    if (event.target.closest?.("#loadPreviousRunBtn") || event.target.closest?.("#previousRunMenu")) return;
    setPreviousRunMenuOpen(false);
  });
  els.calibrationDetails.addEventListener("toggle", () => {
    stateRef.sidebarOpen.calibration = els.calibrationDetails.open;
  });
  els.seriesDetails.addEventListener("toggle", () => {
    stateRef.sidebarOpen.series = els.seriesDetails.open;
  });
  els.cropJobBtn.addEventListener("click", () => startJob("crop"));
  els.approveCropBtn.addEventListener("click", onCropConfirmOrEdit);
  els.axisJobBtn.addEventListener("click", () => startJob("calibration"));
  els.approveAxisBtn.addEventListener("click", onAxisConfirmOrEdit);
  els.seriesJobBtn.addEventListener("click", () => startJob("series"));
  els.confirmSeriesBtn.addEventListener("click", onSeriesConfirmOrEdit);
  els.addSeriesBtn.addEventListener("click", addManualSeries);
  els.seriesChoiceCancelBtn.addEventListener("click", cancelSeriesChoice);
  els.seriesChoiceConfirmBtn.addEventListener("click", confirmSeriesChoice);
  els.debugExport.addEventListener("change", renderExports);
  els.zoomOutBtn.addEventListener("click", () => adjustChartZoom(-1));
  els.zoomInBtn.addEventListener("click", () => adjustChartZoom(1));
  els.imageStage.addEventListener("wheel", onImageStageWheel, { passive: false });
  els.imageWrap.addEventListener("pointerdown", onChartPanPointerDown);
  els.overlaySvg.addEventListener("pointerover", onOverlayPointerOver);
  els.overlaySvg.addEventListener("pointermove", onOverlayPointerHoverMove);
  els.overlaySvg.addEventListener("pointerout", onOverlayPointerOut);
  els.overlaySvg.addEventListener("pointerdown", onPointerDown);
  window.addEventListener("pointermove", onPointerMove);
  window.addEventListener("pointerup", onPointerUp);
  window.addEventListener("pointercancel", onPointerUp);
  window.addEventListener("resize", scheduleOverlayRender);
  window.addEventListener("scroll", renderImageBusyState, { passive: true });
}

function setNewRunOpen(open) {
  stateRef.newRunOpen = Boolean(open);
  if (stateRef.newRunOpen) setPreviousRunMenuOpen(false);
  renderRunBar();
}

function setPreviousRunMenuOpen(open) {
  stateRef.previousRunMenuOpen = Boolean(open);
  renderRunBar();
}

async function onUpload(event) {
  event.preventDefault();
  const form = new FormData();
  form.set("file", els.fileInput.files[0]);
  form.set("model_id", els.modelSelect.value);
  form.set("image_detail", els.detailSelect.value);
  form.set("reasoning_effort", els.reasoningSelect.value);
  const result = await api("/api/runs", { method: "POST", body: form });
  stateRef.runId = result.run_id;
  stateRef.state = result.state;
  resetChartViewport();
  stateRef.sidebarOpen = { calibration: null, series: null };
  stateRef.newRunOpen = false;
  stateRef.previousRunMenuOpen = false;
  els.fileInput.value = "";
  history.replaceState(null, "", `/runs/${stateRef.runId}`);
  render();
  await loadPreviousRuns();
}

async function loadRun(runId, options = {}) {
  if (!options.force && shouldDeferStateRefresh()) return;
  const result = await api(`/api/runs/${runId}`);
  if (!options.force && shouldDeferStateRefresh()) return;
  if (stateRef.runId !== runId) {
    stateRef.sidebarOpen = { calibration: null, series: null };
    stateRef.foregroundCalibrationAxisId = null;
    resetChartViewport();
  }
  stateRef.runId = runId;
  stateRef.state = result.state;
  if (options.updateHistory !== false) history.replaceState(null, "", `/runs/${stateRef.runId}`);
  renderPreviousRunMenu();
  render();
}

async function loadPreviousRuns() {
  const result = await api("/api/runs");
  stateRef.previousRuns = result.runs || [];
  renderPreviousRunMenu();
  renderRunBar();
}

function renderPreviousRunMenu() {
  els.previousRunMenu.innerHTML = "";
  if (!stateRef.previousRuns.length) {
    const empty = document.createElement("div");
    empty.className = "previous-run-empty";
    empty.textContent = "No previous runs";
    els.previousRunMenu.append(empty);
    return;
  }
  for (const run of stateRef.previousRuns) {
    const option = document.createElement("button");
    option.type = "button";
    option.className = "previous-run-option";
    if (run.run_id === stateRef.runId) option.classList.add("is-current");
    const fileName = document.createElement("strong");
    fileName.textContent = run.upload_filename;
    const updated = document.createElement("span");
    updated.textContent = formatDateTime(run.updated_at);
    option.append(fileName, updated);
    option.addEventListener("click", async () => {
      await loadRun(run.run_id, { force: true });
      setPreviousRunMenuOpen(false);
      setNewRunOpen(false);
      await loadEvents();
    });
    els.previousRunMenu.append(option);
  }
}

function resetRunView() {
  stateRef.runId = null;
  stateRef.state = null;
  stateRef.foregroundCalibrationAxisId = null;
  resetChartViewport();
  stateRef.sidebarOpen = { calibration: null, series: null };
  els.fileInput.value = "";
  history.replaceState(null, "", "/");
  render();
}

function startPolling() {
  clearInterval(stateRef.eventsTimer);
  clearInterval(stateRef.stateTimer);
  stateRef.eventsTimer = setInterval(loadEvents, 1200);
  stateRef.stateTimer = setInterval(async () => {
    if (stateRef.runId && stateRef.state?.active_job) await loadRun(stateRef.runId);
  }, 1800);
}

async function loadEvents() {
  if (!stateRef.runId) return;
  if (!showDebugInfo()) return;
  if (shouldDeferStateRefresh()) return;
  const result = await api(`/api/runs/${stateRef.runId}/events`);
  renderEvents(result.events);
}

async function startJob(path) {
  if (!stateRef.runId) return;
  await api(`/api/runs/${stateRef.runId}/jobs/${path}`, { method: "POST" });
  await loadRun(stateRef.runId, { force: true });
}

async function confirmSeriesChoice() {
  if (!stateRef.runId || llmJobActive()) return;
  const selectedIndexes = stateRef.seriesChoiceSelected
    .map((selected, index) => selected ? index : null)
    .filter((index) => index !== null);
  if (!selectedIndexes.length) return;
  stateRef.seriesChoiceKey = null;
  await api(`/api/runs/${stateRef.runId}/jobs/series-selection`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ selected_indexes: selectedIndexes }),
  });
  await loadRun(stateRef.runId, { force: true });
}

async function cancelSeriesChoice() {
  if (!stateRef.runId || llmJobActive()) return;
  stateRef.seriesChoiceKey = null;
  stateRef.seriesChoiceSelected = [];
  const result = await api(`/api/runs/${stateRef.runId}/series-selection/cancel`, { method: "POST" });
  stateRef.state = result.state;
  render();
}

async function deleteCurrentRun() {
  if (!stateRef.runId || llmJobActive()) return;
  const runId = stateRef.runId;
  const fileName = stateRef.state?.upload_filename || runId;
  if (!window.confirm(`Delete run ${fileName}? This removes the run folder and cannot be undone.`)) return;
  await api(`/api/runs/${encodeURIComponent(runId)}`, { method: "DELETE" });
  resetRunView();
  await loadPreviousRuns();
  renderEvents([]);
}

async function onCropConfirmOrEdit() {
  if (stateRef.state?.crop?.approved) {
    await editCrop();
  } else {
    await approveCrop();
  }
}

async function approveCrop() {
  if (llmJobActive()) return;
  const state = stateRef.state;
  if (!state?.crop) return;
  const bbox = state.crop.bbox_full_norm;
  const result = await api(`/api/runs/${stateRef.runId}/crop`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ bbox }),
  });
  stateRef.state = result.state;
  stateRef.sidebarOpen.calibration = true;
  stateRef.sidebarOpen.series = true;
  render();
}

async function editCrop() {
  if (llmJobActive()) return;
  const result = await api(`/api/runs/${stateRef.runId}/crop/edit`, { method: "POST" });
  stateRef.state = result.state;
  stateRef.sidebarOpen.calibration = true;
  stateRef.sidebarOpen.series = true;
  render({ forceEditors: true });
}

async function onAxisConfirmOrEdit() {
  if (axisCalibrationApproved()) {
    await editAxisCalibration();
  } else {
    await approveAxisCalibration();
  }
}

async function approveAxisCalibration() {
  if (llmJobActive()) return;
  const axes = calibratedAxes().filter((axis) => axis.points?.length === 2);
  if (!axes.length) return;
  const result = await api(`/api/runs/${stateRef.runId}/calibration`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ axes }),
  });
  stateRef.state = result.state;
  stateRef.sidebarOpen.calibration = false;
  render();
}

async function editAxisCalibration() {
  if (llmJobActive()) return;
  const result = await api(`/api/runs/${stateRef.runId}/calibration/edit`, { method: "POST" });
  stateRef.state = result.state;
  stateRef.sidebarOpen.calibration = true;
  stateRef.sidebarOpen.series = true;
  render({ forceEditors: true });
}

async function saveCropDraft() {
  if (llmJobActive()) return;
  const state = stateRef.state;
  if (!state?.crop) return;
  if (state.crop.approved) return;
  stateRef.localSaveCount += 1;
  try {
    const result = await api(`/api/runs/${stateRef.runId}/crop-draft`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ bbox: state.crop.bbox_full_norm }),
    });
    stateRef.state = result.state;
    stateRef.sidebarOpen.calibration = true;
    stateRef.sidebarOpen.series = true;
    render();
  } finally {
    stateRef.localSaveCount -= 1;
  }
}

async function saveCalibrationDraft(axis) {
  if (llmJobActive()) return;
  if (axisCalibrationApproved()) return;
  const axisState = findCalibratedAxis(axis);
  const points = axisState?.points || [];
  if (!axisState || !points.length) return;
  stateRef.localSaveCount += 1;
  try {
    const result = await api(`/api/runs/${stateRef.runId}/calibration/${axis}/draft`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ points }),
    });
    stateRef.state = result.state;
    stateRef.sidebarOpen.calibration = true;
    stateRef.sidebarOpen.series = true;
    render();
  } finally {
    stateRef.localSaveCount -= 1;
  }
}

async function saveSeries(source, options = {}) {
  if (llmJobActive()) return;
  if (!stateRef.runId || !stateRef.state) return;
  if (seriesConfirmed() && !options.markComplete) return;
  stateRef.localSaveCount += 1;
  try {
    const result = await api(`/api/runs/${stateRef.runId}/series`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source, series: stateRef.state.series || [], mark_complete: Boolean(options.markComplete) }),
    });
    stateRef.state = result.state;
    stateRef.sidebarOpen.series = options.markComplete ? false : true;
    render({ forceEditors: Boolean(options.forceEditors) });
  } finally {
    stateRef.localSaveCount -= 1;
  }
}

async function onSeriesConfirmOrEdit() {
  if (seriesConfirmed()) {
    await editSeries();
  } else {
    await confirmSeries();
  }
}

async function confirmSeries() {
  if (llmJobActive()) return;
  if (!stateRef.state?.series?.length) return;
  await saveSeries("image", { markComplete: true, forceEditors: true });
}

async function editSeries() {
  if (llmJobActive()) return;
  const result = await api(`/api/runs/${stateRef.runId}/series/edit`, { method: "POST" });
  stateRef.state = result.state;
  stateRef.sidebarOpen.series = true;
  render({ forceEditors: true });
}

function render(options = {}) {
  const state = stateRef.state;
  const hasRun = Boolean(state);
  const waitingForPage = pageSelectionPending(state);
  els.stageBadge.textContent = state ? state.stage.replaceAll("_", " ") : "Idle";
  els.stageControls.hidden = !hasRun;
  els.workflowArea.hidden = !hasRun || waitingForPage;
  els.pageChooser.hidden = !hasRun;
  renderRunBar();
  renderPageChooser();
  renderImage();
  renderControls();
  renderStepProgress();
  renderSidebarStatus();
  if (options.forceEditors || llmJobActive() || !isFocusedInside(els.calibrationEditor)) renderCalibrationEditor();
  if (options.forceEditors || llmJobActive() || !isFocusedInside(els.seriesEditor)) renderSeriesEditor();
  renderDebugInfo();
  renderExports();
  renderSeriesChoiceModal();
  if (!state) renderEvents([]);
}

function renderRunBar() {
  const newRunOpen = stateRef.newRunOpen;
  const previousMenuOpen = stateRef.previousRunMenuOpen;
  els.newRunToggleBtn.classList.toggle("is-active", newRunOpen);
  els.loadPreviousRunBtn.classList.toggle("is-active", previousMenuOpen);
  els.uploadForm.hidden = !newRunOpen;
  els.previousRunMenu.classList.toggle("is-open", previousMenuOpen);
  els.loadPreviousRunBtn.disabled = !stateRef.previousRuns.length;

  for (const control of [
    els.fileInput,
    els.modelSelect,
    els.detailSelect,
    els.reasoningSelect,
    els.initiateRunBtn,
  ]) {
    control.disabled = !newRunOpen;
  }

  renderRunInfoBar();
  for (const section of [els.runInfoBar, els.stageControls, els.pageChooser, els.workflowArea, els.statusGrid]) {
    if (section) section.classList.toggle("is-run-muted", newRunOpen);
  }
}

function renderRunInfoBar() {
  const state = stateRef.state;
  if (!state) {
    els.runInfoName.textContent = "No run loaded";
    els.runInfoFile.textContent = "-";
    els.runInfoModel.textContent = "-";
    els.runInfoDetail.textContent = "-";
    els.runInfoReasoning.textContent = "-";
    els.runInfoModified.textContent = "-";
    els.deleteRunBtn.disabled = true;
    return;
  }
  els.runInfoName.textContent = `Run ${state.run_id}`;
  els.runInfoFile.textContent = state.upload_filename || "-";
  els.runInfoModel.textContent = state.settings?.model_id || "-";
  els.runInfoDetail.textContent = state.settings?.image_detail || "-";
  els.runInfoReasoning.textContent = state.settings?.reasoning_effort || "none";
  els.runInfoModified.textContent = formatDateTime(state.updated_at);
  els.deleteRunBtn.disabled = Boolean(state.active_job);
}

function renderStepProgress() {
  const state = stateRef.state;
  const activeJob = state?.active_job || "";
  const cropDone = Boolean(state?.crop?.approved);
  const axes = calibratedAxes();
  const axesDone = axisCalibrationApproved();
  const hasAxisPoints = axes.some((axis) => (axis.points || []).length >= 2);
  const seriesDone = seriesConfirmed();
  const hasSeries = Boolean(state?.series?.length);
  const hasPendingSeries = Boolean(state?.pending_series?.length);
  const axisActive = activeJob.includes("axis") || activeJob === "axis_calibration";

  setStepState(els.cropStep, { inProgress: Boolean(state?.canonical_image && !cropDone), running: activeJob === "crop", complete: cropDone, blocked: !state?.canonical_image });
  setStepState(els.axisStep, { inProgress: Boolean(cropDone && !axesDone), running: axisActive, complete: axesDone, blocked: !cropDone });
  setStepState(els.seriesStep, { inProgress: Boolean(axesDone && !seriesDone), running: activeJob === "series", complete: seriesDone, blocked: !axesDone });
  setStepState(els.exportStep, { inProgress: seriesDone, running: false, complete: false, blocked: !seriesDone });

  els.cropStepStatus.textContent = cropStepStatusText(state, cropDone, activeJob);
  els.axisStepStatus.textContent = axisStepStatusText(state, axesDone, hasAxisPoints, axisActive);
  els.seriesStepStatus.textContent = seriesStepStatusText(state, seriesDone, hasPendingSeries, hasSeries, activeJob);
  els.exportStepStatus.textContent = seriesDone ? "Ready" : "Waiting for series";
}

function cropStepStatusText(state, cropDone, activeJob) {
  if (cropDone) return "Complete";
  if (activeJob === "crop") return state?.active_step_status || "Finding crop...";
  if (pageSelectionPending(state)) return "Select PDF page";
  if (state?.crop) return "Ready to confirm";
  return "Waiting";
}

function axisStepStatusText(state, axesDone, hasAxisPoints, axisActive) {
  if (axesDone) return "Complete";
  if (axisActive) return state?.active_step_status || (state?.calibration?.identified_axes?.length ? "Calibrating axis..." : "Identifying axes...");
  if (hasAxisPoints) return "Ready to confirm";
  return cropApproved() ? "Waiting for points" : "Waiting for crop";
}

function seriesStepStatusText(state, seriesDone, hasPendingSeries, hasSeries, activeJob) {
  if (seriesDone) return "Complete";
  if (activeJob === "series") {
    if (state?.active_step_status) return state.active_step_status;
    return hasPendingSeries ? `Digitising series 1 of ${state.pending_series.length}...` : "Identifying series...";
  }
  if (hasPendingSeries) return `${state.pending_series.length} series identified`;
  if (hasSeries) return `${state.series.length} series ready to confirm`;
  return axisCalibrationApproved() ? "Waiting" : "Waiting for axes";
}

function activeStepStatusText() {
  const state = stateRef.state;
  const activeJob = state?.active_job || "";
  if (!activeJob) return "";
  if (state?.active_step_status) return state.active_step_status;
  if (activeJob === "crop") return "Finding crop...";
  if (activeJob.includes("axis") || activeJob === "axis_calibration") return "Identifying axes...";
  if (activeJob === "series") return pendingSeries().length ? `Digitising series 1 of ${pendingSeries().length}...` : "Identifying series...";
  return "Working...";
}

function axisCalibrationApproved() {
  const axes = calibratedAxes();
  if (!axes.length) return false;
  const hasX = axes.some((axis) => axis.direction === "x" && axis.approved && (axis.points || []).length === 2);
  const hasY = axes.some((axis) => axis.direction === "y" && axis.approved && (axis.points || []).length === 2);
  return hasX && hasY && axes.every((axis) => axis.approved && (axis.points || []).length === 2);
}

function cropApproved() {
  return Boolean(stateRef.state?.crop?.approved);
}

function seriesConfirmed() {
  return stateRef.state?.stage === "complete" && Boolean(stateRef.state?.series?.length);
}

function setStepState(element, { inProgress, running, complete, blocked }) {
  element.classList.toggle("is-in-progress", Boolean(inProgress && !running && !complete));
  element.classList.toggle("is-running", Boolean(running));
  element.classList.toggle("is-complete", Boolean(complete));
  element.classList.toggle("is-blocked", Boolean(blocked));
}

function renderSidebarStatus() {
  const state = stateRef.state;
  const axes = calibratedAxes();
  const calibrationDone = axisCalibrationApproved();
  const seriesDone = state?.stage === "complete";
  const seriesCount = state?.series?.length || 0;
  const pendingCount = pendingSeries().length;

  els.calibrationStatus.textContent = calibrationDone ? "Complete" : axes.length ? `${axes.filter((axis) => axis.approved).length}/${axes.length} axes` : "Not complete";
  els.seriesStatus.textContent = seriesDone ? "Saved" : pendingCount ? `${pendingCount} identified` : `${seriesCount} series`;

  if (stateRef.sidebarOpen.calibration === null) els.calibrationDetails.open = !calibrationDone;
  else els.calibrationDetails.open = stateRef.sidebarOpen.calibration;
  if (stateRef.sidebarOpen.series === null) els.seriesDetails.open = !seriesDone;
  else els.seriesDetails.open = stateRef.sidebarOpen.series;
  const busy = llmJobActive();
  els.calibrationDetails.classList.toggle("is-busy", busy);
  els.seriesDetails.classList.toggle("is-busy", busy);
}

function renderSeriesChoiceModal() {
  const pending = pendingSeries();
  const open = Boolean(stateRef.runId && pending.length && !llmJobActive() && !seriesConfirmed());
  els.seriesChoiceModal.hidden = !open;
  document.body.classList.toggle("modal-open", open);
  for (const section of [document.querySelector("header"), document.querySelector("main")]) {
    if (section) section.inert = open;
  }
  if (!open) {
    if (!pending.length) {
      stateRef.seriesChoiceKey = null;
      stateRef.seriesChoiceSelected = [];
    }
    return;
  }

  const key = seriesChoiceKey(pending);
  if (stateRef.seriesChoiceKey !== key) {
    stateRef.seriesChoiceKey = key;
    stateRef.seriesChoiceSelected = pending.map(() => true);
  }

  els.seriesChoiceList.innerHTML = "";
  pending.forEach((series, index) => {
    const item = document.createElement("label");
    item.className = "series-choice-item";
    item.innerHTML = `
      <input type="checkbox" data-series-choice-index="${index}" ${stateRef.seriesChoiceSelected[index] ? "checked" : ""}>
      <span class="series-choice-main">
        <strong>${escapeHtml(series.series_name || `Series ${index + 1}`)}</strong>
        <span>${escapeHtml([series.line_color, series.line_style].filter(Boolean).join(" · ") || "Visual style unknown")}</span>
        <small>${escapeHtml(series.visual_description || "No visual description provided.")}</small>
      </span>
    `;
    els.seriesChoiceList.append(item);
  });

  els.seriesChoiceList.querySelectorAll("[data-series-choice-index]").forEach((checkbox) => {
    checkbox.addEventListener("change", () => {
      stateRef.seriesChoiceSelected[Number(checkbox.dataset.seriesChoiceIndex)] = checkbox.checked;
      updateSeriesChoiceConfirmState();
    });
  });
  updateSeriesChoiceConfirmState();
}

function updateSeriesChoiceConfirmState() {
  els.seriesChoiceConfirmBtn.disabled = !stateRef.seriesChoiceSelected.some(Boolean);
}

function pendingSeries() {
  return stateRef.state?.pending_series || [];
}

function seriesChoiceKey(series) {
  return `${stateRef.runId}:${series.map((item, index) => [
    index,
    item.series_name || "",
    item.visual_description || "",
    item.line_color || "",
    item.line_style || "",
    item.x_axis_id || "",
    item.y_axis_id || "",
  ].join("~")).join("|")}`;
}

function shouldDeferStateRefresh() {
  if (stateRef.localSaveCount > 0) return true;
  if (stateRef.drag) return true;
  const active = document.activeElement;
  if (!active) return false;
  if (active.isContentEditable) return true;
  return active.matches?.("input, textarea, select") || false;
}

function isFocusedInside(element) {
  const active = document.activeElement;
  return Boolean(active && element && element.contains(active));
}

function renderPageChooser() {
  const state = stateRef.state;
  els.pageChooser.innerHTML = "";
  const needsSelection = pageSelectionPending(state);
  if (!state?.pages?.length || state.pages.length === 1) {
    els.pageChooser.classList.remove("active");
    els.pageChooser.hidden = true;
    return;
  }
  els.pageChooser.classList.add("active");
  els.pageChooser.hidden = false;
  const header = document.createElement("header");
  header.className = "page-strip-header";
  const selectedLabel = selectedPageLabel(state);
  header.innerHTML = `
    <div>
      <h2>${needsSelection ? "Select PDF Page" : "PDF Page"}</h2>
      <p>${needsSelection ? `${state.pages.length} pages available` : `${selectedLabel} selected`}</p>
    </div>
    <strong>${state.pages.length} pages</strong>
  `;
  const list = document.createElement("div");
  list.className = "page-thumb-list";
  state.pages.forEach((page, index) => {
    const button = document.createElement("button");
    button.type = "button";
    const selected = state.selected_page_index === index;
    button.className = `page-thumb ${selected ? "selected" : ""}`;
    if (selected) button.setAttribute("aria-current", "page");
    const label = page.label || `Page ${index + 1}`;
    button.innerHTML = `
      <img src="${fileUrl(page.path)}" alt="${escapeHtml(label)}">
      <span>${escapeHtml(label)}</span>
      <small>${selected ? "Selected" : "Use page"}</small>
    `;
    button.addEventListener("click", async () => {
      if (stateRef.state?.selected_page_index === index) return;
      if (pageSwitchWillResetWork(stateRef.state) && !window.confirm("Switching PDF pages will clear the current crop, axes, and series for this run.")) return;
      const result = await api(`/api/runs/${state.run_id}/select-page/${index}`, { method: "POST" });
      stateRef.state = result.state;
      resetChartViewport();
      render();
    });
    list.append(button);
  });
  els.pageChooser.append(header, list);
}

function pageSelectionPending(state) {
  return Boolean(state?.pages?.length > 1 && state.selected_page_index === null);
}

function selectedPageLabel(state) {
  if (!state?.pages?.length || state.selected_page_index === null) return "No page";
  return state.pages[state.selected_page_index]?.label || `Page ${state.selected_page_index + 1}`;
}

function pageSwitchWillResetWork(state) {
  if (!state?.canonical_image) return false;
  if (state.crop?.approved) return true;
  if (state.calibration?.calibrated_axes?.length || state.calibration?.x_points?.length || state.calibration?.y_points?.length) return true;
  if (state.pending_series?.length || state.series?.length) return true;
  return false;
}

function renderImage() {
  const state = stateRef.state;
  const asset = activeImageAsset();
  els.overlaySvg.innerHTML = "";
  if (!asset) {
    els.chartImage.removeAttribute("src");
    els.chartImage.removeAttribute("style");
    stateRef.chartAssetKey = null;
    renderImageBusyState();
    renderChartViewportState();
    return;
  }
  syncChartAsset(asset);
  applyChartZoom(asset);
  const src = fileUrl(asset.path);
  if (els.chartImage.getAttribute("src") !== src) {
    els.chartImage.src = src;
  }
  els.chartImage.onload = syncSvgToImage;
  syncSvgToImage();
  renderImageBusyState();
  if (!state) return;
  if (activeImageMode() === "full") renderCropOverlay();
  else {
    renderCalibrationOverlay();
    renderSeriesOverlay();
  }
}

function scheduleOverlayRender() {
  if (stateRef.renderFrame) return;
  stateRef.renderFrame = requestAnimationFrame(() => {
    stateRef.renderFrame = null;
    renderImage();
    updateDebugPanel();
  });
}

function updateLiveOverlay() {
  if (activeImageMode() === "full") updateLiveCropOverlay();
  else {
    updateLiveCalibrationOverlay();
    updateLiveSeriesOverlay();
  }
  updateDebugPanel();
}

function updateLiveCropOverlay() {
  const b = stateRef.state?.crop?.bbox_full_px;
  if (!b) return;
  const rect = els.overlaySvg.querySelector('[data-drag="crop-move"]');
  setSvgAttrs(rect, { x: b.left, y: b.top, width: b.right - b.left, height: b.bottom - b.top });
  const size = cssPxToSvgUnits(14);
  updateCropEdgeHandle("n", b, size);
  updateCropEdgeHandle("s", b, size);
  updateCropEdgeHandle("e", b, size);
  updateCropEdgeHandle("w", b, size);
  const handles = {
    "crop-nw": [b.left, b.top],
    "crop-ne": [b.right, b.top],
    "crop-sw": [b.left, b.bottom],
    "crop-se": [b.right, b.bottom],
  };
  for (const [key, [x, y]] of Object.entries(handles)) {
    setSvgAttrs(els.overlaySvg.querySelector(`[data-drag="${key}"]`), { x: x - size / 2, y: y - size / 2, width: size, height: size });
  }
}

function updateCropEdgeHandle(edge, b, size) {
  const attrs = cropEdgeAttrs(edge, b, size);
  setSvgAttrs(els.overlaySvg.querySelector(`[data-drag="crop-${edge}"]`), attrs);
}

function updateLiveCalibrationOverlay() {
  for (const axis of calibratedAxes()) {
    for (const point of axis.points || []) {
    const p = point.crop_image_px;
    if (!p) continue;
      const key = calibrationPointKey(axis.axis_id, point.label);
      setSvgAttrs(els.overlaySvg.querySelector(`[data-cal-key="${cssAttr(key)}"]`), { cx: p.x, cy: p.y, r: cssPxToSvgUnits(8) });
      setSvgAttrs(els.overlaySvg.querySelector(`[data-label="${cssAttr(key)}"]`), calibrationLabelAttrs(point));
    }
  }
}

function updateLiveSeriesOverlay() {
  const series = stateRef.state?.series || [];
  for (const item of series) {
    const segments = groupBy(item.points || [], (point) => point.segment_index || 0);
    for (const [segmentIndex, points] of Object.entries(segments)) {
      points.sort((a, b) => a.point_index - b.point_index);
      const d = points
        .filter((point) => point.crop_image_px)
        .map((point, index) => `${index === 0 ? "M" : "L"} ${point.crop_image_px.x} ${point.crop_image_px.y}`)
        .join(" ");
      setSvgAttrs(els.overlaySvg.querySelector(`[data-series-path="${cssAttr(`${item.id}:${segmentIndex}`)}"]`), { d });
      for (const point of points) {
        if (!point.crop_image_px) continue;
        const selector = `[data-drag="series"][data-series-id="${cssAttr(item.id)}"][data-segment-index="${point.segment_index}"][data-point-index="${point.point_index}"]`;
        for (const marker of els.overlaySvg.querySelectorAll(selector)) {
          updateSeriesPointMarkerElement(marker, point.crop_image_px);
        }
      }
    }
  }
}

function activeImageAsset() {
  const state = stateRef.state;
  if (!state) return null;
  if (activeImageMode() === "crop") return state.crop?.image || state.canonical_image;
  return state.canonical_image;
}

function activeImageMode() {
  const state = stateRef.state;
  if (!state?.crop?.approved) return "full";
  if (["crop_review", "page_selected", "crop_ready"].includes(state.stage)) return "full";
  return "crop";
}

function syncSvgToImage() {
  const rect = els.chartImage.getBoundingClientRect();
  const wrapRect = els.imageWrap.getBoundingClientRect();
  const geometry = {
    left: `${rect.left - wrapRect.left}px`,
    top: `${rect.top - wrapRect.top}px`,
    width: `${rect.width}px`,
    height: `${rect.height}px`,
  };
  Object.assign(els.overlaySvg.style, geometry);
  hidePointTooltip();
  const asset = activeImageAsset();
  if (asset) els.overlaySvg.setAttribute("viewBox", `0 0 ${asset.width} ${asset.height}`);
  renderChartViewportState();
}

function applyChartZoom(asset) {
  const width = Math.max(1, Math.round(chartFitWidth(asset) * stateRef.chartZoom));
  els.chartImage.style.width = `${width}px`;
}

function chartFitWidth(asset) {
  const stageWidth = els.imageStage?.clientWidth || asset.width;
  const stageHeight = els.imageStage?.clientHeight || asset.height;
  const horizontalPadding = 24;
  const verticalPadding = 64;
  const availableWidth = Math.max(160, stageWidth - horizontalPadding);
  const availableHeight = Math.max(160, stageHeight - verticalPadding);
  const widthByHeight = availableHeight * (asset.width / asset.height);
  return Math.min(asset.width, availableWidth, widthByHeight);
}

function syncChartAsset(asset) {
  const key = `${asset.path}:${asset.width}x${asset.height}`;
  if (stateRef.chartAssetKey === key) return;
  stateRef.chartAssetKey = key;
  stateRef.chartZoom = 1;
  stateRef.pan = null;
  if (els.imageStage) {
    els.imageStage.scrollLeft = 0;
    els.imageStage.scrollTop = 0;
  }
}

function adjustChartZoom(direction) {
  const asset = activeImageAsset();
  if (!asset) return;
  const stage = els.imageStage;
  const centerX = stage.scrollLeft + stage.clientWidth / 2;
  const centerY = stage.scrollTop + stage.clientHeight / 2;
  const previousZoom = stateRef.chartZoom;
  const nextZoom = clampZoom(previousZoom + direction * 0.25);
  if (nextZoom === previousZoom) return;
  stateRef.chartZoom = nextZoom;
  renderImage();
  const ratio = nextZoom / previousZoom;
  stage.scrollLeft = Math.max(0, centerX * ratio - stage.clientWidth / 2);
  stage.scrollTop = Math.max(0, centerY * ratio - stage.clientHeight / 2);
  requestAnimationFrame(() => {
    syncSvgToImage();
    renderChartViewportState();
  });
}

function clampZoom(value) {
  return Math.min(4, Math.max(0.5, Math.round(value * 4) / 4));
}

function resetChartViewport() {
  stateRef.chartZoom = 1;
  stateRef.chartAssetKey = null;
  stateRef.pan = null;
  if (els.imageStage) {
    els.imageStage.scrollLeft = 0;
    els.imageStage.scrollTop = 0;
  }
}

function renderChartViewportState() {
  const asset = activeImageAsset();
  const hasAsset = Boolean(asset);
  els.zoomOutBtn.disabled = !hasAsset || stateRef.chartZoom <= 0.5;
  els.zoomInBtn.disabled = !hasAsset || stateRef.chartZoom >= 4;
  const scale = asset ? (chartFitWidth(asset) * stateRef.chartZoom) / asset.width : 1;
  els.zoomLevel.textContent = `${Math.round(scale * 100)}%`;
  els.imageStage.classList.toggle("needs-y-pan", Boolean(asset && chartContentHeight() > els.imageStage.clientHeight + 4));
  const canPan = hasAsset && chartCanPan();
  els.imageWrap.classList.toggle("can-pan", canPan && !llmJobActive());
}

function chartCanPan() {
  return els.imageStage.scrollWidth > els.imageStage.clientWidth + 1 || els.imageStage.scrollHeight > els.imageStage.clientHeight + 1;
}

function chartContentHeight() {
  return els.chartImage.offsetTop + els.chartImage.offsetHeight + 12;
}

function cssPxToSvgUnits(px) {
  const asset = activeImageAsset();
  const rect = els.overlaySvg.getBoundingClientRect();
  if (!asset || !rect.width) return px;
  return px * (asset.width / rect.width);
}

function fixedSquareAttrs(cx, cy, sizePx) {
  const size = cssPxToSvgUnits(sizePx);
  return {
    x: cx - size / 2,
    y: cy - size / 2,
    width: size,
    height: size,
  };
}

function calibrationLabelAttrs(point) {
  const p = point.crop_image_px;
  const offset = cssPxToSvgUnits(10);
  return {
    x: p.x + offset,
    y: p.y - offset,
    "font-size": cssPxToSvgUnits(15),
    "stroke-width": cssPxToSvgUnits(4),
  };
}

function seriesPointMarkerAttrs(point, arm) {
  const halfSize = cssPxToSvgUnits(4);
  const isBackslash = arm === "a";
  return {
    x1: point.x - halfSize,
    y1: point.y + (isBackslash ? -halfSize : halfSize),
    x2: point.x + halfSize,
    y2: point.y + (isBackslash ? halfSize : -halfSize),
  };
}

function renderSeriesPointMarker(point, kind, commonAttrs) {
  if (kind === "circle") {
    svgEl("circle", {
      ...commonAttrs,
      cx: point.x,
      cy: point.y,
      r: cssPxToSvgUnits(4),
    });
    return;
  }
  if (kind === "square") {
    svgEl("rect", {
      ...commonAttrs,
      ...fixedSquareAttrs(point.x, point.y, 8),
    });
    return;
  }
  if (kind === "diamond") {
    svgEl("path", {
      ...commonAttrs,
      d: diamondPath(point, cssPxToSvgUnits(5)),
    });
    return;
  }
  if (kind === "triangle") {
    svgEl("path", {
      ...commonAttrs,
      d: trianglePath(point, cssPxToSvgUnits(5)),
    });
    return;
  }
  svgEl("line", {
    ...commonAttrs,
    ...seriesPointMarkerAttrs(point, "a"),
    "data-marker-arm": "a",
  });
  svgEl("line", {
    ...commonAttrs,
    ...seriesPointMarkerAttrs(point, "b"),
    "data-marker-arm": "b",
  });
}

function updateSeriesPointMarkerElement(marker, point) {
  const kind = marker.dataset.markerKind || "x";
  if (kind === "circle") {
    setSvgAttrs(marker, { cx: point.x, cy: point.y, r: cssPxToSvgUnits(4) });
    return;
  }
  if (kind === "square") {
    setSvgAttrs(marker, fixedSquareAttrs(point.x, point.y, 8));
    return;
  }
  if (kind === "diamond") {
    setSvgAttrs(marker, { d: diamondPath(point, cssPxToSvgUnits(5)) });
    return;
  }
  if (kind === "triangle") {
    setSvgAttrs(marker, { d: trianglePath(point, cssPxToSvgUnits(5)) });
    return;
  }
  setSvgAttrs(marker, seriesPointMarkerAttrs(point, marker.dataset.markerArm));
}

function diamondPath(point, size) {
  return `M ${point.x} ${point.y - size} L ${point.x + size} ${point.y} L ${point.x} ${point.y + size} L ${point.x - size} ${point.y} Z`;
}

function trianglePath(point, size) {
  return `M ${point.x} ${point.y - size} L ${point.x + size} ${point.y + size} L ${point.x - size} ${point.y + size} Z`;
}

function renderCropOverlay() {
  const state = stateRef.state;
  if (!state?.crop?.bbox_full_px) return;
  const b = state.crop.bbox_full_px;
  const locked = llmJobActive() || Boolean(state.crop.approved);
  svgEl("rect", {
    class: `crop-rect ${locked ? "locked-overlay" : "interactive"}`,
    x: b.left,
    y: b.top,
    width: b.right - b.left,
    height: b.bottom - b.top,
    ...(locked ? {} : { "data-drag": "crop-move" }),
  });
  if (locked) return;
  const edgeSize = cssPxToSvgUnits(14);
  for (const edge of ["n", "s", "e", "w"]) {
    svgEl("rect", {
      class: "crop-edge interactive",
      ...cropEdgeAttrs(edge, b, edgeSize),
      "data-drag": `crop-${edge}`,
    });
  }
  for (const [name, x, y] of [
    ["nw", b.left, b.top],
    ["ne", b.right, b.top],
    ["sw", b.left, b.bottom],
    ["se", b.right, b.bottom],
  ]) {
    svgEl("rect", {
      class: "handle interactive",
      ...fixedSquareAttrs(x, y, 14),
      "data-drag": `crop-${name}`,
    });
  }
}

function cropEdgeAttrs(edge, b, size) {
  const width = b.right - b.left;
  const height = b.bottom - b.top;
  if (edge === "n") return { x: b.left + size / 2, y: b.top - size / 2, width: Math.max(1, width - size), height: size };
  if (edge === "s") return { x: b.left + size / 2, y: b.bottom - size / 2, width: Math.max(1, width - size), height: size };
  if (edge === "e") return { x: b.right - size / 2, y: b.top + size / 2, width: size, height: Math.max(1, height - size) };
  return { x: b.left - size / 2, y: b.top + size / 2, width: size, height: Math.max(1, height - size) };
}

function renderCalibrationOverlay() {
  const locked = llmJobActive() || axisCalibrationApproved();
  const axes = orderedCalibrationAxesForOverlay();
  axes.forEach((axis) => {
    const axisIndex = calibratedAxes().findIndex((item) => item.axis_id === axis.axis_id);
    const color = axisColor(axis, axisIndex);
    for (const point of axis.points || []) {
      const p = point.crop_image_px;
      if (!p) continue;
      const key = calibrationPointKey(axis.axis_id, point.label);
      svgEl("circle", {
        class: `point ${locked ? "locked-overlay" : "interactive"}`,
        cx: p.x,
        cy: p.y,
        r: cssPxToSvgUnits(8),
        fill: color,
        "data-cal-key": key,
        "data-axis-id": axis.axis_id,
        "data-point-label": point.label,
        ...(locked ? {} : { "data-drag": "calibration" }),
      });
      svgText(point.label, p.x, p.y, color, { "data-label": key, ...calibrationLabelAttrs(point) });
    }
  });
}

function orderedCalibrationAxesForOverlay() {
  const axes = calibratedAxes();
  if (!stateRef.foregroundCalibrationAxisId) return axes;
  return [
    ...axes.filter((axis) => axis.axis_id !== stateRef.foregroundCalibrationAxisId),
    ...axes.filter((axis) => axis.axis_id === stateRef.foregroundCalibrationAxisId),
  ];
}

function renderSeriesOverlay() {
  const series = stateRef.state?.series || [];
  const locked = llmJobActive() || seriesConfirmed();
  series.forEach((item, seriesIndex) => {
    const color = seriesColor(item, seriesIndex);
    const dashArray = seriesDashArray(item.line_style);
    const markerKind = seriesMarkerKind(item, seriesIndex);
    const segments = groupBy(item.points || [], (point) => point.segment_index || 0);
    for (const points of Object.values(segments)) {
      points.sort((a, b) => a.point_index - b.point_index);
      const d = points
        .filter((point) => point.crop_image_px)
        .map((point, index) => `${index === 0 ? "M" : "L"} ${point.crop_image_px.x} ${point.crop_image_px.y}`)
        .join(" ");
      if (d) {
        svgEl("path", {
          class: `series-line ${locked ? "locked-overlay" : ""}`,
          d,
          stroke: color,
          ...(dashArray ? { "stroke-dasharray": dashArray } : {}),
          "data-series-path": `${item.id}:${points[0]?.segment_index ?? 0}`,
        });
      }
      for (const point of points) {
        if (!point.crop_image_px) continue;
        const commonAttrs = {
          class: `series-point ${locked ? "locked-overlay" : "interactive"}`,
          stroke: color,
          fill: markerKind === "x" ? "none" : "#fff",
          ...(locked ? {} : { "data-drag": "series" }),
          "data-series-id": item.id,
          "data-segment-index": point.segment_index,
          "data-point-index": point.point_index,
          "data-marker-kind": markerKind,
          "data-tooltip-series-point": "true",
          "data-tooltip-x": formatTooltipChartValue(point.chart_x),
          "data-tooltip-y": formatTooltipChartValue(point.chart_y),
        };
        renderSeriesPointMarker(point.crop_image_px, markerKind, commonAttrs);
      }
    }
  });
}

function onOverlayPointerOver(event) {
  if (stateRef.drag || llmJobActive()) return;
  const target = event.target.closest?.("[data-tooltip-series-point]");
  if (!target) return;
  showPointTooltip(target, event);
}

function onOverlayPointerHoverMove(event) {
  if (stateRef.drag || els.pointTooltip.hidden) return;
  const target = event.target.closest?.("[data-tooltip-series-point]");
  if (!target) {
    hidePointTooltip();
    return;
  }
  showPointTooltip(target, event);
}

function onOverlayPointerOut(event) {
  const target = event.target.closest?.("[data-tooltip-series-point]");
  if (!target) return;
  const related = event.relatedTarget?.closest?.("[data-tooltip-series-point]");
  if (related === target) return;
  hidePointTooltip();
}

function showPointTooltip(target, event) {
  const x = target.dataset.tooltipX || "";
  const y = target.dataset.tooltipY || "";
  if (!x && !y) return;
  showPointTooltipValues(x, y, svgPoint(event));
}

function showPointTooltipValues(x, y, point) {
  if (!x && !y) return;
  els.pointTooltip.innerHTML = `<div>x&nbsp;${escapeHtml(x || "?")}</div><div>y&nbsp;${escapeHtml(y || "?")}</div>`;
  els.pointTooltip.hidden = false;
  positionPointTooltip(point);
}

function positionPointTooltip(point) {
  const display = imagePointToWrapPoint(point);
  if (!display) return;
  els.pointTooltip.style.left = `${display.x + 10}px`;
  els.pointTooltip.style.top = `${Math.max(12, display.y - 8)}px`;
}

function hidePointTooltip() {
  els.pointTooltip.hidden = true;
}

function renderControls() {
  const state = stateRef.state;
  const active = Boolean(state?.active_job);
  const cropDone = cropApproved();
  const axesDone = axisCalibrationApproved();
  const seriesDone = seriesConfirmed();
  const hasSeries = Boolean(state?.series?.length);
  const hasPendingSeries = Boolean(state?.pending_series?.length);
  const hasAxisPoints = calibratedAxes().some((axis) => (axis.points || []).length >= 2);
  for (const button of [els.cropJobBtn, els.approveCropBtn, els.axisJobBtn, els.approveAxisBtn, els.seriesJobBtn, els.confirmSeriesBtn]) {
    button.disabled = !state || active;
    button.classList.remove("is-active", "is-complete", "is-running");
  }
  els.cropJobBtn.disabled = !state?.canonical_image || cropDone || active;
  els.approveCropBtn.disabled = !state?.crop || active;
  els.approveCropBtn.textContent = cropDone ? "Edit" : "Confirm Crop";
  els.axisJobBtn.disabled = !cropDone || axesDone || active;
  els.approveAxisBtn.disabled = (!axesDone && !hasAxisPoints) || active;
  els.approveAxisBtn.textContent = axesDone ? "Edit" : "Confirm Calibration";
  els.seriesJobBtn.disabled = !axesDone || seriesDone || active || hasPendingSeries;
  els.confirmSeriesBtn.disabled = (!seriesDone && !hasSeries) || active;
  els.confirmSeriesBtn.textContent = seriesDone ? "Edit" : "Confirm Series";
  els.addSeriesBtn.disabled = !axesDone || seriesDone || active;
  els.debugExport.disabled = !seriesDone || active;

  setButtonState(els.cropJobBtn, { active: state?.active_job === "crop", complete: cropDone });
  setButtonState(els.approveCropBtn, { complete: cropDone });
  setButtonState(els.axisJobBtn, { active: state?.active_job === "axis_calibration" || state?.active_job === "x_axis" || state?.active_job === "y_axis", complete: axesDone });
  setButtonState(els.approveAxisBtn, { complete: axesDone });
  setButtonState(els.seriesJobBtn, { active: state?.active_job === "series", complete: seriesDone });
  setButtonState(els.confirmSeriesBtn, { complete: seriesDone });
}

function setButtonState(button, { active = false, complete = false }) {
  button.classList.toggle("is-active", Boolean(active));
  button.classList.toggle("is-complete", Boolean(complete));
}

function renderImageBusyState() {
  const active = llmJobActive();
  const asset = activeImageAsset();
  const show = Boolean(active && asset);
  els.imageWrap.classList.toggle("is-busy", show);
  els.imageStage.classList.toggle("is-busy", show);
  els.imageBusyOverlay.hidden = !show;
  els.imageBusyStatus.textContent = show ? activeStepStatusText() : "";
  if (show) positionImageBusyOverlay();
}

function positionImageBusyOverlay() {
  const rect = els.imageStage.getBoundingClientRect();
  Object.assign(els.imageBusyOverlay.style, {
    left: `${rect.left}px`,
    top: `${rect.top}px`,
    width: `${rect.width}px`,
    height: `${rect.height}px`,
  });
}

function renderCalibrationEditor() {
  els.calibrationEditor.innerHTML = "";
  const axes = calibratedAxes();
  const locked = llmJobActive() || axisCalibrationApproved();
  if (!axes.length) {
    els.calibrationEditor.innerHTML = `<div class="empty-state">No axis points</div>`;
    return;
  }
  axes.forEach((axis, axisIndex) => {
    const axisPoints = axis.points || [];
    const axisSection = document.createElement("section");
    axisSection.className = "axis-section";
    axisSection.innerHTML = `
      <h4><span class="axis-color-dot" style="--axis-color: ${escapeHtml(axisColor(axis, axisIndex))}"></span>${escapeHtml(axisDisplayName(axis))}</h4>
      <label class="axis-field-row"><span>Units</span><input data-axis-unit="${escapeHtml(axis.axis_id)}" value="${escapeHtml(axisUnit(axis.axis_id))}" ${locked ? "disabled" : ""}></label>
    `;
    for (const point of axisPoints) {
      const row = document.createElement("label");
      row.className = "axis-field-row";
      const key = calibrationPointKey(axis.axis_id, point.label);
      row.innerHTML = `
        <span>${escapeHtml(point.label)}</span>
        <input data-cal-value="${escapeHtml(key)}" data-axis-id="${escapeHtml(axis.axis_id)}" data-point-label="${escapeHtml(point.label)}" value="${escapeHtml(formatChartValue(point.chart_value))}" ${locked ? "disabled" : ""}>
      `;
      axisSection.append(row);
    }
    els.calibrationEditor.append(axisSection);
  });
  els.calibrationEditor.querySelectorAll("[data-cal-value]").forEach((input) => {
    input.addEventListener("change", (event) => {
      if (llmJobActive()) return;
      const axisId = event.target.dataset.axisId;
      const label = event.target.dataset.pointLabel;
      const point = findCalibrationPoint(axisId, label);
      if (!point) return;
      const valueInput = event.target;
      point.chart_value = chartValueFromInput(valueInput.value);
      point.chart_value.unit = axisUnit(axisId) || null;
      render();
      saveCalibrationDraft(axisId);
    });
  });
  els.calibrationEditor.querySelectorAll("[data-axis-unit]").forEach((input) => {
    input.addEventListener("change", (event) => {
      if (llmJobActive()) return;
      const axis = event.target.dataset.axisUnit;
      setAxisUnit(axis, event.target.value || null);
      render();
      saveCalibrationDraft(axis);
    });
  });
}

function renderSeriesEditor() {
  const series = stateRef.state?.series || [];
  const locked = llmJobActive() || seriesConfirmed();
  els.seriesEditor.innerHTML = "";
  if (!series.length) {
    els.seriesEditor.innerHTML = `<div class="empty-state">No series</div>`;
    return;
  }
  series.forEach((item, seriesIndex) => {
    const block = document.createElement("details");
    block.className = "series-block";
    block.open = !seriesConfirmed();
    block.innerHTML = `
      <summary>
        <label class="series-title">Series ${seriesIndex + 1}: <input value="${escapeHtml(item.name)}" data-series-name="${item.id}" aria-label="Series ${seriesIndex + 1} name" ${locked ? "disabled" : ""}></label>
        ${seriesLegendPreviewMarkup(item, seriesIndex)}
        <span class="mini-status">${(item.points || []).length} pts</span>
      </summary>
      <div class="series-header">
        <button type="button" data-retry-series="${item.id}" ${locked ? "disabled" : ""}>Retry Auto Digitise</button>
        <button type="button" data-add-point="${item.id}" ${locked ? "disabled" : ""}>Add Point</button>
        <button type="button" data-delete-series="${item.id}" ${locked ? "disabled" : ""}>Delete series</button>
      </div>
      <div class="axis-picker-row">
        <label>X axis <select data-series-x-axis="${item.id}" ${locked ? "disabled" : ""}>${axisOptionsMarkup("x", item.x_axis_id)}</select></label>
        <label>Y axis <select data-series-y-axis="${item.id}" ${locked ? "disabled" : ""}>${axisOptionsMarkup("y", item.y_axis_id)}</select></label>
      </div>
      <small>${escapeHtml(item.visual_description || "")}</small>
      <div class="point-list"></div>
    `;
    const list = block.querySelector(".point-list");
    for (const point of item.points || []) {
      const row = document.createElement("div");
      row.className = "point-row";
      row.innerHTML = `
        <strong>${point.point_index}</strong>
        <label>X <input data-point-x="${item.id}:${point.segment_index}:${point.point_index}" value="${escapeHtml(formatChartValue(point.chart_x))}" ${locked ? "disabled" : ""}></label>
        <label>Y <input data-point-y="${item.id}:${point.segment_index}:${point.point_index}" value="${escapeHtml(formatChartValue(point.chart_y))}" ${locked ? "disabled" : ""}></label>
        <button class="icon-button" type="button" data-delete-point="${item.id}:${point.segment_index}:${point.point_index}" aria-label="Delete point" title="Delete point" ${locked ? "disabled" : ""}><span aria-hidden="true">🗑</span></button>
      `;
      list.append(row);
    }
    els.seriesEditor.append(block);
  });
  wireSeriesEditor();
}

function wireSeriesEditor() {
  els.seriesEditor.querySelectorAll("[data-series-name]").forEach((input) => {
    input.addEventListener("click", (event) => event.stopPropagation());
    input.addEventListener("pointerdown", (event) => event.stopPropagation());
    input.addEventListener("change", () => {
      if (llmJobActive()) return;
      const series = findSeries(input.dataset.seriesName);
      if (series) series.name = input.value;
      saveSeries("image", { forceEditors: true });
    });
  });
  els.seriesEditor.querySelectorAll("[data-delete-series]").forEach((button) => {
    button.addEventListener("click", () => {
      if (llmJobActive()) return;
      stateRef.state.series = stateRef.state.series.filter((series) => series.id !== button.dataset.deleteSeries);
      render({ forceEditors: true });
      saveSeries("image", { forceEditors: true });
    });
  });
  els.seriesEditor.querySelectorAll("[data-add-point]").forEach((button) => {
    button.addEventListener("click", () => addPoint(button.dataset.addPoint));
  });
  els.seriesEditor.querySelectorAll("[data-series-x-axis], [data-series-y-axis]").forEach((select) => {
    select.addEventListener("change", () => {
      if (llmJobActive()) return;
      const seriesId = select.dataset.seriesXAxis || select.dataset.seriesYAxis;
      const series = findSeries(seriesId);
      if (!series) return;
      if (select.dataset.seriesXAxis) series.x_axis_id = select.value;
      if (select.dataset.seriesYAxis) series.y_axis_id = select.value;
      saveSeries("image", { forceEditors: true });
    });
  });
  els.seriesEditor.querySelectorAll("[data-retry-series]").forEach((button) => {
    button.addEventListener("click", () => retrySeriesDigitization(button.dataset.retrySeries));
  });
  els.seriesEditor.querySelectorAll("[data-delete-point]").forEach((button) => {
    button.addEventListener("click", () => deletePoint(button.dataset.deletePoint));
  });
  els.seriesEditor.querySelectorAll("[data-point-x], [data-point-y]").forEach((input) => {
    input.addEventListener("change", () => {
      if (llmJobActive()) return;
      const key = input.dataset.pointX || input.dataset.pointY;
      const point = findSeriesPoint(key);
      if (!point) return;
      const [seriesId, segment, index] = key.split(":");
      const xInput = els.seriesEditor.querySelector(`[data-point-x="${seriesId}:${segment}:${index}"]`);
      const yInput = els.seriesEditor.querySelector(`[data-point-y="${seriesId}:${segment}:${index}"]`);
      point.chart_x = chartValueFromInput(xInput.value);
      point.chart_y = chartValueFromInput(yInput.value);
      saveSeries("chart", { forceEditors: true });
    });
  });
}

async function retrySeriesDigitization(seriesId) {
  if (llmJobActive() || !stateRef.runId || !seriesId) return;
  await api(`/api/runs/${stateRef.runId}/jobs/series/${encodeURIComponent(seriesId)}`, { method: "POST" });
  await loadRun(stateRef.runId, { force: true });
}

function showDebugInfo() {
  return stateRef.config?.show_debug_info !== false;
}

function renderDebugInfo() {
  const visible = showDebugInfo();
  els.statusGrid.hidden = !visible;
  if (!visible) {
    els.eventLog.innerHTML = "";
    els.artifactList.innerHTML = "";
    els.debugPanel.textContent = "";
    return;
  }
  renderArtifacts();
  updateDebugPanel();
}

function updateDebugPanel() {
  if (!showDebugInfo()) return;
  els.debugPanel.textContent = stateRef.state ? JSON.stringify(selectedDebugState(stateRef.state), null, 2) : "";
}

function renderEvents(events) {
  if (!showDebugInfo()) return;
  els.eventLog.innerHTML = "";
  for (const event of events.slice(-120).reverse()) {
    const line = document.createElement("div");
    line.className = "log-line";
    line.innerHTML = `<span class="tag ${event.category}">${event.category}</span><span>${escapeHtml(event.message)}${event.artifact_path ? ` <code>${escapeHtml(event.artifact_path)}</code>` : ""}</span>`;
    els.eventLog.append(line);
  }
}

function renderArtifacts() {
  const attempts = stateRef.state?.attempts || [];
  els.artifactList.innerHTML = "";
  for (const attempt of attempts.slice().reverse()) {
    const line = document.createElement("div");
    line.className = "artifact-line";
    const links = [attempt.overlay_path, attempt.request_path, attempt.response_path, attempt.parsed_path]
      .filter(Boolean)
      .map((path) => `<a href="${fileUrl(path)}" target="_blank" rel="noreferrer">${escapeHtml(path.split("/").pop())}</a>`)
      .join(" · ");
    line.innerHTML = `<span class="tag ARTIFACT">${escapeHtml(attempt.stage)}</span><span>${escapeHtml(attempt.id)} · ${escapeHtml(attempt.validation_status)} · ${links}</span>`;
    els.artifactList.append(line);
  }
}

function renderExports() {
  const links = [els.csvLink, els.xlsxLink, els.archiveLink];
  const canExport = Boolean(stateRef.runId && seriesConfirmed());
  for (const link of links) {
    link.classList.toggle("is-disabled", !canExport);
    link.setAttribute("aria-disabled", canExport ? "false" : "true");
  }
  if (!canExport) {
    for (const link of links) link.href = "#";
    return;
  }
  const debug = els.debugExport.checked ? "true" : "false";
  els.csvLink.href = `/api/runs/${stateRef.runId}/export.csv?debug=${debug}`;
  els.xlsxLink.href = `/api/runs/${stateRef.runId}/export.xlsx?debug=${debug}`;
  els.archiveLink.href = `/api/runs/${stateRef.runId}/archive.zip`;
}

function onPointerDown(event) {
  if (llmJobActive()) return;
  const dragType = event.target.dataset.drag;
  if (!dragType) return;
  if (isDragLocked(dragType)) return;
  event.preventDefault();
  event.stopPropagation();
  const point = svgPoint(event);
  stateRef.drag = {
    type: dragType,
    start: point,
    original: JSON.parse(JSON.stringify(stateRef.state)),
    axisId: event.target.dataset.axisId,
    pointLabel: event.target.dataset.pointLabel,
    seriesId: event.target.dataset.seriesId,
    segmentIndex: event.target.dataset.segmentIndex,
    pointIndex: event.target.dataset.pointIndex,
  };
  if (dragType === "calibration") bringCalibrationPairToForeground(event.target.dataset.axisId);
  if (dragType === "series") showDraggedSeriesTooltip(point);
  else hidePointTooltip();
  if (isPointDrag(dragType)) showDragLoupe(event, point);
  event.target.setPointerCapture?.(event.pointerId);
}

function bringCalibrationPairToForeground(axisId) {
  if (!axisId) return;
  stateRef.foregroundCalibrationAxisId = axisId;
  const axisSelector = cssAttr(axisId);
  const nodes = [
    ...els.overlaySvg.querySelectorAll(`[data-axis-id="${axisSelector}"]`),
    ...els.overlaySvg.querySelectorAll(`[data-label^="${axisSelector}:"]`),
  ];
  for (const node of nodes) els.overlaySvg.append(node);
}

function onChartPanPointerDown(event) {
  if (stateRef.drag || stateRef.pan || llmJobActive() || !activeImageAsset()) return;
  if (event.button !== 0 || !chartCanPan()) return;
  if (event.target.closest?.("[data-drag]")) return;
  event.preventDefault();
  stateRef.pan = {
    pointerId: event.pointerId,
    startX: event.clientX,
    startY: event.clientY,
    scrollLeft: els.imageStage.scrollLeft,
    scrollTop: els.imageStage.scrollTop,
  };
  els.imageWrap.classList.add("is-panning");
  els.imageWrap.setPointerCapture?.(event.pointerId);
}

function onImageStageWheel(event) {
  if (Math.abs(event.deltaY) <= Math.abs(event.deltaX)) return;
  const stage = els.imageStage;
  const maxScrollTop = Math.max(0, stage.scrollHeight - stage.clientHeight);
  const canScrollUp = stage.scrollTop > 1;
  const canScrollDown = stage.scrollTop < maxScrollTop - 1;
  const chartCanConsume = (event.deltaY < 0 && canScrollUp) || (event.deltaY > 0 && canScrollDown);
  event.preventDefault();
  if (chartCanConsume) {
    stage.scrollTop += normalizedWheelDeltaY(event);
    return;
  }
  window.scrollBy({ top: normalizedWheelDeltaY(event), left: 0, behavior: "auto" });
}

function normalizedWheelDeltaY(event) {
  if (event.deltaMode === WheelEvent.DOM_DELTA_LINE) return event.deltaY * 16;
  if (event.deltaMode === WheelEvent.DOM_DELTA_PAGE) return event.deltaY * els.imageStage.clientHeight;
  return event.deltaY;
}

function onPointerMove(event) {
  if (stateRef.pan) {
    updateChartPan(event);
    return;
  }
  if (!stateRef.drag) return;
  if (llmJobActive()) return;
  const point = svgPoint(event);
  const dx = point.x - stateRef.drag.start.x;
  const dy = point.y - stateRef.drag.start.y;
  if (stateRef.drag.type.startsWith("crop")) updateCropDrag(dx, dy);
  if (stateRef.drag.type === "calibration") updateCalibrationDrag(stateRef.drag.axisId, stateRef.drag.pointLabel, point);
  if (stateRef.drag.type === "series") updateSeriesDrag(point);
  const focusPoint = currentDraggedPointImagePosition(point);
  if (stateRef.drag.type === "series") showDraggedSeriesTooltip(focusPoint);
  if (isPointDrag(stateRef.drag.type)) updateDragLoupe(event, focusPoint);
  updateLiveOverlay();
}

async function onPointerUp(event) {
  if (stateRef.pan) {
    endChartPan(event);
    return;
  }
  if (!stateRef.drag) return;
  const type = stateRef.drag.type;
  const axisId = stateRef.drag.axisId;
  stateRef.drag = null;
  hideDragLoupe();
  if (type.startsWith("crop")) await saveCropDraft();
  else if (type === "calibration") await saveCalibrationDraft(axisId);
  else if (type === "series") await saveSeries("image");
}

function updateCropDrag(dx, dy) {
  const state = stateRef.state;
  const original = stateRef.drag.original.crop?.bbox_full_px;
  const asset = state.canonical_image;
  if (!original || !asset) return;
  let b = { ...original };
  const type = stateRef.drag.type;
  if (type === "crop-move") {
    const width = b.right - b.left;
    const height = b.bottom - b.top;
    b.left = clamp(original.left + dx, 0, asset.width - width);
    b.top = clamp(original.top + dy, 0, asset.height - height);
    b.right = b.left + width;
    b.bottom = b.top + height;
  } else {
    if (type.includes("n")) b.top = clamp(original.top + dy, 0, b.bottom - 10);
    if (type.includes("s")) b.bottom = clamp(original.bottom + dy, b.top + 10, asset.height - 1);
    if (type.includes("w")) b.left = clamp(original.left + dx, 0, b.right - 10);
    if (type.includes("e")) b.right = clamp(original.right + dx, b.left + 10, asset.width - 1);
  }
  state.crop.bbox_full_px = b;
  state.crop.bbox_full_norm = pxBBoxToNorm(b, asset.width, asset.height);
}

function updateCalibrationDrag(axisId, label, point) {
  const state = stateRef.state;
  const asset = state.crop?.image;
  if (!asset) return;
  const axis = findCalibratedAxis(axisId);
  const points = axis?.points || [];
  const target = points.find((item) => item.label === label);
  if (!target) return;
  if (label.startsWith("x")) {
    target.crop_image_px.x = clamp(point.x, 0, asset.width - 1);
    for (const item of points) item.crop_image_px.y = clamp(point.y, 0, asset.height - 1);
  } else {
    target.crop_image_px.y = clamp(point.y, 0, asset.height - 1);
    for (const item of points) item.crop_image_px.x = clamp(point.x, 0, asset.width - 1);
  }
  for (const item of points) item.crop_image_norm = pxPointToNorm(item.crop_image_px, asset.width, asset.height);
}

function updateSeriesDrag(point) {
  const { seriesId, segmentIndex, pointIndex } = stateRef.drag;
  const target = findSeriesPoint(`${seriesId}:${segmentIndex}:${pointIndex}`);
  const asset = stateRef.state.crop?.image;
  if (!target || !asset) return;
  target.crop_image_px = { x: clamp(point.x, 0, asset.width - 1), y: clamp(point.y, 0, asset.height - 1) };
  target.crop_image_norm = pxPointToNorm(target.crop_image_px, asset.width, asset.height);
  const chartValues = imagePointToChartValues(target.crop_image_px, findSeries(seriesId));
  if (chartValues) {
    target.chart_x = chartValues.x;
    target.chart_y = chartValues.y;
  }
}

function addManualSeries() {
  if (llmJobActive()) return;
  if (!stateRef.state?.crop?.image) return;
  const id = `manual-${Date.now().toString(36)}`;
  stateRef.state.series = stateRef.state.series || [];
  stateRef.state.series.push({
    id,
    name: "Manual series",
    source: "manual",
    line_color: "#7c3aed",
    x_axis_id: defaultAxisId("x"),
    y_axis_id: defaultAxisId("y"),
    points: [],
    warnings: [],
  });
  addPoint(id);
}

function addPoint(seriesId) {
  if (llmJobActive()) return;
  const series = findSeries(seriesId);
  if (!series || !stateRef.state?.crop?.image) return;
  const asset = stateRef.state.crop.image;
  const index = series.points?.length || 0;
  series.points = series.points || [];
  series.points.push({
    point_index: index,
    segment_index: 0,
    crop_image_norm: { x: 500, y: 500 },
    crop_image_px: { x: asset.width / 2, y: asset.height / 2 },
    chart_x: chartValueFromInput("0"),
    chart_y: chartValueFromInput("0"),
  });
  render({ forceEditors: true });
  saveSeries("image", { forceEditors: true });
}

function isDragLocked(dragType) {
  if (llmJobActive()) return true;
  if (dragType.startsWith("crop")) return cropApproved();
  if (dragType === "calibration") return axisCalibrationApproved();
  if (dragType === "series") return stateRef.state?.stage === "complete";
  return false;
}

function updateChartPan(event) {
  if (!stateRef.pan) return;
  const dx = event.clientX - stateRef.pan.startX;
  const dy = event.clientY - stateRef.pan.startY;
  els.imageStage.scrollLeft = stateRef.pan.scrollLeft - dx;
  els.imageStage.scrollTop = stateRef.pan.scrollTop - dy;
}

function endChartPan(event) {
  if (!stateRef.pan) return;
  const pointerId = stateRef.pan.pointerId ?? event?.pointerId;
  if (pointerId !== undefined) els.imageWrap.releasePointerCapture?.(pointerId);
  stateRef.pan = null;
  els.imageWrap.classList.remove("is-panning");
  renderChartViewportState();
}

function isPointDrag(type) {
  return type === "series" || type === "calibration";
}

function showDraggedSeriesTooltip(focusPoint) {
  if (!stateRef.drag || stateRef.drag.type !== "series" || !focusPoint) return;
  const point = findSeriesPoint(`${stateRef.drag.seriesId}:${stateRef.drag.segmentIndex}:${stateRef.drag.pointIndex}`);
  const chartValues = imagePointToChartValues(focusPoint, findSeries(stateRef.drag.seriesId));
  if (point && chartValues) {
    point.chart_x = chartValues.x;
    point.chart_y = chartValues.y;
  }
  const x = formatTooltipChartValue(point?.chart_x || chartValues?.x);
  const y = formatTooltipChartValue(point?.chart_y || chartValues?.y);
  showPointTooltipValues(x, y, focusPoint);
}

function showDragLoupe(event, focusPoint) {
  els.dragLoupe.hidden = false;
  updateDragLoupe(event, focusPoint);
}

function updateDragLoupe(event, focusPoint) {
  const asset = activeImageAsset();
  if (!asset || !focusPoint) {
    hideDragLoupe();
    return;
  }
  const imageRect = els.chartImage.getBoundingClientRect();
  const magnification = 2.8;
  const loupeSize = 112;
  const radius = loupeSize / 2;
  const displayX = (focusPoint.x / asset.width) * imageRect.width;
  const displayY = (focusPoint.y / asset.height) * imageRect.height;
  const gap = 22;
  const useLeft = event.clientX + loupeSize + gap > window.innerWidth;
  const useTop = event.clientY + loupeSize + gap > window.innerHeight;
  const left = useLeft ? event.clientX - loupeSize - gap : event.clientX + gap;
  const top = useTop ? event.clientY - loupeSize - gap : event.clientY + gap;

  Object.assign(els.dragLoupe.style, {
    left: `${Math.max(8, left)}px`,
    top: `${Math.max(8, top)}px`,
    width: `${loupeSize}px`,
    height: `${loupeSize}px`,
    backgroundImage: `url(${JSON.stringify(els.chartImage.currentSrc || els.chartImage.src)})`,
    backgroundSize: `${imageRect.width * magnification}px ${imageRect.height * magnification}px`,
    backgroundPosition: `${radius - displayX * magnification}px ${radius - displayY * magnification}px`,
  });
}

function hideDragLoupe() {
  els.dragLoupe.hidden = true;
}

function currentDraggedPointImagePosition(fallbackPoint) {
  if (!stateRef.drag) return fallbackPoint;
  if (stateRef.drag.type === "series") {
    return findSeriesPoint(`${stateRef.drag.seriesId}:${stateRef.drag.segmentIndex}:${stateRef.drag.pointIndex}`)?.crop_image_px || fallbackPoint;
  }
  if (stateRef.drag.type === "calibration") {
    return findCalibrationPoint(stateRef.drag.axisId, stateRef.drag.pointLabel)?.crop_image_px || fallbackPoint;
  }
  return fallbackPoint;
}

function llmJobActive() {
  return Boolean(stateRef.state?.active_job);
}

function seriesColor(item, seriesIndex) {
  return item.line_color || DEFAULT_SERIES_COLORS[seriesIndex % DEFAULT_SERIES_COLORS.length];
}

function axisColor(axis, axisIndex) {
  return axis.color || AXIS_COLORS[axisIndex % AXIS_COLORS.length];
}

function seriesDashArray(style) {
  const normalized = String(style || "").toLowerCase();
  if (normalized.includes("dash") && normalized.includes("dot")) return "8 4 2 4";
  if (normalized.includes("dash")) return "8 5";
  if (normalized.includes("dot")) return "1 5";
  return "";
}

function seriesMarkerKind(item, seriesIndex) {
  const series = stateRef.state?.series || [];
  const colorKey = String(seriesColor(item, seriesIndex)).trim().toLowerCase();
  const sameColor = series
    .map((candidate, index) => ({ candidate, index, color: String(seriesColor(candidate, index)).trim().toLowerCase() }))
    .filter((entry) => entry.color === colorKey);
  if (sameColor.length <= 1) return "x";
  const rank = sameColor.findIndex((entry) => entry.candidate.id === item.id);
  return SERIES_MARKER_KINDS[Math.max(0, rank) % SERIES_MARKER_KINDS.length];
}

function seriesLegendPreviewMarkup(item, seriesIndex) {
  const color = escapeHtml(seriesColor(item, seriesIndex));
  const dashArray = seriesDashArray(item.line_style);
  const dashAttr = dashArray ? ` stroke-dasharray="${dashArray}"` : "";
  const marker = seriesLegendMarkerMarkup(seriesMarkerKind(item, seriesIndex), color);
  return `
    <svg class="series-legend-sample" viewBox="0 0 48 20" aria-hidden="true" focusable="false">
      <line x1="4" y1="10" x2="44" y2="10" stroke="${color}" stroke-width="4" stroke-linecap="round"${dashAttr}></line>
      ${marker}
    </svg>
  `;
}

function seriesLegendMarkerMarkup(kind, color) {
  if (kind === "circle") return `<circle cx="24" cy="10" r="4" fill="#fff" stroke="${color}" stroke-width="2"></circle>`;
  if (kind === "square") return `<rect x="20" y="6" width="8" height="8" fill="#fff" stroke="${color}" stroke-width="2"></rect>`;
  if (kind === "diamond") return `<path d="M 24 5 L 29 10 L 24 15 L 19 10 Z" fill="#fff" stroke="${color}" stroke-width="2"></path>`;
  if (kind === "triangle") return `<path d="M 24 5 L 29 15 L 19 15 Z" fill="#fff" stroke="${color}" stroke-width="2"></path>`;
  return `
    <line x1="20" y1="6" x2="28" y2="14" stroke="${color}" stroke-width="2" stroke-linecap="round"></line>
    <line x1="20" y1="14" x2="28" y2="6" stroke="${color}" stroke-width="2" stroke-linecap="round"></line>
  `;
}

function deletePoint(key) {
  if (llmJobActive()) return;
  const [seriesId, segment, index] = key.split(":");
  const series = findSeries(seriesId);
  if (!series) return;
  series.points = series.points.filter((point) => !(String(point.segment_index) === segment && String(point.point_index) === index));
  series.points.forEach((point, idx) => { point.point_index = idx; });
  render({ forceEditors: true });
  saveSeries("image", { forceEditors: true });
}

function calibratedAxes() {
  const cal = stateRef.state?.calibration;
  if (!cal) return [];
  if (cal.calibrated_axes?.length) return cal.calibrated_axes;
  const axes = [];
  if (cal.x_points?.length) axes.push({ axis_id: "x", direction: "x", name: "X axis", unit: axisUnitFromPoints(cal.x_points), location_description: "Legacy x-axis", points: cal.x_points, approved: Boolean(cal.approved_x) });
  if (cal.y_points?.length) axes.push({ axis_id: "y", direction: "y", name: "Y axis", unit: axisUnitFromPoints(cal.y_points), location_description: "Legacy y-axis", points: cal.y_points, approved: Boolean(cal.approved_y) });
  return axes;
}

function axisOptionsMarkup(direction, selectedId) {
  const axes = approvedAxes(direction);
  const selected = selectedId || axes[0]?.axis_id || "";
  return axes.map((axis) => {
    const isSelected = axis.axis_id === selected ? " selected" : "";
    return `<option value="${escapeHtml(axis.axis_id)}"${isSelected}>${escapeHtml(axisDisplayName(axis))}</option>`;
  }).join("");
}

function approvedAxes(direction) {
  return calibratedAxes().filter((axis) => axis.direction === direction && axis.approved && (axis.points || []).length === 2);
}

function findCalibratedAxis(axisId) {
  return calibratedAxes().find((axis) => axis.axis_id === axisId);
}

function defaultAxisId(direction) {
  return approvedAxes(direction)[0]?.axis_id || calibratedAxes().find((axis) => axis.direction === direction)?.axis_id || null;
}

function findCalibrationPoint(axisId, label) {
  return findCalibratedAxis(axisId)?.points?.find((point) => point.label === label);
}

function axisUnit(axisId) {
  const axis = findCalibratedAxis(axisId);
  return axis?.unit || axisUnitFromPoints(axis?.points || []);
}

function axisUnitFromPoints(points) {
  return points.find((point) => point.chart_value?.unit)?.chart_value.unit || "";
}

function setAxisUnit(axisId, unit) {
  const axis = findCalibratedAxis(axisId);
  if (!axis) return;
  axis.unit = unit || null;
  for (const point of axis.points || []) {
    if (point.chart_value) point.chart_value.unit = unit || null;
  }
}

function axisDisplayName(axis) {
  const unit = axis.unit || axisUnitFromPoints(axis.points || []);
  return `${axis.name || axis.axis_id}${unit ? ` (${unit})` : ""}`;
}

function calibrationPointKey(axisId, label) {
  return `${axisId}:${label}`;
}

function findSeries(id) {
  return (stateRef.state?.series || []).find((series) => series.id === id);
}

function findSeriesPoint(key) {
  const [seriesId, segment, index] = key.split(":");
  const series = findSeries(seriesId);
  return series?.points?.find((point) => String(point.segment_index) === segment && String(point.point_index) === index);
}

function svgPoint(event) {
  const svg = els.overlaySvg;
  const pt = svg.createSVGPoint();
  pt.x = event.clientX;
  pt.y = event.clientY;
  const transformed = pt.matrixTransform(svg.getScreenCTM().inverse());
  return { x: transformed.x, y: transformed.y };
}

function imagePointToWrapPoint(point) {
  const asset = activeImageAsset();
  if (!asset) return null;
  const imageRect = els.chartImage.getBoundingClientRect();
  const wrapRect = els.imageWrap.getBoundingClientRect();
  return {
    x: imageRect.left - wrapRect.left + (point.x / asset.width) * imageRect.width,
    y: imageRect.top - wrapRect.top + (point.y / asset.height) * imageRect.height,
  };
}

function svgEl(name, attrs) {
  const el = document.createElementNS("http://www.w3.org/2000/svg", name);
  for (const [key, value] of Object.entries(attrs)) el.setAttribute(key, value);
  els.overlaySvg.append(el);
  return el;
}

function svgText(text, x, y, color, attrs = {}) {
  const el = svgEl("text", { class: "overlay-label", x, y, fill: color, ...attrs });
  el.textContent = text;
  return el;
}

function setSvgAttrs(element, attrs) {
  if (!element) return;
  for (const [key, value] of Object.entries(attrs)) element.setAttribute(key, value);
}

function cssAttr(value) {
  return String(value).replaceAll("\\", "\\\\").replaceAll('"', '\\"');
}

function fileUrl(path) {
  if (!stateRef.runId || !path) return "";
  return `/api/runs/${stateRef.runId}/files/${path.split("/").map(encodeURIComponent).join("/")}`;
}

function pxPointToNorm(point, width, height) {
  return {
    x: clamp(Math.round((point.x / (width - 1)) * 999), 0, 999),
    y: clamp(Math.round((point.y / (height - 1)) * 999), 0, 999),
  };
}

function pxBBoxToNorm(b, width, height) {
  return {
    left: pxPointToNorm({ x: b.left, y: b.top }, width, height).x,
    top: pxPointToNorm({ x: b.left, y: b.top }, width, height).y,
    right: pxPointToNorm({ x: b.right, y: b.bottom }, width, height).x,
    bottom: pxPointToNorm({ x: b.right, y: b.bottom }, width, height).y,
  };
}

function imagePointToChartValues(point, series = null) {
  const xAxis = findCalibratedAxis(series?.x_axis_id || defaultAxisId("x"));
  const yAxis = findCalibratedAxis(series?.y_axis_id || defaultAxisId("y"));
  const xPoints = xAxis?.points || [];
  const yPoints = yAxis?.points || [];
  if (xPoints.length !== 2 || yPoints.length !== 2) return null;
  const [x1, x2] = xPoints;
  const [y1, y2] = yPoints;
  if (!x1.crop_image_px || !x2.crop_image_px || !y1.crop_image_px || !y2.crop_image_px) return null;
  const xScalar = linearMap(
    point.x,
    x1.crop_image_px.x,
    x2.crop_image_px.x,
    chartValueToScalar(x1.chart_value),
    chartValueToScalar(x2.chart_value),
  );
  const yScalar = linearMap(
    point.y,
    y1.crop_image_px.y,
    y2.crop_image_px.y,
    chartValueToScalar(y1.chart_value),
    chartValueToScalar(y2.chart_value),
  );
  if (!Number.isFinite(xScalar) || !Number.isFinite(yScalar)) return null;
  return {
    x: scalarToChartValue(xScalar, x1.chart_value),
    y: scalarToChartValue(yScalar, y1.chart_value),
  };
}

function chartValueToScalar(value) {
  if (!value) return Number.NaN;
  if (value.value_type === "datetime") return Date.parse(value.parsed_datetime || value.value_raw) / 1000;
  if (value.parsed_value !== null && value.parsed_value !== undefined) return Number(value.parsed_value);
  return Number(String(value.value_raw || "").replace(/,/g, ""));
}

function scalarToChartValue(scalar, template = {}) {
  if (template.value_type === "datetime") {
    const iso = new Date(scalar * 1000).toISOString();
    return { value_raw: iso, value_type: "datetime", parsed_datetime: iso, unit: template.unit || null };
  }
  return { value_raw: formatNumber(scalar), value_type: "number", parsed_value: scalar, unit: template.unit || null };
}

function linearMap(value, src1, src2, dst1, dst2) {
  if (src1 === src2) return Number.NaN;
  return dst1 + ((value - src1) / (src2 - src1)) * (dst2 - dst1);
}

function chartValueFromInput(value) {
  const trimmed = String(value ?? "").trim();
  if (looksLikeDate(trimmed)) {
    const iso = new Date(trimmed).toISOString();
    return { value_raw: trimmed, value_type: "datetime", parsed_datetime: iso, unit: null };
  }
  const numeric = Number(trimmed.replace(/,/g, ""));
  const rounded = Number.isFinite(numeric) ? roundToTwo(numeric) : 0;
  return { value_raw: Number.isFinite(numeric) ? formatNumber(rounded) : trimmed || "0", value_type: "number", parsed_value: rounded, unit: null };
}

function formatChartValue(value) {
  if (!value) return "";
  if (value.value_type === "datetime") return value.parsed_datetime || value.value_raw;
  if (value.parsed_value !== null && value.parsed_value !== undefined) return formatNumber(value.parsed_value);
  const numeric = Number(String(value.value_raw || "").replace(/,/g, ""));
  return Number.isFinite(numeric) ? formatNumber(numeric) : value.value_raw || "";
}

function formatTooltipChartValue(value) {
  if (!value) return "";
  const base = formatChartValue(value);
  return value.unit ? `${base} ${value.unit}` : base;
}

function roundToTwo(value) {
  return Math.round((Number(value) + Number.EPSILON) * 100) / 100;
}

function formatNumber(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return String(value ?? "");
  const rounded = roundToTwo(numeric);
  return Number.isInteger(rounded) ? String(rounded) : rounded.toFixed(2).replace(/0+$/, "").replace(/\.$/, "");
}

function formatDateTime(value) {
  if (!value) return "unknown date";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "unknown date";
  return date.toLocaleString();
}

function previousRunLabel(run) {
  const updated = formatDateTime(run.updated_at);
  return `${run.upload_filename} · ${run.stage.replaceAll("_", " ")} · ${updated}`;
}

function looksLikeDate(value) {
  return /\d{4}-\d{1,2}-\d{1,2}/.test(String(value)) && !Number.isNaN(Date.parse(value));
}

function selectedDebugState(state) {
  return {
    coordinate_frames: ["full_image_px", "full_image_norm", "crop_image_px", "crop_image_norm", "chart_space"],
    canonical_image: state.canonical_image,
    crop: state.crop,
    calibration: state.calibration,
    pending_series_count: state.pending_series?.length || 0,
    series_count: state.series?.length || 0,
    warnings: state.warnings,
  };
}

function groupBy(items, keyFn) {
  return items.reduce((acc, item) => {
    const key = keyFn(item);
    acc[key] = acc[key] || [];
    acc[key].push(item);
    return acc;
  }, {});
}

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  if (!response.ok) {
    let message = response.statusText;
    try {
      const data = await response.json();
      message = data.detail || message;
    } catch (_) {
      // Keep status text.
    }
    throw new Error(message);
  }
  return response.json();
}
