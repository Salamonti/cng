// STEP 4 — frontend silent-catch triage: wave 4f small files
// (workspace_file_camera, patient_materials_ui, mobile_tools, api_error_format)
//
// Final small-module wave. All remaining empty catches are genuinely category
// (a) best-effort guards, now documented with rationale comments (no behavior
// change): camera stream cleanup / .muted / autoplay; patient-materials
// localStorage load (corrupt→{}) + save (quota/blocked); mobile-nav scroll;
// api_error_format CommonJS export guard.
//
// The one already-correct catch in this set was NOT touched: patient_materials
// generation failure (L191) is a documented (b) reporting path (console.error +
// explicit caught-error report) — it is NOT silent, so it stays as-is.
//
// These are source-assertion regression guards:
//   1. The patient-materials generation catch stays a REPORTING path (not
//      downgraded to a silent swallow).
//   2. The pure best-effort guards keep their documented rationale.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const js = path.join(__dirname, '..', 'web', 'js');
const read = (p) => fs.readFileSync(path.join(js, p), 'utf8');

test('patient_materials generation failure stays a REPORTING path (not silent)', () => {
  const src = read('patient_materials_ui.js');
  assert.match(src, /console\.error\('Patient material generation failed:'/, 'logs the error');
  assert.match(src, /Report explicitly\./, 'documented that it reports explicitly because caught');
});

test('patient_materials storage guards documented as best-effort', () => {
  const src = read('patient_materials_ui.js');
  assert.match(src, /corrupt entry \(or blocked storage\) falls back to empty defaults/, 'load rationale');
  assert.match(src, /quota\/blocked-storage failure must not break material generation/, 'save rationale');
});

test('workspace_file_camera guards documented as best-effort', () => {
  const src = read('workspace_file_camera.js');
  assert.match(src, /stream cleanup|stopping tracks is cosmetic resource release/, 'stream cleanup rationale');
  assert.match(src, /Autoplay may be rejected/, 'autoplay rationale');
});

test('mobile_tools + api_error_format guards documented', () => {
  const mt = read('mobile_tools.js');
  assert.match(mt, /scrollIntoView-with-options throw/, 'mobile nav rationale');
  const aef = read('api_error_format.js');
  assert.match(aef, /Best-effort CommonJS export/, 'module export rationale');
  // api_error_format's JSON-parse fall-through stays documented.
  assert.match(aef, /fall through/, 'JSON parse fall-through documented');
});
