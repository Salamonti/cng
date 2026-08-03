// P8a: Medical literature summaries from GET /api/rag/recent_updates
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

  function esc(s) {
    return String(s || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  window.openLiteraturePanel = function () {
    const a = auth();
    if (!a || !a.isWorkspaceReady()) {
      toast('Sign in', 'Sign in to view literature updates.', 'warning');
      return;
    }
    const panel = document.getElementById('literaturePanel');
    const backdrop = document.getElementById('literatureBackdrop');
    if (panel) {
      panel.classList.add('open');
      panel.setAttribute('aria-hidden', 'false');
    }
    if (backdrop) {
      backdrop.classList.add('open');
      backdrop.setAttribute('aria-hidden', 'false');
    }
    refreshLiteratureList();
  };

  window.toggleLiteraturePanel = function () {
    const panel = document.getElementById('literaturePanel');
    if (panel && panel.classList.contains('open')) {
      window.closeLiteraturePanel();
      return;
    }
    window.openLiteraturePanel();
  };

  window.closeLiteraturePanel = function () {
    const panel = document.getElementById('literaturePanel');
    const backdrop = document.getElementById('literatureBackdrop');
    if (panel) {
      panel.classList.remove('open');
      panel.setAttribute('aria-hidden', 'true');
    }
    if (backdrop) {
      backdrop.classList.remove('open');
      backdrop.setAttribute('aria-hidden', 'true');
    }
  };

  window.refreshLiteratureList = async function () {
    const a = auth();
    const listEl = document.getElementById('literatureList');
    const metaEl = document.getElementById('literatureMeta');
    if (!a || !listEl || !a.isWorkspaceReady()) return;
    listEl.innerHTML = '<div class="literature-loading">Loading…</div>';
    if (metaEl) metaEl.textContent = '';
    try {
      const resp = await a.request('/api/rag/recent_updates');
      if (!resp.ok) {
        let detail = 'Unable to load summaries.';
        try {
          const err = await resp.json();
          detail = err.detail || err.message || detail;
        } catch (_) {
          detail = (await resp.text()).slice(0, 200) || detail;
        }
        listEl.innerHTML =
          '<div class="literature-error"><p><strong>Literature cache unavailable</strong></p>' +
          '<p class="small">' +
          esc(detail) +
          '</p><p class="small text-muted">Run the weekly RAG ingest and <code>summarize_recent_updates.py</code> on the server, or ask your operator.</p></div>';
        return;
      }
      const data = await resp.json();
      const docs = Array.isArray(data.documents) ? data.documents : [];
      if (metaEl && data.generated_at) {
        try {
          const t = new Date(data.generated_at);
          let line = 'Updated ' + t.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
          if (data.fallback === 'ingest_snippets') {
            line += ' · Snippets from last weekly ingest (run RAG/summarize_recent_updates.py for AI digests)';
          } else if (data.fallback === 'empty') {
            line += ' · No ingest data found';
          }
          if (data.stale) {
            line += ' · LLM cache older than 7 days';
          }
          metaEl.textContent = line;
        } catch (_) {
          metaEl.textContent = '';
        }
      }
      if (!docs.length) {
        listEl.innerHTML = '<div class="literature-empty">No document summaries in this run.</div>';
        return;
      }
      listEl.innerHTML = '';
      docs.forEach(function (d) {
        listEl.appendChild(renderDoc(d));
      });
    } catch (e) {
      console.warn('[Literature]', e);
      listEl.innerHTML =
        '<div class="literature-error">Could not load literature updates. Check connection and try Refresh.</div>';
    }
  };

  function renderDoc(d) {
    const row = document.createElement('div');
    row.className = 'literature-row';

    const head = document.createElement('button');
    head.type = 'button';
    head.className = 'literature-row-head';
    const chev = document.createElement('span');
    chev.className = 'literature-row-chev';
    chev.setAttribute('aria-hidden', 'true');
    chev.textContent = '\u25B8';

    const title = document.createElement('div');
    title.className = 'literature-row-title';
    title.textContent = d.title || 'Untitled';

    const src = document.createElement('div');
    src.className = 'literature-row-meta';
    src.textContent = d.source ? String(d.source) : '';

    head.appendChild(chev);
    head.appendChild(title);
    head.appendChild(src);

    const details = document.createElement('div');
    details.className = 'literature-row-details';
    details.hidden = true;

    const body = document.createElement('div');
    body.className = 'literature-row-body';
    body.textContent = d.summary || '';
    details.appendChild(body);

    if (d.link) {
      const a = document.createElement('a');
      a.className = 'literature-row-link';
      a.href = String(d.link);
      a.target = '_blank';
      a.rel = 'noopener noreferrer';
      a.textContent = 'Open source link';
      details.appendChild(a);
    }

    head.addEventListener('click', function () {
      const open = details.hidden;
      details.hidden = !open;
      row.classList.toggle('literature-row-open', open);
      chev.textContent = open ? '\u25BE' : '\u25B8';
      head.setAttribute('aria-expanded', open ? 'true' : 'false');
    });
    head.setAttribute('aria-expanded', 'false');

    row.appendChild(head);
    row.appendChild(details);
    return row;
  }

  document.addEventListener('DOMContentLoaded', function () {
    const backdrop = document.getElementById('literatureBackdrop');
    if (backdrop) {
      backdrop.addEventListener('click', function () {
        window.closeLiteraturePanel();
      });
    }
    document.addEventListener('keydown', function (e) {
      if (e.key !== 'Escape') return;
      const panel = document.getElementById('literaturePanel');
      if (panel && panel.classList.contains('open')) {
        e.preventDefault();
        window.closeLiteraturePanel();
      }
    });
  });
})();
