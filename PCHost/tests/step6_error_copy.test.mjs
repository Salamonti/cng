// STEP 6 — user-facing error copy (Task 5): patient-facing wording review.
//
// Two concrete items from the plan's audit §7:
//   1. Release-recording failure copy in workspace_app.js: was
//      'Failed — please refresh page.' — instructs a destructive refresh that
//      loses a stuck recording (the exact thing the button exists to prevent),
//      AND is factually wrong: forceResetRecording() runs FIRST and never
//      throws (its body is a catch-all), so reaching the catch means the
//      recording was already released + retained; only the UI-sync after it
//      could have failed. New copy states what happened + a non-destructive
//      next step, and RE-ENABLES the button (retry is real & idempotent).
//   2. patient_materials_ui.js toast fallback: 'Unexpected error' was a dead-end
//      with no next step → replaced with an actionable
//      'The AI service is temporarily unavailable. Please try again.'
//
// These are source-assertion regression guards: assert the new copy (and the
// re-enable) is present, and that the old harmful "please refresh" copy and the
// bare 'Unexpected error' fallback are GONE.

import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const js = path.join(__dirname, '..', 'web', 'js');
const web = path.join(__dirname, '..', 'web');
const read = (p) => fs.readFileSync(path.join(js, p), 'utf8');

test('release-recording failure: no longer tells the clinician to refresh', () => {
  const src = read('workspace_app.js');
  // The destructive/incorrect copy is gone.
  assert.ok(!/Failed — please refresh page\./.test(src), 'must not instruct a refresh');
  assert.ok(!/Failed - please refresh page\./.test(src), 'must not instruct a refresh (hyphen variant)');
});

test('release-recording failure: states what happened + non-destructive next step', () => {
  const src = read('workspace_app.js');
  assert.match(
    src,
    /Release not completed\. Tap Re-transcribe to finish, or try again\./,
    'new copy names the state and the non-destructive next step'
  );
  // Documented why: refresh would discard the retained copy + forceReset already ran.
  assert.match(src, /never throws/, 'documents forceResetRecording is non-throwing');
  assert.match(src, /Never tell the/, 'documents the refresh hazard (start of rationale)');
  assert.match(src, /clinician to refresh/, 'documents the refresh hazard');
});

test('release-recording failure: button is RE-ENABLED so retry is real', () => {
  const src = read('workspace_app.js');
  // The old code set btn.disabled = true in the catch (contradicting "try again");
  // now it re-enables so a retry re-runs the idempotent reset + repairs UI sync.
  assert.match(src, /btn\.disabled = false;/, 're-enables the release button on failure');
  // The successful branch must still style the button as released.
  assert.match(src, /Released\. Tap Re-transcribe to finish\./, 'success copy intact');
});

test('patient-materials toast: bare "Unexpected error" fallback replaced with action', () => {
  const src = read('patient_materials_ui.js');
  const detailLine = src.match(/var detail = \(error && error\.message\) \|\| ([^;]+);/);
  assert.ok(detailLine, 'detail fallback line present');
  assert.match(
    detailLine[1],
    /The AI service is temporarily unavailable\. Please try again\./,
    'fallback is actionable, not a dead-end "Unexpected error"'
  );
  assert.ok(!/Unexpected error/.test(src), 'bare "Unexpected error" is gone from the toast path');
});

test('patient-materials toast still carries the material name + true error when available', () => {
  const src = read('patient_materials_ui.js');
  assert.match(src, /'Failed to generate ' \+ MATERIAL_TYPES\[category\] \+ ': ' \+ detail/, 'toast prefixes the material name');
});
