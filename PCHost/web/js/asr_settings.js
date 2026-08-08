/**
 * ASR settings — chunking and diarization toggles (independent).
 * Diarization runs one LLM pass (normalize + speaker labels) when enabled.
 */
(function (global) {
  var CAPTURE_MODE_KEY = 'cng_asr_capture_mode';
  var DIARIZE_KEY = 'cng_asr_diarize';
  var PROFILE_KEY = 'cng_asr_profile';

  function migrateCaptureMode(raw) {
    if (raw === 'stream') return 'chunk';
    return raw;
  }

  function migrateLegacyProfile() {
    try {
      var profile = localStorage.getItem(PROFILE_KEY);
      if (profile === 'asr_diarize' && localStorage.getItem(DIARIZE_KEY) == null) {
        localStorage.setItem(DIARIZE_KEY, '1');
      }
    } catch (_) {
      // Best-effort legacy migration: storage may be blocked (privacy mode /
      // quota). Skipping only affects a one-time toggle default, not a value we
      // can recover by falling back to the default below.
    }
  }

  function getCaptureMode() {
    migrateLegacyProfile();
    try {
      var v = migrateCaptureMode(localStorage.getItem(CAPTURE_MODE_KEY));
      if (v === 'batch' || v === 'chunk') return v;
    } catch (_) {
      // localStorage read may throw when storage is blocked; falls through to
      // the 'batch' default below by design.
    }
    return 'batch';
  }

  function setCaptureMode(mode) {
    if (mode !== 'batch' && mode !== 'chunk') return getCaptureMode();
    try {
      localStorage.setItem(CAPTURE_MODE_KEY, mode);
    } catch (_) {
      // Persisting a setting is best-effort: a failed write (quota / blocked)
      // must not break the in-memory toggle, which still returns `mode`.
    }
    return mode;
  }

  function isChunkingEnabled() {
    return getCaptureMode() === 'chunk';
  }

  function setChunkingEnabled(on) {
    return setCaptureMode(on ? 'chunk' : 'batch');
  }

  function getDiarizeEnabled() {
    migrateLegacyProfile();
    try {
      return localStorage.getItem(DIARIZE_KEY) === '1';
    } catch (_) {
      // Blocked storage -> treat as disabled; 'false' is the safe default.
    }
    return false;
  }

  function setDiarizeEnabled(on) {
    try {
      localStorage.setItem(DIARIZE_KEY, on ? '1' : '0');
    } catch (_) {
      // Best-effort persistence; in-memory state is read back below.
    }
    return getDiarizeEnabled();
  }

  function buildRefineQueryString() {
    if (!getDiarizeEnabled()) return '';
    return '?diarize=true';
  }

  function appendRefineQuery(url) {
    var base = String(url || '');
    var qs = buildRefineQueryString();
    if (!qs) return base;
    return base + (base.indexOf('?') >= 0 ? qs.replace('?', '&') : qs);
  }

  function appendDiarizeQuery(url) {
    return appendRefineQuery(url);
  }

  function shouldShowToggles(capabilities, opts) {
    opts = opts || {};
    if (opts.allowUi === false) return false;
    if (!capabilities) return false;
    return !!(capabilities.chunk_asr_enabled || capabilities.refine_available || capabilities.diarization_available);
  }

  function resolveToggleState(capabilities) {
    var visible = shouldShowToggles(capabilities);
    return {
      visible: visible,
      chunkingEnabled: isChunkingEnabled(),
      diarizeEnabled: getDiarizeEnabled(),
      chunkAsrEnabled: !!(capabilities && capabilities.chunk_asr_enabled),
      refineAvailable: !!(capabilities && capabilities.refine_available),
      diarizationAvailable: !!(capabilities && (capabilities.diarization_available || capabilities.refine_available)),
    };
  }

  global.AsrSettings = {
    CAPTURE_MODE_KEY: CAPTURE_MODE_KEY,
    DIARIZE_KEY: DIARIZE_KEY,
    getCaptureMode: getCaptureMode,
    setCaptureMode: setCaptureMode,
    isChunkingEnabled: isChunkingEnabled,
    setChunkingEnabled: setChunkingEnabled,
    getDiarizeEnabled: getDiarizeEnabled,
    setDiarizeEnabled: setDiarizeEnabled,
    buildRefineQueryString: buildRefineQueryString,
    appendRefineQuery: appendRefineQuery,
    appendDiarizeQuery: appendDiarizeQuery,
    shouldShowToggles: shouldShowToggles,
    resolveToggleState: resolveToggleState,
  };
})(typeof window !== 'undefined' ? window : globalThis);
