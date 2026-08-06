/**
 * ASR settings drawer + modes prefetch.
 */
(function (global) {
  function apiBase() {
    try {
      if (typeof global.getApiBase === 'function') {
        return String(global.getApiBase((global.app && global.app.settings && global.app.settings.serverUrl) || '/api')).replace(/\/+$/, '');
      }
    } catch (_) {}
    return '/api';
  }

  function setToggleButton(btn, on, disabled) {
    if (!btn) return;
    btn.classList.toggle('asr-setting-toggle--on', !!on);
    btn.setAttribute('aria-pressed', on ? 'true' : 'false');
    btn.disabled = !!disabled;
  }

  // No global getAuthToken() is ever defined in this app -- every reference
  // to it is a consumer guarded by typeof, so this resolved to null and the
  // request went out as "Authorization: Bearer null". Once /asr/modes and
  // /asr/capabilities began requiring auth, that 401'd, capabilities stayed
  // null, shouldShowToggles(null) returned false, and BOTH the streaming and
  // diarization toggles silently disappeared from Settings. Read the same
  // session token the rest of the app uses, and omit the header entirely
  // when there is no token rather than sending a literal "null".
  function authToken() {
    try {
      if (typeof global.getAuthToken === 'function') {
        var fromFn = global.getAuthToken();
        if (fromFn) return fromFn;
      }
    } catch (_) {}
    try {
      return global.sessionStorage.getItem('auth_access_token')
        || global.sessionStorage.getItem('admin_workspace_token')
        || null;
    } catch (_) {
      return null;
    }
  }

  async function refreshAsrCapabilities() {
    if (!global.app) global.app = {};
    var base = apiBase();
    var token = authToken();
    var headers = token ? { 'Authorization': 'Bearer ' + token } : {};
    try {
      var resp = await fetch(base + '/asr/modes', { credentials: 'same-origin', headers: headers });
      if (resp.ok) {
        global.app.asrCapabilities = await resp.json();
        return global.app.asrCapabilities;
      }
    } catch (_) {}
    try {
      var resp2 = await fetch(base + '/asr/capabilities', { credentials: 'same-origin', headers: headers });
      if (resp2.ok) {
        global.app.asrCapabilities = await resp2.json();
        return global.app.asrCapabilities;
      }
    } catch (_) {}
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
