// STEP 4 — frontend silent-catch triage: wave 4e small modules
// (recording_recovery, generate_ui_flow, encounters_ui, client_usage_reporter,
//  client_error_reporter)
//
// Key reconfirmation from this wave: the two reporter modules
// (client_usage_reporter.js + client_error_reporter.js) swallow errors BY DESIGN
// and are already fully documented as intentional fire-and-forget ("Telemetry
// must never itself throw", "this handler exists to catch problems, not cause
// them"). They were NOT converted to throwing code — that would re-trigger the
// very handlers that exist to guard the app. The three feature modules'
// remaining empty catches are all category (a) best-effort guards, now with
// rationale comments (no behavior change).
//
// These are source-assertion regression guards:
//   1. Both reporters stay fire-and-forget (fetch chains + bodies swallowed).
//   2. The app's caught-error reporting path (reportClientError) stays exposed.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const web = path.join(__dirname, '..', 'web');
const read = (p) => fs.readFileSync(path.join(web, p), 'utf8');

test('client_error_reporter stays fire-and-forget (fetch chain + body swallowed)', () => {
  const src = read('js/client_error_reporter.js');
  assert.match(src, /\.catch\(function \(\) \{\}\)/, 'report fetch must be fire-and-forget');
  assert.match(src, /catch \(_\) \{\n\s*\/\/ Swallow/, 'whole body swallow + rationale present');
  assert.match(src, /not cause them/, 'documented intent present');
  // The caught-error entry point stays exposed on window.
  assert.match(src, /window\.reportClientError = function/, 'reportClientError stays exposed');
});

test('client_usage_reporter stays fire-and-forget (telemetry must never throw)', () => {
  const src = read('js/client_usage_reporter.js');
  assert.match(src, /\.catch\(function \(\) \{\}\)/, 'usage fetch must be fire-and-forget');
  assert.match(src, /Telemetry must never itself throw/, 'documented intent present');
  assert.match(src, /navigator\.sendBeacon\(/, 'unload beacon path present');
});

test('recording_recovery swallows are best-effort guards, not silent data loss', () => {
  const src = read('js/recording_recovery.js');
  // appendChunk converts a failed write to false (real error, not silent success).
  assert.match(src, /\)\.catch\(\(\) => false\)/, 'appendChunk failure -> false (surfaced to caller)');
  // Recover loop leaves failed uploads for retry (within TTL), documented.
  assert.match(src, /Leave it so it can be retried later/, 'recovery retry intent documented');
  // safeUserKey / safeEncounterId fall back to '' (not thrown), documented.
  assert.match(src, /safeUserKey is best-effort/, 'safeUserKey rationale');
  assert.match(src, /safeEncounterId is best-effort/, 'safeEncounterId rationale');
});

test('generate_ui_flow focus/scroll guard fallbacks are documented best-effort', () => {
  const src = read('generate_ui_flow.js');
  assert.match(src, /scrollIntoView with options can throw/, 'scroll guard rationale');
  assert.match(src, /best-effort a11y polish/, 'focus guard rationale');
});

test('encounters_ui compliance + best-effort guards documented', () => {
  const src = read('encounters_ui.js');
  assert.match(src, /Compliance cleanup is best-effort/, 'delete recovery cleanup rationale');
  assert.match(src, /Queue reload after switching is fire-and-forget/, 'queue reload rationale');
  assert.match(src, /A malformed date must not break row rendering/, 'date guard rationale');
});
