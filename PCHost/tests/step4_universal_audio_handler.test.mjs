// STEP 4 — frontend silent-catch triage: universal_audio_handler.js (wave 4c)
//
// This file is the recording glue. Per the plan, recording-START and
// recording-FINALIZE are user-visible paths (category c) — but in this file
// those failure paths are NOT silent: start failure surfaces via
// _asrStatus('error', ...) + _reportAsrIncident, and finalize/transcription
// failure surfaces via _asrStatus('error', ...). The remaining empty catches
// are genuinely-redundant category (a) defensive guards (documented with
// rationale comments, no behavior change).
//
// These are source-assertion regression guards so a future edit can't silently
// un-reclassify them:
//   1. The incident reporter (_reportAsrIncident) stays fire-and-forget — it
//      must never throw into the recording path.
//   2. Recording-start failure reports to the incident store (not swallowed).
//   3. Transcription/finalize failure surfaces to the user (not swallowed).
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SRC = fs.readFileSync(
  path.join(__dirname, '..', 'web', 'universal_audio_handler.js'),
  'utf8'
);

test('incident reporter is fire-and-forget (never throws into recording)', () => {
  const idx = SRC.indexOf('_reportAsrIncident(payload) {');
  assert.ok(idx !== -1, 'reporter should exist');
  const fn = SRC.slice(idx, idx + 2200);
  // Both the fetch chain and the whole body are swallowed by design.
  assert.match(fn, /\.catch\(\(\) => \{\}\)/, 'fetch chain must be fire-and-forget');
  assert.match(fn, /catch \(_\) \{\}/, 'reporter body must be guarded');
  assert.match(fn, /fire-and-forget reporter/, 'rationale comment present');
});

test('recording-start failure is reported, not swallowed', () => {
  const idx = SRC.indexOf("stage: 'client.recording_start_failed'");
  assert.ok(idx !== -1, 'start-failure incident should exist');
  const ctx = SRC.slice(idx - 400, idx + 200);
  // The catch(error) block calls onRecordingError + _reportAsrIncident + rethrows.
  assert.match(ctx, /_reportAsrIncident/, 'start failure reports via _reportAsrIncident');
  assert.match(ctx, /onRecordingError\(error\)/, 'start failure surfaces to the user');
});

test('transcription/finalize failure surfaces to the user, not swallowed', () => {
  const idx = SRC.indexOf("this._asrStatus('error', 'Transcription failed: '");
  assert.ok(idx !== -1, 'transcription-failure status should exist');
  // It is set inside a catch(e) with reportedErr=true so the user sees it.
  assert.match(SRC.slice(idx - 200, idx + 80), /catch \(e\) \{/, 'failure is caught (not silent)');
});
