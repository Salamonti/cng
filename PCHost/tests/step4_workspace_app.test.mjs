// STEP 4 — frontend silent-catch triage: workspace_app.js (wave 4b, recording/ASR region)
//
// workspace_app.js is a several-thousand-line browser IIFE, so unlike
// asr_settings_ui.js it cannot be behaviourally driven in a vm sandbox the same
// way. Here we assert the (b)-classification decisions directly on source:
//
//   1. recoverLocalRecordingsToServer() whole-chain failure is no longer a bare
//      silent swallow — it routes to window.reportClientError (typeof-guarded so
//      an absent reporter can never throw), matching the step4a pattern of
//      reporting only on whole-chain failure.
//   2. The pre-existing incident-store reporters (chunkWorker, drain) keep their
//      fire-and-forget protection (they must never throw) — i.e. recategorized
//      with rationale rather than being "fixed" into something that can crash the
//      recording pipeline.
//
// These are regression guards: if a future edit reverts the wiring or un-guards
// the reporter, the test fails.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SRC = fs.readFileSync(
  path.join(__dirname, '..', 'web', 'js', 'workspace_app.js'),
  'utf8'
);

test('recoverLocalRecordingsToServer whole-chain failure reports via reportClientError', () => {
  // Locate the recovery catch block.
  const marker = 'async function recoverLocalRecordingsToServer(options = {})';
  const idx = SRC.indexOf(marker);
  assert.ok(idx !== -1, 'recoverLocalRecordingsToServer should exist');
  const recoverStart = idx;
  // End of function: find the closing '}\n        }' after the catch.
  const fnBody = SRC.slice(recoverStart, recoverStart + 4000);

  // The catch is no longer a bare swallow.
  assert.match(fnBody, /catch\s*\(e\)\s*\{/, 'recovery should have a catch(e) block');
  assert.match(fnBody, /reportClientError/, 'wildcard whole-chain failure should call reportClientError');

  // The report must be type-checked so an absent reporter can never throw
  // (fire-and-forget guarantee preserved).
  assert.match(fnBody, /typeof window\.reportClientError === 'function'/, 'reporter call must be typeof-guarded');
  assert.match(fnBody, /'Recording recovery failed; orphaned audio may be lost'/, 'report message should be present');
});

test('chunk-worker finalization error still reports to incident store, fire-and-forget', () => {
  const site = "stage: 'client.chunkWorker'";
  const idx = SRC.indexOf(site);
  assert.ok(idx !== -1, 'chunkWorker reporter should exist');
  const ctx = SRC.slice(idx - 200, idx + 300);

  // It already reports via _reportAsrIncident (not a silent swallow).
  assert.match(ctx, /_reportAsrIncident/, 'chunkWorker should report via _reportAsrIncident');
  // ...and the outer try/catch must remain to protect the fire-and-forget reporter.
  assert.match(ctx, /catch \(_\) \{\}/, 'reporter must stay fire-and-forget (guarded from throwing)');
});

test('drain POST-timeout still reports to incident store, fire-and-forget', () => {
  const site = "stage: 'client.drain'";
  const idx = SRC.indexOf(site);
  assert.ok(idx !== -1, 'drain reporter should exist');
  const ctx = SRC.slice(idx - 200, idx + 300);
  assert.match(ctx, /_reportAsrIncident/, 'drain should report via _reportAsrIncident');
  assert.match(ctx, /catch \(_\) \{\}/, 'drain reporter must stay fire-and-forget');
});
