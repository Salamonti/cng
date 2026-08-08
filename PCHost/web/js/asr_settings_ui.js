/**
 * ASR settings drawer + modes prefetch.
 */
(function (global) {
  function apiBase() {
    try {
      if (typeof global.getApiBase === 'function') {
        return String(global.getApiBase((global.app && global.app.settings && global.app.settings.serverUrl) || '/api')).replace(/\/+$/, '');
      }
    } catch (_) {
      // Probe guard: getApiBase may be undefined early at load; '/api' default.
    }
    return '/api';
  }

  function setToggleButton(btn, on, disabled) {
    if (!btn) return;
    btn.classList.toggle('asr-setting-toggle--on', !!on);
    btn.setAttribute('aria-pressed', on ? 'true' : 'false');
    btn.disabled = !!disabled;
  }

  // Delegates to the single global definition in workspace_app.js
  // (window.getAuthToken), which reads both auth_access_token and
  // admin_workspace_token. An earlier version of this comment claimed no
  // global existed and this function reimplemented the sessionStorage lookup;
  // the global has been there all along, and the duplicate has been removed so
  // there is one place to change when token storage changes.
  //
  // Safe despite this file loading before workspace_app.js: both entry points
  // into this module (initAsrSettingsUi, refreshAsrCapabilities) are called
  // from workspace_app.js itself, so the global is always defined by the time
  // this runs.
  //
  // The original bug is still worth stating. When this resolved to null the
  // request went out as "Authorization: Bearer null". Once /asr/modes and
  // /asr/capabilities began requiring auth that 401'd, capabilities stayed
  // null, shouldShowToggles(null) returned false, and BOTH the streaming and
  // diarization toggles silently disappeared from Settings. Return null rather
  // than a token-shaped string so the caller omits the header entirely instead
  // of sending a literal "null".
  function authToken() {
    try {
      if (typeof global.getAuthToken === 'function') {
        return global.getAuthToken() || null;
      }
    } catch (_) {
      // Probe guard: getAuthToken may be unresolved at load; caller omits the
      // Authorization header rather than sending a token-shaped string.
    }
    return null;
  }

  // Report ONLY once the whole /asr/modes -> /asr/capabilities fallback chain
  // has failed (we are about to return null and hide the ASR toggles). A single
  // transient  404 on the first but a win on the second is a non-event and must
  // not be noise in the incident store.
  function _reportCapabilitiesFailure(stage, e) {
    if (typeof global.reportClientError === 'function') {
      global.reportClientError(
        'ASR capabilities: ' + stage + ' fetch failed; ASR toggles hidden',
        (e && (e.stack || e.message)) || undefined,
        'caught'
      );
    }
  }

  async function refreshAsrCapabilities() {
    if (!global.app) global.app = {};
    var base = apiBase();
    var token = authToken();
    var headers = token ? { 'Authorization': 'Bearer ' + token } : {};
    var modesError = null;
    try {
      var resp = await fetch(base + '/asr/modes', { credentials: 'same-origin', headers: headers });
      if (resp.ok) {
        global.app.asrCapabilities = await resp.json();
        return global.app.asrCapabilities;
      }
    } catch (e) {
      modesError = e;
    }
    var capsError = null;
    try {
      var resp2 = await fetch(base + '/asr/capabilities', { credentials: 'same-origin', headers: headers });
      if (resp2.ok) {
        global.app.asrCapabilities = await resp2.json();
        return global.app.asrCapabilities;
      }
    } catch (e) {
      capsError = e;
    }
    // Reached here only when neither endpoint produced capabilities.
    if (modesError) _reportCapabilitiesFailure('/asr/modes', modesError);
    if (capsError) _reportCapabilitiesFailure('/asr/capabilities', capsError);
    return null;
  }


  function bindToggleHoverHint(btn, hint) {
    if (!btn || !hint || btn.__hintWired) return;
    btn.__hintWired = true;
    hint.hidden = true;
    btn.addEventListener('mouseenter', function () {
      hint.hidden = false;
    });
    btn.addEventListener('mouseleave', function () {
      hint.hidden = true;
    });
  }

  function syncAsrSettingsDrawer() {
    var AS = global.AsrSettings;
    var details = document.getElementById('asrSettingsDetails');
    var chunkBtn = document.getElementById('asrChunkToggle');
    var diarizeBtn = document.getElementById('asrDiarizeToggle');
    if (!AS || !details) return;

    var caps = (global.app && global.app.asrCapabilities) || null;
    var state = AS.resolveToggleState(caps);
    if (state.visible) {
      details.classList.remove('hidden');
      details.style.display = '';
    } else {
      details.classList.add('hidden');
      details.style.display = 'none';
      return;
    }

    setToggleButton(chunkBtn, state.chunkingEnabled, !state.chunkAsrEnabled);
    setToggleButton(diarizeBtn, state.diarizeEnabled, !state.diarizationAvailable);
  }

  function bindAsrSettingsDrawer() {
    var AS = global.AsrSettings;
    var chunkBtn = document.getElementById('asrChunkToggle');
    var diarizeBtn = document.getElementById('asrDiarizeToggle');
    var chunkHint = document.getElementById('asrChunkToggleHint');
    var diarizeHint = document.getElementById('asrDiarizeToggleHint');
    if (!AS) return;

    bindToggleHoverHint(chunkBtn, chunkHint);
    bindToggleHoverHint(diarizeBtn, diarizeHint);

    if (chunkBtn && !chunkBtn.__wired) {
      chunkBtn.__wired = true;
      chunkBtn.addEventListener('click', function () {
        AS.setChunkingEnabled(!AS.isChunkingEnabled());
        syncAsrSettingsDrawer();
      });
    }
    if (diarizeBtn && !diarizeBtn.__wired) {
      diarizeBtn.__wired = true;
      diarizeBtn.addEventListener('click', function () {
        AS.setDiarizeEnabled(!AS.getDiarizeEnabled());
        syncAsrSettingsDrawer();
      });
    }
  }

  async function initAsrSettingsUi() {
    bindAsrSettingsDrawer();
    await refreshAsrCapabilities();
    syncAsrSettingsDrawer();
  }

  global.AsrSettingsUi = {
    apiBase: apiBase,
    refreshAsrCapabilities: refreshAsrCapabilities,
    syncAsrSettingsDrawer: syncAsrSettingsDrawer,
    initAsrSettingsUi: initAsrSettingsUi,
  };
})(typeof window !== 'undefined' ? window : globalThis);
