/* Progress Notes print UI — Print▾ menu + hospital sheet modal (AJ Code-39). */
(function () {
  'use strict';

  var DEFAULT_SERVICE = 'Internal Medicine - Respiratory';
  var metaCache = null;      // last print-meta payload for the active encounter
  var metaCacheEid = null;

  function eid() {
    return (window.app && window.app.activeEncounterId) ? String(window.app.activeEncounterId) : '';
  }

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  // ---------- dropdown ----------
  function ensureMenuCss() {
    if (document.getElementById('pnPrintStyle')) return;
    var st = document.createElement('style');
    st.id = 'pnPrintStyle';
    st.textContent = [
      '.pn-print-wrap{position:relative;display:flex;}',
      '#pnPrintMenuBtn{white-space:nowrap;flex:1 1 auto;min-width:max-content;}',
      '.pn-print-menu{position:absolute;top:calc(100% + 4px);right:0;z-index:60;',
      'background:#fff;border:1px solid #d0d4da;border-radius:8px;',
      'box-shadow:0 6px 18px rgba(0,0,0,.15);min-width:230px;max-width:calc(100vw - 24px);',
      'padding:4px;display:none;}',
      '.pn-print-menu.open{display:block;}',
      '.pn-print-menu button{display:block;width:100%;text-align:left;background:none;border:0;',
      'padding:8px 10px;font-size:13px;cursor:pointer;border-radius:6px;color:inherit;}',
      '.pn-print-menu button:hover{background:#eef2f7;}',
      '.pn-modal-field{margin-bottom:8px;}',
      /* #pnSheetModal: 'modal-overlay' has no app CSS — self-contain it */
      '#pnSheetModal{position:fixed;inset:0;z-index:1000;background:rgba(0,0,0,.45);',
      'display:flex;align-items:center;justify-content:center;padding:12px;}',
      '#pnSheetModal .modal-content{background:#fff;border-radius:12px;padding:16px;',
      'width:100%;max-width:560px;max-height:88vh;overflow:auto;box-shadow:0 10px 40px rgba(0,0,0,.3);',
      'color:#222;}',
      '.pn-modal-field label{display:block;font-size:11px;color:#666;margin-bottom:2px;}',
      '.pn-modal-field .pn-src{font-size:10px;color:#8a6d3b;margin-left:4px;}',
      '.pn-modal-grid{display:grid;grid-template-columns:1fr 1fr;gap:0 12px;}',
      '.pn-aj-cands{margin-top:2px;}.pn-aj-cands button{margin-right:6px;font-size:12px;}',
      '.pn-aj-warn{color:#b00020;font-size:12px;min-height:15px;}',
      /* match the row rules workspace.css applies to .note-header-actions children */
      /* the app hides .note-download-btn <=850px; printing must stay available on mobile */
      '@media (max-width:850px){',
      '#pnPrintMenuBtn{display:inline-flex !important;}',
      '}',
      '@media (max-width:640px){',
      '.note-header-actions .pn-print-wrap{width:calc(50% - 6px);flex:0 0 auto;}',
      '.pn-print-menu{min-width:200px;}',
      '}',
      '@media (max-width:480px){',
      '.pn-modal-grid{grid-template-columns:1fr;}',
      '#pnPrintMenuBtn{padding-left:8px;padding-right:8px;}',
      '}',
      '@media (max-width:380px){',
      '#pnPrintMenuBtn{font-size:0.72rem !important;padding:6px;}',
      '}'
    ].join('');
    document.head.appendChild(st);
  }

  function buildMenu() {
    ensureMenuCss();
    var anchor = document.querySelector('.note-download-btn:not(#pnPrintMenuBtn)');
    if (!anchor || anchor.closest('[data-pn-print]') || document.getElementById('pnPrintMenuBtn')) return;
    var title = anchor.getAttribute('title') || '';
    anchor.setAttribute('title', title ? title + ' (plain PDF)' : 'Download plain PDF');

    var wrap = document.createElement('span');
    wrap.className = 'pn-print-wrap';
    wrap.setAttribute('data-pn-print', '1');

    var btn = document.createElement('button');
    btn.type = 'button';
    btn.id = 'pnPrintMenuBtn';
    btn.className = 'btn btn-secondary note-download-btn';
    btn.title = 'Print options';
    btn.innerHTML = '\uD83D\uDDA8 Print \u25BE';
    btn.setAttribute('aria-haspopup', 'menu');

    var menu = document.createElement('div');
    menu.className = 'pn-print-menu';
    menu.setAttribute('role', 'menu');
    menu.innerHTML =
      '<button type="button" role="menuitem" data-act="plain">\uD83D\uDCC4 PDF (plain)</button>' +
      '<button type="button" role="menuitem" data-act="sheet">\uD83C\uDFE5 Hospital Progress Notes sheet</button>';

    btn.addEventListener('click', function (e) {
      e.stopPropagation();
      var open = menu.classList.toggle('open');
      btn.setAttribute('aria-expanded', open ? 'true' : 'false');
    });
    menu.addEventListener('click', function (e) {
      var act = e.target && e.target.getAttribute && e.target.getAttribute('data-act');
      if (!act) return;
      menu.classList.remove('open');
      if (act === 'plain') {
        if (typeof window.saveNote === 'function') window.saveNote();
      } else {
        openSheetModal();
      }
    });
    document.addEventListener('click', function () { menu.classList.remove('open'); });

    wrap.appendChild(btn);
    wrap.appendChild(menu);
    anchor.insertAdjacentElement('afterend', wrap);
  }

  // ---------- modal ----------
  function ensureModal() {
    if (document.getElementById('pnSheetModal')) return;
    var ov = document.createElement('div');
    ov.id = 'pnSheetModal';
    ov.className = 'modal-overlay';
    ov.style.display = 'none';
    ov.innerHTML =
      '<div class="modal-content" style="max-width:560px;max-height:88vh;overflow:auto;">' +
      '<h3 style="margin:0 0 4px;">Hospital Progress Notes sheet</h3>' +
      '<div style="font-size:12px;color:#666;margin-bottom:10px;">Permanent record sheet with Code-39 barcode. ' +
      'AJ registration number (e.g. AJ0001948/26) is <b>required</b>. Fields marked <i>(EMR)</i> were extracted ' +
      'from the chart paste — check before printing.</div>' +
      '<div class="pn-modal-field"><label>Registration # (AJ) *</label>' +
      '<input class="form-control" id="pnAj" placeholder="AJ0001948/26" autocomplete="off" spellcheck="false">' +
      '<div class="pn-aj-cands" id="pnAjCands"></div><div class="pn-aj-warn" id="pnAjWarn"></div></div>' +
      '<div class="pn-modal-grid">' +
      '<div class="pn-modal-field"><label>Last, First Name</label><input class="form-control" id="pnName"></div>' +
      '<div class="pn-modal-field"><label>DOB</label><input class="form-control" id="pnDob" placeholder="20 Jul 1942"></div>' +
      '<div class="pn-modal-field"><label>Sex</label><select class="form-control" id="pnSex"><option value=""></option><option>M</option><option>F</option></select></div>' +
      '<div class="pn-modal-field"><label>Age</label><input class="form-control" id="pnAge" placeholder="84Y"></div>' +
      '<div class="pn-modal-field"><label>MRN (YR#)</label><input class="form-control" id="pnMrn"></div>' +
      '<div class="pn-modal-field"><label>Health Card (UPI)</label><input class="form-control" id="pnUpi"></div>' +
      '<div class="pn-modal-field"><label>Ward</label><input class="form-control" id="pnWard"></div>' +
      '<div class="pn-modal-field"><label>Bed</label><input class="form-control" id="pnBed"></div>' +
      '<div class="pn-modal-field"><label>Service</label><input class="form-control" id="pnService"></div>' +
      '<div class="pn-modal-field"><label>Encounter date</label><input class="form-control" id="pnEnc" placeholder="2026-08-28"></div>' +
      '</div>' +
      '<div style="display:flex;gap:8px;justify-content:flex-end;margin-top:12px;">' +
      '<button type="button" class="btn btn-secondary" id="pnCancel">Cancel</button>' +
      '<button type="button" class="btn btn-success" id="pnPrintGo">Print sheet</button>' +
      '</div></div>';
    document.body.appendChild(ov);
    ov.addEventListener('click', function (e) { if (e.target === ov) closeModal(); });
    document.getElementById('pnCancel').addEventListener('click', closeModal);
    document.getElementById('pnPrintGo').addEventListener('click', submitPrint);
    document.getElementById('pnAj').addEventListener('input', updateAjWarn);
  }

  function srcTag(sources, field) {
    var s = (sources || {})[field];
    if (s === 'emr') return ' <span class="pn-src">(EMR)</span>';
    if (s === 'saved') return ' <span class="pn-src">(last print)</span>';
    if (s === 'note') return ' <span class="pn-src">(note)</span>';
    return '';
  }

  function fieldEl(id) { return document.getElementById(id); }

  function fill(meta) {
    var src = meta.sources || {};
    fieldEl('pnName').value = meta.patient_name || '';
    fieldEl('pnName').insertAdjacentHTML('afterend', '');
    fieldEl('pnDob').value = meta.dob || '';
    fieldEl('pnSex').value = meta.sex || '';
    fieldEl('pnAge').value = meta.age || '';
    fieldEl('pnMrn').value = meta.mrn || '';
    fieldEl('pnUpi').value = meta.upi || '';
    fieldEl('pnWard').value = meta.ward || meta.facility || '';
    fieldEl('pnBed').value = (meta.bed || (meta.saved && meta.saved.bed) || '');
    fieldEl('pnService').value = meta.service || DEFAULT_SERVICE;
    fieldEl('pnEnc').value = meta.enc_date || '';
    // AJ: candidates + confidence
    var aj = meta.aj || '';
    fieldEl('pnAj').value = aj;
    var cands = (meta.aj_candidates || []).filter(function (c) { return c !== aj; });
    var candsEl = fieldEl('pnAjCands');
    candsEl.innerHTML = cands.map(function (c) {
      return '<button type="button" class="btn btn-outline btn-sm" data-aj="' + esc(c) + '">' + esc(c) + '</button>';
    }).join('');
    Array.prototype.forEach.call(candsEl.querySelectorAll('button'), function (b) {
      b.addEventListener('click', function () { fieldEl('pnAj').value = b.getAttribute('data-aj'); updateAjWarn(); });
    });
    // provenance chips next to labels
    var labels = { pnName: 'patient_name', pnDob: 'dob', pnSex: 'sex', pnAge: 'age', pnMrn: 'mrn', pnService: 'service' };
    Object.keys(labels).forEach(function (id) {
      var el = fieldEl(id);
      var lab = el && el.parentElement && el.parentElement.querySelector('label');
      if (!lab) return;
      var old = lab.querySelector('.pn-src'); if (old) old.remove();
      lab.insertAdjacentHTML('beforeend', srcTag(src, labels[id]));
    });
    var conf = meta.aj_confidence || 'none';
    var warn = fieldEl('pnAjWarn');
    if (conf === 'high' && aj) warn.textContent = 'AJ matched chart row (high confidence).';
    else if (aj) warn.textContent = 'AJ extracted with ' + conf + ' confidence — verify against chart.';
    else warn.textContent = meta.has_draft === false ? 'No note draft yet — generate a note first.' : 'No AJ found — type the registration number.';
  }

  function updateAjWarn() {
    var v = fieldEl('pnAj').value.trim();
    var warn = fieldEl('pnAjWarn');
    if (!v) { warn.textContent = 'AJ number is required for the barcode.'; return; }
    if (!/^AJ[\s#-]?\d{3,}(\s?[/\-]\s?\d{1,4})?$/i.test(v)) {
      warn.textContent = 'Format: AJ#######/YY (e.g. AJ0001948/26)';
    } else {
      warn.textContent = '';
    }
  }

  function openSheetModal() {
    var id = eid();
    if (!id) { alert('No active encounter.'); return; }
    ensureModal();
    var ov = document.getElementById('pnSheetModal');
    ov.style.display = 'flex';
    fieldEl('pnPrintGo').disabled = false;
    fieldEl('pnPrintGo').textContent = 'Print sheet';
    if (metaCacheEid !== id) { metaCache = null; }
    if (metaCache) { fill(metaCache); return; }
    fieldEl('pnAjWarn').textContent = 'Loading chart fields\u2026';
    window.authFetch('/api/encounters/' + id + '/print-meta')
      .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
      .then(function (m) { metaCache = m; metaCacheEid = id; fill(m); })
      .catch(function (e) { fieldEl('pnAjWarn').textContent = 'Could not load chart fields (' + e.message + ') — fill manually.'; });
  }

  function closeModal() {
    var ov = document.getElementById('pnSheetModal');
    if (ov) ov.style.display = 'none';
  }

  function submitPrint() {
    var id = eid();
    if (!id) return;
    var aj = fieldEl('pnAj').value.trim();
    if (!aj) { updateAjWarn(); return; }
    var body = {
      aj: aj,
      patient_name: fieldEl('pnName').value.trim(),
      dob: fieldEl('pnDob').value.trim(),
      sex: fieldEl('pnSex').value,
      age: fieldEl('pnAge').value.trim(),
      mrn: fieldEl('pnMrn').value.trim(),
      upi: fieldEl('pnUpi').value.trim(),
      ward: fieldEl('pnWard').value.trim(),
      bed: fieldEl('pnBed').value.trim(),
      service: fieldEl('pnService').value.trim(),
      enc_date: fieldEl('pnEnc').value.trim()
    };
    var go = fieldEl('pnPrintGo');
    go.disabled = true;
    go.textContent = 'Rendering\u2026';
    window.authFetch('/api/encounters/' + id + '/print-progress-note', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    }).then(function (r) {
      if (!r.ok) {
        return r.text().then(function (t) {
          var msg = t;
          try { msg = (JSON.parse(t) || {}).detail || t; } catch (e) {}
          throw new Error(String(msg).slice(0, 200));
        });
      }
      return r.blob();
    }).then(function (blob) {
      var url = URL.createObjectURL(blob);
      var a = document.createElement('a');
      var last = (body.patient_name.split(',')[0] || 'Note').trim().split(/\s+/).pop() || 'Note';
      a.href = url;
      a.download = last + '_' + aj.replace(/[^A-Za-z0-9]+/g, '_') + '.pdf';
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(function () { URL.revokeObjectURL(url); }, 30000);
      closeModal();
    }).catch(function (e) {
      go.disabled = false;
      go.textContent = 'Print sheet';
      fieldEl('pnAjWarn').textContent = 'Print failed: ' + e.message;
    });
  }

  // stale cache when encounter switches
  function invalidateOnEncounterSwitch() {
    var t = setInterval(function () {
      var id = eid();
      if (id !== metaCacheEid) { metaCache = null; }
    }, 2000);
    document.addEventListener('visibilitychange', function () { if (!document.hidden) { metaCache = null; } });
    window.addEventListener('beforeunload', function () { clearInterval(t); });
  }

  function init() {
    buildMenu();
    invalidateOnEncounterSwitch();
    // menu anchor may not exist yet (dynamic render) — retry a few times
    var tries = 0;
    var t = setInterval(function () {
      tries++;
      buildMenu();
      if (document.querySelector('[data-pn-print]') || tries > 20) clearInterval(t);
    }, 500);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () { setTimeout(init, 300); });
  } else {
    setTimeout(init, 300);
  }

  window.PnPrintUI = { open: openSheetModal, refresh: function () { metaCache = null; } };
})();
