// P6: ChatGPT-style encounters panel (server: /api/encounters + active encounter via /api/workspace/)
(function () {
  'use strict';

  function auth() {
    return window.AuthWorkspace;
  }

  function toast(title, msg, level) {
    if (typeof window.showToast === 'function') {
      window.showToast(title, msg, level || 'info');
    }
  }

  function busyGate() {
    const a = auth();
    if (!a || typeof a.isClinicalBusy !== 'function') return false;
    if (a.isClinicalBusy()) {
      toast('Busy', 'Finish recording, OCR, or note generation first.', 'warning');
      return true;
    }
    return false;
  }

  /**
   * List order from GET /api/encounters/ is updated_at descending, so the active encounter
   * is always moved to index 0 after a switch — Prev stayed disabled until the end.
   * Stable id sort gives a fixed order for strip prev/next.
   */
  function stableEncounterOrder(rows) {
    const list = Array.isArray(rows) ? rows : [];
    return [...list].sort((a, b) => String(a.id).localeCompare(String(b.id)));
  }

  function updateEncounterStripNavFromList(rows) {
    const prevBtn = document.getElementById('aeStripPrevBtn');
    const nextBtn = document.getElementById('aeStripNextBtn');
    const delBtn = document.getElementById('aeStripDeleteBtn');
    if (!prevBtn || !nextBtn) return;
    const list = stableEncounterOrder(rows);
    const n = list.length;
    const activeIdx = list.findIndex((e) => e.is_active);
    if (n === 0 || activeIdx < 0) {
      prevBtn.disabled = true;
      nextBtn.disabled = true;
      if (delBtn) delBtn.disabled = true;
      return;
    }
    prevBtn.disabled = activeIdx <= 0;
    nextBtn.disabled = activeIdx >= n - 1;
    if (delBtn) delBtn.disabled = false;
  }

  async function navigateEncounterStripByDelta(delta) {
    if (busyGate()) return;
    const a = auth();
    if (!a || !a.isWorkspaceReady()) return;
    try {
      const resp = await a.request('/api/encounters/');
      if (!resp.ok) return;
      const data = await resp.json();
      const rows = stableEncounterOrder(data.encounters);
      const activeIdx = rows.findIndex((e) => e.is_active);
      if (activeIdx < 0 || rows.length < 2) return;
      const nextIdx = activeIdx + delta;
      if (nextIdx < 0 || nextIdx >= rows.length) return;
      await activateEncounterId(rows[nextIdx].id, { silent: true, keepOpen: true });
    } catch (e) {
      console.warn('[Encounters] strip nav', e);
    }
  }

  async function refreshActiveEncounterFromServer() {
    const a = auth();
    const inp = document.getElementById('activeEncounterNameInput');
    if (!a || !inp || !a.isWorkspaceReady()) return;
    const skipValueSync = document.activeElement === inp;
    try {
      const resp = await a.request('/api/encounters/');
      if (!resp.ok) return;
      const data = await resp.json();
      const rows = Array.isArray(data.encounters) ? data.encounters : [];
      if (!skipValueSync) {
        const active = rows.find((e) => e.is_active);
        if (active) {
          const lab = active.label || 'Encounter';
          inp.value = lab;
          inp.dataset.encounterId = String(active.id);
          inp.dataset.aeSnapshot = lab;
        } else {
          inp.value = '';
          inp.dataset.encounterId = '';
          inp.dataset.aeSnapshot = '';
        }
      }
      updateEncounterStripNavFromList(rows);
    } catch (e) {
      console.warn('[Encounters] active strip', e);
    }
  }

  async function saveActiveEncounterNameIfChanged() {
    const inp = document.getElementById('activeEncounterNameInput');
    if (!inp) return;
    const id = inp.dataset.encounterId;
    if (!id) return;
    const label = String(inp.value || '').trim();
    if (!label) {
      toast('Name required', 'Encounter name cannot be empty.', 'warning');
      await refreshActiveEncounterFromServer();
      return;
    }
    const snap = String(inp.dataset.aeSnapshot || '').trim();
    if (label === snap) return;
    const a = auth();
    if (!a) return;
    try {
      const resp = await a.request(`/api/encounters/${id}`, {
        method: 'PATCH',
        body: JSON.stringify({ label }),
      });
      if (!resp.ok) {
        toast('Error', 'Could not rename encounter.', 'error');
        await refreshActiveEncounterFromServer();
        return;
      }
      inp.dataset.aeSnapshot = label;
      window.dispatchEvent(new CustomEvent('encounters-refresh'));
    } catch (e) {
      console.warn(e);
      toast('Error', 'Could not rename encounter.', 'error');
      await refreshActiveEncounterFromServer();
    }
  }

  function setupActiveEncounterStripControls() {
    const delBtn = document.getElementById('aeStripDeleteBtn');
    const addBtn = document.getElementById('aeStripAddBtn');
    const prevBtn = document.getElementById('aeStripPrevBtn');
    const nextBtn = document.getElementById('aeStripNextBtn');
    if (delBtn && delBtn.dataset.aeBound !== '1') {
      delBtn.dataset.aeBound = '1';
      delBtn.addEventListener('click', () => {
        const inp = document.getElementById('activeEncounterNameInput');
        if (!inp || !inp.dataset.encounterId) {
          toast('No encounter', 'Nothing to delete.', 'warning');
          return;
        }
        deleteEncounter(inp.dataset.encounterId, inp.value || 'Encounter');
      });
    }
    if (addBtn && addBtn.dataset.aeBound !== '1') {
      addBtn.dataset.aeBound = '1';
      addBtn.addEventListener('click', () => {
        if (typeof window.encountersNewThread === 'function') {
          window.encountersNewThread();
        }
      });
    }
    if (prevBtn && prevBtn.dataset.aeBound !== '1') {
      prevBtn.dataset.aeBound = '1';
      prevBtn.addEventListener('click', () => navigateEncounterStripByDelta(-1));
    }
    if (nextBtn && nextBtn.dataset.aeBound !== '1') {
      nextBtn.dataset.aeBound = '1';
      nextBtn.addEventListener('click', () => navigateEncounterStripByDelta(1));
    }
  }

  function setupActiveEncounterNameInput() {
    const inp = document.getElementById('activeEncounterNameInput');
    if (!inp || inp.dataset.aeBound === '1') return;
    inp.dataset.aeBound = '1';
    inp.addEventListener('focus', () => {
      inp.dataset.aeSnapshot = String(inp.value || '');
    });
    inp.addEventListener('blur', () => {
      saveActiveEncounterNameIfChanged();
    });
    inp.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        inp.blur();
      }
    });
  }

  async function reloadWorkspaceAfterEncounterOp() {
    const a = auth();
    if (!a || !a.isWorkspaceReady()) return;
    const resp = await a.request('/api/workspace/');
    if (!resp.ok) return;
    const data = await resp.json();
    a.workspaceVersion = data.version;
    a.applyWorkspaceState(data.state || {}, { force: true });
    a.updateWorkspaceMeta();
    try {
      if (typeof window.syncProfileSpecialtyToBar === 'function') {
        await window.syncProfileSpecialtyToBar();
      }
    } catch (e) {}
    await refreshActiveEncounterFromServer();
  }

  window.openEncountersPanel = function () {
    const a = auth();
    if (!a || !a.isWorkspaceReady()) {
      toast('Sign in', 'Sign in to manage encounters.', 'warning');
      return;
    }
    if (busyGate()) return;
    const panel = document.getElementById('encountersPanel');
    const backdrop = document.getElementById('encountersBackdrop');
    if (panel) {
      panel.classList.add('open');
      panel.setAttribute('aria-hidden', 'false');
    }
    if (backdrop) {
      backdrop.classList.add('open');
      backdrop.setAttribute('aria-hidden', 'false');
    }
    refreshEncountersList();
  };

  /** Rail / primary control: open if closed, close if open (does not affect floating-bar open-only). */
  window.toggleEncountersPanel = function () {
    const panel = document.getElementById('encountersPanel');
    if (panel && panel.classList.contains('open')) {
      window.closeEncountersPanel();
      return;
    }
    window.openEncountersPanel();
  };

  window.closeEncountersPanel = function () {
    const panel = document.getElementById('encountersPanel');
    const backdrop = document.getElementById('encountersBackdrop');
    if (panel) {
      panel.classList.remove('open');
      panel.setAttribute('aria-hidden', 'true');
    }
    if (backdrop) {
      backdrop.classList.remove('open');
      backdrop.setAttribute('aria-hidden', 'true');
    }
  };

  async function refreshEncountersList() {
    const a = auth();
    const listEl = document.getElementById('encountersList');
    if (!a || !listEl || !a.isWorkspaceReady()) return;
    listEl.innerHTML = '<div class="encounters-loading">Loading…</div>';
    try {
      const resp = await a.request('/api/encounters/');
      if (!resp.ok) throw new Error('list failed');
      const data = await resp.json();
      const rows = Array.isArray(data.encounters) ? data.encounters : [];
      listEl.innerHTML = '';
      if (!rows.length) {
        const d = document.createElement('div');
        d.className = 'encounters-empty';
        d.textContent = 'No encounters yet.';
        listEl.appendChild(d);
        return;
      }
      rows.forEach((enc) => {
        listEl.appendChild(renderEncounterRow(enc));
      });
    } catch (e) {
      console.warn('[Encounters]', e);
      listEl.innerHTML = '';
      const d = document.createElement('div');
      d.className = 'encounters-error';
      d.textContent = 'Could not load encounters.';
      listEl.appendChild(d);
    }
  }

  function renderEncounterRow(enc) {
    const row = document.createElement('div');
    row.className = 'encounters-row' + (enc.is_active ? ' active' : '');
    row.dataset.id = String(enc.id);

    const title = document.createElement('div');
    title.className = 'encounters-row-title';
    title.textContent = enc.label || 'Encounter';

    const meta = document.createElement('div');
    meta.className = 'encounters-row-meta';
    if (enc.is_active) {
      meta.textContent = 'Active';
    } else if (enc.updated_at) {
      try {
        const t = new Date(enc.updated_at);
        meta.textContent = t.toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'short' });
      } catch (_) {
        meta.textContent = '';
      }
    }

    const actions = document.createElement('div');
    actions.className = 'encounters-row-actions';

    const openBtn = document.createElement('button');
    openBtn.type = 'button';
    openBtn.className = 'btn btn-sm';
    openBtn.textContent = enc.is_active ? 'Current' : 'Open';
    openBtn.disabled = !!enc.is_active;
    openBtn.onclick = (e) => {
      e.stopPropagation();
      activateEncounterId(enc.id);
    };

    const renBtn = document.createElement('button');
    renBtn.type = 'button';
    renBtn.className = 'btn btn-sm btn-secondary';
    renBtn.textContent = 'Rename';
    renBtn.onclick = (e) => {
      e.stopPropagation();
      renameEncounter(enc.id, enc.label);
    };

    const delBtn = document.createElement('button');
    delBtn.type = 'button';
    delBtn.className = 'btn btn-sm btn-danger';
    delBtn.textContent = 'Delete';
    delBtn.onclick = (e) => {
      e.stopPropagation();
      deleteEncounter(enc.id, enc.label);
    };

    actions.appendChild(openBtn);
    actions.appendChild(renBtn);
    actions.appendChild(delBtn);

    row.appendChild(title);
    row.appendChild(meta);
    row.appendChild(actions);

    row.onclick = () => {
      if (!enc.is_active) activateEncounterId(enc.id);
    };

    return row;
  }

  async function activateEncounterId(encounterId, opts) {
    if (busyGate()) return;
    const a = auth();
    if (!a) return;
    clearTimeout(a.saveTimer);
    await a.saveWorkspace();
    // Block switch if the save failed — do not risk losing edits.
    if (a.lastSaveFailed) {
      toast('Save failed', 'Could not save current encounter. Fix sync issues before switching.', 'error');
      return;
    }
    try {
      const resp = await a.request(`/api/encounters/${encounterId}/activate`, { method: 'POST' });
      if (!resp.ok) {
        toast('Error', 'Could not switch encounter.', 'error');
        return;
      }
      await reloadWorkspaceAfterEncounterOp();
      try {
        if (typeof window.loadQueue === 'function') {
          window.loadQueue().catch(() => {});
        } else if (typeof window.updateQueueDisplay === 'function') {
          window.updateQueueDisplay();
        }
      } catch (e) {}
      if (!opts || !opts.silent) {
        toast('Encounter', 'Switched active encounter.', 'success');
      }
      await refreshEncountersList();
      if (!opts || !opts.keepOpen) {
        if (typeof window.closeEncountersPanel === 'function') {
          window.closeEncountersPanel();
        }
      }
    } catch (e) {
      console.warn(e);
      toast('Error', 'Could not switch encounter.', 'error');
    }
  }

  window.encountersNewThread = async function () {
    if (busyGate()) return;
    const a = auth();
    if (!a) return;
    clearTimeout(a.saveTimer);
    await a.saveWorkspace();
    try {
      const resp = await a.request('/api/encounters/', {
        method: 'POST',
        body: JSON.stringify({ label: 'Encounter' }),
      });
      if (!resp.ok) {
        toast('Error', 'Could not create encounter.', 'error');
        return;
      }
      const created = await resp.json();
      const id = created.id;
      await activateEncounterId(id, { silent: true });
      toast('New encounter', 'Created and opened.', 'success');
    } catch (e) {
      console.warn(e);
      toast('Error', 'Could not create encounter.', 'error');
    }
  };

  async function renameEncounter(id, currentLabel) {
    if (busyGate()) return;
    const a = auth();
    if (!a) return;
    const next = prompt('Encounter name', currentLabel || 'Encounter');
    if (next === null) return;
    const label = String(next).trim();
    if (!label) {
      toast('Name required', 'Enter a non-empty label.', 'warning');
      return;
    }
    try {
      const resp = await a.request(`/api/encounters/${id}`, {
        method: 'PATCH',
        body: JSON.stringify({ label }),
      });
      if (!resp.ok) {
        toast('Error', 'Rename failed.', 'error');
        return;
      }
      await refreshEncountersList();
      await refreshActiveEncounterFromServer();
      toast('Saved', 'Encounter renamed.', 'success');
    } catch (e) {
      console.warn(e);
      toast('Error', 'Rename failed.', 'error');
    }
  }

  async function deleteEncounter(id, label) {
    if (busyGate()) return;
    const a = auth();
    if (!a) return;
    const ok = confirm(
      `Permanently delete "${label || 'Encounter'}"?\n\n` +
        'This removes the encounter and its queued jobs on the server. This cannot be undone.'
    );
    if (!ok) return;
    try {
      const resp = await a.request(`/api/encounters/${id}`, {
        method: 'DELETE',
        body: JSON.stringify({ confirm: true }),
      });
      if (!resp.ok) {
        let t = '';
        try {
          t = await resp.text();
        } catch (_) {}
        toast('Error', t || 'Delete failed.', 'error');
        return;
      }
      // Compliance: delete any locally persisted audio recovery for this (user, encounter).
      try {
        if (window.RecordingRecovery && typeof window.RecordingRecovery.deleteByEncounter === 'function') {
          await window.RecordingRecovery.deleteByEncounter({ encounterId: String(id) });
        }
      } catch (_) {}
      await reloadWorkspaceAfterEncounterOp();
      await refreshEncountersList();
      toast('Deleted', 'Encounter removed.', 'success');
    } catch (e) {
      console.warn(e);
      toast('Error', 'Delete failed.', 'error');
    }
  }

  window.closeCurrentEncounterContent = async function () {
    if (busyGate()) return;
    const a = auth();
    if (!a || typeof a.closeCurrentEncounter !== 'function') return;
    await a.closeCurrentEncounter();
    if (typeof window.closeEncountersPanel === 'function') {
      window.closeEncountersPanel();
    }
  };

  window.refreshEncountersList = refreshEncountersList;
  window.refreshActiveEncounterStrip = refreshActiveEncounterFromServer;

  document.addEventListener('DOMContentLoaded', () => {
    (function closeEncountersOnLoad() {
      const p = document.getElementById('encountersPanel');
      const b = document.getElementById('encountersBackdrop');
      if (p) p.classList.remove('open');
      if (b) b.classList.remove('open');
    })();
    setupActiveEncounterNameInput();
    setupActiveEncounterStripControls();
    const a = auth();
    if (a && typeof a.isWorkspaceReady === 'function' && a.isWorkspaceReady()) {
      refreshActiveEncounterFromServer();
    }
    const backdrop = document.getElementById('encountersBackdrop');
    if (backdrop) {
      backdrop.addEventListener('click', () => window.closeEncountersPanel());
    }
    document.addEventListener('keydown', (e) => {
      if (e.key !== 'Escape') return;
      const panel = document.getElementById('encountersPanel');
      if (panel && panel.classList.contains('open')) {
        e.preventDefault();
        window.closeEncountersPanel();
      }
    });
  });

  window.addEventListener('encounters-refresh', () => {
    refreshActiveEncounterFromServer();
    const panel = document.getElementById('encountersPanel');
    if (panel && panel.classList.contains('open')) refreshEncountersList();
  });
})();
