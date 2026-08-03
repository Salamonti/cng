/**
 * Diarized transcript view — raw Doctor:/Patient:/Unknown: text stays in #transcriptionDisplay.
 * Read-only UI: one row per turn with small emoji badges + inline styles (no CSS dependency).
 */
(function (global) {
  var SPEAKERS = {
    doctor: {
      icon: '\uD83E\uDE7A',
      label: 'Doctor',
      badge: 'display:inline-flex;align-items:center;justify-content:center;flex:0 0 auto;width:1.35rem;height:1.35rem;margin-top:0.1rem;border-radius:0.3rem;background:#dbeafe;font-size:0.82rem;line-height:1;',
    },
    patient: {
      icon: '\uD83D\uDCAC',
      label: 'Patient',
      badge: 'display:inline-flex;align-items:center;justify-content:center;flex:0 0 auto;width:1.35rem;height:1.35rem;margin-top:0.1rem;border-radius:0.3rem;background:#d1fae5;font-size:0.82rem;line-height:1;',
    },
    unknown: {
      icon: '\u2753',
      label: 'Other',
      badge: 'display:inline-flex;align-items:center;justify-content:center;flex:0 0 auto;width:1.35rem;height:1.35rem;margin-top:0.1rem;border-radius:0.3rem;background:#e7e5e4;font-size:0.82rem;line-height:1;',
    },
  };

  function looksDiarized(text) {
    return /(?:^|\n|\s)(Doctor|Patient|Unknown):/i.test(String(text || ''));
  }

  function parseDiarizedTurns(text) {
    var raw = String(text || '').trim();
    if (!raw || !looksDiarized(raw)) return null;

    var parts = raw.split(/(?=(?:Doctor|Patient|Unknown):)/i).map(function (p) {
      return String(p || '').trim();
    }).filter(Boolean);

    var turns = [];
    parts.forEach(function (part) {
      var m = /^(Doctor|Patient|Unknown):\s*(.*)$/is.exec(part);
      if (!m) return;
      var roleKey = m[1].toLowerCase();
      var meta = SPEAKERS[roleKey] || SPEAKERS.unknown;
      var body = String(m[2] || '').replace(/\s+/g, ' ').trim();
      if (!body) return;
      turns.push({ role: roleKey, icon: meta.icon, label: meta.label, badgeStyle: meta.badge, text: body });
    });
    return turns.length ? turns : null;
  }

  function renderTurns(turns) {
    var wrap = document.createElement('div');
    wrap.style.cssText = 'display:flex;flex-direction:column;gap:0.45rem;';

    turns.forEach(function (turn) {
      var row = document.createElement('div');
      row.style.cssText = 'display:flex;align-items:flex-start;gap:0.55rem;line-height:1.45;';

      var badge = document.createElement('span');
      badge.setAttribute('aria-label', turn.label);
      badge.title = turn.label;
      badge.style.cssText = turn.badgeStyle;
      badge.textContent = turn.icon;

      var txt = document.createElement('span');
      txt.style.cssText = 'flex:1 1 auto;min-width:0;color:inherit;';
      txt.textContent = turn.text;

      row.appendChild(badge);
      row.appendChild(txt);
      wrap.appendChild(row);
    });
    return wrap;
  }

  function els() {
    return {
      tx: document.getElementById('transcriptionDisplay'),
      view: document.getElementById('transcriptionDisplayView'),
      shell: document.getElementById('transcriptionDisplayShell'),
    };
  }

  function syncView() {
    var e = els();
    if (!e.tx || !e.view || !e.shell) return;

    var editing = !e.tx.hasAttribute('readonly') || e.tx.classList.contains('is-editable');
    var text = String(e.tx.value || '');
    var turns = !editing ? parseDiarizedTurns(text) : null;

    // Capture the textarea's current computed height so the view can match it.
    var txHeight = e.tx.offsetHeight;

    if (turns) {
      e.view.replaceChildren(renderTurns(turns));
      e.view.classList.remove('hidden');
      e.shell.classList.add('is-diarized-view');
      e.tx.style.display = 'none';
      e.tx.setAttribute('aria-hidden', 'true');
      e.view.setAttribute('role', 'textbox');
      e.view.setAttribute('aria-readonly', 'true');
      e.view.setAttribute('aria-label', 'Transcription with speaker labels');
      // Set a stable height ONCE when first entering the diarized view, then never
      // re-apply it. Re-applying on every transcript update both caused the 'jump'
      // (hidden textarea measured 0) and stomped the user's manual drag-resize.
      // Setting it once lets the box keep whatever size the user drags it to.
      if (!e.view.style.height) {
        e.view.style.height = (txHeight > 0 ? txHeight : 180) + 'px';
      }
    } else {
      e.view.replaceChildren();
      e.view.classList.add('hidden');
      e.shell.classList.remove('is-diarized-view');
      e.tx.style.display = '';
      e.tx.removeAttribute('aria-hidden');
      e.view.removeAttribute('role');
      e.view.removeAttribute('aria-readonly');
      e.view.removeAttribute('aria-label');
      e.view.style.maxHeight = '';
    }
  }

  function installValueHook() {
    var e = els();
    if (!e.tx || e.tx.__asrViewHooked) return;
    e.tx.__asrViewHooked = true;

    var desc = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value');
    if (!desc || !desc.set) {
      e.tx.addEventListener('input', syncView);
      syncView();
      return;
    }

    Object.defineProperty(e.tx, 'value', {
      get: function () { return desc.get.call(this); },
      set: function (v) {
        desc.set.call(this, v);
        syncView();
      },
      configurable: true,
    });
    syncView();
  }

  function setEditMode(editing) {
    var e = els();
    if (!e.shell) return;
    e.shell.classList.toggle('is-editing', !!editing);
    syncView();
  }

  function init() {
    installValueHook();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  global.AsrTranscriptView = {
    looksDiarized: looksDiarized,
    parseDiarizedTurns: parseDiarizedTurns,
    syncView: syncView,
    setEditMode: setEditMode,
    init: init,
  };
})(typeof window !== 'undefined' ? window : globalThis);
