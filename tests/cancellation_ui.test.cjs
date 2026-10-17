// Offline tests of the real frontend functions with a minimal DOM; no browser or API calls.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

function frontend(fetch) {
  const nodes = new Map();
  const context = vm.createContext({
    window: {}, setTimeout, clearTimeout, fetch, console,
    history: { replaceState() {} },
    document: { activeElement: null, querySelector(selector) {
      if (!nodes.has(selector)) nodes.set(selector, { hidden: true, disabled: false, textContent: '', classList: { toggle() {} } });
      return nodes.get(selector);
    } },
  });
  const source = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8').replace(/^init\(\);$/m, '');
  vm.runInContext(source + `
    render = () => renderImageBusyState();
    renderControls = () => {};
    renderPreviousRunMenu = () => {};
    loadEvents = async () => {};
    activeImageAsset = () => ({ path: 'chart.png' });
    positionImageBusyOverlay = () => {};
    stateRef.runId = 'test-run';
    stateRef.state = { active_job: 'crop', active_job_id: 'job-one', stage: 'crop_ready' };
    this.ui = { stateRef, els, cancelActiveJob, loadRun, startJob, fileUrl };
  `, context);
  return context.ui;
}

const response = (state, outcome = 'cancelled') => ({ ok: true, json: async () => ({ state, outcome }) });
const restored = { active_job: null, active_job_id: null, stage: 'crop_review' };

test('Cancel remains disabled until restoration, then restores state and invalidates image URLs', async () => {
  const pending = deferred();
  const calls = [];
  const ui = frontend((url, options) => { calls.push({ url, options }); return pending.promise; });
  const before = ui.fileUrl('chart.png');
  const cancellation = ui.cancelActiveJob();
  assert.equal(ui.els.cancelJobBtn.disabled, true);
  assert.equal(ui.els.cancelJobBtn.textContent, 'Cancelling…');
  assert.equal(ui.stateRef.state.active_job, 'crop');
  await ui.cancelActiveJob();
  assert.equal(calls.length, 1);
  assert.equal(JSON.parse(calls[0].options.body).job_id, 'job-one');
  pending.resolve(response(restored));
  await cancellation;
  assert.equal(ui.stateRef.state.stage, 'crop_review');
  assert.equal(ui.els.imageBusyOverlay.hidden, true);
  assert.equal(ui.els.jobNotice.hidden, true);
  assert.equal(ui.els.jobNotice.textContent, '');
  assert.notEqual(ui.fileUrl('chart.png'), before);
});

test('an old poll arriving after cancellation cannot put the job back', async () => {
  const poll = deferred();
  const ui = frontend((url) => url.endsWith('/jobs/cancel') ? Promise.resolve(response(restored)) : poll.promise);
  const loading = ui.loadRun('test-run', { force: true });
  await ui.cancelActiveJob();
  poll.resolve(response({ active_job: 'crop', active_job_id: 'job-one', stage: 'crop_ready' }));
  await loading;
  assert.equal(ui.stateRef.state.stage, 'crop_review');
  assert.equal(ui.stateRef.state.active_job, null);
});

test('failed cancellation leaves the job active and permits a retry', async () => {
  const ui = frontend(async () => { throw new Error('Connection lost'); });
  await ui.cancelActiveJob();
  assert.equal(ui.stateRef.state.active_job, 'crop');
  assert.equal(ui.els.cancelJobBtn.disabled, false);
  assert.equal(ui.els.imageBusyOverlay.hidden, false);
  assert.match(ui.els.jobNotice.textContent, /Connection lost/);
});

test('a completed operation is shown accurately when cancellation arrives too late', async () => {
  const ui = frontend(async () => response({ ...restored, stage: 'series_review' }, 'already_finished'));
  await ui.cancelActiveJob();
  assert.equal(ui.stateRef.state.stage, 'series_review');
  assert.equal(ui.els.jobNotice.textContent, 'Operation had already finished.');
});

test('starting waits for a draft save and prevents duplicate starts', async () => {
  const calls = [];
  const ui = frontend(async (url) => { calls.push(url); return response({ active_job: 'crop', active_job_id: 'new-job' }); });
  ui.stateRef.state = restored;
  ui.stateRef.localSaveCount = 1;
  const start = ui.startJob('crop');
  await ui.startJob('crop');
  assert.equal(calls.length, 0);
  ui.stateRef.localSaveCount = 0;
  await start;
  assert.equal(calls.filter((url) => url.endsWith('/jobs/crop')).length, 1);
  assert.equal(ui.stateRef.state.active_job_id, 'new-job');
});

test('failed draft saves prevent starting with an outdated baseline', async () => {
  let calls = 0;
  const ui = frontend(async () => { calls += 1; return response(restored); });
  ui.stateRef.state = restored;
  ui.stateRef.localSaveError = new Error('Save failed');
  await assert.rejects(ui.startJob('crop'), /Save your latest edits/);
  assert.equal(calls, 0);
  assert.equal(ui.stateRef.jobStarting, false);
});
