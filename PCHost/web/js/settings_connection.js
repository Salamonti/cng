const SETTINGS_KEY = 'cng_settings_v1';
const LEGACY_KEY = 'clinicalNoteSettings';

// Uses window.apiFetch from workspace_app.js (loads before this file). Do NOT define apiFetch here —
// a duplicate without /api prefix caused 404 on /generate_v8_stream (P10 regression, fixed 2026-06).

function readSettings() {
  return { serverUrl: '/api', apiKey: getAuthToken() };
}

function writeSettings(obj) {
  const token = obj?.apiKey || '';
  try {
    if (token) {
      sessionStorage.setItem('auth_access_token', token);
    } else {
      sessionStorage.removeItem('auth_access_token');
    }
  } catch (e) {
    console.warn('[Settings] Unable to persist auth token:', e);
  }
  return { serverUrl: '/api', apiKey: token };
}

async function getConnectionState() {
  try {
    const fetchFn = typeof window.apiFetch === 'function' ? window.apiFetch : (typeof apiFetch === 'function' ? apiFetch : null);
    if (!fetchFn) {
      return { ok: false, reason: 'API client not loaded' };
    }
    const h = await fetchFn('/health');
    if (!h.ok) return { ok: false, reason: `Health check failed: ${h.status}` };
    return { ok: true, reason: '' };
  } catch (e) {
    return { ok: false, reason: e.message || 'Network error' };
  }
}

/** Delegates to workspace_app checkConnection — avoids overwriting "Queued (n)" with "Connected". */
async function refreshBadge() {
  if (window.app && app.isStreaming) return;
  if (typeof checkConnection === 'function') {
    await checkConnection();
  } else if (typeof syncQueueConnectionPill === 'function') {
    syncQueueConnectionPill();
  }
}

setTimeout(() => {
  debugLog('[Init] Initializing connection management...');
  refreshBadge();
  // Health + queue pill: workspace_app.js runs checkConnection on its own interval — do not duplicate here.
  debugLog('[Init] Connection management initialized');
}, 500);

window.addEventListener('workspace-auth-changed', (event) => {
  if (event.detail?.signedIn) {
    if (typeof applySavedSidebarRailPreference === 'function') applySavedSidebarRailPreference();
    loadSettings();
    checkConnection();
    // Load queue from server and update display (pill sync runs inside updateQueueDisplay)
    loadQueue().then(() => updateQueueDisplay());
    // Load workspace and prompts after login
    loadWorkspaceState().then(() => loadDefaultPrompts()).then(() => loadProfileNoteTypes()).then(() => syncProfileSpecialtyToBar()).catch(err => {
      console.error('[Init] Failed to load workspace/prompts:', err);
    });
  } else {
    document.body.classList.remove('sidebar-rail-collapsed');
    try {
      const ep = document.getElementById('encountersPanel');
      const eb = document.getElementById('encountersBackdrop');
      if (ep) ep.classList.remove('open');
      if (eb) eb.classList.remove('open');
      if (typeof window.closeLiteraturePanel === 'function') window.closeLiteraturePanel();
    } catch (e) {
      // Benign logout-time UI cleanup: a panel already missing/unwired must not
      // break the rest of the signed-out teardown below.
    }
    // Preserve UI data when logged out
    const consultCard = document.getElementById('consultCommentCard');
    if (consultCard) consultCard.classList.add('hidden');
    const orderBtn = document.getElementById('openOrderRequestsBtn');
    if (orderBtn) orderBtn.classList.add('hidden');
    updateConnectionStatus('disconnected');
    const statusEl = document.getElementById('connectionStatus');
    if (statusEl) {
      statusEl.classList.remove('connected');
      statusEl.classList.add('disconnected');
      statusEl.textContent = 'Disconnected';
    }
    updateQueueDisplay(); // hide queue when signed out
  }
});

window.addEventListener('workspace-synced', (event) => {
  const version = event.detail?.version;
  if (typeof version === 'number' && version > 0) {
    if (window.app && app.connection) app.connection.lastHealthOk = true;
    if (typeof syncQueueConnectionPill === 'function') syncQueueConnectionPill();
  }
  // Load workspace state and prompts after sync
  if (typeof loadWorkspaceState === 'function' && typeof loadDefaultPrompts === 'function') {
    loadWorkspaceState().then(() => loadDefaultPrompts()).then(() => loadProfileNoteTypes()).then(() => syncProfileSpecialtyToBar()).catch(err => {
      console.error('[Init] Failed to load workspace/prompts after sync:', err);
    });
  }
});

window.addEventListener('storage', (event) => {
});
