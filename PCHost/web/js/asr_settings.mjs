/**
 * ESM version of ASR settings state for Node tests.
 * Keep logic in sync with `asr_settings.js`.
 */

export const CAPTURE_MODE_KEY = 'cng_asr_capture_mode';
export const PROFILE_KEY = 'cng_asr_profile';

const DEFAULT_PROFILES = ['asr_only', 'asr_diarize', 'full'];

const PROFILE_LABELS = {
  asr_only: 'ASR only',
  asr_diarize: 'ASR + diarization',
  full: 'Full (refine)',
};

export function getCaptureMode(storage) {
  try {
    const v = storage.getItem(CAPTURE_MODE_KEY);
    if (v === 'batch' || v === 'stream') return v;
  } catch (_) {}
  return 'batch';
}

export function setCaptureMode(mode, storage) {
  if (mode !== 'batch' && mode !== 'stream') return getCaptureMode(storage);
  try {
    storage.setItem(CAPTURE_MODE_KEY, mode);
  } catch (_) {}
  return mode;
}

export function getProfile(storage) {
  try {
    const v = storage.getItem(PROFILE_KEY);
    if (v === 'asr_only' || v === 'asr_diarize' || v === 'full') return v;
  } catch (_) {}
  return 'asr_only';
}

export function setProfile(p, storage) {
  if (p !== 'asr_only' && p !== 'asr_diarize' && p !== 'full') return getProfile(storage);
  try {
    storage.setItem(PROFILE_KEY, p);
  } catch (_) {}
  return p;
}

export function shouldShowToggles(capabilities, opts) {
  if (!capabilities || !capabilities.streaming_enabled) return false;
  opts = opts || {};
  if (opts.allowUi === false) return false;
  return true;
}

export function resolveToggleState(capabilities, storage) {
  var visible = shouldShowToggles(capabilities);
  var captureMode = getCaptureMode(storage);
  var profile = getProfile(storage);

  var diarizationAvailable = false;
  var vadEnabled = false;
  var rawProfiles = DEFAULT_PROFILES.slice();

  if (capabilities) {
    diarizationAvailable = !!capabilities.diarization_available;
    vadEnabled = !!capabilities.vad_enabled;
    if (capabilities.profiles && Array.isArray(capabilities.profiles) && capabilities.profiles.length > 0) {
      rawProfiles = capabilities.profiles.slice();
    }
  }

  var profiles = rawProfiles.map(function (pid) {
    var degraded = false;
    if (pid === 'asr_diarize' || pid === 'full') {
      if (!diarizationAvailable) degraded = true;
    }
    return {
      id: pid,
      label: PROFILE_LABELS[pid] || pid,
      degraded: degraded,
    };
  });

  return {
    visible: visible,
    captureMode: captureMode,
    profile: profile,
    profiles: profiles,
    diarizationAvailable: diarizationAvailable,
    vadEnabled: vadEnabled,
  };
}
