import assert from 'node:assert/strict';
import test from 'node:test';

import {
  CAPTURE_MODE_KEY,
  PROFILE_KEY,
  getCaptureMode,
  setCaptureMode,
  getProfile,
  setProfile,
  shouldShowToggles,
  resolveToggleState,
} from '../web/js/asr_settings.mjs';

function makeStorage() {
  const store = new Map();
  return {
    getItem: (key) => store.get(key) ?? null,
    setItem: (key, value) => store.set(key, value),
    removeItem: (key) => store.delete(key),
    clear: () => store.clear(),
  };
}

test('P4-S11-T1: default getters return batch/asr_only when storage empty; setters reject invalid values', () => {
  const s = makeStorage();

  // Defaults when empty
  assert.equal(getCaptureMode(s), 'batch');
  assert.equal(getProfile(s), 'asr_only');

  // Valid setters
  assert.equal(setCaptureMode('stream', s), 'stream');
  assert.equal(getCaptureMode(s), 'stream');
  assert.equal(setProfile('full', s), 'full');
  assert.equal(getProfile(s), 'full');

  // Invalid capture mode — rejected, prior value kept
  assert.equal(setCaptureMode('invalid', s), 'stream');
  assert.equal(getCaptureMode(s), 'stream');

  // Invalid profile — rejected, prior value kept
  assert.equal(setProfile('bogus', s), 'full');
  assert.equal(getProfile(s), 'full');

  // Keys are correct
  assert.equal(CAPTURE_MODE_KEY, 'cng_asr_capture_mode');
  assert.equal(PROFILE_KEY, 'cng_asr_profile');
});

test('P4-S11-T2: shouldShowToggles — false when streaming_enabled false or null; true when true; false when allowUi:false', () => {
  // null capabilities
  assert.equal(shouldShowToggles(null), false);
  assert.equal(shouldShowToggles(undefined), false);

  // streaming_enabled: false
  assert.equal(shouldShowToggles({ streaming_enabled: false }), false);

  // streaming_enabled: true, default allowUi (true)
  assert.equal(shouldShowToggles({ streaming_enabled: true }), true);

  // streaming_enabled: true, allowUi explicitly true
  assert.equal(shouldShowToggles({ streaming_enabled: true }, { allowUi: true }), true);

  // streaming_enabled: true, allowUi: false -> blocked
  assert.equal(shouldShowToggles({ streaming_enabled: true }, { allowUi: false }), false);
});

test('P4-S11-T3: resolveToggleState marks asr_diarize/full as degraded when diarization_available:false; visible follows streaming_enabled', () => {
  const s = makeStorage();

  // Case 1: no streaming — not visible, no diarization, defaults
  const capsOff = {
    streaming_enabled: false,
    diarization_available: false,
    vad_enabled: false,
    profiles: ['asr_only', 'asr_diarize', 'full'],
  };
  const stateOff = resolveToggleState(capsOff, s);
  assert.equal(stateOff.visible, false);
  assert.equal(stateOff.diarizationAvailable, false);
  assert.equal(stateOff.vadEnabled, false);
  assert.equal(stateOff.profiles.length, 3);
  assert.equal(stateOff.profiles[0].id, 'asr_only');
  assert.equal(stateOff.profiles[0].degraded, false);
  assert.equal(stateOff.profiles[1].id, 'asr_diarize');
  assert.equal(stateOff.profiles[1].degraded, true);
  assert.equal(stateOff.profiles[1].label, 'ASR + diarization');
  assert.equal(stateOff.profiles[2].id, 'full');
  assert.equal(stateOff.profiles[2].degraded, true);
  assert.equal(stateOff.profiles[2].label, 'Full (refine)');

  // Case 2: streaming on + diarization available — all good
  const capsOn = {
    streaming_enabled: true,
    diarization_available: true,
    vad_enabled: true,
    profiles: ['asr_only', 'asr_diarize', 'full'],
  };
  const stateOn = resolveToggleState(capsOn, s);
  assert.equal(stateOn.visible, true);
  assert.equal(stateOn.diarizationAvailable, true);
  assert.equal(stateOn.vadEnabled, true);
  assert.equal(stateOn.profiles[0].degraded, false);
  assert.equal(stateOn.profiles[1].degraded, false);
  assert.equal(stateOn.profiles[2].degraded, false);

  // Case 3: streaming on + diarization NOT available — degraded flags
  const capsDegraded = {
    streaming_enabled: true,
    diarization_available: false,
    vad_enabled: false,
    profiles: ['asr_only', 'asr_diarize', 'full'],
  };
  const stateDeg = resolveToggleState(capsDegraded, s);
  assert.equal(stateDeg.visible, true);
  assert.equal(stateDeg.diarizationAvailable, false);
  assert.equal(stateDeg.profiles[0].degraded, false);
  assert.equal(stateDeg.profiles[1].degraded, true);
  assert.equal(stateDeg.profiles[2].degraded, true);

  // Case 4: fallback profiles when capabilities.profiles is missing
  const capsMinimal = {
    streaming_enabled: true,
    diarization_available: false,
    vad_enabled: false,
  };
  const stateMin = resolveToggleState(capsMinimal, s);
  assert.equal(stateMin.profiles.length, 3);
  assert.equal(stateMin.profiles[0].id, 'asr_only');
});
