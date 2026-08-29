window.WORKSPACE_PAGE_TYPE = 'main';

        /** API root for fetch(): ``/api`` (PCHost proxy) or absolute URL when AuthWorkspace targets FastAPI directly. */
        function getApiBase(serverUrl) {
            try {
                if (window.AuthWorkspace && window.AuthWorkspace.apiBase &&
                    window.AuthWorkspace.apiBase !== '/api') {
                    return String(window.AuthWorkspace.apiBase).replace(/\/+$/, '');
                }
            } catch (e) {}
            // (a) getApiBase: guarded property/storage read for the server URL; a
            // hiccup here falls through to the local default below (never fatal).
            const raw = (serverUrl != null && String(serverUrl).trim() !== '') ? String(serverUrl).trim() : '/api';
            if (raw.startsWith('http://') || raw.startsWith('https://')) {
                return raw.replace(/\/+$/, '');
            }
            return raw.startsWith('/') ? raw.replace(/\/+$/, '') : '/api';
        }

        function apiUrl(path) {
            const base = getApiBase((window.app && window.app.settings && window.app.settings.serverUrl) || '/api');
            const suffix = String(path || '');
            return `${base}${suffix.startsWith('/') ? suffix : `/${suffix}`}`;
        }

        /** Authenticated fetch via AuthWorkspace.authFetch (G3). Path is /api/... or short /queue/... */
        async function apiFetch(relativePath, options = {}) {
            let path = String(relativePath || '');
            if (path.startsWith('http://') || path.startsWith('https://')) {
                if (window.authFetch) {
                    return window.authFetch(path, options);
                }
                return fetch(path, { credentials: 'include', ...options });
            }
            if (!path.startsWith('/api')) {
                path = path.startsWith('/') ? `/api${path}` : `/api/${path}`;
            }
            if (typeof window.authFetch === 'function') {
                return window.authFetch(path, options);
            }
            const url = apiUrl(path.replace(/^\/api/, ''));
            const headers = { ...(options.headers || {}) };
            const token = (window.AuthWorkspace && window.AuthWorkspace.accessToken) || '';
            if (token && !headers.Authorization) {
                headers.Authorization = `Bearer ${token}`;
            }
            return fetch(url, { credentials: 'include', ...options, headers });
        }

        // G3: single canonical API client — do NOT redefine in settings_connection.js or other modules.
        window.apiFetch = apiFetch;
        window.getApiBase = getApiBase;
        function safeStr(val) {
            if (val === null || val === undefined) return '';
            return String(val);
        }

        function cleanMarkdownFences(text) {
            if (!text) return text;

            // Remove markdown code fences
            text = text.replace(/^```[\w]*\n/gm, '');
            text = text.replace(/\n```$/gm, '');
            text = text.replace(/```text\n/g, '');
            text = text.replace(/```\n/g, '');
            text = text.replace(/\n```/g, '');
            text = text.replace(/---/g, '');

            // Remove XML/HTML-like wrapper tags at start and end
            text = text.replace(/^<[\w_-]+>\s*/m, '');
            text = text.replace(/\s*<\/[\w_-]+>$/m, '');

            // Remove any standalone tags at very beginning or end
            text = text.replace(/^<[^>]+>\s*/m, '');
            text = text.replace(/\s*<\/[^>]+>$/m, '');

            return text.trim();
        }

        function stripBracketArtifacts(text) {
            if (!text) return '';
            const lines = text
                .split('\n')
                .filter(line => !/^[\s\[\]\{\}]+$/.test(line.trim()));
            let cleaned = lines.join('\n');
            cleaned = cleaned.replace(/^[\s\[\]\{\}]+/, '').replace(/[\s\[\]\{\}]+$/, '');
            return cleaned.trim();
        }

        /** Line that is only a Conflicts heading (markdown # / ** / stray * allowed). */
        function isConflictsHeadingLine(line) {
            let t = String(line || '').trim();
            t = t.replace(/^#+\s*/, '');
            t = t.replace(/\*+/g, '').trim();
            // Model variance: "Conflicts", "Conflicts Section:", "Conflict section:", "conflicts section info"
            return /^conflicts?(?:\s+section)?(?:\s+info)?\s*:?\s*$/i.test(t);
        }

        function extractConflictsSection(fullText) {
            const text = String(fullText || '').replace(/\r\n/g, '\n');
            const lines = text.split('\n');
            let startIdx = -1;
            for (let i = 0; i < lines.length; i++) {
                if (isConflictsHeadingLine(lines[i])) {
                    startIdx = i;
                    break;
                }
            }
            if (startIdx < 0) {
                return { main: text, conflicts: '' };
            }
            const mainLines = lines.slice(0, startIdx);
            const conflictLines = lines.slice(startIdx + 1);
            const main = mainLines.join('\n').replace(/\s+$/, '');
            const conflicts = conflictLines.join('\n').replace(/^\s+/, '').replace(/\s+$/, '');
            return { main, conflicts };
        }

        function mergeGeneratedNoteParts(main, conflicts) {
            const m = String(main || '').replace(/\s+$/, '');
            const c = String(conflicts || '').trim();
            if (!c) return m;
            if (!m) return 'Conflicts\n\n' + c;
            return m + '\n\nConflicts\n\n' + c;
        }

        function setConflictsPanelMarkdown(raw) {
            const body = document.getElementById('noteConflictsBody');
            if (!body) return;
            const text = String(raw || '').trim();
            try {
                delete body.dataset.rawConflicts;
            } catch (e) {}
            if (!text) {
                body.innerHTML = '';
                return;
            }
            body.dataset.rawConflicts = text;
            if (window.CNGMarkdown && typeof window.CNGMarkdown.renderMarkdownSimple === 'function') {
                body.innerHTML = window.CNGMarkdown.renderMarkdownSimple(text);
            } else {
                body.textContent = text;
            }
        }

        function getMergedGeneratedNoteText() {
            const ta = document.getElementById('generatedNote');
            const body = document.getElementById('noteConflictsBody');
            const main = ta ? ta.value : '';
            let conf = '';
            if (body) {
                if (body.dataset && body.dataset.rawConflicts !== undefined && body.dataset.rawConflicts !== '') {
                    conf = String(body.dataset.rawConflicts);
                } else {
                    // Fallback read: clone and exclude the display-only
                    // salvage banner so it can never leak into the saved /
                    // copied note text (it is a UI aid, not note content).
                    const clone = body.cloneNode(true);
                    const banner = clone.querySelector ? clone.querySelector('.note-salvage-banner') : null;
                    if (banner) banner.remove();
                    conf = (clone.textContent || '').trim();
                }
            }
            return mergeGeneratedNoteParts(main, conf);
        }

        /** Main note only (no Conflicts panel / no merged conflicts) — for copy-to-clipboard. */
        function getGeneratedNoteMainText() {
            const ta = document.getElementById('generatedNote');
            const raw = ta ? ta.value : '';
            const { main } = extractConflictsSection(raw);
            return main;
        }

        function applyConflictsSplitFromFullNote(fullText) {
            const ta = document.getElementById('generatedNote');
            const wrap = document.getElementById('noteConflictsCard');
            const body = document.getElementById('noteConflictsBody');
            if (!ta || !wrap || !body) return;
            const { main, conflicts } = extractConflictsSection(fullText);
            ta.value = main;
            setConflictsPanelMarkdown(conflicts || '');
            if ((conflicts || '').trim()) {
                wrap.classList.remove('hidden');
                wrap.setAttribute('aria-hidden', 'false');
            } else {
                wrap.classList.add('hidden');
                wrap.setAttribute('aria-hidden', 'true');
            }
            syncGeneratedNotePreview();
            syncGeneratedNoteView();
            // (a) Best-effort persist after a conflicts-split apply; storage failure
            // must not break the already-applied note text in the DOM.
            try {
                if (typeof saveToStorage === 'function') saveToStorage();
            } catch (e) {}
        }

        function clearConflictsPanel() {
            const wrap = document.getElementById('noteConflictsCard');
            const body = document.getElementById('noteConflictsBody');
            if (body) {
                try {
                    delete body.dataset.rawConflicts;
                } catch (e) {}
                // Drop any salvage banner too (it is display-only and never
                // part of the saved note text).
                const salvage = body.querySelector ? body.querySelector('.note-salvage-banner') : null;
                if (salvage) salvage.remove();
                body.innerHTML = '';
            }
            if (wrap) {
                wrap.classList.add('hidden');
                wrap.setAttribute('aria-hidden', 'true');
            }
        }

        /**
         * Salvage banner: when BOTH generation attempts fail auto-validation,
         * the draft is retained in the note box and its rejection reasons are
         * explained HERE — in the separate Conflicts panel, in an amber block,
         * never inside the copyable note box. The clinician reads the note,
         * sees exactly what the auto-check flagged, and can correct that part.
         * Display-only: DOM node, not part of dataset.rawConflicts, so it is
         * never copied to the clipboard and never saved with the note.
         */
        function renderSalvageBanner(reasons) {
            const wrap = document.getElementById('noteConflictsCard');
            const body = document.getElementById('noteConflictsBody');
            if (!body) return;
            const old = body.querySelector ? body.querySelector('.note-salvage-banner') : null;
            if (old && old.parentNode) old.parentNode.removeChild(old);

            const banner = document.createElement('div');
            banner.className = 'note-salvage-banner';
            banner.setAttribute('role', 'alert');

            const title = document.createElement('div');
            title.className = 'note-salvage-title';
            title.textContent = '⚠ Auto-validation failed (2 of 2 attempts) — review before use';
            banner.appendChild(title);

            const list = document.createElement('ul');
            list.className = 'note-salvage-reasons';
            (Array.isArray(reasons) ? reasons : []).forEach(function (r) {
                const li = document.createElement('li');
                li.textContent = String(r || '').trim() || 'unspecified';
                list.appendChild(li);
            });
            if (list.children.length === 0) {
                const li = document.createElement('li');
                li.textContent = 'no specific reason captured';
                list.appendChild(li);
            }
            banner.appendChild(list);

            const hint = document.createElement('div');
            hint.className = 'note-salvage-hint';
            hint.textContent = 'The draft is kept in the note box for you to review and correct. Press Generate to try a fresh draft instead.';
            banner.appendChild(hint);

            body.insertBefore(banner, body.firstChild);

            if (wrap) {
                wrap.classList.remove('hidden');
                wrap.setAttribute('aria-hidden', 'false');
            }
        }
        window.renderSalvageBanner = renderSalvageBanner;

        /** --- "section preview" disabled: it mirrored the note and broke layout; Conflicts uses a separate panel. */
        let generatedNotePreviewRaf = 0;
        function syncGeneratedNotePreview() {
            const wrap = document.getElementById('generatedNotePreviewWrap');
            const inner = document.getElementById('generatedNotePreview');
            if (inner) inner.innerHTML = '';
            if (wrap) {
                wrap.classList.add('hidden');
                wrap.setAttribute('aria-hidden', 'true');
            }
        }

        function requestGeneratedNotePreviewSync() {
            if (generatedNotePreviewRaf) return;
            generatedNotePreviewRaf = requestAnimationFrame(() => {
                generatedNotePreviewRaf = 0;
                syncGeneratedNotePreview();
                syncGeneratedNoteView();
            });
        }

        function stripNoteStreamMarkers(text) {
            return String(text || '')
                .replace(/__STREAM_END__/g, '')
                .replace(/\r\n/g, '\n');
        }

        function syncGeneratedNoteView() {
            const ta = document.getElementById('generatedNote');
            const view = document.getElementById('generatedNoteView');
            if (!view) return;
            const shell = document.querySelector('.generated-note-preview-shell');
            if (shell && shell.classList.contains('is-editing')) {
                return;
            }
            const raw = ta ? String(ta.value || '') : '';
            const streaming = !!(ta && ta.dataset.busy === '1');
            const prevTop = view.scrollTop;
            const prevClient = view.clientHeight;
            const prevScrollH = view.scrollHeight;

            if (!raw.trim()) {
                view.innerHTML = '<p class="generated-note-placeholder">Generated note will appear here…</p>';
                requestAnimationFrame(() => {
                    view.scrollTop = 0;
                });
                return;
            }
            if (window.CNGMarkdown && typeof window.CNGMarkdown.renderMarkdownSimple === 'function') {
                view.innerHTML = window.CNGMarkdown.renderMarkdownSimple(raw);
            } else {
                const p = document.createElement('p');
                p.textContent = raw;
                view.textContent = '';
                view.appendChild(p);
            }
            requestAnimationFrame(() => {
                if (streaming) {
                    view.scrollTop = view.scrollHeight;
                    return;
                }
                const maxScroll = Math.max(0, view.scrollHeight - view.clientHeight);
                if (maxScroll <= 0) return;
                const denom = Math.max(1, prevScrollH - prevClient);
                const ratio = prevTop / denom;
                view.scrollTop = Math.min(maxScroll, ratio * maxScroll);
            });
        }
        window.syncGeneratedNoteView = syncGeneratedNoteView;

        function applyGeneratedNoteMarkdownFormat(kind) {
            const ta = document.getElementById('generatedNote');
            if (!ta || ta.readOnly) return;

            const start = ta.selectionStart;
            const end = ta.selectionEnd;
            const val = ta.value;
            const sel = val.slice(start, end);

            function commit(newVal, selStart, selEnd) {
                ta.value = newVal;
                ta.selectionStart = selStart;
                ta.selectionEnd = selEnd;
                ta.dispatchEvent(new Event('input', { bubbles: true }));
                updateGeneratedNoteEmptyState();
                try {
                    ta.focus();
                } catch (_) {}
            }

            switch (kind) {
                case 'bold': {
                    if (sel) {
                        const wrapped = '**' + sel + '**';
                        commit(val.slice(0, start) + wrapped + val.slice(end), start + 2, start + 2 + sel.length);
                    } else {
                        const ins = '**text**';
                        commit(val.slice(0, start) + ins + val.slice(end), start + 2, start + 6);
                    }
                    break;
                }
                case 'italic': {
                    if (sel) {
                        const wrapped = '_' + sel + '_';
                        commit(val.slice(0, start) + wrapped + val.slice(end), start + 1, start + 1 + sel.length);
                    } else {
                        const ins = '_text_';
                        commit(val.slice(0, start) + ins + val.slice(end), start + 1, start + 5);
                    }
                    break;
                }
                case 'h2': {
                    const lineStart = val.lastIndexOf('\n', start - 1) + 1;
                    const nextNl = val.indexOf('\n', start);
                    const lineEnd = nextNl === -1 ? val.length : nextNl;
                    let line = val.slice(lineStart, lineEnd);
                    if (/^#{1,6}\s/.test(line)) {
                        line = line.replace(/^#{1,6}\s/, '## ');
                    } else {
                        line = '## ' + line;
                    }
                    commit(val.slice(0, lineStart) + line + val.slice(lineEnd), lineStart + line.length, lineStart + line.length);
                    break;
                }
                case 'bullet': {
                    if (sel) {
                        const lines = sel.split('\n');
                        const bulleted = lines
                            .map((ln) => {
                                if (!ln.trim()) return ln;
                                return /^[-*]\s/.test(ln.trim()) ? ln : '- ' + ln.replace(/^\s*/, '');
                            })
                            .join('\n');
                        commit(val.slice(0, start) + bulleted + val.slice(end), start, start + bulleted.length);
                    } else {
                        const ins = '- ';
                        commit(val.slice(0, start) + ins + val.slice(end), start + ins.length, start + ins.length);
                    }
                    break;
                }
                case 'numbered': {
                    if (sel) {
                        const lines = sel.split('\n');
                        let n = 1;
                        const numbered = lines
                            .map((ln) => {
                                if (!ln.trim()) return ln;
                                if (/^\d+\.\s/.test(ln.trim())) return ln;
                                return `${n++}. ${ln.replace(/^\s*/, '')}`;
                            })
                            .join('\n');
                        commit(val.slice(0, start) + numbered + val.slice(end), start, start + numbered.length);
                    } else {
                        const ins = '1. ';
                        commit(val.slice(0, start) + ins + val.slice(end), start + ins.length, start + ins.length);
                    }
                    break;
                }
                case 'hr': {
                    const needsLead = start > 0 && val[start - 1] !== '\n';
                    const ins = (needsLead ? '\n\n' : '') + '---\n';
                    const pos = start + ins.length;
                    commit(val.slice(0, start) + ins + val.slice(end), pos, pos);
                    break;
                }
                default:
                    break;
            }
        }
        window.applyGeneratedNoteMarkdownFormat = applyGeneratedNoteMarkdownFormat;

        function initNoteFormatToolbar() {
            const tb = document.querySelector('.note-format-toolbar');
            if (!tb || tb.dataset.bound === '1') return;
            tb.dataset.bound = '1';
            tb.addEventListener('click', (e) => {
                const btn = e.target.closest('[data-md]');
                if (!btn || !tb.contains(btn)) return;
                e.preventDefault();
                const k = btn.getAttribute('data-md');
                if (k) applyGeneratedNoteMarkdownFormat(k);
            });
        }

        function resetGeneratedNoteEditToggleToPreview() {
            const shell = document.querySelector('.generated-note-preview-shell');
            const btn = document.getElementById('generatedNoteEditToggleBtn');
            const pane = document.getElementById('generatedNoteEditPane');
            if (shell) shell.classList.remove('is-editing');
            if (pane) pane.setAttribute('aria-hidden', 'true');
            if (btn) {
                btn.textContent = 'Edit';
                btn.setAttribute('aria-pressed', 'false');
                btn.title = 'Edit note text';
            }
        }

        function updateGeneratedNoteEditToggleDisabled() {
            const ta = document.getElementById('generatedNote');
            const btn = document.getElementById('generatedNoteEditToggleBtn');
            if (!btn) return;
            const busy = !!(ta && ta.dataset.busy === '1');
            btn.disabled = busy;
        }

        function setGeneratedNoteEditMode(editing) {
            const shell = document.querySelector('.generated-note-preview-shell');
            const btn = document.getElementById('generatedNoteEditToggleBtn');
            const pane = document.getElementById('generatedNoteEditPane');
            const ta = document.getElementById('generatedNote');
            if (!shell || !btn || !pane) return;

            if (editing) {
                if (ta && ta.dataset.busy === '1') return;
                shell.classList.add('is-editing');
                pane.setAttribute('aria-hidden', 'false');
                btn.textContent = 'Save changes';
                btn.setAttribute('aria-pressed', 'true');
                btn.title = 'Save and return to preview';
                try {
                    if (ta) ta.focus();
                } catch (_) {}
            } else {
                shell.classList.remove('is-editing');
                pane.setAttribute('aria-hidden', 'true');
                btn.textContent = 'Edit';
                btn.setAttribute('aria-pressed', 'false');
                btn.title = 'Edit note text';
                syncGeneratedNoteView();
                try {
                    markNoteDirty();
                } catch (_) {}
                try {
                    if (typeof saveToStorage === 'function') saveToStorage();
                } catch (_) {}
            }
        }

        function initGeneratedNoteEditToggle() {
            const btn = document.getElementById('generatedNoteEditToggleBtn');
            if (!btn || btn.dataset.bound === '1') return;
            btn.dataset.bound = '1';
            btn.addEventListener('click', (e) => {
                e.preventDefault();
                const ta = document.getElementById('generatedNote');
                if (ta && ta.dataset.busy === '1') return;
                const shell = document.querySelector('.generated-note-preview-shell');
                if (!shell) return;
                if (shell.classList.contains('is-editing')) {
                    setGeneratedNoteEditMode(false);
                } else {
                    setGeneratedNoteEditMode(true);
                }
            });
        }

        function updateGeneratedNoteEmptyState() {
            const noteTextarea = document.getElementById('generatedNote');
            if (!noteTextarea) return;
            try {
                if (noteTextarea.dataset && noteTextarea.dataset.busy === '1') {
                    noteTextarea.classList.add('note-dimmed');
                    noteTextarea.setAttribute('readonly', 'readonly');
                    return;
                }
                if (!noteTextarea.value.trim()) {
                    noteTextarea.classList.add('note-dimmed');
                    noteTextarea.removeAttribute('readonly');
                } else {
                    noteTextarea.classList.remove('note-dimmed');
                    noteTextarea.removeAttribute('readonly');
                }
            } finally {
                syncGeneratedNotePreview();
                syncGeneratedNoteView();
                updateGeneratedNoteEditToggleDisabled();
            }
        }

        function setGeneratedNoteBusy(isBusy) {
            const noteTextarea = document.getElementById('generatedNote');
            if (!noteTextarea) return;
            noteTextarea.dataset.busy = isBusy ? '1' : '0';
            if (isBusy) {
                noteTextarea.classList.add('note-dimmed');
                noteTextarea.setAttribute('readonly', 'readonly');
            } else {
                noteTextarea.classList.remove('note-dimmed');
                noteTextarea.removeAttribute('readonly');
                updateGeneratedNoteEmptyState();
            }
            updateGeneratedNoteEditToggleDisabled();
        }

        function updateNoteStatus(message) {
            const statusEl = document.getElementById('noteStatus');
            if (!statusEl) return;
            if (message) {
                statusEl.textContent = message;
                statusEl.classList.remove('hidden');
            } else {
                statusEl.textContent = '';
                statusEl.classList.add('hidden');
            }
        }

        function sanitizeUserText(text) {
            if (!text) return '';
            return String(text)
                .replace(/[^\x09\x0A\x0D\x20-\x7E]/g, ' ')
                .replace(/\r/g, '')
                .trim();
        }

        function cleanStreamedNoteText(piece) {
            if (!piece) return '';
            return piece.replace(/__STREAM_END__/g, '');
        }

        function finalizeNoteText(text) {
            if (!text) return '';

            let cleaned = text
                .replace(/__STREAM_END__/g, '')
                .replace(/```text/gi, '')
                .replace(/```/g, '')
                .replace(/#/g, '');

            // Strip standalone separator lines some models include (e.g., "---" metadata separators).
            cleaned = cleaned.replace(/^[ \t]*(?:-{3,}|={3,}|_{3,})[ \t]*$/gm, '');

            // ====== COMPREHENSIVE EMR COMPATIBILITY SANITIZATION ======
            // Defense-in-depth: Backend already sanitizes, but this provides extra safety

            // 1. Replace subscript digits with normal digits (FEV₁ → FEV1)
            cleaned = cleaned.replace(/[₀₁₂₃₄₅₆₇₈₉]/g, (match) => {
                return '0123456789'['₀₁₂₃₄₅₆₇₈₉'.indexOf(match)];
            });

            // 2. Replace superscript digits with normal digits (10⁹ → 10^9)
            cleaned = cleaned.replace(/[⁰¹²³⁴⁵⁶⁷⁸⁹]/g, (match) => {
                return '0123456789'['⁰¹²³⁴⁵⁶⁷⁸⁹'.indexOf(match)];
            });

            // 3. Replace all Unicode hyphens/dashes with ASCII hyphen
            cleaned = cleaned.replace(/[\u2010-\u2015\u00AD\u2011\u2212]/g, '-');

            // 4. Replace special math symbols with ASCII equivalents
            cleaned = cleaned.replace(/\u00D7/g, 'x');   // Multiplication sign → x
            cleaned = cleaned.replace(/\u00F7/g, '/');   // Division sign → /
            cleaned = cleaned.replace(/\u2264/g, '<=');  // Less than or equal
            cleaned = cleaned.replace(/\u2265/g, '>=');  // Greater than or equal
            cleaned = cleaned.replace(/\u2260/g, '!=');  // Not equal
            cleaned = cleaned.replace(/\u2248/g, '~=');  // Approximately equal
            cleaned = cleaned.replace(/\u00B1/g, '+/-'); // Plus-minus

            // 5. Replace smart quotes with straight quotes
            cleaned = cleaned.replace(/[\u2018\u2019]/g, "'");  // Single quotes
            cleaned = cleaned.replace(/[\u201C\u201D]/g, '"');  // Double quotes

            // 6. Replace special spaces with normal space
            cleaned = cleaned.replace(/[\u00A0\u2009\u202F\u2007\u2008]/g, ' ');
            cleaned = cleaned.replace(/\u200B/g, ''); // Zero-width space (remove)

            // 7. Strip markdown emphasis so EMR plain-text paste has no ** or __ (bold shows as words only)
            for (let k = 0; k < 8; k++) {
                const prev = cleaned;
                cleaned = cleaned
                    .replace(/\*\*([^*]+)\*\*/g, '$1')
                    .replace(/__([^_]+)__/g, '$1');
                if (cleaned === prev) break;
            }
            cleaned = cleaned.replace(/\*\*/g, '');

            // Normalize CRLF to LF. Do not insert extra newlines; only cap excessive blank lines.
            cleaned = cleaned.replace(/\r\n/g, '\n').replace(/\r/g, '\n');
            cleaned = cleaned.replace(/\n{3,}/g, '\n\n');
            return cleaned.trim();
        }

        // Helper function to deduplicate and index references
        function deduplicateReferences(refs) {
            if (!refs || !Array.isArray(refs)) return [];

            const seen = new Map();
            const deduped = [];

            for (const r of refs) {
                const key = [
                    safeStr(r.source).trim(),
                    safeStr(r.title).trim(),
                    safeStr(r.section).trim()
                ].join('||').toLowerCase();

                if (!seen.has(key)) {
                    seen.set(key, true);
                    deduped.push(r);
                }
            }

            return deduped.map((r, idx) => ({
                ...r,
                index: idx + 1
            }));
        }

        /** True while server queue batch is running (avoid hiding queue strip when queue is temporarily cleared). */
        let queueProcessRunning = false;

        /** Query suffix for generation endpoints scoped to the active encounter (server validates). */
        function encounterQueryString() {
            const eid = (window.app && window.app.activeEncounterId) ? String(window.app.activeEncounterId) : '';
            return eid ? `encounter_id=${encodeURIComponent(eid)}` : '';
        }

        function getActiveEncounterIdForQueue() {
            return (window.app && window.app.activeEncounterId) ? String(window.app.activeEncounterId) : '';
        }

        /** Only jobs for the currently active encounter (strict match on encounter_id). */
        function queueJobMatchesActiveEncounter(r) {
            if (!r) return false;
            const active = getActiveEncounterIdForQueue();
            if (!active) return false;
            const eid = r.encounter_id != null && r.encounter_id !== '' ? String(r.encounter_id) : '';
            return eid === active;
        }

        function requestQueueForActiveEncounter() {
            return (Array.isArray(app.requestQueue) ? app.requestQueue : []).filter((r) => {
                if (!queueJobMatchesActiveEncounter(r)) return false;
                return true;
            });
        }

        function updateConnectionStatus(status) {
            app.connection.status = status;
            app.connection.lastCheck = new Date();

            const statusElement = document.getElementById('connectionStatus');
            statusElement.className = `connection-status ${status}`;

            switch (status) {
                case 'connected':
                    statusElement.textContent = 'Connected';
                    break;
                case 'disconnected':
                    statusElement.textContent = 'Offline';
                    break;
                case 'queued':
                    statusElement.textContent = `Queued (${requestQueueForActiveEncounter().length})`;
                    break;
                default:
                    statusElement.textContent = 'Unknown';
                    break;
            }

            if (!queueProcessRunning) {
                updateQueueDisplay();
            }
        }

        /** True when API health last succeeded; allows processing while pill shows "Queued (n)". */
        function canRunQueuedJobs() {
            if (app.connection.status === 'disconnected') return false;
            if (app.connection.lastHealthOk === false) return false;
            return true;
        }

        /**
         * Single source of truth for the top-left connection / queue pill.
         * Fixes: health checks and settings_connection overwrote "Queued (n)" with "Connected".
         */
        function syncQueueConnectionPill() {
            if (queueProcessRunning) return;
            const statusElement = document.getElementById('connectionStatus');
            if (!statusElement) return;

            const token = typeof getAuthToken === 'function' ? getAuthToken() : null;
            const n = requestQueueForActiveEncounter().length;

            if (!token) {
                app.connection.status = 'disconnected';
                statusElement.className = 'connection-status disconnected';
                statusElement.textContent = 'Offline';
                statusElement.title = '';
                return;
            }

            if (app.connection.lastHealthOk === false) {
                app.connection.status = 'disconnected';
                statusElement.className = 'connection-status disconnected';
                statusElement.textContent = n > 0 ? `Offline · Queued (${n})` : 'Offline';
                statusElement.title = n > 0 ? 'Server unreachable — open Encounter Queue below when back online' : '';
                return;
            }

            if (n > 0) {
                app.connection.status = 'queued';
                statusElement.className = 'connection-status queued';
                statusElement.textContent = `Queued (${n})`;
                statusElement.title = 'Encounter Queue below — Retry, Download, or Delete';
            } else {
                app.connection.status = 'connected';
                statusElement.className = 'connection-status connected';
                statusElement.textContent = 'Connected';
                statusElement.title = '';
            }
        }

        // Speech Recognition
        /** Combine prior transcript text with a new ASR session. */
        function combineAsrBaselineWithSession(baseline, sessionText) {
            const b = String(baseline ?? '');
            const s = String(sessionText ?? '').trim();
            if (!s) return b;
            const trimmedBase = b.replace(/\s+$/, '');
            if (!trimmedBase) return s;
            return trimmedBase + '\n\n' + s;
        }

        const PIPELINE_PHASES = ['idle', 'recording', 'stopping', 'transcribing', 'generating_note'];

        /** Single source of truth for Record / Stop labels, colors, and disabled state. */
        function setAudioPipelinePhase(phase) {
            const next = PIPELINE_PHASES.includes(phase) ? phase : 'idle';
            app.audioPipelinePhase = next;
            if (window.CNGAudioUI && typeof window.CNGAudioUI.syncRecordingPipelineUi === 'function') {
                window.CNGAudioUI.syncRecordingPipelineUi(next);
            } else if (window.CNGAudioUI && typeof window.CNGAudioUI.setRecordingButtonsState === 'function') {
                window.CNGAudioUI.setRecordingButtonsState(next === 'recording');
            }
            syncEncounterRecordingStatus();
            // Track stuck "Transcribing" — show emergency button after threshold.
            if (phase === 'transcribing') {
                _resetStuckTranscriptTimer();
            } else {
                if (_stuckTranscriptTimerId) {
                    clearTimeout(_stuckTranscriptTimerId);
                    _stuckTranscriptTimerId = null;
                }
                _hideReleaseButton();
            }
        }

        /** Timer tracking how long UI has been stuck in "transcribing" state. */
        var _stuckTranscriptTimerId = null;
        var _releaseBtnEl = null;
        var STUCK_TRANSCRIPT_THRESHOLD_MS = 60000; // Show emergency button after 60s in transcribing

        /** Reset the stuck-timer — call whenever we enter or leave transcribing. */
        function _resetStuckTranscriptTimer() {
            if (_stuckTranscriptTimerId) {
                clearTimeout(_stuckTranscriptTimerId);
                _stuckTranscriptTimerId = null;
            }
            _hideReleaseButton();
            _stuckTranscriptTimerId = setTimeout(function () {
                if ((app && app.audioPipelinePhase) === 'transcribing') {
                    _showReleaseButton();
                }
            }, STUCK_TRANSCRIPT_THRESHOLD_MS);
        }

        function _hideReleaseButton() {
            if (_releaseBtnEl && _releaseBtnEl.parentNode) {
                _releaseBtnEl.parentNode.removeChild(_releaseBtnEl);
                _releaseBtnEl = null;
            }
        }

        function _showReleaseButton() {
            var statusEl = document.getElementById('encounterRecordingStatus');
            if (!statusEl) return;
            var btn = document.createElement('button');
            btn.id = 'asrEmergencyReleaseBtn';
            btn.textContent = 'Release recording';
            btn.style.cssText = 'display:block;align-self:flex-start;margin-top:6px;padding:6px 14px;background:#dc3545;color:#fff;border:none;border-radius:4px;cursor:pointer;font-size:13px;font-weight:600;opacity:0.9;';
            btn.addEventListener('click', function () {
                btn.disabled = true;
                btn.textContent = 'Releasing...';
                try {
                    if (_stuckTranscriptTimerId) { clearTimeout(_stuckTranscriptTimerId); _stuckTranscriptTimerId = null; }
                    // The live handler is window.universalAudio. The `audioHandler`
                    // local in initUniversalAudio() is not in scope here, and
                    // reading a property off an undeclared name throws a
                    // ReferenceError -- `typeof x.y` does not guard the way
                    // `typeof x` does. That threw before anything below ran, so
                    // this button always fell through to the catch and told the
                    // clinician to refresh, losing the recording it exists to
                    // release. It has never once succeeded.
                    var handler = window.universalAudio;
                    if (handler && typeof handler.forceResetRecording === 'function') {
                        handler.forceResetRecording();
                    }
                    // Invalidate session ID so any detached old pipeline's callbacks
                    // are rejected by the capturedSessionId guard in onStatus/onTranscript.
                    app._asrChunkSessionId = '';
                    // (a) Defensive clear of an already-released/possibly-absent property;
                    // the emergency-release path is guaranteed fire-and-forget.
                    try { app._asrChunkPipeline = null; } catch (_) {}
                    setAudioPipelinePhase('idle');
                    btn.textContent = 'Released. Tap Re-transcribe to finish.';
                    btn.style.background = '#28a745';
                    if (typeof showToast === 'function') {
                        showToast('Recording released', 'Recording retained locally when available; reconnect and recover or re-transcribe.', 'info');
                    }
                } catch (e) {
                    // forceResetRecording() above runs FIRST and never throws
                    // (its body is a catch-all), so reaching here means the
                    // recording was already released and its recovery copy kept
                    // -- only the UI-sync after it failed. Never tell the
                    // clinician to refresh: that discards the retained copy this
                    // button exists to preserve. Keep it re-enabled so the retry
                    // is real (idempotent re-run repairs the UI sync).
                    console.error('[ASR] Emergency-release UI sync failed (recording already reset)', e);
                    btn.disabled = false;
                    btn.textContent = 'Release not completed. Tap Re-transcribe to finish, or try again.';
                }
            });
            var container = statusEl.parentNode;
            if (container) {
                if (statusEl.nextSibling) {
                    container.insertBefore(btn, statusEl.nextSibling);
                } else {
                    container.appendChild(btn);
                }
                _releaseBtnEl = btn;
            }
        }

        function syncRecordingUiFromPipeline() {
            setAudioPipelinePhase(app.audioPipelinePhase || 'idle');
        }

        function setRecordingButtonsState(isRecording) {
            setAudioPipelinePhase(isRecording ? 'recording' : 'idle');
        }

        /** Small label on the active encounter strip — driven by audioPipelinePhase first. */
        function syncEncounterRecordingStatus() {
            const el = document.getElementById('encounterRecordingStatus');
            if (!el) return;
            const phase = app.audioPipelinePhase || 'idle';
            if (phase === 'recording') {
                el.textContent = 'Recording';
                el.className = 'ae-rec-status ae-rec-status--recording';
                return;
            }
            if (phase === 'transcribing' || phase === 'stopping') {
                el.textContent = 'Transcribing';
                el.className = 'ae-rec-status ae-rec-status--transcribing';
                return;
            }
            if (phase === 'generating_note') {
                el.textContent = 'Generating note';
                el.className = 'ae-rec-status ae-rec-status--transcribing';
                return;
            }
            el.textContent = 'Stopped';
            el.className = 'ae-rec-status ae-rec-status--idle';
        }

        const _asrTranscribeOkStorageKey = (eid) => `cng_asr_transcribe_ok_${String(eid || '').slice(0, 36)}`;
        function setEncounterAsrTranscribeSucceeded(eid) {
            try {
                if (!eid) return;
                localStorage.setItem(_asrTranscribeOkStorageKey(eid), '1');
            } catch (_) {
                // (a) Storage guard: blocked/quota localStorage must never break
                // the in-memory transcribed-state that drives the retranscribe UI.
            }
        }
        function hasEncounterAsrTranscribeSucceeded(eid) {
            try {
                return localStorage.getItem(_asrTranscribeOkStorageKey(eid)) === '1';
            } catch (_) {
                return false;
            }
        }

        /**
         * Banner under header: (1) idle + encounter open → remind to Record after 1 min; stays visible (no blink cycle).
         * (2) Recording + sustained digital silence → mic hint; gentle 5s on / 10s off.
         */
        function createWorkspaceRecordingHints() {
            const IDLE_AFTER_MS = 60000;
            const SHOW_MS = 5000;
            const HIDE_MS = 10000;

            let idleDeadlineId = null;
            const gentleTimers = [];
            let activeGentleMode = null;

            function clearGentleTimers() {
                while (gentleTimers.length) {
                    const id = gentleTimers.pop();
                    clearTimeout(id);
                }
            }

            function hideBannerEl() {
                const el = document.getElementById('workspaceHintBanner');
                if (!el) return;
                el.classList.remove('workspace-hint-banner--visible', 'workspace-hint-banner--idle', 'workspace-hint-banner--silence');
            }

            function showBannerEl(mode) {
                const el = document.getElementById('workspaceHintBanner');
                const txt = document.getElementById('workspaceHintBannerText');
                if (!el || !txt) return;
                el.classList.remove('workspace-hint-banner--idle', 'workspace-hint-banner--silence');
                if (mode === 'idle') {
                    txt.textContent = 'When you\'re ready, tap Record to dictate this encounter.';
                    el.classList.add('workspace-hint-banner--idle');
                } else {
                    txt.textContent = 'Very little sound is reaching the mic — try moving closer or checking the microphone.';
                    el.classList.add('workspace-hint-banner--silence');
                }
                el.classList.add('workspace-hint-banner--visible');
            }

            function eligibleIdle() {
                try {
                    if (typeof getAuthToken !== 'function' || !getAuthToken()) return false;
                    if (!app.activeEncounterId) return false;
                    return (app.audioPipelinePhase || 'idle') === 'idle';
                } catch (_) {
                    return false;
                }
            }

            function eligibleSilence() {
                try {
                    return (app.audioPipelinePhase || '') === 'recording';
                } catch (_) {
                    return false;
                }
            }

            function beginGentleCycle(mode) {
                clearGentleTimers();
                hideBannerEl();
                activeGentleMode = mode;

                if (mode === 'idle') {
                    if (!eligibleIdle()) {
                        activeGentleMode = null;
                        return;
                    }
                    showBannerEl('idle');
                    return;
                }

                const schedule = () => {
                    const ok = eligibleSilence();
                    if (!ok) {
                        hideBannerEl();
                        activeGentleMode = null;
                        return;
                    }
                    showBannerEl('silence');
                    const t1 = setTimeout(() => {
                        hideBannerEl();
                        const t2 = setTimeout(schedule, HIDE_MS);
                        gentleTimers.push(t2);
                    }, SHOW_MS);
                    gentleTimers.push(t1);
                };

                schedule();
            }

            function stopSilenceGentle() {
                if (activeGentleMode !== 'silence') return;
                clearGentleTimers();
                hideBannerEl();
                activeGentleMode = null;
            }

            function bumpRecordingRelatedActivity() {
                cancelIdleDeadline();
                scheduleIdleDeadline();
                if (activeGentleMode === 'idle') {
                    clearGentleTimers();
                    hideBannerEl();
                    activeGentleMode = null;
                }
            }

            function cancelIdleDeadline() {
                if (idleDeadlineId) {
                    clearTimeout(idleDeadlineId);
                    idleDeadlineId = null;
                }
            }

            function scheduleIdleDeadline() {
                cancelIdleDeadline();
                idleDeadlineId = setTimeout(() => {
                    idleDeadlineId = null;
                    if (!eligibleIdle()) return;
                    if (activeGentleMode === 'silence') return;
                    beginGentleCycle('idle');
                }, IDLE_AFTER_MS);
            }

            window.addEventListener('encounters-refresh', bumpRecordingRelatedActivity);

            const ua = window.universalAudio;
            if (ua && typeof ua.setInputSilenceCallback === 'function') {
                ua.setInputSilenceCallback((isSilent) => {
                    if (isSilent && eligibleSilence()) {
                        beginGentleCycle('silence');
                        // Aggressive warning: after 10s of dead air, the gentle banner
                        // alone is too easy to miss. Show a toast so the doctor knows
                        // their mic isn't picking anything up.
                        showToast('Microphone', 'No audio detected. Check your microphone or move closer.', 'warning');
                    } else {
                        stopSilenceGentle();
                    }
                });
            }

            scheduleIdleDeadline();

            return { bumpRecordingRelatedActivity };
        }

        function initSpeechRecognition() {
            if (!window.universalAudio) {
                window.universalAudio = initUniversalAudio();
            }

            const audioHandler = window.universalAudio;
            const recordingHints = createWorkspaceRecordingHints();
            const guidance = audioHandler.getBrowserGuidance();

            const _getAsrSegmentIds = () => getAsrSegmentIdsForEncounter((app && app.activeEncounterId) ? String(app.activeEncounterId) : '');

            const _refreshRetranscribeUi = () => {
                try {
                    const row = document.getElementById('retranscribeRow');
                    const btn = document.getElementById('retranscribeBtn');
                    const meta = document.getElementById('retranscribeMeta');
                    const segmentsToggle = document.getElementById('asrSegmentsToggle');
                    const segmentsList = document.getElementById('asrSegmentsList');
                    const eid = (app && app.activeEncounterId) ? String(app.activeEncounterId) : '';
                    const memEid = (app && app.lastAsrBackupEncounterId) ? String(app.lastAsrBackupEncounterId) : '';
                    if (memEid && eid && memEid !== eid) {
                        app.lastAsrBackupFile = null;
                        app.lastAsrBackupMeta = null;
                        app.lastAsrBackupEncounterId = null;
                    }
                    const segmentIds = _getAsrSegmentIds();
                    const nSeg = segmentIds.length;
                    const hasServer = nSeg > 0;
                    const hasLocalFile = !!app.lastAsrBackupFile;
                    const hasAsset = hasServer || hasLocalFile;
                    /** Show Re-transcribe whenever this encounter has full audio (server or memory), even if live ASR never succeeded — retry must not depend on a prior success. */
                    const showRow = !!(eid && hasAsset);
                    if (row) {
                        if (showRow) {
                            row.classList.remove('hidden');
                            row.style.display = 'flex';
                        } else {
                            row.classList.add('hidden');
                            row.style.display = 'none';
                        }
                    }
                    const saving = !!(app && app.lastAsrBackupMeta && app.lastAsrBackupMeta.saving);
                    const allRetryBusy = !!(app && app.asrAllRetryInProgress);
                    const cachedSegs = eid ? getCachedAsrSegmentsForEncounter(eid) : [];
                    const groupedUi = groupAsrSegmentsForDisplay(cachedSegs, app._asrChunkSessionId);
                    const recordingGroupCount = groupedUi.chunkGroups.length + (groupedUi.batchSegments.length > 0 ? 1 : 0);
                    if (btn) {
                        btn.disabled = !showRow || saving || allRetryBusy;
                        btn.classList.add('asr-retranscribe-whole');
                        btn.textContent = allRetryBusy ? 'Re-transcribing…' : 'Re-transcribe all';
                        btn.title = 'Re-transcribe every saved recording on this encounter';
                    }
                    if (meta) {
                        if (!showRow) {
                            meta.textContent = '';
                            meta.style.display = 'none';
                        } else if (hasServer) {
                            meta.className = 'text-muted asr-retranscribe-meta';
                            if (saving) {
                                meta.textContent = 'Uploading…';
                            } else if (groupedUi.chunkGroups.length > 0) {
                                const chunkCount = groupedUi.chunkGroups.reduce(function (n, g) {
                                    return n + (g.segments ? g.segments.length : 0);
                                }, 0);
                                meta.textContent = recordingGroupCount + ' recording' + (recordingGroupCount === 1 ? '' : 's')
                                    + ' · ' + chunkCount + ' chunk' + (chunkCount === 1 ? '' : 's')
                                    + ' · one action above';
                            } else {
                                meta.textContent = `${nSeg} saved recording segment${nSeg === 1 ? '' : 's'}`;
                            }
                            meta.style.display = '';
                        } else {
                            const label = app.lastAsrBackupMeta?.fileName || app.lastAsrBackupFile?.name || 'recording';
                            meta.textContent = saving ? `Uploading…: ${label}` : '';
                            meta.style.display = saving ? '' : 'none';
                        }
                    }
                    const canShowSegments = showRow && (groupedUi.chunkGroups.length > 0 || nSeg > 1);
                    if (segmentsToggle) {
                        if (canShowSegments) {
                            segmentsToggle.classList.remove('hidden');
                            segmentsToggle.style.display = '';
                            segmentsToggle.disabled = saving || allRetryBusy;
                            const expanded = !!(app && app.asrSegmentsExpanded);
                            segmentsToggle.setAttribute('aria-expanded', expanded ? 'true' : 'false');
                            if (groupedUi.chunkGroups.length > 0) {
                                segmentsToggle.textContent = expanded ? 'Hide chunks' : 'Show chunks';
                                segmentsToggle.title = expanded
                                    ? 'Hide saved recording breakdown'
                                    : 'Show how recordings are split into chunks';
                            } else {
                                segmentsToggle.textContent = expanded ? 'Hide segments' : 'Segments';
                                segmentsToggle.title = expanded
                                    ? 'Hide saved recording segments'
                                    : 'Show saved recording segments';
                            }
                        } else {
                            segmentsToggle.classList.add('hidden');
                            segmentsToggle.style.display = 'none';
                            segmentsToggle.setAttribute('aria-expanded', 'false');
                        }
                    }
                    if ((!canShowSegments || !(app && app.asrSegmentsExpanded)) && segmentsList) {
                        segmentsList.classList.add('hidden');
                    }
                    renderAsrSegmentListForEncounter(eid);
                    // (a) Best-effort UI refresh: any DOM hiccup here must not
                    // take down the recording pipeline; the next explicit
                    // _refreshRetranscribeUi() call repairs the panel.
                } catch (_) {}
            };

            window._refreshRetranscribeUi = _refreshRetranscribeUi;
            window._getAsrSegmentIds = _getAsrSegmentIds;

            audioHandler.setTranscriptionCallback((transcript, type) => {
                if (type !== 'replace') return;
                const tx = document.getElementById('transcriptionDisplay');
                const base = app.asrBaselineBeforeRecording != null
                    ? app.asrBaselineBeforeRecording
                    : '';
                if (tx) tx.value = combineAsrBaselineWithSession(base, transcript);
                if (typeof updateCharacterCounter === 'function') {
                    updateCharacterCounter();
                }
                saveToStorage();
            });

            if (typeof audioHandler.setAsrStatusCallback === 'function') {
                audioHandler.setAsrStatusCallback((state, detail) => {
                    app.lastAsrState = state;
                    if (state === 'recording') {
                        setAudioPipelinePhase('recording');
                    } else if (state === 'processing' || state === 'fallback') {
                        setAudioPipelinePhase('transcribing');
                    } else {
                        /* done/error/etc.: phase often unchanged — refresh strip without duplicating setAudioPipelinePhase sync */
                        syncEncounterRecordingStatus();
                    }
                    if (state === 'done') {
                        try {
                            const eidDone = app.activeEncounterId ? String(app.activeEncounterId) : '';
                            if (eidDone) setEncounterAsrTranscribeSucceeded(eidDone);
                            // (a) Storage + UI-refresh guards on the 'done' path:
                            // worst case the 'transcribed' flag is lost and the
                            // retranscribe panel is briefly stale — never fatal.
                        } catch (_) {}
                        try { _refreshRetranscribeUi(); } catch (_) {}
                        if (!app.suppressNextAsrTranscribedToast) {
                            showToast('Success', 'Speech transcribed', 'success');
                        }
                        app.suppressNextAsrTranscribedToast = false;
                        if (!app.pendingGenerateAfterTranscription && app.audioPipelinePhase !== 'generating_note') {
                            setAudioPipelinePhase('idle');
                        }
                    } else if (state === 'error') {
                        if (!app.pendingGenerateAfterTranscription && app.audioPipelinePhase !== 'generating_note') {
                            setAudioPipelinePhase('idle');
                        }
                    }
                    if (state === 'error' && app.pendingGenerateAfterTranscription) {
                        clearPendingGenerateAfterTranscription();
                        showToast('Audio Error', 'Transcription did not finish before generate. Please try again.', 'error');
                        setAudioPipelinePhase('idle');
                    }
                    const s = document.getElementById('asrStreamStatus');
                    if (!s) return;
                    s.classList.remove('hidden');
                    s.textContent = `ASR: ${state}${detail ? ` (${detail})` : ''}`;
                });
            } else {
                console.warn('[ASR] audioHandler.setAsrStatusCallback missing in audio handler');
            }

            audioHandler.setAudioFileCallback(async (audioFile, meta = {}) => {
                // Batch mode: persist one full_backup for optional re-transcribe.
                // Chunk mode: segments are the only server copy — no redundant full file.
                if (meta && meta.storeLatest) {
                    const eidAtCapture = (app && app.activeEncounterId) ? String(app.activeEncounterId) : '';
                    if (meta.chunkMode) {
                        const persistLayerOk = meta.chunkPersistOk !== false;
                        try {
                            app.lastAsrBackupFile = null;
                            app.lastAsrBackupMeta = { ...(meta || {}), saving: false, chunkMode: true };
                            app.lastAsrBackupEncounterId = eidAtCapture || null;
                            app._recordingPersistLayerOk = persistLayerOk;
                        } catch (_) {}
                        _refreshRetranscribeUi();
                        return {
                            ok: persistLayerOk,
                            retained: persistLayerOk,
                            segmentId: '',
                            mode: persistLayerOk ? 'server' : 'local',
                        };
                    }
                    let persistLayerOk = false;
                    try {
                        app.lastAsrBackupFile = audioFile;
                        app.lastAsrBackupMeta = meta || {};
                        // Tag in-memory backup with the encounter at capture time so encounter
                        // switches can drop the reference instead of replaying the wrong audio.
                        app.lastAsrBackupEncounterId = eidAtCapture || null;
                    } catch (_) {}
                    // universal_audio_handler awaits this callback before transcribing, so
                    // transcription runs against the retained server file when save succeeds.
                    try {
                        app.lastAsrBackupMeta = { ...(app.lastAsrBackupMeta || {}), saving: true };
                    } catch (_) {}
                    _refreshRetranscribeUi();

                    let retainedSegmentId = '';
                    const token = getAuthToken();
                    if (!token) {
                        console.warn('[ASR] Cannot persist recording segment: missing auth token');
                    } else if (!eidAtCapture) {
                        console.warn('[ASR] Cannot persist recording segment: no active encounter');
                    } else {
                        try {
                            await recoverLocalRecordingsToServer({
                                excludeRecordingId: meta.recordingSessionId || '',
                                includeRecording: false,
                                quiet: true,
                            });
                        } catch (_) {}
                        const browserOffline =
                            typeof navigator !== 'undefined' && navigator.onLine === false;
                        const serverKnownBad =
                            app.connection.lastHealthOk === false && connectionFailures > 0;
                        if (!browserOffline && !serverKnownBad) {
                            const uploaded = await uploadAsrRecordingSegment(audioFile, {
                                encounterId: eidAtCapture,
                                fileName: meta.fileName || audioFile.name,
                                clientRecordingId: (app._asrChunkSessionId ? app._asrChunkSessionId + '-full' : (meta.recordingSessionId || '') + '-full'),
                                recordingSessionId: app._asrChunkSessionId || meta.recordingSessionId || '',
                                segmentRole: 'full_backup',
                                durationSec: meta.durationSec,
                                startedAt: meta.startedAt || '',
                                stoppedAt: meta.stoppedAt || '',
                            });
                            if (uploaded && uploaded.ok && uploaded.segmentId) {
                                retainedSegmentId = String(uploaded.segmentId);
                                persistLayerOk = true;
                            }
                        }
                        if (!persistLayerOk) {
                            showToast('Recording saved locally', 'Upload did not verify yet. It will retry from this device.', 'warning');
                        }
                    }

                    try {
                        app._recordingPersistLayerOk = persistLayerOk;
                    } catch (_) {}

                    try {
                        if (app && app.lastAsrBackupMeta) {
                            app.lastAsrBackupMeta = { ...(app.lastAsrBackupMeta || {}), saving: false };
                        }
                    } catch (_) {}
                    _refreshRetranscribeUi();
                    // Return upload confirmation status — caller (onstop) uses this
                    // to decide whether to delete the local IndexedDB backup.
                    // true  = server has the file → local copy can be deleted
                    // false = upload failed → keep local copy for retry
                    return {
                        ok: persistLayerOk,
                        retained: persistLayerOk,
                        segmentId: retainedSegmentId,
                        mode: persistLayerOk ? 'server' : 'local',
                    };
                }
                if (meta && meta.fullFileTranscribe) {
                    let txOk = false;
                    if (meta.segmentId && meta.retainedSegment) {
                        const segmentTx = await transcribeAsrSegment(meta.segmentId, {
                            recordingBackupTrace: true,
                            quiet: true,
                            returnResult: true,
                        });
                        txOk = segmentTx === true || !!(segmentTx && segmentTx.ok === true);
                    } else if (!txOk) {
                        app._recordingTranscribeOutcome = { transcribeOk: false };
                        showToast('Recording saved locally', 'Transcription will be available after the recording uploads.', 'warning');
                    }
                    try {
                        const txOut = app._recordingTranscribeOutcome || {};
                        const rd =
                            typeof window !== 'undefined' &&
                            window.RecordingDurability &&
                            typeof window.RecordingDurability.computeRecordingDurable === 'function'
                                ? window.RecordingDurability.computeRecordingDurable({
                                      persistLayerOk: !!app._recordingPersistLayerOk,
                                      transcribeOk: !!txOut.transcribeOk,
                                  })
                                : !!app._recordingPersistLayerOk || !!txOut.transcribeOk;
                        if (!rd) {
                            await promptRecordingBackupIfNeeded(audioFile, meta.fileName || audioFile.name);
                        }
                    } catch (e) {
                        console.warn('[Recording backup prompt]', e);
                    }
                    try {
                        delete app._recordingTranscribeOutcome;
                        delete app._recordingPersistLayerOk;
                    } catch (_) {}
                    return txOk;
                }
                return false;
            });

            // Optional, non-blocking "Re-transcribe" button (full recording).
            try {
                const btn = document.getElementById('retranscribeBtn');
                if (btn && !btn.__wired) {
                    btn.__wired = true;
                    btn.addEventListener('click', async (event) => {
                        event.preventDefault();
                        event.stopPropagation();
                        _refreshRetranscribeUi();
                        const token = getAuthToken();
                        const eid = (app && app.activeEncounterId) ? String(app.activeEncounterId) : '';
                        let segmentIds = _getAsrSegmentIds();
                        let latestSegments = [];
                        if (token && eid) {
                            try {
                                latestSegments = await fetchAsrSegmentsForEncounter(eid);
                                segmentIds = (latestSegments || []).map((s) => String(s && s.id || '').trim()).filter(Boolean);
                            } catch (e) {
                                console.warn('[Re-transcribe] Could not refresh segment list before retry', e);
                            }
                        }
                        if (!segmentIds.length) {
                            showToast('Info', 'No saved recording available to re-transcribe yet.', 'info');
                            return;
                        }
                        if (!token) {
                            showToast('Sign In Required', 'Please sign in to transcribe the saved recording.', 'warning');
                            return;
                        }
                        app.asrAllRetryInProgress = true;
                        _refreshRetranscribeUi();
                        btn.disabled = true;
                        try {
                            showToast('Re-transcribing', 'Re-transcribing all saved recordings on this encounter…', 'info');
                            showProgress('audioProgress', 'audioProgressBar');
                            try {
                                const result = await retranscribeAllEncounter(eid);
                                const merged = String(result && result.transcript_text || '').trim();
                                if (result && Array.isArray(result.segments)) {
                                    app.asrSegmentsByEncounter = app.asrSegmentsByEncounter || {};
                                    app.asrSegmentsByEncounter[eid] = result.segments.slice().sort(function (a, b) {
                                        const ai = Number(a && a.sequence_index) || 0;
                                        const bi = Number(b && b.sequence_index) || 0;
                                        if (ai !== bi) return ai - bi;
                                        return String(a && a.created_at || '').localeCompare(String(b && b.created_at || ''));
                                    });
                                    localStorage.setItem(
                                        asrSegmentIdsKey(eid),
                                        JSON.stringify(result.segments.map(function (s) { return String(s && s.id || '').trim(); }).filter(Boolean))
                                    );
                                }
                                if (merged) {
                                    app.asrBaselineBeforeRecording = "";
                                await applyEncounterTranscriptText(eid, merged);
                                    renderAsrSegmentListForEncounter(eid);
                                }
                                if (merged) {
                                    showToast('Success', 'All saved recordings re-transcribed.', 'success');
                                } else {
                                    await fetchAsrSegmentsForEncounter(eid).catch(function () {});
                                    showToast('Transcription failed', 'Could not re-transcribe saved recordings.', 'warning');
                                }
                            } finally {
                                hideProgress('audioProgress');
                            }
                        } finally {
                            app.asrAllRetryInProgress = false;
                            btn.disabled = false;
                            _refreshRetranscribeUi();
                        }
                    });
                }
                const segmentsToggle = document.getElementById('asrSegmentsToggle');
                if (segmentsToggle && !segmentsToggle.__wired) {
                    segmentsToggle.__wired = true;
                    segmentsToggle.addEventListener('click', (event) => {
                        event.preventDefault();
                        event.stopPropagation();
                        app.asrSegmentsExpanded = !app.asrSegmentsExpanded;
                        _refreshRetranscribeUi();
                    });
                }
                // Default: disabled until we have a saved recording.
                _refreshRetranscribeUi();
            } catch (_) {}

            audioHandler.onSpeechStart = () => {
                setAudioPipelinePhase('recording');
            };

            audioHandler.onSpeechStop = () => {
                if (app.audioPipelinePhase === 'recording') {
                    setAudioPipelinePhase('idle');
                }
            };

            audioHandler.onSpeechError = (error) => {
                showToast('Speech Error', `Speech error: ${error}`, 'error');
            };

            audioHandler.onRecordingStart = () => {
                try {
                    recordingHints.bumpRecordingRelatedActivity();
                } catch (_) {}
                const tx = document.getElementById('transcriptionDisplay');
                app.asrBaselineBeforeRecording = tx ? String(tx.value || '') : '';
                setAudioPipelinePhase('recording');
            };

            audioHandler.onRecordingStop = () => {
                try {
                    recordingHints.bumpRecordingRelatedActivity();
                } catch (_) {}
                /* Phase after stop is driven by ASR callbacks (transcribing → idle), not here. */
            };

            audioHandler.onRecordingError = (error) => {
                try {
                    recordingHints.bumpRecordingRelatedActivity();
                } catch (_) {}
                showToast('Recording Error', `Recording failed: ${error.message}`, 'error');
                setAudioPipelinePhase('idle');
            };

            const speechResult = audioHandler.initSpeechRecognition();
            const recordResult = audioHandler.initAudioRecording();

            if (!speechResult.available && !recordResult.available) {
                showToast('Audio Info', guidance.recommendations.join('. '), 'info');
            }
            syncRecordingUiFromPipeline();
        }

        /** First Record press per encounter: play consent MP3 before opening the mic. Not a Windows/WSL path — copy `consent_recording.mp3` into `web/media/`. See `media/README.txt`. */
        const CNG_CONSENT_RECORDING_DEFAULT_PATH = 'media/consent_recording.mp3';

        function _cngConsentPlayedStorageKey(eid) {
            return `cng_encounter_consent_recording_${String(eid || '').slice(0, 48)}`;
        }

        async function playConsentRecordingIfNeededForEncounter() {
            // Consent pre-roll removed by operator request (2026-06-16): recording must start instantly.
            // Re-enable by setting window.CNG_CONSENT_RECORDING_ENABLED = true before the first Record press.
            if (window.CNG_CONSENT_RECORDING_ENABLED !== true) return;
            if (window.CNG_CONSENT_RECORDING_DISABLED === true) return;
            const eid = app && app.activeEncounterId ? String(app.activeEncounterId).trim() : '';
            if (!eid) return;
            try {
                if (localStorage.getItem(_cngConsentPlayedStorageKey(eid)) === '1') return;
            } catch (_) {}

            let href =
                typeof window.CNG_CONSENT_RECORDING_URL === 'string' && window.CNG_CONSENT_RECORDING_URL.trim()
                    ? window.CNG_CONSENT_RECORDING_URL.trim()
                    : null;
            if (!href) {
                try {
                    href = new URL(CNG_CONSENT_RECORDING_DEFAULT_PATH, window.location.href).href;
                } catch (_) {
                    href = CNG_CONSENT_RECORDING_DEFAULT_PATH;
                }
            }

            const audio = new Audio(href);
            try {
                await new Promise((resolve) => {
                    const finish = () => resolve();
                    audio.addEventListener('ended', finish, { once: true });
                    audio.addEventListener('error', finish, { once: true });
                    const p = audio.play();
                    if (p && typeof p.catch === 'function') {
                        p.catch(() => finish());
                    }
                });
            } catch (_) {}
            try {
                localStorage.setItem(_cngConsentPlayedStorageKey(eid), '1');
            } catch (_) {}
        }

        async function toggleSpeechRecognition() {
            console.log('[Audio] toggleSpeechRecognition called');
            const audioHandler = window.universalAudio;
            if (!audioHandler) {
                console.warn('[Audio] universalAudio not found');
                return;
            }

            const guidance = audioHandler.getBrowserGuidance();
            console.log('[Audio] recordingAvailable:', guidance.recordingAvailable);

            if (guidance.recordingAvailable) {
                const wasListening = audioHandler.isListening;
                console.log('[Audio] wasListening:', wasListening);

                if (wasListening) {
                    console.log('[Audio] stopping speech recognition');
                    setAudioPipelinePhase('transcribing');
                    await audioHandler.stopSpeechRecognition();
                } else {
                    // Require an active encounter before starting a recording.
                    // Without one, the IndexedDB backup silently no-ops and the
                    // recording has no durable home for compliance.
                    if (!app || !app.activeEncounterId) {
                        showToast('No encounter', 'Create or select an encounter before recording.', 'warning');
                        return;
                    }
                    if (['transcribing', 'generating_note'].includes(app.audioPipelinePhase)) {
                        showToast('Please wait', 'Finish transcription or note generation before recording again.', 'warning');
                        return;
                    }
                    if (typeof window.CNGGenerateUI?.clearPendingGenerateAfterTranscription === 'function') {
                        window.CNGGenerateUI.clearPendingGenerateAfterTranscription();
                    }
                    try {
                        console.log('[Audio] starting speech recognition');
                        await playConsentRecordingIfNeededForEncounter();
                        const tx = document.getElementById('transcriptionDisplay');
                        app.asrBaselineBeforeRecording = tx ? String(tx.value || '') : '';
                        try {
                            const AS = window.AsrSettings;
                            let caps = (window.app && window.app.asrCapabilities) || null;
                            if (!caps && window.AsrSettingsUi && typeof window.AsrSettingsUi.refreshAsrCapabilities === 'function') {
                                caps = await window.AsrSettingsUi.refreshAsrCapabilities();
                            }
                            const mode = AS ? AS.getCaptureMode() : 'batch';
                            if (mode === 'chunk' && caps && caps.chunk_asr_enabled && window.AsrChunkPipeline && typeof audioHandler.setChunkPipeline === 'function') {
                                app._asrChunkSessionId = 'rec_' + Date.now() + '_' + Math.random().toString(16).slice(2);
                                const eidRec = String(app.activeEncounterId);
                                // Capture session ID and baseline at construction time so callbacks
                                // can detect if a new recording has started and discard stale updates.
                                var capturedSessionId = app._asrChunkSessionId;
                                var capturedBaseline = app.asrBaselineBeforeRecording != null ? app.asrBaselineBeforeRecording : '';
                                const chunkPipeline = new window.AsrChunkPipeline({
                                    uploadFn: function (file, chunkMeta) {
                                        return uploadAsrRecordingSegment(file, {
                                            encounterId: eidRec,
                                            clientRecordingId: chunkMeta.clientRecordingId,
                                            recordingSessionId: chunkMeta.recordingSessionId,
                                            segmentRole: chunkMeta.segmentRole,
                                            chunkIndex: chunkMeta.chunkIndex,
                                            fileName: chunkMeta.clientRecordingId + '.webm',
                                            startedAt: chunkMeta.startedAt || '',
                                            stoppedAt: chunkMeta.stoppedAt || '',
                                        });
                                    },
                                    transcribeFn: function (segmentId) {
                                        return transcribeAsrSegment(segmentId, {
                                            quiet: true,
                                            returnResult: true,
                                            mode: 'chunk',
                                        });
                                    },
                                    fetchMergedFn: function () {
                                        if (app._asrChunkSessionId !== capturedSessionId) return '';
                                        return fetchAsrPipelineMerged(eidRec, capturedSessionId);
                                    },
                                    drainFn: function (encounterId, sessionId) {
                                        return drainAsrChunkPipeline(encounterId, sessionId);
                                    },
                                    onStatus: function (state, detail) {
                                        if (!app) return;
                                        // Stale-session guard: if a new recording has started (different session ID
                                        // or we're in a newer phase), discard this callback to avoid corrupting UI.
                                        if (app._asrChunkSessionId !== capturedSessionId) return;
                                        var recording = !!audioHandler.isListening;
                                        if (state === 'transcribing') {
                                            setAudioPipelinePhase('transcribing');
                                            try { _resetStuckTranscriptTimer(); } catch (_) {}
                                        } else if (state === 'recording' && recording) {
                                            // Only map 'recording' while mic is live — avoids flickering during finalization.
                                            setAudioPipelinePhase('recording');
                                        } else if (state === 'error') {
                                            if (recording) {
                                                // Warning-only while recording: worker will retry next segment; don't force idle or stop mic.
                                                console.warn('[ASR] Segment error (non-fatal during recording)', detail);
                                            } else {
                                                // Finalization: terminal error — force transcribeOk=false so downstream sees failure.
                                                // (b) This ALREADY reports to the incident store via _reportAsrIncident;
                                                // the outer catch only protects the reporter (best-effort) and the
                                                // outcome flag (fire-and-forget) from ever throwing.
                                                try { _reportAsrIncident({ stage: 'client.chunkWorker', outcome: 'error_during_finalization', traceId: '', message: String(detail || ''), ctx_state: app.audioPipelinePhase }); } catch (_) {}
                                                try { app._recordingTranscribeOutcome = { transcribeOk: false }; } catch (_) {}
                                            }
                                        }
                                    },
                                    onTranscript: function (merged) {
                                        // Stale-session guard: only apply transcript if this session is still current.
                                        if (app._asrChunkSessionId !== capturedSessionId) return;
                                        const tx = document.getElementById('transcriptionDisplay');
                                        const base = capturedBaseline ? capturedBaseline : '';
                                        if (tx && merged) {
                                            tx.value = combineAsrBaselineWithSession(base, merged);
                                        }
                                        if (typeof updateCharacterCounter === 'function') updateCharacterCounter();
                                        saveToStorage();
                                    },
                                });
                                chunkPipeline.start({ encounterId: eidRec, sessionId: app._asrChunkSessionId });
                                audioHandler.setChunkPipeline(chunkPipeline);
                                app._asrChunkPipeline = chunkPipeline;
                            } else if (typeof audioHandler.setChunkPipeline === 'function') {
                                audioHandler.setChunkPipeline(null);
                                app._asrChunkPipeline = null;
                                app._asrChunkSessionId = '';
                            }
                        } catch (chunkErr) {
                            console.warn('[ASR chunk] setup failed; using batch', chunkErr);
                            try {
                                if (typeof audioHandler.setChunkPipeline === 'function') audioHandler.setChunkPipeline(null);
                                app._asrChunkPipeline = null;
                                app._asrChunkSessionId = '';
                            } catch (_) {}
                        }
                        await audioHandler.startSpeechRecognition();
                    } catch (error) {
                        console.error('[Audio] startSpeechRecognition failed:', error);
                        // Force-reset any partially-initialised recorder state so the
                        // button isn't stuck orange and the user can try again immediately.
                        try { audioHandler.forceResetRecording(); } catch (_) {}
                        showToast('Speech Error', 'Failed to start speech recognition', 'error');
                        setAudioPipelinePhase('idle');
                        return;
                    }
                }
                // Labels and colors follow app.audioPipelinePhase via ASR callbacks (recording / transcribing / idle).
            } else {
                const suggestions = guidance.recommendations.join('. ');
                console.log('[Audio] recording not available:', suggestions);
                showToast('Audio Input', suggestions, 'info');
            }
        }

        function stopSpeechRecognition() {
            app.isListening = false;
            setAudioPipelinePhase('idle');
        }

        const CONTROL_CHAR_REGEX = /[\x00-\x08\x0B-\x1F\x7F]/g;
        const ZERO_WIDTH_REGEX = /[\u200B-\u200D\u2060\uFEFF]/g;
        const MULTISPACE_REGEX = /[^\S\r\n]+/g;
        const SYMBOL_LINE_REGEX = /([#*_=\-]{3,}|>{3,}|<{3,})/g;

        function sanitizeClinicalText(rawText) {
            if (rawText == null) return '';
            let text = String(rawText);
            text = text.replace(/\r\n?/g, '\n');
            text = text.replace(CONTROL_CHAR_REGEX, ' ');
            text = text.replace(ZERO_WIDTH_REGEX, '');
            text = text.replace(/\u00A0/g, ' ');
            text = text.replace(/```+/g, ' ');
            text = text.replace(/<\/?think>/gi, ' ');
            text = text.replace(SYMBOL_LINE_REGEX, ' ');
            text = text.replace(/[*#!<>+&^$-]/g, ''); // strip inline symbol noise
            // Strip known boilerplate lab report block (eGFR interpretation)
            text = text.replace(/eGFR Lab Report Interpretive Comment[\s\S]*?does not use a race coefficient\./gi, '');
            const lines = text
                .split('\n')
                .map(line => line.replace(MULTISPACE_REGEX, ' ').trim())
                .filter(line => line); // drop empty lines entirely
            return lines.join('\n').trim();
        }

        function appendToTextarea(el, text) {
            if (!el || !text) return;
            const current = el.value || '';
            const separator = current ? '\n' : '';
            const addition = separator + text;
            if (document.activeElement === el && typeof el.selectionStart === 'number') {
                const start = el.selectionStart;
                const end = el.selectionEnd;
                const atEnd = end === current.length;
                el.value = current + addition;
                if (!atEnd) {
                    el.selectionStart = start;
                    el.selectionEnd = end;
                }
            } else {
                el.value = current + addition;
            }
        }

        function setFieldValue(fieldId, rawText, options = {}) {
            const el = document.getElementById(fieldId);
            if (!el) return '';
            const source = typeof rawText === 'string'
                ? rawText
                : (rawText != null ? String(rawText) : (el.value || ''));
            const cleaned = sanitizeClinicalText(source);
            if (cleaned !== el.value) {
                if (options.preserveCaret && document.activeElement === el && typeof el.selectionStart === 'number') {
                    const caret = Math.min(el.selectionStart, cleaned.length);
                    const scrollTop = el.scrollTop;
                    el.value = cleaned;
                    el.selectionStart = el.selectionEnd = caret;
                    el.scrollTop = scrollTop;
                } else {
                    el.value = cleaned;
                }
            }
            if (typeof updateCharacterCounter === 'function') {
                try {
                    updateCharacterCounter();
                } catch (err) {
                    console.warn('[Counter] Failed to update character counter:', err);
                }
            }
            return cleaned;
        }

        function setChartDataValue(rawText, options = {}) {
            return setFieldValue('chartData', rawText, options);
        }

        // API Functions
        const TRANSCRIBE_TIMEOUT_MS = 100000;
        const TRANSCRIBE_DIARIZE_TIMEOUT_MS = 240000;

        function isAsrDebugEnabled() {
            try { return !!(window && window.__ASR_DEBUG); } catch (_) {}
            try {
                const v = localStorage.getItem('asr_debug');
                return !!(v && String(v).trim() && String(v).trim() !== '0');
            } catch (_) {}
            return false;
        }

        async function applyTranscriptionText(trimmed, opts = {}, traceId = '') {
            const replace = !!opts.replace;
            const sessionAppend = !!opts.sessionAppend;
            const tx = document.getElementById('transcriptionDisplay');
            if (replace && tx) {
                tx.value = trimmed;
            } else if (sessionAppend && tx) {
                const cur = tx.value || '';
                tx.value = cur ? cur.replace(/\s+$/, '') + '\n\n' + trimmed : trimmed;
            } else {
                appendToTextarea(tx, trimmed);
            }
            if (typeof updateCharacterCounter === 'function') {
                updateCharacterCounter();
            }
            saveToStorage();
            hideServiceErrorBanner();
            const traceNote = traceId ? ` Trace: ${traceId}` : '';
            if (!app.pendingGenerateAfterTranscription) {
                showToast('Success', `Audio transcribed with speaker labels.${traceNote}`, 'success');
            }

            if (app.pendingGenerateAfterTranscription) {
                clearPendingGenerateAfterTranscription();
                app.suppressNextAsrTranscribedToast = true;
                showToast('Generating', 'Transcription received — generating note…', 'info');
                setAudioPipelinePhase('generating_note');
                await generateNote();
                /* idle: onRecordingStop after full stop pipeline + _asrStatus('done') */
            }
            if (app.activeEncounterId) {
                setEncounterAsrTranscribeSucceeded(String(app.activeEncounterId));
            }
            try {
                if (typeof window._refreshRetranscribeUi === 'function') window._refreshRetranscribeUi();
            } catch (_) {}
            if (opts.recordingBackupTrace && app._recordingTranscribeOutcome) {
                app._recordingTranscribeOutcome.transcribeOk = true;
            }
            return true;
        }

        function asrSegmentIdsKey(encounterId) {
            const eid = encounterId || (app && app.activeEncounterId ? String(app.activeEncounterId) : '');
            return `asr_recording_segments_${eid || 'none'}`;
        }

        function getAsrSegmentIdsForEncounter(encounterId) {
            try {
                const raw = localStorage.getItem(asrSegmentIdsKey(encounterId)) || '';
                if (!raw) return [];
                const arr = JSON.parse(raw);
                if (!Array.isArray(arr)) return [];
                return arr.map((x) => String(x || '').trim()).filter(Boolean);
            } catch (_) {
                return [];
            }
        }

        function getCachedAsrSegmentsForEncounter(encounterId) {
            const eid = String(encounterId || (app && app.activeEncounterId) || '');
            const cached = app && app.asrSegmentsByEncounter && Array.isArray(app.asrSegmentsByEncounter[eid])
                ? app.asrSegmentsByEncounter[eid]
                : [];
            if (cached.length) return cached;
            return getAsrSegmentIdsForEncounter(eid).map((id, idx) => ({
                id,
                encounter_id: eid,
                sequence_index: idx + 1,
                transcription_status: 'saved',
            }));
        }

        function isChunkLikeAsrSegment(segment) {
            const r = String(segment && segment.segment_role || '');
            return r === 'chunk' || r === 'partial_tail';
        }

        function resolveChunkRecordingSessionId(segments, preferredSessionId) {
            const pref = String(preferredSessionId || app._asrChunkSessionId || '').trim();
            if (pref) return pref;
            const chunkSegs = (segments || []).filter(isChunkLikeAsrSegment);
            if (!chunkSegs.length) return '';
            const sorted = chunkSegs.slice().sort(function (a, b) {
                return String(b && b.created_at || '').localeCompare(String(a && a.created_at || ''));
            });
            return String(sorted[0] && sorted[0].recording_session_id || '').trim();
        }

        function chunkSegmentsForSession(segments, recordingSessionId) {
            const sess = String(recordingSessionId || '').trim();
            if (!sess) return [];
            return (segments || []).filter(function (s) {
                return isChunkLikeAsrSegment(s) && String(s && s.recording_session_id || '') === sess;
            });
        }

        function formatAsrSegmentBytes(bytes) {
            const n = Number(bytes || 0);
            if (!Number.isFinite(n) || n <= 0) return '';
            if (n >= 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`;
            return `${Math.max(1, Math.round(n / 1024))} KB`;
        }

        function formatAsrSegmentDuration(sec) {
            const n = Number(sec || 0);
            if (!Number.isFinite(n) || n <= 0) return '';
            const total = Math.round(n);
            const m = Math.floor(total / 60);
            const s = String(total % 60).padStart(2, '0');
            return `${m}:${s}`;
        }

        function formatAsrSegmentDate(raw) {
            if (!raw) return '';
            try {
                return new Date(raw).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
            } catch (_) {
                return '';
            }
        }

        function groupAsrSegmentsForDisplay(segments, preferredSessionId) {
            const chunkBySession = {};
            const batchSegments = [];
            (segments || []).forEach(function (s) {
                if (isChunkLikeAsrSegment(s)) {
                    const sid = String(s && s.recording_session_id || '').trim() || 'unknown';
                    if (!chunkBySession[sid]) chunkBySession[sid] = [];
                    chunkBySession[sid].push(s);
                } else {
                    batchSegments.push(s);
                }
            });
            const pref = String(preferredSessionId || app._asrChunkSessionId || '').trim();
            const chunkGroups = Object.keys(chunkBySession).map(function (sid) {
                return {
                    sessionId: sid,
                    segments: chunkBySession[sid].slice().sort(function (a, b) {
                        return (Number(a && a.chunk_index) || 0) - (Number(b && b.chunk_index) || 0);
                    }),
                };
            }).sort(function (a, b) {
                if (pref) {
                    if (a.sessionId === pref && b.sessionId !== pref) return -1;
                    if (b.sessionId === pref && a.sessionId !== pref) return 1;
                }
                const aTs = String(a.segments[0] && a.segments[0].created_at || '');
                const bTs = String(b.segments[0] && b.segments[0].created_at || '');
                return bTs.localeCompare(aTs);
            });
            return { chunkGroups: chunkGroups, batchSegments: batchSegments };
        }

        function summarizeChunkGroup(segments) {
            let totalSec = 0;
            let transcribed = 0;
            (segments || []).forEach(function (s) {
                const d = Number(s && s.duration_sec);
                if (Number.isFinite(d) && d > 0) totalSec += d;
                if (String(s && s.transcription_status || '').toLowerCase() === 'transcribed') transcribed += 1;
            });
            return { totalSec: totalSec, transcribed: transcribed, count: (segments || []).length };
        }

        function appendAsrSegmentRow(listEl, encounterId, segment, opts) {
            opts = opts || {};
            const eid = String(encounterId || '');
            const isChunkPart = !!opts.isChunkPart;
            const chunkLabel = opts.chunkLabel || '';
            const seq = Number(segment.sequence_index || 0) || 0;
            const statusRaw = String(segment.transcription_status || segment.status || 'saved').toLowerCase();
            const statusClass = statusRaw === 'transcribed'
                ? 'transcribed'
                : statusRaw === 'failed'
                    ? 'failed'
                    : statusRaw === 'transcribing'
                        ? 'transcribing'
                        : '';
            const statusLabel = statusRaw === 'transcribed'
                ? 'Transcribed'
                : statusRaw === 'failed'
                    ? 'Failed'
                    : statusRaw === 'transcribing'
                        ? 'Running'
                        : 'Saved';

            const row = document.createElement('div');
            row.className = 'asr-segment-row' + (isChunkPart ? ' asr-segment-row--chunk-part' : '');

            const title = document.createElement('div');
            title.className = 'asr-segment-title';
            title.textContent = chunkLabel || ('Segment ' + (seq || '?'));

            const detailParts = [
                formatAsrSegmentDate(segment.transcribed_at || segment.uploaded_at || segment.created_at),
                formatAsrSegmentDuration(segment.duration_sec),
                formatAsrSegmentBytes(segment.file_size),
            ].filter(Boolean);
            const detail = document.createElement('div');
            detail.className = 'asr-segment-detail';
            detail.textContent = detailParts.join(' · ');
            if (!detail.textContent) detail.textContent = String(segment.file_name || '').trim();

            const controls = document.createElement('div');
            controls.className = 'asr-segment-controls';

            const badge = document.createElement('span');
            badge.className = ('asr-segment-status ' + statusClass).trim();
            badge.textContent = statusLabel;
            controls.appendChild(badge);

            row.appendChild(title);
            row.appendChild(detail);
            row.appendChild(controls);
            listEl.appendChild(row);
            return { badge: badge };
        }

        function renderAsrSegmentListForEncounter(encounterId) {
            const list = document.getElementById('asrSegmentsList');
            if (!list) return;
            const eid = String(encounterId || (app && app.activeEncounterId) || '');
            const segments = getCachedAsrSegmentsForEncounter(eid);
            list.textContent = '';
            const grouped = groupAsrSegmentsForDisplay(segments, app._asrChunkSessionId);
            const hasDisplayable = grouped.chunkGroups.length > 0 || grouped.batchSegments.length > 0;
            if (!eid || !hasDisplayable || !(app && app.asrSegmentsExpanded)) {
                list.classList.add('hidden');
                return;
            }
            list.classList.remove('hidden');

            grouped.chunkGroups.forEach(function (group, groupIdx) {
                const summary = summarizeChunkGroup(group.segments);
                const isPrimary = groupIdx === 0;
                const card = document.createElement('div');
                card.className = 'asr-segment-group' + (isPrimary ? ' asr-segment-group--primary' : '');

                const header = document.createElement('div');
                header.className = 'asr-segment-group-header';

                const heading = document.createElement('div');
                heading.className = 'asr-segment-group-heading';
                const titleEl = document.createElement('span');
                titleEl.className = 'asr-segment-group-title';
                titleEl.textContent = 'Full recording';
                const badgeEl = document.createElement('span');
                badgeEl.className = 'asr-segment-group-badge';
                badgeEl.textContent = summary.count + ' chunk' + (summary.count === 1 ? '' : 's');
                heading.appendChild(titleEl);
                heading.appendChild(badgeEl);

                const hint = document.createElement('p');
                hint.className = 'asr-segment-group-hint';
                hint.textContent = grouped.chunkGroups.length > 1
                    ? 'One of the saved recordings on this encounter. Use Re-transcribe all above to update everything.'
                    : 'Saved recording split into chunks during capture. Use Re-transcribe all above to update the transcript.';

                const meta = document.createElement('div');
                meta.className = 'asr-segment-group-meta';
                const metaParts = [];
                if (summary.totalSec > 0) metaParts.push(formatAsrSegmentDuration(summary.totalSec));
                metaParts.push(summary.transcribed + '/' + summary.count + ' transcribed');
                meta.textContent = metaParts.join(' · ');

                header.appendChild(heading);
                header.appendChild(hint);
                header.appendChild(meta);
                card.appendChild(header);

                const parts = document.createElement('div');
                parts.className = 'asr-segment-group-parts';
                group.segments.forEach(function (segment, idx) {
                    const role = String(segment && segment.segment_role || '');
                    const chunkNum = Number(segment && segment.chunk_index);
                    const label = role === 'partial_tail'
                        ? 'Final chunk'
                        : ('Chunk ' + (Number.isFinite(chunkNum) ? (chunkNum + 1) : (idx + 1)));
                    appendAsrSegmentRow(parts, eid, segment, { isChunkPart: true, chunkLabel: label });
                });
                card.appendChild(parts);
                list.appendChild(card);
            });

            if (grouped.batchSegments.length > 0) {
                const batchWrap = document.createElement('div');
                batchWrap.className = 'asr-segment-group asr-segment-group--batch';
                if (grouped.chunkGroups.length > 0) {
                    const batchHeader = document.createElement('div');
                    batchHeader.className = 'asr-segment-group-header asr-segment-group-header--compact';
                    const batchTitle = document.createElement('span');
                    batchTitle.className = 'asr-segment-group-title';
                    batchTitle.textContent = 'Other saved audio';
                    batchHeader.appendChild(batchTitle);
                    batchWrap.appendChild(batchHeader);
                }
                const batchParts = document.createElement('div');
                batchParts.className = 'asr-segment-group-parts';
                grouped.batchSegments.forEach(function (segment) {
                    appendAsrSegmentRow(batchParts, eid, segment, { isChunkPart: false });
                });
                batchWrap.appendChild(batchParts);
                list.appendChild(batchWrap);
            }
        }

        function rememberAsrSegment(segment) {
            try {
                const eid = segment && segment.encounter_id ? String(segment.encounter_id) : '';
                const id = segment && segment.id ? String(segment.id) : '';
                if (!eid || !id) return;
                const ids = getAsrSegmentIdsForEncounter(eid);
                if (!ids.includes(id)) ids.push(id);
                localStorage.setItem(asrSegmentIdsKey(eid), JSON.stringify(ids));
                app.asrSegmentsByEncounter = app.asrSegmentsByEncounter || {};
                const existing = Array.isArray(app.asrSegmentsByEncounter[eid]) ? app.asrSegmentsByEncounter[eid] : [];
                const next = existing.filter((s) => String(s && s.id) !== id);
                next.push(segment);
                next.sort((a, b) => {
                    const ai = Number(a && a.sequence_index) || 0;
                    const bi = Number(b && b.sequence_index) || 0;
                    if (ai !== bi) return ai - bi;
                    return String(a && a.created_at || '').localeCompare(String(b && b.created_at || ''));
                });
                app.asrSegmentsByEncounter[eid] = next;
                renderAsrSegmentListForEncounter(eid);
            } catch (_) {}
        }

        async function fileSha256Hex(file) {
            const buf = await file.arrayBuffer();
            if (window.crypto && window.crypto.subtle && typeof window.crypto.subtle.digest === 'function') {
                const digest = await window.crypto.subtle.digest('SHA-256', buf);
                return Array.from(new Uint8Array(digest)).map((b) => b.toString(16).padStart(2, '0')).join('');
            }
            // Very old browsers: server verification still runs, but the client cannot pre-check the hash.
            return '';
        }

        async function fetchAsrSegmentsForEncounter(encounterId) {
            const token = getAuthToken();
            const eid = String(encounterId || (app && app.activeEncounterId) || '');
            if (!token || !eid) return [];
            const resp = await apiFetch(`/asr/segments?encounter_id=${encodeURIComponent(eid)}`, {
                method: 'GET',
                headers: { 'Authorization': `Bearer ${token}` },
            });
            if (!resp.ok) {
                throw new Error(`Could not load recording segments: ${resp.status}`);
            }
            const segments = await resp.json();
            app.asrSegmentsByEncounter = app.asrSegmentsByEncounter || {};
            app.asrSegmentsByEncounter[eid] = Array.isArray(segments) ? segments : [];
            try {
                localStorage.setItem(
                    asrSegmentIdsKey(eid),
                    JSON.stringify((app.asrSegmentsByEncounter[eid] || []).map((s) => String(s.id)).filter(Boolean))
                );
            } catch (_) {}
            try {
                if (typeof window._refreshRetranscribeUi === 'function') window._refreshRetranscribeUi();
            } catch (_) {}
            renderAsrSegmentListForEncounter(eid);
            return app.asrSegmentsByEncounter[eid];
        }

        async function fetchEncounterTranscriptText(encounterId) {
            const token = getAuthToken();
            const eid = String(encounterId || (app && app.activeEncounterId) || '');
            if (!token || !eid) return '';
            let url = '/asr/segments/encounter/' + encodeURIComponent(eid) + '/transcript';
            if (window.AsrSettings && typeof window.AsrSettings.appendRefineQuery === 'function') {
                url = window.AsrSettings.appendRefineQuery(url);
            }
            const resp = await apiFetch(url, {
                headers: { 'Authorization': 'Bearer ' + token },
            });
            if (!resp.ok) return '';
            const data = await resp.json();
            return String(data && data.transcript_text || '').trim();
        }

        async function applyEncounterTranscriptText(encounterId, text) {
            const eid = String(encounterId || (app && app.activeEncounterId) || '');
            const merged = String(text || '').trim();
            if (!eid || !merged) return false;
            const tx = document.getElementById('transcriptionDisplay');
            if (tx) {
                const useBaseline = app && app.asrBaselineBeforeRecording != null;
                const base = useBaseline ? app.asrBaselineBeforeRecording : '';
                tx.value = base ? combineAsrBaselineWithSession(base, merged) : merged;
            }
            if (typeof updateCharacterCounter === 'function') {
                updateCharacterCounter();
            }
            saveToStorage();
            return true;
        }

        async function retranscribeAllEncounter(encounterId) {
            const token = getAuthToken();
            const eid = String(encounterId || (app && app.activeEncounterId) || '');
            if (!token || !eid) {
                throw new Error('missing auth or encounter for re-transcribe all');
            }
            let url = '/asr/segments/encounter/' + encodeURIComponent(eid) + '/retranscribe-all';
            if (window.AsrSettings && typeof window.AsrSettings.appendRefineQuery === 'function') {
                url = window.AsrSettings.appendRefineQuery(url);
            }
            const resp = await apiFetch(url, {
                method: 'POST',
                headers: { 'Authorization': 'Bearer ' + token },
            });
            if (!resp.ok) {
                throw new Error('Re-transcribe all failed: ' + resp.status);
            }
            return resp.json();
        }

        async function renderAsrSegmentsForEncounter(encounterId, options = {}) {
            const eid = String(encounterId || (app && app.activeEncounterId) || '');
            if (!eid) return false;
            const segments = await fetchAsrSegmentsForEncounter(eid);
            let text = '';
            if (getAuthToken()) {
                try {
                    text = await fetchEncounterTranscriptText(eid);
                } catch (_) {}
            }
            if (!text) {
                const transcribed = (segments || []).filter(function (s) {
                    return String(s && s.transcription_status || '').toLowerCase() === 'transcribed';
                });
                const backup = transcribed.find(function (s) { return String(s && s.segment_role || '') === 'full_backup'; });
                if (backup) {
                    text = String(backup.committed_text || backup.transcript_text || '').trim();
                } else {
                    text = transcribed
                        .map(function (s) { return String((s && (s.refined_committed_text || s.committed_text || s.transcript_text)) || '').trim(); })
                        .filter(Boolean)
                        .join('\n\n');
                }
            }
            if (!text && !options.allowEmpty) return false;
            await applyEncounterTranscriptText(eid, text);
            return true;
        }

        /** Lightweight polling fetch+parse for /pipeline — single deadline covers both fetch and body-parse. */
        function _drainPollOnce(eid, pollQs, token, timeoutMs) {
            var ctrl = (typeof AbortController !== 'undefined') ? new AbortController() : null;
            var tmr = null;
            if (ctrl) tmr = setTimeout(function () { try { ctrl.abort(); } catch (_) {} }, timeoutMs || 10000);
            return apiFetch('/asr/segments/encounter/' + encodeURIComponent(eid) + '/pipeline' + pollQs, {
                headers: { 'Authorization': 'Bearer ' + token },
                signal: ctrl ? ctrl.signal : undefined,
            }).then(function (resp) {
                if (!resp.ok) throw new Error('HTTP ' + resp.status);
                return resp.json();
            }).then(function (data) {
                if (tmr) clearTimeout(tmr);           // clear AFTER parse completes
                return data;
            }).catch(function (e) {
                if (tmr) clearTimeout(tmr);
                return null;
            });
        }

        /** Timeout-wrapped GET /pipeline fetch — 15s × 3 attempts. Safe to retry. */
        async function fetchAsrPipelineMerged(encounterId, recordingSessionId) {
            var attempts = 3;
            var timeoutMs = 15000;
            for (var att = 1; att <= attempts; att++) {
                var token = getAuthToken();
                var eid = String(encounterId || (app && app.activeEncounterId) || '');
                var sess = String(recordingSessionId || '');
                if (!token || !eid || !sess) return '';
                var qs = '?recording_session_id=' + encodeURIComponent(sess);
                var ctrl = (typeof AbortController !== 'undefined') ? new AbortController() : null;
                var tmr = null;
                if (ctrl) tmr = setTimeout(function () { try { ctrl.abort(); } catch (_) {} }, timeoutMs);
                try {
                    var pr = await apiFetch('/asr/segments/encounter/' + encodeURIComponent(eid) + '/pipeline' + qs, {
                        headers: { 'Authorization': 'Bearer ' + token },
                        signal: ctrl ? ctrl.signal : undefined,
                    });
                    if (!pr.ok) {
                        if (tmr) clearTimeout(tmr);
                        console.warn('[ASR] fetchMerged attempt ' + att + ': HTTP ' + pr.status);
                        continue;
                    }
                    var pipeline = await pr.json();
                    if (tmr) clearTimeout(tmr);
                    return String(pipeline && pipeline.merged_transcript_text || '').trim();
                } catch (e) {
                    if (tmr) clearTimeout(tmr);
                    console.warn('[ASR] fetchMerged attempt ' + att + ' failed', e);
                    if (att === attempts) {
                        try { _reportAsrIncident({ stage: 'client.fetchMerged', outcome: 'retries_exhausted', traceId: '', message: 'GET /pipeline failed after ' + attempts + ' attempts', ctx_state: (app && app.audioPipelinePhase) || '' }); } catch (_) {}
                        throw new Error('fetchAsrPipelineMerged retries exhausted');
                    }
                }
                if (att < attempts) await new Promise(function (res) { setTimeout(res, 1000); });
            }
            throw new Error('fetchAsrPipelineMerged should not reach here');
        }

        /** Drain: single POST with 60s timeout, then fall back to polling GET /pipeline.
            Never re-POSTs — timeout does not prove the server stopped, so a second POST risks
            overlapping with a still-running drain and racing through pending chunks. */
        async function drainAsrChunkPipeline(encounterId, recordingSessionId) {
            var token = getAuthToken();
            var eid = String(encounterId || (app && app.activeEncounterId) || '');
            var sess = String(recordingSessionId || '');
            if (!token || !eid) throw new Error('missing auth or encounter for chunk drain');

            var drainUrl = '/asr/segments/encounter/' + encodeURIComponent(eid) + '/drain';
            var drainParams = [];
            if (sess) drainParams.push('recording_session_id=' + encodeURIComponent(sess));
            if (window.AsrSettings && typeof window.AsrSettings.getDiarizeEnabled === 'function' && window.AsrSettings.getDiarizeEnabled()) {
                drainParams.push('diarize=true');
            }
            if (drainParams.length) drainUrl += '?' + drainParams.join('&');

            // Phase 1: Single POST with 60s timeout — deadline covers response-body parsing too.
            var drainCtrl = (typeof AbortController !== 'undefined') ? new AbortController() : null;
            var drainTmr = null;
            if (drainCtrl) drainTmr = setTimeout(function () { try { drainCtrl.abort(); } catch (_) {} }, 60000);
            var drainTimedOut = false;
            var drainPipeline = null;
            try {
                var drainResp = await apiFetch(drainUrl, {
                    method: 'POST',
                    headers: { 'Authorization': 'Bearer ' + token },
                    signal: drainCtrl ? drainCtrl.signal : undefined,
                });
                // Include json() in same block as fetch — a failed parse or mid-parse abort
                // should be treated the same as a timeout: fall through to Phase 2 polling.
                if (drainResp.ok) {
                    drainPipeline = await drainResp.json();
                }
            } catch (e) {
                if (drainTmr) clearTimeout(drainTmr);
                drainTimedOut = true;
            }

            if (drainTmr) clearTimeout(drainTmr);           // clear timer before any path continues
            if (!drainTimedOut && drainPipeline) {
                var pollQs = sess ? ('?recording_session_id=' + encodeURIComponent(sess)) : '';
                for (var pi = 0; pi < 30 && drainPipeline && drainPipeline.state === 'processing'; pi++) {
                    await new Promise(function (res) { setTimeout(res, 2000); });
                    var p = await _drainPollOnce(eid, pollQs, token, 10000);
                    if (p) drainPipeline = p;
                }
                return drainPipeline;
            }

            // Phase 2: POST timed out — poll GET /pipeline instead of re-POSTing.
            // (b) Already reports to the incident store via _reportAsrIncident; the outer
            // catch only protects the fire-and-forget reporter from throwing.
            try { _reportAsrIncident({ stage: 'client.drain', outcome: drainTimedOut ? 'timeout_polling' : 'failed_polling', traceId: '', message: 'Drain POST did not complete, polling instead', ctx_state: (app && app.audioPipelinePhase) || '' }); } catch (_) {}
            pollQs = sess ? ('?recording_session_id=' + encodeURIComponent(sess)) : '';
            var lastP = null;
            for (var pp = 0; pp < 8; pp++) {
                await new Promise(function (res) { setTimeout(res, 5000); });
                var result = await _drainPollOnce(eid, pollQs, token, 10000);
                if (result) {
                    lastP = result;
                    if ('ready' === result.state || 'failed' === result.state) return lastP;
                }
            }
            return lastP || { state: 'timeout' };
        }

        async function uploadAsrRecordingSegment(file, meta = {}) {
            if (!file) return { ok: false, mode: 'local', reason: 'missing_file' };
            const token = getAuthToken();
            const eid = String(meta.encounterId || (app && app.activeEncounterId) || '');
            if (!token || !eid) {
                return { ok: false, mode: 'local', reason: !token ? 'missing_token' : 'missing_encounter' };
            }
            const clientRecordingId = String(
                meta.clientRecordingId
                || (meta.recordingSessionId ? meta.recordingSessionId + '-full' : '')
                || `rec_${Date.now()}_${Math.random().toString(16).slice(2)}`
            );
            const fileName = String(meta.fileName || file.name || `${clientRecordingId}.webm`);
            const sha256 = await fileSha256Hex(file);
            const formData = new FormData();
            formData.append('encounter_id', eid);
            formData.append('client_recording_id', clientRecordingId);
            formData.append('expected_file_name', fileName);
            formData.append('expected_size_bytes', String(file.size || 0));
            if (sha256) formData.append('expected_sha256', sha256);
            if (meta.durationSec != null && Number.isFinite(Number(meta.durationSec))) {
                formData.append('duration_sec', String(meta.durationSec));
            }
            if (meta.startedAt) formData.append('started_at', String(meta.startedAt));
            if (meta.stoppedAt) formData.append('stopped_at', String(meta.stoppedAt));
            if (meta.recordingSessionId) formData.append('recording_session_id', String(meta.recordingSessionId));
            if (meta.segmentRole) formData.append('segment_role', String(meta.segmentRole));
            if (meta.chunkIndex != null && Number.isFinite(Number(meta.chunkIndex))) {
                formData.append('chunk_index', String(meta.chunkIndex));
            }
            formData.append('file', file, fileName);

            const ctrl = (typeof AbortController !== 'undefined') ? new AbortController() : null;
            let timer = null;
            if (ctrl) {
                timer = setTimeout(() => {
                    try { ctrl.abort(); } catch (_) {}
                }, TRANSCRIBE_TIMEOUT_MS);
            }
            try {
                const resp = await apiFetch('/asr/segments', {
                    method: 'POST',
                    headers: { 'Authorization': `Bearer ${token}` },
                    body: formData,
                    signal: ctrl ? ctrl.signal : undefined,
                });
                if (!resp.ok) {
                    const err = new Error(`Recording segment upload failed: ${resp.status}`);
                    err.httpStatus = resp.status;
                    throw err;
                }
                const segment = await resp.json();
                if (String(segment.client_recording_id || '') !== clientRecordingId) {
                    throw new Error('Server returned a different recording id');
                }
                if (String(segment.encounter_id || '') !== eid) {
                    throw new Error('Server returned a different encounter id');
                }
                if (Number(segment.file_size || 0) !== Number(file.size || 0)) {
                    throw new Error('Server returned a different file size');
                }
                if (sha256 && String(segment.sha256 || '').toLowerCase() !== sha256.toLowerCase()) {
                    throw new Error('Server returned a different file hash');
                }
                rememberAsrSegment(segment);
                return {
                    ok: true,
                    mode: 'server',
                    retained: true,
                    segmentId: segment.id,
                    segment,
                };
            } catch (e) {
                console.warn('[ASR] Recording segment upload failed; local copy remains for retry', e);
                return {
                    ok: false,
                    mode: 'local',
                    reason: String(e && e.message ? e.message : e),
                    httpStatus: Number(e && e.httpStatus ? e.httpStatus : 0),
                };
            } finally {
                if (timer) clearTimeout(timer);
            }
        }

        async function transcribeAsrSegment(segmentId, options = {}) {
            const opts = options || {};
            const quiet = !!opts.quiet;
            const returnResult = !!opts.returnResult;
            if (!segmentId) {
                return returnResult ? { ok: false, reason: 'missing_segment', httpStatus: 0 } : false;
            }
            const token = getAuthToken();
            if (!token) {
                if (!quiet) showToast('Sign In Required', 'Please sign in to transcribe audio.', 'warning');
                return returnResult ? { ok: false, reason: 'missing_token', httpStatus: 0 } : false;
            }
            let mode = opts.mode || 'batch';
            if (!opts.mode) {
                try {
                    const eid = String(app && app.activeEncounterId || '');
                    const cached = (app.asrSegmentsByEncounter && app.asrSegmentsByEncounter[eid]) || [];
                    const seg = cached.find(function (s) { return String(s && s.id || '') === String(segmentId); });
                    const role = String(seg && seg.segment_role || '');
                    if (role === 'chunk' || role === 'partial_tail') mode = 'chunk';
                } catch (_) {}
            }
            const recordingBackupTrace = !!opts.recordingBackupTrace;
            if (recordingBackupTrace) {
                app._recordingTranscribeOutcome = { transcribeOk: false };
            }
            const ctrl = (typeof AbortController !== 'undefined') ? new AbortController() : null;
            let timer = null;
            let traceId = `ui_${Date.now()}_${Math.random().toString(16).slice(2)}`;
            const diarizeOn = window.AsrSettings && typeof window.AsrSettings.getDiarizeEnabled === 'function' && window.AsrSettings.getDiarizeEnabled();
            const diarizeQs = diarizeOn ? '&diarize=true' : '';
            const timeoutMs = diarizeOn ? TRANSCRIBE_DIARIZE_TIMEOUT_MS : TRANSCRIBE_TIMEOUT_MS;
            showProgress('audioProgress', 'audioProgressBar');
            try {
                if (ctrl) {
                    timer = setTimeout(() => {
                        try { ctrl.abort(); } catch (_) {}
                    }, timeoutMs);
                }
                const resp = await apiFetch(`/asr/segments/${encodeURIComponent(segmentId)}/transcribe?mode=${encodeURIComponent(mode)}${diarizeQs}`, {
                    method: 'POST',
                    headers: {
                        'Authorization': `Bearer ${token}`,
                        'x-asr-trace-id': traceId,
                    },
                    signal: ctrl ? ctrl.signal : undefined,
                });
                if (!resp.ok) {
                    const err = new Error(`Segment transcription failed: ${resp.status}`);
                    err.httpStatus = resp.status;
                    throw err;
                }
                const segment = await resp.json();
                rememberAsrSegment(segment);
                if (quiet && mode === 'chunk') {
                    /* Chunk pipeline updates UI via fetchMerged / onTranscript. */
                } else if (mode === 'batch') {
                    const display = String(
                        segment.refined_committed_text || segment.committed_text || segment.transcript_text || ''
                    ).trim();
                    const eid = segment.encounter_id || app.activeEncounterId;
                    if (eid) {
                        if (diarizeOn) {
                            app.asrBaselineBeforeRecording = "";
                            await renderAsrSegmentsForEncounter(eid);
                        } else if (display) {
                            await applyEncounterTranscriptText(eid, display);
                        }
                    }
                } else {
                    await renderAsrSegmentsForEncounter(segment.encounter_id || app.activeEncounterId);
                }
                if (recordingBackupTrace && app._recordingTranscribeOutcome) {
                    app._recordingTranscribeOutcome.transcribeOk = true;
                }
                if (!quiet) showToast('Success', 'Audio segment transcribed.', 'success');
                return returnResult ? { ok: true, segment, httpStatus: resp.status } : true;
            } catch (e) {
                console.warn('[ASR] Segment transcription failed', e);
                if (!quiet) showToast('Transcription unavailable', 'Audio segment saved — retry transcription when ready.', 'warning');
                if (app.pendingGenerateAfterTranscription) {
                    clearPendingGenerateAfterTranscription();
                    setAudioPipelinePhase('idle');
                }
                return returnResult ? {
                    ok: false,
                    httpStatus: Number(e && e.httpStatus ? e.httpStatus : 0),
                    message: String(e && e.message ? e.message : e),
                } : false;
            } finally {
                if (timer) clearTimeout(timer);
                hideProgress('audioProgress');
            }
        }

        async function transcribeUploadedAudioFile(file) {
            if (!file) {
                showToast('Error', 'No audio file selected.', 'error');
                return false;
            }
            const eid = (app && app.activeEncounterId) ? String(app.activeEncounterId) : '';
            if (!eid) {
                showToast('No Encounter', 'Open an encounter before importing audio.', 'warning');
                return false;
            }
            const recordingSessionId = `import_${Date.now()}_${Math.random().toString(16).slice(2)}`;
            showProgress('audioProgress', 'audioProgressBar');
            try {
                const uploaded = await uploadAsrRecordingSegment(file, {
                    encounterId: eid,
                    fileName: file.name || `${recordingSessionId}.webm`,
                    recordingSessionId,
                });
                if (!uploaded || !uploaded.ok || !uploaded.segmentId) {
                    showToast('Upload failed', 'Could not save the audio file to this encounter.', 'error');
                    return false;
                }
                return await transcribeAsrSegment(uploaded.segmentId, { quiet: false, returnResult: false });
            } finally {
                hideProgress('audioProgress');
            }
        }

        window.transcribeUploadedAudioFile = transcribeUploadedAudioFile;

        async function ocrOnServer(file) {
            const token = getAuthToken();
            if (!token) {
                showToast('Sign In Required', 'Please sign in before running OCR.', 'warning');
                throw new Error('Missing authentication token');
            }
            lastServiceRetry = () => ocrOnServer(file);
            const apiBase = getApiBase(app.settings.serverUrl || '/api');
            const uploadName = file.name || "upload";
            const uploadType = file.type || "application/octet-stream";
            const uploadCopy = new File([await file.arrayBuffer()], uploadName, { type: uploadType });
            const form = new FormData();
            form.append("file", uploadCopy, uploadName);

            const resp = await apiFetch('/ocr', {
                method: 'POST',
                headers: { 'Authorization': `Bearer ${token}` },
                body: form
            });

            if (!resp.ok) {
                let detail = null;
                try { detail = await resp.json(); } catch (e) {}
                if (detail) {
                    showServiceErrorBanner(detail?.detail || detail, lastServiceRetry);
                }
                const err = new Error(`OCR server error ${resp.status}`);
                err.status = resp.status;
                throw err;
            }
            const data = await resp.json();
            if (!data?.success) {
                throw new Error('Server OCR response missing success flag');
            }
            hideServiceErrorBanner();
            return {
                text: data.text || '',
                confidence: data.confidence ?? null,
                engine: data.engine_used || 'server'
            };
        }

        async function processDocuments(files) {
            showProgress('documentProgress', 'documentProgressBar');
            try { window.__cngOcrBusy = true; } catch (e) {}

            // V7 API: Determine target field from drop or default to oldVisitsData
            const targetFieldId = window.currentDropTargetField || window.lastInputFieldId || 'oldVisitsData';
            const targetEl = document.getElementById(targetFieldId);
            const existing = targetEl ? targetEl.value : '';
            const results = [];

            // Client-side stabilization: even though we process sequentially, adding a small
            // cooldown between OCR calls helps avoid back-to-back VRAM spikes / server overload.
            const sleep = (ms) => new Promise(r => setTimeout(r, ms));
            const perFileCooldownMs = files.length > 1 ? 750 : 0;

            try {
                for (let i = 0; i < files.length; i++) {
                    const file = files[i];
                    const pct = Math.min(90, Math.round(((i) / Math.max(1, files.length)) * 90) + 5);
                    document.getElementById('documentProgressBar').style.width = pct + '%';

                    let text = '';
                    let note = '';
                    let engine = '';
                    let conf = null;

                    // Match ASR behavior - check health (queued is still online)
                    if (app.connection.lastHealthOk !== false) {
                        try {
                            const out = await ocrOnServer(file);
                            text = sanitizeClinicalText(out.text || '');
                            note = out.confidence != null ? ` (OCR, conf ${out.confidence})` : ' (OCR)';
                            engine = 'ocr';
                            conf = out.confidence ?? null;
                        } catch (e) {
                            const code = e && (e.status || 0);
                            if ([0, 401, 403, 404, 408, 429, 500, 502, 503, 504].includes(code)) {
                                console.warn('Server OCR failed; queuing for retry:', e);
                                queueRequest('ocr', { file: file, fileName: file.name });
                                // (a) Toast is the user-facing 'queued' surfacing; this guard only
                                // keeps a toast DOM failure from aborting the already-queued flow.
                                try { showToast('Queued for Retry', `Will retry once connected${code ? ` (reason: ${code})` : ''}`, 'warning'); } catch (_) {}
                                continue;
                            }
                            throw e;
                        }
                    } else {
                        queueRequest('ocr', { file: file, fileName: file.name });
                        // (a) Same best-effort toast guard: queueing already happened above.
                        try { showToast('Queued for Retry', 'Connect in Settings, will auto-run when online', 'warning'); } catch (_) {}
                        continue;
                    }

                    results.push(`--- ${file.name}${note} ---\n${text || '[No text detected]'}`);

                    if (perFileCooldownMs && i < files.length - 1) {
                        await sleep(perFileCooldownMs);
                    }
                }

                if (results.length) {
                    const block = 'Extracted Text:\n\n' + results.join('\n\n');
                    const newText = existing ? `${existing}\n\n${block}` : block;

                    // V7 API: Append to the correct target field
                    if (targetEl) {
                        setFieldValue(targetFieldId, newText);
                    } else {
                        setChartDataValue(newText);
                    }
                    saveToStorage();
                    showToast('Success', `Processed ${files.length} document(s) to ${getFieldDisplayName(targetFieldId)}`, 'success');
                } else {
                    showToast('Warning', 'No text extracted', 'warning');
                }
            } catch (err) {
                console.error('OCR pipeline error:', err);
                showToast('Error', 'OCR processing failed', 'error');
            } finally {
                hideProgress('documentProgress');
                try { window.__cngOcrBusy = false; } catch (e) {}
                // Clear the drop target
                window.currentDropTargetField = null;
            }
        }

        // Note types safe to generate WHILE recording: document-oriented types
        // consume prior visits / chart data, not the live conversation, so a
        // mid-visit generation cannot be "premature" and does not race the ASR
        // pipeline (whisper ports are separate services). MUST stay in sync with
        // DOCUMENT_ORIENTED_NOTE_TYPES in server/core/prompt/builder.py (server is truth).
        // Only pre_encounter_prep unlocked for now; 'summarize' deferred by design.
        const MID_RECORDING_ALLOWED_TYPES = ['pre_encounter_prep'];

        async function generateNote() {
            const noteType = document.getElementById('noteType').value;
            if (window.CNGGenerateUI && typeof window.CNGGenerateUI.isAudioCaptureActive === 'function') {
                if (window.CNGGenerateUI.isAudioCaptureActive()
                    && MID_RECORDING_ALLOWED_TYPES.indexOf(noteType) < 0) {
                    showToast(
                        'Recording in progress',
                        'Stop recording first, then generate your note.',
                        'info'
                    );
                    return;
                }
            }

            // Prevent multiple concurrent generations
            if (app.isStreaming) {
                showToast('Already Generating', 'A note is already being generated. Please wait or stop the current generation.', 'warning');
                return;
            }

            // V7 API: Check if any of the 3 input fields has content
            const transcriptionDisplayValue = document.getElementById('transcriptionDisplay')?.value?.trim() || '';
            const transcriptionNotesValue = document.getElementById('transcriptionData')?.value?.trim() || '';
            const oldVisitsValue = document.getElementById('oldVisitsData')?.value?.trim() || '';
            const mixedOtherValue = document.getElementById('mixedOtherData')?.value?.trim() || '';

            const hasAnyContent = transcriptionDisplayValue || transcriptionNotesValue || oldVisitsValue || mixedOtherValue;

            if (!hasAnyContent) {
                showToast('Error', 'Please enter content in at least one input field before generating note', 'error');
                return;
            }

            try {
                const hasQueuedOcr = Array.isArray(app.requestQueue) && app.requestQueue.some(
                    (r) => r && r.type === 'ocr' && queueJobMatchesActiveEncounter(r)
                );
                if (hasQueuedOcr) {
                    const proceed = confirm('There are queued OCR items that haven\'t finished yet.\n\nGenerate now without them, or cancel and wait for OCR to finish?\n\nOK = Generate now, Cancel = Wait');
                    if (!proceed) {
                        showToast('Queued', 'Please run Generate after OCR completes', 'warning');
                        return;
                    }
                }
            } catch (e) { /* ignore */ }

            try {
                // "Queued" means the server is reachable but there are pending jobs for this encounter.
                // Only block generation when health is actually down.
                if (app.connection.lastHealthOk !== false) {
                    await generateNoteOnline(null, noteType);
                } else {
                    // Don't queue note generation - user can simply click again when online
                    showToast('Offline', 'Cannot generate note while offline. Please check your connection.', 'error');
                }
            } catch (error) {
                console.error('Error in generateNote:', error);
                showToast('Generation Error', `Failed to generate note: ${error.message}`, 'error');
            }
        }

        let generationAbortController = null;

        function syncStreamingControlsVisibility() {
            const streamingControls = document.getElementById('streamingControls');
            const isActive = !!generationAbortController || !!app?.isStreaming;
            if (!streamingControls) return;
            if (isActive) {
                streamingControls.classList.remove('hidden');
                streamingControls.style.display = 'flex';
            } else {
                streamingControls.classList.add('hidden');
                streamingControls.style.display = 'none';
            }
        }

        // Defensive reset on load: never show stop bar unless actively streaming.
        document.addEventListener('DOMContentLoaded', () => {
            generationAbortController = null;
            if (window.app) app.isStreaming = false;
            syncStreamingControlsVisibility();
        });

        async function stopGeneration() {
            if (generationAbortController) {
                generationAbortController.abort();
                generationAbortController = null;
                showToast('Stopped', 'Note generation stopped', 'info');
                app.isStreaming = false;
                syncStreamingControlsVisibility();
            }
        }

        async function generateNoteOnline(chartData, noteType) {
            showProgress('noteProgress', 'noteProgressBar');
            document.getElementById('noteCard').classList.remove('hidden');
            document.getElementById('noteCard')?.classList.add('note-generating-active');
            showToast('Generating', 'Creating clinical note...', 'info');
                const noteTextarea = document.getElementById('generatedNote');
            setGeneratedNoteBusy(true);
            updateNoteStatus('Generating note...');
            resetGeneratedNoteEditToggleToPreview();
            if (noteTextarea) {
                noteTextarea.value = '';
            }
            clearConflictsPanel();
            requestGeneratedNotePreviewSync();

            try {
                // Direct API note type mapping (critical: custom note type ids must pass through)
                const apiNoteType = (typeof window.toApiNoteType === 'function')
                    ? window.toApiNoteType(noteType)
                    : (String(noteType || '').trim() || 'consult');

            // Collect inputs from 3 fields
            const transcriptionOnly = document.getElementById('transcriptionDisplay')?.value || '';
            const transcriptionNotes = document.getElementById('transcriptionData')?.value || '';
            const oldVisitsValue = document.getElementById('oldVisitsData')?.value || '';
            const mixedOtherValue = document.getElementById('mixedOtherData')?.value || '';
            const userSpeciality = document.getElementById('userSpeciality')?.value || '';
            const customPrompt = (document.getElementById('encounterInstructionsInput')?.value || '').trim();

                // Sanitize all inputs
                const sanitizedTranscriptOnly = sanitizeUserText(transcriptionOnly);
                const sanitizedNotes = sanitizeUserText(transcriptionNotes);
                const transcriptionSections = [];
                if (sanitizedTranscriptOnly.trim()) {
                    transcriptionSections.push(`TRANSCRIPTION:\n${sanitizedTranscriptOnly.trim()}`);
                }
                if (sanitizedNotes.trim()) {
                    transcriptionSections.push(`CURRENT ENCOUNTER NOTES:\n${sanitizedNotes.trim()}`);
                }
                const sanitizedTranscription = transcriptionSections.join('\n\n');
                const sanitizedOldVisits = sanitizeUserText(oldVisitsValue);
                const sanitizedMixedOther = sanitizeUserText(mixedOtherValue);
                const sanitizedCustomPrompt = sanitizeUserText(customPrompt);

                // Build direct payload
                const formData = new FormData();
                formData.append('note_type', apiNoteType);
                formData.append('transcription_text', sanitizedTranscription);
                formData.append('old_visits_text', sanitizedOldVisits);
                formData.append('mixed_other_text', sanitizedMixedOther);
                formData.append('custom_prompt', sanitizedCustomPrompt);
                formData.append('user_speciality', sanitizeUserText(userSpeciality));
                if (window.app && window.app.activeEncounterId) {
                    formData.append('encounter_id', String(window.app.activeEncounterId));
                }

                // Optional settings
                if (window.app?.settings?.temperature) {
                    formData.append('temperature', window.app.settings.temperature);
                }
                if (window.app?.settings?.maxTokens) {
                    formData.append('max_tokens', window.app.settings.maxTokens);
                }

                const token = getAuthToken();
                if (!token) {
                    showToast('Sign In Required', 'Please sign in to generate notes.', 'warning');
                    return;
                }
                app.isStreaming = true;
                lastServiceRetry = () => generateNoteOnline(chartData, noteType);

                // Create abort controller for streaming
                generationAbortController = new AbortController();
                syncStreamingControlsVisibility();

                // Use direct streaming endpoint
                const apiBase = getApiBase(app.settings.serverUrl || '/api');
                const response = await apiFetch('/generate_v8_stream', {
                    method: 'POST',
                    headers: { 'Authorization': `Bearer ${token}` },
                    body: formData,
                    signal: generationAbortController.signal
                });

                if (!response.ok) {
                    let detail = null;
                    // (a) Best-effort error-detail parse: if the body isn't JSON the
                    // generic `Server error: <status>` throw below still surfaces.
                    try { detail = await response.json(); } catch (e) {}
                    if (detail) {
                        showServiceErrorBanner(detail?.detail || detail, lastServiceRetry);
                    }
                    throw new Error(`Server error: ${response.status}`);
                }

                window.lastGenerationId = response.headers.get('X-Generation-Id');
                updateUiState({ lastGenerationId: window.lastGenerationId });

                const reader = response.body.getReader();
                const decoder = new TextDecoder();

                // --- Marker-aware streaming reader (streamed-note fix) ---
                // The v8 stream now carries two control lines:
                //   __NOTE_RETRY__        -> attempt 1 failed validation; clear
                //                            the box, attempt 2 types in fresh
                //   __NOTE_FINAL__{json}  -> authoritative text + salvage flag
                //                            + rejection reasons (one JSON line)
                // Everything else is live note text, line-buffered on the
                // server. A client-side line buffer makes the reader robust to
                // arbitrary byte boundaries (a marker or the final JSON may be
                // split across TCP chunks).
                let pending = '';
                let finalNote = null; // { text, salvaged, reasons } | null
                const processStreamLine = (line) => {
                    if (line.indexOf('__NOTE_FINAL__') === 0) {
                        const raw = line.slice('__NOTE_FINAL__'.length).trim();
                        let payload = null;
                        try { payload = JSON.parse(raw); }
                        catch (e) { payload = { text: raw, salvaged: false, reasons: [] }; }
                        if (payload && typeof payload === 'object') {
                            finalNote = {
                                text: String(payload.text || ''),
                                salvaged: !!payload.salvaged,
                                reasons: Array.isArray(payload.reasons) ? payload.reasons.map(String) : []
                            };
                        }
                        return;
                    }
                    if (line.indexOf('__NOTE_RETRY__') === 0) {
                        if (noteTextarea) {
                            noteTextarea.value = '';
                            requestGeneratedNotePreviewSync();
                        }
                        updateNoteStatus('Regenerating — first draft failed safety checks (attempt 2 of 2)…');
                        return;
                    }
                    if (noteTextarea && line) {
                        const piece = cleanStreamedNoteText(line);
                        if (piece) {
                            noteTextarea.value += piece + '\n';
                            noteTextarea.scrollTop = noteTextarea.scrollHeight;
                        }
                    }
                };
                while (true) {
                    const { done, value } = await reader.read();
                    if (done) break;
                    pending += decoder.decode(value, { stream: true });
                    let nl;
                    while ((nl = pending.indexOf('\n')) !== -1) {
                        const line = pending.slice(0, nl);
                        pending = pending.slice(nl + 1);
                        processStreamLine(line);
                    }
                }
                pending += decoder.decode(); // flush decoder tail
                if (pending) processStreamLine(pending);

                if (noteTextarea) {
                    if (finalNote) {
                        // The final marker carries the authoritative text
                        // (sanitized + validated, or the retained salvaged
                        // draft) — it wins over the live preview.
                        noteTextarea.value = finalNote.text;
                        applyConflictsSplitFromFullNote(noteTextarea.value);
                        if (finalNote.salvaged) {
                            // Rejection cause lives in the SEPARATE Conflicts
                            // panel (amber block), never in the note box.
                            renderSalvageBanner(finalNote.reasons);
                            updateNoteStatus('Draft kept for review — auto-check flagged it, see Conflicts panel.');
                        }
                        requestGeneratedNotePreviewSync();
                    } else {
                        // No final marker (legacy server / error text): the
                        // old clean-the-whole-box behavior.
                        const cleaned = stripBracketArtifacts(stripNoteStreamMarkers(noteTextarea.value));
                        applyConflictsSplitFromFullNote(cleaned);
                        requestGeneratedNotePreviewSync();
                    }
                }

                // End “busy” UI immediately after the stream finishes — everything below used to run before
                // `finally` hid the progress bar and undimmed the note (saveToStorage, RAG state, etc.).
                generationAbortController = null;
                app.isStreaming = false;
                syncStreamingControlsVisibility();
                hideProgress('noteProgress');
                setGeneratedNoteBusy(false);
                updateNoteStatus('');

                queueMicrotask(() => {
                    try {
                        hideServiceErrorBanner();
                        saveToStorage();
                        if (window.AuthWorkspace && typeof window.AuthWorkspace.queueSave === 'function') {
                            window.AuthWorkspace.queueSave();
                        }
                        showToast('Success', 'Note generated successfully', 'success');
                        // (a) Best-effort post-success UI: scroll-to-actions is a nicety;
                        // a DOM hiccup here must not mask the successful generation toast.
                        try {
                            requestAnimationFrame(() => {
                                requestAnimationFrame(() => {
                                    if (
                                        window.CNGGenerateUI &&
                                        typeof window.CNGGenerateUI.goToGeneratedNoteCardActions === 'function'
                                    ) {
                                        window.CNGGenerateUI.goToGeneratedNoteCardActions();
                                    }
                                });
                            });
                        } catch (_) {}
                        // (a) Best-effort connection-state update: a DOM hiccup here must
                        // not mask the successful generation toast above.
                        try {
                            app.connection.lastHealthOk = true;
                            syncQueueConnectionPill();
                        } catch (_) {}
                        // (a) Fire-and-forget background connectivity probe: non-event by design.
                        setTimeout(() => { try { checkConnection(); } catch {} }, 1000);

                        const genId = window.lastGenerationId;
                        if (genId) {
                            // P4-4 pilot instrumentation: snapshot the freshly
                            // generated text so we can report an edit-distance
                            // ratio later (on pagehide) without ever sending
                            // raw note text off the client -- see
                            // client_usage_reporter.js.
                            try {
                                const baselineText = (typeof window.getMergedGeneratedNoteText === 'function')
                                    ? (window.getMergedGeneratedNoteText() || '').trim()
                                    : (document.getElementById('generatedNote')?.value || '').trim();
                                window.__pilotEditBaseline = { genId: genId, text: baselineText };
                            } catch (_) {}
                            const commentEl = document.getElementById('consultComment');
                            const refsEl = document.getElementById('consultRefs');
                            const ragHintCard = document.getElementById('ragHint');
                            const retryBtn = document.getElementById('retryConsultComment');
                            const consultCard = document.getElementById('consultCommentCard');
                            if (commentEl) setConsultComment('');
                            if (refsEl) refsEl.textContent = '';
                            if (consultCard) consultCard.classList.add('hidden');
                            if (retryBtn) retryBtn.classList.add('hidden');
                            if (ragHintCard) ragHintCard.classList.add('hidden');

                            window.ragState = {
                                ...defaultRagState(),
                                generationId: genId
                            };
                            window.orderRequestsState = {
                                ...defaultOrderRequestsState(),
                                generationId: genId
                            };
                            setEvidenceButtonVisible(true);
                            setOrderRequestsButtonVisible(true);
                            setPatientMaterialsButtonVisible(true);
                            persistRagState();
                            persistOrderRequestsState();

                            void (async () => {
                                try {
                                    const encQs = encounterQueryString();
                                    const metaResponse = await apiFetch(
                                        `/generation/${genId}/meta${encQs ? `?${encQs}` : ''}`,
                                        { headers: { 'Authorization': `Bearer ${token}` } }
                                    );
                                    if (metaResponse.ok) {
                                        const meta = await metaResponse.json();
                                        if (meta.stats) {
                                            debugLog('[Map-Reduce Stats]', meta.stats);
                                        }
                                        if (meta._internal) {
                                            displayUncertainItems(meta._internal);
                                        }
                                    }
                                } catch (e) {
                                    console.warn('Could not fetch generation metadata:', e);
                                }
                            })();
                        }
                    } catch (e) {
                        console.warn('[generateNote] post-stream follow-up', e);
                    }
                });

        } catch (error) {
            if (error.name === 'AbortError') {
                console.log('Generation aborted by user');
                // Mark the partially-generated note so the user knows it was interrupted
                updateNoteStatus('⚠ Generation interrupted');
                const noteTextarea = document.getElementById('generatedNote');
                if (noteTextarea && noteTextarea.value) {
                    noteTextarea.value += '\n\n[⚠ Generation interrupted — partial note above]';
                }
            } else {
                console.error('Generate note error:', error);
                showToast('Error', `Failed to generate note: ${error.message}`, 'error');
            }
            // Don't queue - user can retry manually
        } finally {
            // Reset abort controller and streaming flag
            generationAbortController = null;
            app.isStreaming = false;
            syncStreamingControlsVisibility();
            hideProgress('noteProgress');
            setGeneratedNoteBusy(false);
            updateNoteStatus('');
            document.getElementById('noteCard')?.classList.remove('note-generating-active');
        }
    }

        const queueStorage = {
            db: null,
            initPromise: null,
            supported: typeof indexedDB !== 'undefined',
            init() {
                if (!this.supported) {
                    return Promise.resolve(null);
                }
                if (this.db) {
                    return Promise.resolve(this.db);
                }
                if (this.initPromise) {
                    return this.initPromise;
                }
                this.initPromise = new Promise((resolve) => {
                    try {
                        const request = indexedDB.open('clinical-note-queue', 1);
                        request.onupgradeneeded = (event) => {
                            const db = event.target.result;
                            if (!db.objectStoreNames.contains('files')) {
                                db.createObjectStore('files');
                            }
                        };
                        request.onsuccess = () => {
                            this.db = request.result;
                            if (this.db) {
                                this.db.onclose = () => {
                                    this.db = null;
                                    this.initPromise = null;
                                };
                            }
                            resolve(this.db);
                        };
                        request.onerror = () => {
                            console.error('[QueueStorage] init failed:', request.error);
                            this.supported = false;
                            resolve(null);
                        };
                    } catch (err) {
                        console.error('[QueueStorage] init error:', err);
                        this.supported = false;
                        resolve(null);
                    }
                });
                return this.initPromise;
            },
            async ensureDb() {
                return await this.init();
            },
            async storeFile(key, file) {
                if (!file) return;
                const db = await this.ensureDb();
                if (!db) {
                    throw new Error('IndexedDB unavailable');
                }
                return new Promise((resolve, reject) => {
                    const tx = db.transaction('files', 'readwrite');
                    tx.oncomplete = () => resolve(true);
                    tx.onerror = () => reject(tx.error || new Error('IndexedDB store failed'));
                    tx.objectStore('files').put(file, key);
                });
            },
            async getFile(key) {
                if (!key) return null;
                const db = await this.ensureDb();
                if (!db) return null;
                return new Promise((resolve, reject) => {
                    const tx = db.transaction('files', 'readonly');
                    const req = tx.objectStore('files').get(key);
                    req.onsuccess = () => resolve(req.result || null);
                    req.onerror = () => reject(req.error || new Error('IndexedDB get failed'));
                });
            },
            async hasFile(key) {
                if (!key) return false;
                const db = await this.ensureDb();
                if (!db) return false;
                return new Promise((resolve, reject) => {
                    const tx = db.transaction('files', 'readonly');
                    const req = tx.objectStore('files').get(key);
                    req.onsuccess = () => resolve(!!req.result);
                    req.onerror = () => reject(req.error || new Error('IndexedDB get failed'));
                });
            },
            async deleteFile(key) {
                if (!key) return;
                const db = await this.ensureDb();
                if (!db) return;
                return new Promise((resolve, reject) => {
                    const tx = db.transaction('files', 'readwrite');
                    tx.oncomplete = () => resolve(true);
                    tx.onerror = () => reject(tx.error || new Error('IndexedDB delete failed'));
                    tx.objectStore('files').delete(key);
                });
            },
            async deleteMany(keys) {
                if (!keys || !keys.length) return;
                const db = await this.ensureDb();
                if (!db) return;
                return new Promise((resolve, reject) => {
                    const tx = db.transaction('files', 'readwrite');
                    const store = tx.objectStore('files');
                    keys.forEach((key) => store.delete(key));
                    tx.oncomplete = () => resolve(true);
                    tx.onerror = () => reject(tx.error || new Error('IndexedDB deleteMany failed'));
                });
            },
            async clearAll() {
                const db = await this.ensureDb();
                if (!db) return;
                return new Promise((resolve, reject) => {
                    const tx = db.transaction('files', 'readwrite');
                    tx.oncomplete = () => resolve(true);
                    tx.onerror = () => reject(tx.error || new Error('IndexedDB clear failed'));
                    tx.objectStore('files').clear();
                });
            }
        };

        function formatBytes(bytes) {
            if (typeof bytes !== 'number' || isNaN(bytes)) return '';
            if (bytes < 1024) return `${bytes} B`;
            if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
            return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
        }

        function getQueueStorageMessage(state) {
            switch (state) {
                case 'pending':
                    return 'Saving file for retry...';
                case 'unsupported':
                    return 'Browser cannot persist queued files. Download before refreshing.';
                case 'missing':
                    return 'Stored file missing. Download may fail.';
                default:
                    return '';
            }
        }

        async function downloadQueuedFile(request) {
            const token = getAuthToken();
            if (!token) {
                showToast('Sign In Required', 'Log in to download queued files.', 'warning');
                return;
            }
            // Local-only (offline) queued file
            if (request && request.localOnly && request.data?.fileKey) {
                try {
                    const stored = await queueStorage.getFile(request.data.fileKey);
                    if (!stored) {
                        showToast('Missing', 'Local queued file is missing.', 'error');
                        return;
                    }
                    const blob = stored instanceof Blob ? stored : new Blob([stored]);
                    const fileName = request.data?.fileName || `${request.type}_${request.id}.bin`;
                    const url = URL.createObjectURL(blob);
                    const anchor = document.createElement('a');
                    anchor.href = url;
                    anchor.download = fileName;
                    document.body.appendChild(anchor);
                    anchor.click();
                    setTimeout(() => {
                        URL.revokeObjectURL(url);
                        document.body.removeChild(anchor);
                    }, 200);
                    showToast('Download started', 'Queued file retained. Delete it manually after confirming the download.', 'info');
                } catch (error) {
                    console.error('[Queue] local download failed:', error);
                    showToast('Error', 'Failed to download local queued file', 'error');
                }
                return;
            }

            const jobId = request.id;
            try {
                const resp = await apiFetch(`/queue/${jobId}/download`, {
                    headers: { 'Authorization': `Bearer ${token}` }
                });
                if (!resp.ok) {
                    const raw = await resp.text();
                    const msg =
                        typeof formatApiErrorMessage === 'function'
                            ? formatApiErrorMessage(raw, { httpStatus: resp.status })
                            : `Download failed (${resp.status}).`;
                    throw new Error(msg);
                }
                const blob = await resp.blob();
                const fileName = request.data?.fileName || `${request.type}_${jobId}.bin`;
                const url = URL.createObjectURL(blob);
                const anchor = document.createElement('a');
                anchor.href = url;
                anchor.download = fileName;
                document.body.appendChild(anchor);
                anchor.click();
                setTimeout(() => {
                    URL.revokeObjectURL(url);
                    document.body.removeChild(anchor);
                }, 200);
                showToast('Download started', 'Queued file retained. Delete it manually after confirming the download.', 'info');
            } catch (error) {
                console.error('[Queue] download failed:', error);
                showToast('Error', 'Failed to download queued file', 'error');
            }
        }

        async function reconcileQueueStorage() {
            // No-op: queue storage is server-side
        }

        async function saveFileLocally(file, suggestedName = null) {
            const fileName = suggestedName || file.name || `${file.type || 'unknown'}_${Date.now()}.bin`;
            try {
                const url = URL.createObjectURL(file);
                const anchor = document.createElement('a');
                anchor.href = url;
                anchor.download = fileName;
                document.body.appendChild(anchor);
                anchor.click();
                setTimeout(() => {
                    URL.revokeObjectURL(url);
                    document.body.removeChild(anchor);
                }, 200);
                return true;
            } catch (error) {
                console.error('[SaveLocal] Failed:', error);
                showToast('Error', 'Could not save file locally', 'error');
                return false;
            }
        }

        /** Save WebM via File System Access API (Chrome/Edge desktop). */
        async function tryFileSystemSaveWebm(file, suggestedName) {
            const name = suggestedName || (file && file.name) || `recording_${Date.now()}.webm`;
            const blob = file instanceof Blob ? file : new Blob([]);
            if (!window.showSaveFilePicker) {
                return { ok: false, canceled: false };
            }
            try {
                const handle = await window.showSaveFilePicker({
                    suggestedName: name,
                    types: [{ description: 'WebM audio', accept: { 'audio/webm': ['.webm'] } }],
                });
                const writable = await handle.createWritable();
                await writable.write(blob);
                await writable.close();
                showToast('Saved', 'Recording saved to the location you chose.', 'success');
                return { ok: true, canceled: false };
            } catch (e) {
                if (e && (e.name === 'AbortError' || e.name === 'SecurityError')) {
                    return { ok: false, canceled: true };
                }
                return { ok: false, canceled: false };
            }
        }

        /**
         * Last-resort UX when the server did not verify the saved recording.
         * Does not throw; uses existing modal CSS (.modal / .show).
         */
        function promptRecordingBackupIfNeeded(file, suggestedName) {
            if (!file) return;
            const baseName = suggestedName || file.name || `recording_${Date.now()}.webm`;

            const ensureModal = () => {
                let modal = document.getElementById('recordingBackupModal');
                if (modal) return modal;
                modal = document.createElement('div');
                modal.id = 'recordingBackupModal';
                modal.className = 'modal hidden';
                modal.setAttribute('role', 'alertdialog');
                modal.setAttribute('aria-modal', 'true');
                modal.setAttribute('aria-labelledby', 'recordingBackupTitle');
                modal.innerHTML =
                    '<div class="modal-content">' +
                    '<div class="modal-header">' +
                    '<div>' +
                    '<div class="modal-title" id="recordingBackupTitle">Could not verify recording upload</div>' +
                    '<div class="modal-subtitle">The recording is still on this device for automatic retry while this browser data remains available. Save a copy now if you may close, refresh, switch devices, or clear browser data before the upload succeeds.</div>' +
                    '</div>' +
                    '<button type="button" class="modal-close" data-rbm-close aria-label="Close">&times;</button>' +
                    '</div>' +
                    '<div class="modal-footer" style="flex-wrap:wrap;gap:0.5rem;">' +
                    '<button type="button" class="btn btn-outline" data-rbm-dismiss>Not now</button>' +
                    '<button type="button" class="btn btn-primary" data-rbm-save>Save copy…</button>' +
                    '</div>' +
                    '</div>';
                document.body.appendChild(modal);
                return modal;
            };

            const modal = ensureModal();
            const btnSave = modal.querySelector('[data-rbm-save]');
            const btnDismiss = modal.querySelector('[data-rbm-dismiss]');
            const btnClose = modal.querySelector('[data-rbm-close]');

            return new Promise((resolve) => {
                let settled = false;
                const done = () => {
                    if (settled) return;
                    settled = true;
                    modal.classList.remove('show');
                    modal.classList.add('hidden');
                    resolve();
                };

                const onSave = async () => {
                    const fsr = await tryFileSystemSaveWebm(file, baseName);
                    if (fsr.ok) {
                        done();
                        return;
                    }
                    if (fsr.canceled) {
                        return;
                    }
                    try {
                        if (typeof isMobileDevice === 'function' && isMobileDevice()) {
                            const shareFile =
                                file instanceof File
                                    ? file
                                    : new File([file], baseName, { type: file.type || 'audio/webm' });
                            if (navigator.canShare && navigator.canShare({ files: [shareFile] })) {
                                await navigator.share({
                                    files: [shareFile],
                                    title: 'Recording backup',
                                    text: 'Clinical recording backup',
                                });
                                showToast('Shared', 'Use Files or another app to keep a copy.', 'success');
                                done();
                                return;
                            }
                        }
                    } catch (shareErr) {
                        if (shareErr && (shareErr.name === 'AbortError' || shareErr.name === 'NotAllowedError')) {
                            return;
                        }
                    }
                    const anchorOk = await saveFileLocally(file, baseName);
                    if (anchorOk) {
                        showToast('Download started', 'Check your Downloads folder for the recording.', 'info');
                    }
                    done();
                };

                const onDismiss = () => {
                    showToast(
                        'Recording not backed up',
                        'Closing or refreshing this page may discard this audio. Save a copy when you can.',
                        'warning'
                    );
                    done();
                };

                const saveOnce = () => {
                    onSave();
                };
                const dismissOnce = () => {
                    onDismiss();
                };

                btnSave.onclick = (e) => {
                    e.preventDefault();
                    saveOnce();
                };
                btnDismiss.onclick = (e) => {
                    e.preventDefault();
                    dismissOnce();
                };
                if (btnClose) {
                    btnClose.onclick = (e) => {
                        e.preventDefault();
                        dismissOnce();
                    };
                }

                modal.classList.remove('hidden');
                modal.classList.add('show');
            });
        }

        /**
         * Enqueue file for server or local processing.
         * @returns {{ ok: boolean, mode?: string, duplicate?: boolean, reason?: string }}
         */
        const QUEUE_UPLOAD_TIMEOUT_MS = 90000;

        async function fetchQueueWithTimeout(url, options = {}, timeoutMs = QUEUE_UPLOAD_TIMEOUT_MS) {
            const ctrl = (typeof AbortController !== 'undefined') ? new AbortController() : null;
            let timer = null;
            if (ctrl) {
                timer = setTimeout(() => {
                    try { ctrl.abort(); } catch (_) {}
                }, timeoutMs);
            }
            try {
                let path = String(url || '');
                const base = getApiBase((window.app && window.app.settings && window.app.settings.serverUrl) || '/api');
                if (path.startsWith(base)) {
                    path = path.slice(base.length);
                }
                if (!path.startsWith('/api')) {
                    path = path.startsWith('/') ? `/api${path}` : `/api/${path}`;
                }
                return await apiFetch(path, {
                    ...(options || {}),
                    signal: ctrl ? ctrl.signal : options.signal,
                });
            } finally {
                if (timer) clearTimeout(timer);
            }
        }

        function isRemovedAudioQueueType(type) {
            return ['transcribe', 'asr', 'asr_recording', 'asr_segment'].includes(String(type || '').trim().toLowerCase());
        }

        async function queueLocalOnly(type, data, options = {}) {
            if (isRemovedAudioQueueType(type)) {
                console.warn('[Queue] Audio recordings are no longer stored in the generic queue:', type);
                return { ok: false, reason: 'asr_queue_removed' };
            }
            const typeNorm = String(type || '').trim().toLowerCase();
            const maybeFile = data && data.file instanceof File ? data.file : null;
            if (!maybeFile) {
                return { ok: false, reason: 'no_file' };
            }
            const encounterId =
                options.encounterId != null
                    ? String(options.encounterId || '')
                    : ((app && app.activeEncounterId) ? String(app.activeEncounterId) : '');
            const localId = `local-${crypto.randomUUID ? crypto.randomUUID() : (Date.now() + '-' + Math.random().toString(16).slice(2))}`;
            const fileKey = `${localId}`;
            await queueStorage.storeFile(fileKey, maybeFile);
            const entry = {
                id: localId,
                type: typeNorm,
                localOnly: true,
                encounter_id: encounterId || null,
                data: {
                    fileName: data.fileName || maybeFile.name || 'unknown',
                    fileSize: maybeFile.size || 0,
                    mimeType: maybeFile.type || 'application/octet-stream',
                    fileKey,
                    storageState: 'local',
                },
                timestamp: new Date().toISOString()
            };
            app.requestQueue.push(entry);
            persistLocalQueueMeta();
            updateConnectionStatus('queued');
            updateQueueDisplay();
            if (options.toast !== false) {
                showToast(
                    options.toastTitle || 'Queued (offline)',
                    options.toastMessage || 'Saved locally. Retry when online.',
                    options.toastLevel || 'warning'
                );
            }
            return { ok: true, mode: 'local', id: localId };
        }

        async function queueRequest(type, data, meta = {}) {
            const typeNorm = String(type || '').trim().toLowerCase();
            if (isRemovedAudioQueueType(typeNorm)) {
                console.warn('[Queue] Audio recordings use ASR segments, not the generic queue:', type);
                return { ok: false, reason: 'asr_queue_removed' };
            }
            // Guard: must be authenticated to queue on server
            const token = getAuthToken();
            if (!token) {
                showToast('Sign In Required', 'Log in to queue files.', 'warning');
                return { ok: false, reason: 'no_token' };
            }
            const maybeFile = data && data.file instanceof File ? data.file : null;
            if (!maybeFile) {
                console.warn('[Queue] No file provided');
                return { ok: false, reason: 'no_file' };
            }
            // Avoid duplicate queue entries for same file & type (await queueRequest so retries respect this)
            const existing = app.requestQueue.find(j => j.data?.fileName === maybeFile.name && j.type === typeNorm);
            if (existing) {
                console.log('[Queue] Duplicate job already queued:', existing);
                showToast('Already Queued', `${maybeFile.name} is already in the queue`, 'info');
                return { ok: true, duplicate: true, mode: 'duplicate', id: existing.id };
            }
            // Check if server is reachable
            if (app.connection.status === 'disconnected') {
                // P7: unified offline queue (store locally + show in queue strip)
                try {
                    const localId = `local-${crypto.randomUUID ? crypto.randomUUID() : (Date.now() + '-' + Math.random().toString(16).slice(2))}`;
                    const fileKey = `${localId}`;
                    await queueStorage.storeFile(fileKey, maybeFile);
                    const entry = {
                        id: localId,
                        type: typeNorm,
                        localOnly: true,
                        encounter_id: (app && app.activeEncounterId) ? String(app.activeEncounterId) : null,
                        data: {
                            fileName: data.fileName || maybeFile.name || 'unknown',
                            fileSize: maybeFile.size || 0,
                            mimeType: maybeFile.type || 'application/octet-stream',
                            fileKey,
                            storageState: 'local',
                        },
                        timestamp: new Date().toISOString()
                    };
                    app.requestQueue.push(entry);
                    persistLocalQueueMeta();
                    updateConnectionStatus('queued');
                    updateQueueDisplay();
                    showToast('Queued (offline)', 'Saved locally. Retry when online.', 'warning');
                    return { ok: true, mode: 'local', id: localId };
                } catch (e) {
                    console.warn('[Queue] Local queue store failed; falling back to direct download:', e);
                    const saved = await saveFileLocally(maybeFile, data.fileName || maybeFile.name);
                    if (saved) {
                        showToast('File Saved', 'Download started. Upload manually when back online.', 'info');
                        return { ok: true, mode: 'download_fallback' };
                    }
                    showToast('Skipped', 'File not saved. Try again when connection restored.', 'warning');
                    return { ok: false, reason: 'offline_store_failed' };
                }
            }

            // Upload to server queue
            const formData = new FormData();
            formData.append('type', typeNorm);
            if (app && app.activeEncounterId) {
                formData.append('encounter_id', String(app.activeEncounterId));
            }
            formData.append('file', maybeFile, data.fileName || maybeFile.name);
            try {
                const traceHdr = (meta && meta.traceId) ? String(meta.traceId) : `q_${Date.now()}_${Math.random().toString(16).slice(2)}`;
                const resp = await fetchQueueWithTimeout(apiUrl('/queue'), {
                    method: 'POST',
                    headers: {
                        'Authorization': `Bearer ${token}`,
                        'x-asr-trace-id': traceHdr,
                    },
                    body: formData
                });
                if (!resp.ok) {
                    const errText = await resp.text();
                    const msg =
                        typeof formatApiErrorMessage === 'function'
                            ? formatApiErrorMessage(errText, { httpStatus: resp.status })
                            : `Could not queue file (${resp.status}).`;
                    const httpErr = new Error(msg);
                    httpErr.httpStatus = resp.status;
                    throw httpErr;
                }
                const job = await resp.json();
                // Store job ID in lastServiceRetry for ribbon retry
                if (typeof lastServiceRetry !== 'undefined' && lastServiceRetry) {
                    lastServiceRetry.jobId = job.id;
                }
                // Transform to same format as loadQueue() for consistent display
                const mappedJob = {
                    id: job.id,
                    type: job.type,
                    encounter_id: job.encounter_id || null,
                    status: job.status || 'pending',
                    data: {
                        fileName: job.file_name || maybeFile.name || 'unknown',
                        fileSize: job.file_size,
                        mimeType: job.mime_type,
                        serverFileKey: job.server_file_key,
                        fileKey: job.id, // used for download endpoint
                        storageState: 'stored',
                    },
                    timestamp: job.created_at
                };
                // Add to local cache
                if (!app.requestQueue.find(j => j.id === job.id)) {
                    app.requestQueue.push(mappedJob);
                }
                persistLocalQueueMeta();
                updateConnectionStatus('queued');
                updateQueueDisplay();
                showToast('Queued', `${maybeFile.name} added to queue`, 'success');
                return { ok: true, mode: 'server', id: job.id };
            } catch (error) {
                console.error('[Queue] Upload failed:', error);
                // Check if it's a network error (server unreachable mid-upload)
                const isNetworkError =
                    error.message.includes('Failed to fetch') ||
                    error.name === 'TypeError' ||
                    error.name === 'AbortError' ||
                    error.message.includes('NetworkError') ||
                    error.message.toLowerCase().includes('abort');
                // A transient failure must NOT lose the audio: a network drop OR a
                // server-side 5xx (502/503 while FastAPI restarts, 500/504) keeps a
                // local copy for retry. Only a deliberate 4xx rejection (e.g. 400
                // corrupt/too-small audio) means a retry would be pointless.
                const httpStatus = Number(error && error.httpStatus) || 0;
                const isTransient = isNetworkError || (httpStatus >= 500 && httpStatus <= 599);
                if (isTransient) {
                    // Server unreachable or temporarily failing — fall back to local queue
                    try {
                        const localId = `local-${crypto.randomUUID ? crypto.randomUUID() : (Date.now() + '-' + Math.random().toString(16).slice(2))}`;
                        const fileKey = `${localId}`;
                        await queueStorage.storeFile(fileKey, maybeFile);
                        const entry = {
                            id: localId,
                            type: typeNorm,
                            localOnly: true,
                            encounter_id: (app && app.activeEncounterId) ? String(app.activeEncounterId) : null,
                            data: {
                                fileName: data.fileName || maybeFile.name || 'unknown',
                                fileSize: maybeFile.size || 0,
                                mimeType: maybeFile.type || 'application/octet-stream',
                                fileKey,
                                storageState: 'local',
                            },
                            timestamp: new Date().toISOString()
                        };
                        app.requestQueue.push(entry);
                        persistLocalQueueMeta();
                        updateConnectionStatus('queued');
                        updateQueueDisplay();
                        showToast('Queued (offline)', 'Saved locally. Retry when online.', 'warning');
                        return { ok: true, mode: 'local', id: localId };
                    } catch (e) {
                        console.warn('[Queue] Local fallback failed; downloading instead:', e);
                        const saved = await saveFileLocally(maybeFile, data.fileName || maybeFile.name);
                        if (saved) {
                            showToast('File Saved', 'Download started. Upload manually when back online.', 'info');
                            return { ok: true, mode: 'download_fallback' };
                        }
                        showToast('Upload Failed', 'Could not reach server and file not saved.', 'error');
                        return { ok: false, reason: 'network_fallback_failed' };
                    }
                }
                showToast('Queue Failed', 'Could not add file to server queue', 'error');
                return { ok: false, reason: 'server_error' };
            }
        }

        async function recoverLocalRecordingsToServer(options = {}) {
            try {
                if (!window.RecordingRecovery || typeof window.RecordingRecovery.recoverStoppedToServer !== 'function') return;
                if (!window.AuthWorkspace || !window.AuthWorkspace.user) return;
                if (!app || !app.activeEncounterId) return;
                // Recovery must go by what's actually sitting in IndexedDB, not by
                // AsrSettings.getCaptureMode() -- that's the CURRENT persisted
                // localStorage preference, unrelated to what mode was active when
                // any given orphaned session was recorded. Only universal_audio_handler.js
                // (batch mode) ever writes to this store; asr_chunk_pipeline.js
                // (chunk mode) never touches it, so there's no double-recovery risk
                // either way. Gating on the current mode meant a doctor who crashed
                // mid-recording in batch mode, then had (or later switched to) 'chunk'
                // as their default, would silently never get that audio back --
                // it just sat until TTL cleanup deleted it.
                await window.RecordingRecovery.cleanupExpired();
                const res = await window.RecordingRecovery.recoverStoppedToServer({
                    userKey: window.RecordingRecovery.safeUserKey ? window.RecordingRecovery.safeUserKey() : '',
                    encounterId: String(app.activeEncounterId),
                    includeRecording: options.includeRecording !== false,
                    excludeRecordingIds: options.excludeRecordingId ? [options.excludeRecordingId] : [],
                    uploadRecording: async (data) => {
                        const uploaded = await uploadAsrRecordingSegment(data.file, {
                            encounterId: String(app.activeEncounterId),
                            fileName: data.fileName || data.file?.name || '',
                            recordingSessionId: data.recordingSessionId || '',
                        });
                        if (uploaded && uploaded.ok && uploaded.segmentId) {
                            await transcribeAsrSegment(uploaded.segmentId, { quiet: true, returnResult: true });
                            return { ok: true, mode: 'server', id: uploaded.segmentId };
                        }
                        return uploaded;
                    },
                });
                if (res && res.recovered && !options.quiet) {
                    showToast('Recovered audio', `Recovered ${res.recovered} recording segment(s).`, 'info');
                }
                return res;
            } catch (e) {
                // (b) Whole recovery chain failed (an orphaned recording would
                // otherwise sit until TTL cleanup and then be lost). Since this
                // recovery is best-effort by design and runs in the background,
                // we must not toast at the user, but the failure IS worth the
                // incident store. Report only now that the entire chain has
                // failed — a partial recoverStoppedToServer result is a non-event.
                if (typeof window.reportClientError === 'function') {
                    window.reportClientError(
                        'Recording recovery failed; orphaned audio may be lost',
                        (e && (e.stack || e.message)) || undefined,
                        'caught'
                    );
                }
            }
        }

        async function sendFeedback(rating, suggestion = '', skipRatingEvent = false) {
            try {
                const genId = window.lastGenerationId;
                if (!genId) {
                    showToast('Info', 'No generation to rate yet', 'info');
                    return;
                }
                const noteTextarea = document.getElementById('generatedNote');
                const body = {
                    generation_id: genId,
                    rating: rating,
                    output: typeof getMergedGeneratedNoteText === 'function' ? getMergedGeneratedNoteText() : (noteTextarea ? noteTextarea.value : ''),
                    suggestion: suggestion,
                    skip_rating_event: skipRatingEvent
                };
                const token = getAuthToken();
                if (!token) {
                    showToast('Sign In Required', 'Log in to send feedback.', 'warning');
                    return;
                }
                const r = await apiFetch('/feedback', {
                    method: 'POST',
                    headers: {
                        'Authorization': `Bearer ${token}`,
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify(body)
                });
                if (r.ok) {
                    if (suggestion) {
                        showToast('Thanks', 'Suggestion submitted', 'success');
                    } else {
                        showToast('Thanks', rating === 2 ? 'Marked as good' : 'Marked as not good', 'success');
                    }
                } else {
                    showToast('Error', 'Failed to record feedback', 'error');
                }
            } catch (e) {
                showToast('Error', 'Failed to record feedback', 'error');
            }
        }

        async function openFeedbackSuggestionModal() {
            await sendFeedback(0);
            const modal = document.getElementById('feedbackSuggestionModal');
            const input = document.getElementById('feedbackSuggestionText');
            if (input) input.value = '';
            if (modal) {
                modal.classList.remove('hidden');
                modal.classList.add('show');
            }
        }

        function closeFeedbackSuggestionModal() {
            const modal = document.getElementById('feedbackSuggestionModal');
            if (!modal) return;
            modal.classList.remove('show');
            modal.classList.add('hidden');
        }

        async function submitFeedbackSuggestion() {
            const input = document.getElementById('feedbackSuggestionText');
            const text = (input && input.value ? input.value : '').trim();
            if (text) {
                await sendFeedback(0, text, true);
            }
            closeFeedbackSuggestionModal();
        }

        function isQueueJobAutoProcessable(r) {
            if (!r) return false;
            if (r._queueProcessing) return false;
            if (isRemovedAudioQueueType(r.type)) return false;
            if (r.localOnly) return true;
            const s = String(r.status || 'pending').toLowerCase();
            // Abandoned jobs have failed too many times — don't retry.
            if (s === 'abandoned') return false;
            return s === 'pending' || s === 'processing';
        }

        async function processQueue() {
            if (queueProcessRunning) return;
            if (!canRunQueuedJobs()) {
                return;
            }
            const token = getAuthToken();
            if (!token) {
                showToast('Sign In Required', 'Log in to process queued files.', 'warning');
                return;
            }

            const toProcess = (app.requestQueue || []).filter(
                (r) =>
                    isQueueJobAutoProcessable(r) &&
                    queueJobMatchesActiveEncounter(r)
            );
            if (toProcess.length === 0) {
                return;
            }

            queueProcessRunning = true;
            try {
            for (const request of toProcess) {
                try {
                    console.log('[Queue] Processing job:', request);
                    request._queueProcessing = true;
                    updateQueueDisplay();

                    // Local-only item: upload to server queue first.
                    if (request && request.localOnly && request.data?.fileKey) {
                        const localBlobKey = request.data.fileKey;
                        const stored = await queueStorage.getFile(localBlobKey);
                        if (!stored) {
                            throw new Error('Local queued file missing');
                        }
                        const blob = stored instanceof Blob ? stored : new Blob([stored]);
                        const fileName = request.data?.fileName || 'queued.bin';
                        const file = new File([blob], fileName, { type: request.data?.mimeType || 'application/octet-stream' });
                        const formData = new FormData();
                        formData.append('type', request.type);
                        if (request.encounter_id) {
                            formData.append('encounter_id', String(request.encounter_id));
                        }
                        formData.append('file', file, fileName);
                        const uploadResp = await fetchQueueWithTimeout(apiUrl('/queue'), {
                            method: 'POST',
                            headers: { 'Authorization': `Bearer ${token}` },
                            body: formData
                        });
                        if (!uploadResp.ok) {
                            throw new Error(`Upload failed: HTTP ${uploadResp.status}`);
                        }
                        const job = await uploadResp.json();
                        // Replace local request with server-backed request for processing.
                        request.localOnly = false;
                        const originalLocalId = request.id;
                        request.id = job.id;
                        request.encounter_id = job.encounter_id || request.encounter_id || null;
                        request.data.serverFileKey = job.server_file_key;
                        request.data.fileKey = job.id;
                        request.data.storageState = 'stored';
                        // Remove local stored blob immediately (server now holds it)
                        // (a) Best-effort local-blob delete: if it fails we leak a blob until
                        // queueStorage cleanup, but never block an already-uploaded job.
                        try { await queueStorage.deleteFile(localBlobKey); } catch (e) {}
                        removeLocalQueueMetaById(originalLocalId);
                    }

                    // Show progress bar matching the job type
                    const progressMap = { ocr: 'documentProgress' };
                    const barMap = { ocr: 'documentProgressBar' };
                    if (progressMap[request.type]) {
                        showProgress(progressMap[request.type], barMap[request.type]);
                    }

                    const resp = await apiFetch(`/queue/${request.id}/process`, {
                        method: 'POST',
                        headers: { 'Authorization': `Bearer ${token}` }
                    });
                    if (!resp.ok) {
                        const errText = await resp.text();
                        const detail =
                            typeof formatApiErrorMessage === 'function'
                                ? formatApiErrorMessage(errText, { httpStatus: resp.status })
                                : errText;
                        request.status = resp.status === 422 ? 'needs_review' : 'failed';
                        request.error = String(detail || '').slice(0, 500);
                        // Cap retries: after 5 failures, mark as dead so the poller
                        // stops retrying and generating ClientDisconnect noise.
                        request._retryCount = (request._retryCount || 0) + 1;
                        if (request._retryCount >= 5) {
                            console.warn('[Queue] Job ' + request.id + ' abandoned after ' + request._retryCount + ' failures:', request.error);
                            request.status = 'abandoned';
                        }
                        updateQueueDisplay();
                        showToast('Error', `Could not process ${request.type}: ${detail}`, 'error');
                        continue;
                    }
                    const data = await resp.json();
                    console.log('[Queue] Process response:', data);

                    if (request.type === 'ocr') {
                        // V7: OCR results go to the target field (default: oldVisitsData)
                        const targetFieldId = window.currentDropTargetField || window.lastInputFieldId || 'oldVisitsData';
                        const targetEl = document.getElementById(targetFieldId);
                        const existing = targetEl ? targetEl.value : '';
                        const addition = sanitizeClinicalText(data.text || '');
                        const block = addition || '[No text detected]';
                        const newVal = existing
                            ? `${existing}\n\nExtracted Text:\n\n${block}`
                            : `Extracted Text:\n\n${block}`;
                        if (targetEl) {
                            setFieldValue(targetFieldId, newVal);
                        }
                        // (a) Best-effort persistence after a queued OCR applies: a storage
                        // failure must not drop the already-applied field value or its toast.
                        try { saveToStorage && saveToStorage(); } catch(e) {}
                        hideServiceErrorBanner();
                        showToast('Processed', `OCR complete (queued) → ${getFieldDisplayName(targetFieldId)}`, 'success');
                    } else {
                        throw new Error(`Unknown queue job type: ${request.type}`);
                    }

                    // Success — remove from client list (job completed on server)
                    app.requestQueue = (app.requestQueue || []).filter((r) => r && String(r.id) !== String(request.id));
                    updateQueueDisplay();
                } catch (error) {
                    console.error(`[Queue] Failed to process job ${request.id}:`, error);
                    const msg = (error && error.message) ? String(error.message) : String(error);
                    request.status = 'failed';
                    request.error = msg.slice(0, 500);
                    updateQueueDisplay();
                    const errorMsg = msg.length > 100 ? `${msg.substring(0, 100)}...` : msg;
                    showToast('Error', `Failed to process ${request.type}: ${errorMsg}`, 'error');
                } finally {
                    request._queueProcessing = false;
                    // Hide progress bar
                    const progressMap = { ocr: 'documentProgress' };
                    if (progressMap[request.type]) {
                        hideProgress(progressMap[request.type]);
                    }
                }
            }

            saveQueue();
            persistLocalQueueMeta();
            } finally {
                queueProcessRunning = false;
                updateQueueDisplay();
            }
        }

        function updateQueueDisplay() {
            const queueCard = document.getElementById('queueCard');
            const queueList = document.getElementById('queueList');

            if (!queueCard || !queueList) return;

            try {
            // Security: never show queue when not authenticated (patient data exposure)
            // Must use getAuthToken(): _hasToken lives inside settings_drawer IIFE and is not global.
            const isAuthenticated = typeof getAuthToken === 'function' && !!getAuthToken();
            if (!isAuthenticated) {
                queueCard.classList.add('hidden');
                queueList.innerHTML = '';
                return;
            }

            // Active encounter only — each encounter has its own queue view.
            const rows = (Array.isArray(app.requestQueue) ? app.requestQueue : []).filter(
                (r) => !!r && queueJobMatchesActiveEncounter(r) && !isRemovedAudioQueueType(r.type)
            );

            if (rows.length === 0) {
                queueCard.classList.add('hidden');
                queueList.innerHTML = '';
                return;
            }

            queueCard.classList.remove('hidden');
            queueList.innerHTML = '';

            rows.forEach(request => {
                const item = document.createElement('div');
                item.className = 'queue-item';
                const fileLabel = request.data?.fileName || 'Text data';
                const sizeLabel = request.data?.fileSize ? ` (${formatBytes(request.data.fileSize)})` : '';
                let status = request.localOnly ? 'local' : (request.status || 'pending');
                if (request._queueProcessing) status = 'processing';
                const statusLabel = status === 'needs_review' ? 'needs review' : status;
                const originLabel = request.localOnly ? 'LOCAL' : 'SERVER';
                const idHint = request.id
                    ? `<small class="text-muted">Job ${String(request.id).replace(/-/g, '').slice(0, 10)}…</small><br>`
                    : '';
                item.innerHTML = `
                    <strong>${escapeHtml(request.type.toUpperCase())}</strong><br>
                    <small>${escapeHtml(fileLabel)}${escapeHtml(sizeLabel)}</small><br>
                    ${idHint}
                    <small class="text-muted">${new Date(request.timestamp).toLocaleString()}</small><br>
                    <small class="text-muted">${escapeHtml(originLabel)} · ${escapeHtml(statusLabel)}</small>
                `;

                if (!request.localOnly && request.error) {
                    const warn = document.createElement('div');
                    warn.className = 'queue-warning';
                    warn.textContent = String(request.error).slice(0, 200);
                    item.appendChild(warn);
                }

                // Actions: local needs blob key; server jobs need server id (download uses /api/queue/{id}/...)
                if (request.localOnly ? request.data?.fileKey : request.id) {
                    const actions = document.createElement('div');
                    actions.className = 'queue-actions';

                    const retryBtn = document.createElement('button');
                    retryBtn.type = 'button';
                    retryBtn.className = 'btn btn-primary queue-download-btn';
                    retryBtn.textContent = 'Retry';
                    retryBtn.disabled = !!request._queueProcessing;
                    retryBtn.addEventListener('click', (e) => { e.preventDefault(); e.stopPropagation(); retryQueueItem(request); });
                    actions.appendChild(retryBtn);

                    const downloadBtn = document.createElement('button');
                    downloadBtn.type = 'button';
                    downloadBtn.className = 'btn btn-secondary queue-download-btn';
                    downloadBtn.textContent = 'Download';
                    downloadBtn.disabled = !!request._queueProcessing;
                    downloadBtn.addEventListener('click', () => downloadQueuedFile(request));
                    actions.appendChild(downloadBtn);

                    const deleteBtn = document.createElement('button');
                    deleteBtn.type = 'button';
                    deleteBtn.className = 'btn btn-danger queue-download-btn';
                    deleteBtn.textContent = 'Delete';
                    deleteBtn.disabled = !!request._queueProcessing;
                    deleteBtn.addEventListener('click', (e) => { e.preventDefault(); e.stopPropagation(); deleteQueueItem(request); });
                    actions.appendChild(deleteBtn);

                    item.appendChild(actions);
                }

                queueList.appendChild(item);
            });
            } finally {
                if (!queueProcessRunning) {
                    // (a) Best-effort UI refresh after a queue pass completes.
                    try { syncQueueConnectionPill(); } catch (e) {}
                }
            }
        }

        function saveQueue() {
            // No-op: queue now stored server-side (local-only meta persisted separately)
        }

        async function clearEncounterQueue() {
            const activeId = (app && app.activeEncounterId) ? String(app.activeEncounterId) : '';
            if (!activeId) {
                showToast('No encounter', 'No active encounter selected.', 'warning');
                return;
            }
            const ok = confirm('Clear queued items for the active encounter only?');
            if (!ok) return;

            // Local-only items for this encounter
            const locals = (Array.isArray(app.requestQueue) ? app.requestQueue : []).filter(r => r && r.localOnly && String(r.encounter_id || '') === activeId);
            for (const r of locals) {
                try { if (r.data?.fileKey) await queueStorage.deleteFile(r.data.fileKey); } catch (e) {}
                // (a) Above is a best-effort local-blob delete per cleared item: a failure
                // leaks a blob until queueStorage cleanup, but never blocks queue clearing.
                removeLocalQueueMetaById(r.id);
            }

            // Server items for this encounter
            const token = getAuthToken();
            if (token) {
                try {
                    await apiFetch(`/queue?encounter_id=${encodeURIComponent(activeId)}`, {
                        method: 'DELETE',
                        headers: { 'Authorization': `Bearer ${token}` }
                    });
                } catch (e) {
                    console.warn('[Queue] Failed to clear server encounter queue:', e);
                }
            }

            // Remove from UI cache
            app.requestQueue = (Array.isArray(app.requestQueue) ? app.requestQueue : []).filter(r => {
                if (!r) return false;
                const eid = r.encounter_id ? String(r.encounter_id) : '';
                return eid !== activeId;
            });
            persistLocalQueueMeta();
            updateQueueDisplay();
            showToast('Cleared', 'Encounter queue cleared.', 'success');
        }

        function _localQueueMetaKey() { return 'cng_local_queue_meta_v1'; }

        function loadLocalQueueMeta() {
            try {
                const raw = localStorage.getItem(_localQueueMetaKey());
                const arr = raw ? JSON.parse(raw) : [];
                return Array.isArray(arr) ? arr : [];
            } catch (e) {
                return [];
            }
        }

        function persistLocalQueueMeta() {
            try {
                const locals = (Array.isArray(app.requestQueue) ? app.requestQueue : []).filter(
                    r => r && r.localOnly && !isRemovedAudioQueueType(r.type)
                );
                const slim = locals.map(r => ({
                    id: r.id,
                    type: r.type,
                    localOnly: true,
                    encounter_id: r.encounter_id || null,
                    data: {
                        fileName: r.data?.fileName || 'unknown',
                        fileSize: r.data?.fileSize || 0,
                        mimeType: r.data?.mimeType || 'application/octet-stream',
                        fileKey: r.data?.fileKey || null,
                        storageState: r.data?.storageState || 'local',
                    },
                    timestamp: r.timestamp || new Date().toISOString()
                }));
                localStorage.setItem(_localQueueMetaKey(), JSON.stringify(slim));
            } catch (e) {}
            // (a) persistLocalQueueMeta is a best-effort queue-meta write: blocked/quota
            // storage must not break the in-memory queue that continues to drive the UI.
        }

        function removeLocalQueueMetaById(id) {
            try {
                const arr = loadLocalQueueMeta().filter(x => x && x.id !== id);
                localStorage.setItem(_localQueueMetaKey(), JSON.stringify(arr));
            } catch (e) {}
            // (a) removeLocalQueueMetaById best-effort localStorage write; guard is
            // purely defensive so a blocked-storage state can't break queue operations.
        }

        async function deleteQueueItem(request, opts = {}) {
            const silent = !!opts.silent;
            if (!request) return;
            if (!silent) {
                const ok = confirm('Delete this queued item?');
                if (!ok) return;
            }
            // Local-only
            if (request.localOnly && request.data?.fileKey) {
                // (a) Best-effort local-blob delete when deleting a local-only item; a
                // failure leaks a blob but never blocks the requested queue deletion.
                try { await queueStorage.deleteFile(request.data.fileKey); } catch (e) {}
                removeLocalQueueMetaById(request.id);
                app.requestQueue = (app.requestQueue || []).filter(r => r && r.id !== request.id);
                updateQueueDisplay();
                return;
            }
            const token = getAuthToken();
            if (!token) return;
            try {
                await apiFetch(`/queue/${request.id}`, { method: 'DELETE', headers: { 'Authorization': `Bearer ${token}` } });
            } catch (e) {
                console.warn('[Queue] delete failed:', e);
            }
            app.requestQueue = (app.requestQueue || []).filter(r => r && r.id !== request.id);
            updateQueueDisplay();
        }

        async function retryQueueItem(request) {
            if (!request) return;
            if (!canRunQueuedJobs()) {
                showToast('Offline', 'Connect to retry queued jobs.', 'warning');
                return;
            }
            const token = getAuthToken();
            if (!token) {
                showToast('Sign In Required', 'Log in to retry queued jobs.', 'warning');
                return;
            }
            if (!request.localOnly && request.id) {
                try {
                    const r = await apiFetch(`/queue/${request.id}/retry`, {
                        method: 'POST',
                        headers: { 'Authorization': `Bearer ${token}` }
                    });
                    if (r.ok) {
                        const job = await r.json();
                        request.status = job.status || 'pending';
                        request.error = null;
                    } else {
                        request.status = 'pending';
                        request.error = null;
                    }
                } catch {
                    request.status = 'pending';
                    request.error = null;
                }
            } else {
                request.status = 'pending';
                request.error = null;
            }
            // Use existing flow (processQueue) on a single-item list
            const prev = app.requestQueue || [];
            app.requestQueue = [request];
            await processQueue();
            // merge any leftover back (processQueue re-adds failures)
            const leftover = app.requestQueue || [];
            app.requestQueue = [...leftover, ...prev.filter(r => r && r.id !== request.id)];
            persistLocalQueueMeta();
            updateQueueDisplay();
        }

        // Local-only queue meta is merged in loadQueue() so the strip always reflects both sources.

        function clearTranscription() {
            const transcriptEl = document.getElementById('transcriptionDisplay');
            const notesEl = document.getElementById('transcriptionData');
            if (transcriptEl) transcriptEl.value = '';
            if (notesEl) notesEl.value = '';
            saveToStorage();
            if (typeof updateCharacterCounter === 'function') {
                updateCharacterCounter();
            }
        }

        function copyTranscription() {
            const transcriptEl = document.getElementById('transcriptionDisplay');
            const notesEl = document.getElementById('transcriptionData');
            const text = [transcriptEl?.value || '', notesEl?.value || ''].filter(Boolean).join('\n\n');
            if (!text) {
                showToast('Error', 'No transcription to copy', 'error');
                return;
            }
            if (navigator.clipboard && window.isSecureContext) {
                navigator.clipboard.writeText(text)
                    .then(() => showToast('Copied', 'Transcription copied to clipboard', 'success'))
                    .catch(() => fallbackCopy(text));
            } else {
                fallbackCopy(text);
            }
        }

        async function loadQueue() {
            const token = getAuthToken();
            if (!token) {
                app.requestQueue = [];
                updateQueueDisplay();
                return;
            }
            try {
                const resp = await apiFetch('/queue', {
                    headers: { 'Authorization': `Bearer ${token}` }
                });
                if (resp.ok) {
                    const jobs = await resp.json();
                    console.log('[Queue] Loaded server jobs:', jobs);
                    // Transform server jobs to match existing UI expectations
                    const serverRows = jobs.filter((job) => !isRemovedAudioQueueType(job && job.type)).map(job => {
                        const mapped = {
                            id: job.id,
                            type: job.type,
                            encounter_id: job.encounter_id || null,
                            status: job.status || 'pending',
                            error: job.error || null,
                            data: {
                                fileName: job.file_name || 'unknown',
                                fileSize: job.file_size,
                                mimeType: job.mime_type,
                                serverFileKey: job.server_file_key,
                                // compatibility fields
                                fileKey: job.id, // used for download endpoint
                                storageState: 'stored',
                            },
                            timestamp: job.created_at
                        };
                        console.log('[Queue] Mapped job:', mapped);
                        return mapped;
                    });
                    // Merge with any local-only queued items (offline queue)
                    const locals = loadLocalQueueMeta().filter((r) => !isRemovedAudioQueueType(r && r.type));
                    app.requestQueue = [...serverRows, ...(Array.isArray(locals) ? locals : [])];
                } else {
                    console.warn('[Queue] Failed to load server queue:', resp.status);
                    app.requestQueue = loadLocalQueueMeta().filter((r) => !isRemovedAudioQueueType(r && r.type));
                }
            } catch (error) {
                console.error('[Queue] Load failed:', error);
                app.requestQueue = loadLocalQueueMeta().filter((r) => !isRemovedAudioQueueType(r && r.type));
            }
            updateQueueDisplay();
            // Keep queueStorage.init for any leftover local cleanup (will be unused)
            queueStorage.init().catch(() => {});
        }

        try {
            window.loadQueue = loadQueue;
            window.processQueue = processQueue;
            window.updateQueueDisplay = updateQueueDisplay;
        } catch (e) {}

        let pendingTemplate = null;

        function openTemplateModal(template) {
            pendingTemplate = template;
            const modal = document.getElementById('templateModal');
            modal.classList.add('show');
        }

        function closeTemplateModal() {
            const modal = document.getElementById('templateModal');
            modal.classList.remove('show');
            pendingTemplate = null;
        }

        function applyTemplateReplace() {
            if (!pendingTemplate) { closeTemplateModal(); return; }
            setChartDataValue(pendingTemplate);
            saveToStorage();
            showToast('Template Loaded', 'Replaced chart data with template', 'success');
            closeTemplateModal();
        }

        function applyTemplateAppend() {
            if (!pendingTemplate) { closeTemplateModal(); return; }
            const current = setChartDataValue();
            const needsSep = current && !current.endsWith('\n');
            const sep = current ? (needsSep ? '\n\n' : '\n') : '';
            const combined = current ? `${current}${sep}${pendingTemplate}` : pendingTemplate;
            setChartDataValue(combined);
            saveToStorage();
            showToast('Template Appended', 'Appended template to chart data', 'success');
            closeTemplateModal();
        }

        function loadTemplate() {
            const noteType = document.getElementById('noteType').value;
            const template = app.templates[noteType];

            if (!template) {
                showToast('Template', 'No template found for selection', 'warning');
                return;
            }

            const current = setChartDataValue().trim();

            if (!current) {
                setChartDataValue(template);
                saveToStorage();
                showToast('Template Loaded', 'Inserted template into empty chart area', 'success');
                return;
            }

            openTemplateModal(template);
        }

        function clearChartData() {
            clearOldVisits();
        }

        function clearOldVisits() {
            const el = document.getElementById('oldVisitsData');
            if (!el || !el.value) return;
            if (confirm('Clear Prior Visits data?')) {
                el.value = '';
                updateCharacterCounter();
                saveToStorage();
                showToast('Cleared', 'Prior visits cleared', 'success');
            }
        }

        function clearMixedOther() {
            const el = document.getElementById('mixedOtherData');
            if (!el || !el.value) return;
            if (confirm('Clear Other Chart Data?')) {
                el.value = '';
                updateCharacterCounter();
                saveToStorage();
                showToast('Cleared', 'Other chart data cleared', 'success');
            }
        }

        // V7 API: Uncertain Items Display Functions
        function displayUncertainItems(internalData) {
            const card = document.getElementById('uncertainItemsCard');
            const list = document.getElementById('uncertainItemsList');

            if (!card || !list) return;

            const items = internalData?.uncertain_items || [];

            if (items.length === 0) {
                card.classList.add('hidden');
                return;
            }

            list.innerHTML = items.map((item) => {
                const factType = item.fact_type || 'unknown';
                const icon = getFactTypeIcon(factType);
                const summary = formatFactSummary(item);
                const quote = item.quote ? `"<span class="text-muted fst-italic">${escapeHtml(item.quote)}</span>"` : '';

                return `
                    <div class="list-group-item list-group-item-warning">
                        <div style="display: flex; justify-content: space-between; align-items: flex-start;">
                            <div>
                                <span class="badge bg-warning" style="color: #000;">${escapeHtml(icon)} ${escapeHtml(factType)}</span>
                                <strong style="margin-left: 0.5rem;">${escapeHtml(summary)}</strong>
                            </div>
                        </div>
                        ${quote ? `<small class="text-muted fst-italic d-block mt-1">${quote}</small>` : ''}
                    </div>
                `;
            }).join('');

            card.classList.remove('hidden');
        }

        function getFactTypeIcon(factType) {
            const icons = {
                medication: '\uD83D\uDC8A',   // pill
                lab: '\uD83D\uDD2C',          // microscope
                vital: '\uD83D\uDCCA',        // chart
                diagnosis: '\uD83E\uDE7A',    // stethoscope
                procedure: '\u2702\uFE0F',    // scissors
                allergy: '\u26A0\uFE0F',      // warning
                imaging: '\uD83D\uDCF7',      // camera
                symptom: '\uD83E\uDD12',      // sick face
                social_history: '\uD83D\uDC65', // group
                family_history: '\uD83D\uDC68\u200D\uD83D\uDC69\u200D\uD83D\uDC67', // family
                demographic: '\uD83D\uDCCB'   // clipboard
            };
            return icons[factType] || '\uD83D\uDCC4'; // document
        }

        function formatFactSummary(item) {
            switch (item.fact_type) {
                case 'medication':
                    return `${item.name || 'Unknown'} ${item.dose || ''} ${item.frequency || ''}`.trim();
                case 'lab':
                    return `${item.test_name || 'Unknown'}: ${item.value || item.result_text || 'pending'}`;
                case 'vital':
                    return `${item.name || 'Unknown'}: ${item.value || ''} ${item.unit || ''}`.trim();
                case 'diagnosis':
                    return `${item.condition || 'Unknown'} (${item.status || 'unknown'})`;
                case 'allergy':
                    return `${item.allergen || 'Unknown'} - ${item.reaction || 'reaction unknown'}`;
                case 'imaging':
                    return `${item.study_type || 'Study'}: ${item.body_part || ''} - ${item.impression || item.findings || ''}`.trim();
                case 'procedure':
                    return `${item.name || 'Unknown procedure'}`;
                case 'symptom':
                    return `${item.symptom || 'Unknown'} ${item.duration ? `(${item.duration})` : ''}`.trim();
                default:
                    return Object.values(item).filter(v => typeof v === 'string' && v.length < 50).slice(0, 2).join(' - ') || 'Unknown item';
            }
        }

        function toggleUncertainItems() {
            const body = document.getElementById('uncertainItemsBody');
            const icon = document.getElementById('uncertainToggleIcon');

            if (!body || !icon) return;

            if (body.classList.contains('hidden')) {
                body.classList.remove('hidden');
                icon.innerHTML = '&#9660;'; // down arrow
            } else {
                body.classList.add('hidden');
                icon.innerHTML = '&#9654;'; // right arrow
            }
        }

        function focusCurrentSection() {
            if (window.CNGGenerateUI && typeof window.CNGGenerateUI.focusCurrentSection === 'function') {
                return window.CNGGenerateUI.focusCurrentSection();
            }
        }

        function clearGeneratedNote() {
            const noteEl = document.getElementById('generatedNote');
            if (!noteEl) return;
            if (!noteEl.value) return;
            if (confirm('Clear the generated note? This cannot be undone.')) {
                noteEl.value = '';
                if (typeof clearConflictsPanel === 'function') clearConflictsPanel();
                saveToStorage();
                app.noteState.lastSavedHash = null;
                app.noteState.edited = false;
                showToast('Cleared', 'Generated note cleared', 'success');
                updateGeneratedNoteEmptyState();
            }
        }

        async function clearAll(skipConfirm = false, clearServerQueue = true) {
            const aw = window.AuthWorkspace;
            const signedIn = aw && typeof aw.isWorkspaceReady === 'function' && aw.isWorkspaceReady();
            // P6: signed-in users get the Encounters panel (switch / new / delete / close), not an instant wipe.
            if (signedIn && !skipConfirm) {
                if (typeof window.openEncountersPanel === 'function') {
                    window.openEncountersPanel();
                    return;
                }
                if (typeof aw.closeCurrentEncounter === 'function') {
                    return aw.closeCurrentEncounter();
                }
            }
            if (skipConfirm || confirm('This will clear ALL data (chart, note, audio, queue). Continue?')) {
                setChartDataValue('');
                document.getElementById('generatedNote').value = '';
                if (typeof clearConflictsPanel === 'function') clearConflictsPanel();
                document.getElementById('transcriptionData').value = '';
                const transcriptionDisplay = document.getElementById('transcriptionDisplay');
                if (transcriptionDisplay) transcriptionDisplay.value = '';
                const oldVisitsEl = document.getElementById('oldVisitsData');
                if (oldVisitsEl) oldVisitsEl.value = '';
                const mixedOtherEl = document.getElementById('mixedOtherData');
                if (mixedOtherEl) mixedOtherEl.value = '';
                updateGeneratedNoteEmptyState();

                // Clear/hide evidence-based comment section
                setConsultComment('');
                document.getElementById('consultRefs').textContent = '';
                const consultCard = document.getElementById('consultCommentCard');
                if (consultCard) consultCard.classList.add('hidden');
                const retryConsultBtn = document.getElementById('retryConsultComment');
                if (retryConsultBtn) retryConsultBtn.classList.add('hidden');
                const ragHintCard = document.getElementById('ragHint');
                if (ragHintCard) ragHintCard.classList.add('hidden');
                if (window.ragPollInterval) {
                    clearInterval(window.ragPollInterval);
                    window.ragPollInterval = null;
                }
                if (window.orderRequestsPollInterval) {
                    clearInterval(window.orderRequestsPollInterval);
                    window.orderRequestsPollInterval = null;
                }
                setOrderRequestsButtonVisible(false);
                // Patient Materials button is ALWAYS visible - don't hide it
                setEvidenceButtonVisible(false);
                window.ragState = defaultRagState();
                window.orderRequestsState = defaultOrderRequestsState();
                closeOrderRequestsModal();
                updateUiState({
                    lastGenerationId: null,
                    showOrderRequestsButton: false,
                    showEvidenceButton: false,
                    // showPatientMaterialsButton: ALWAYS true - don't save as false
                    ragHasGenerated: false,
                    orderHasGenerated: false,
                    ragContent: null,
                    orderItems: [],
                    ragLastUpdated: null,
                    orderLastUpdated: null
                });

                if (app.isListening) {
                    stopSpeechRecognition();
                }

                // Clear server queue only when explicitly requested (P6 close encounter should NOT clear global queue)
                if (clearServerQueue) {
                    const token = getAuthToken();
                    if (token) {
                        try {
                            await apiFetch('/queue', {
                                method: 'DELETE',
                                headers: { 'Authorization': `Bearer ${token}` }
                            });
                        } catch (error) {
                            console.warn('[Queue] Failed to clear server queue:', error);
                        }
                    }
                }

                app.requestQueue = [];
                saveQueue();
                updateQueueDisplay();

                // Local device only: clear queued blobs
                try {
                    await queueStorage.clearAll();
                } catch (error) {
                    console.warn('[QueueStorage] Failed to clear stored files:', error);
                }

                saveToStorage();
                // (a) Best-effort local-draft clears on 'clear all': blocked/quota storage
                // must not fail the encounter reset the user explicitly requested.
                try { localStorage.removeItem('clinicalNoteData'); } catch(e) {}
                try { localStorage.removeItem('notegen_draft'); } catch(e) {}
                try { localStorage.removeItem('customPrompts'); } catch(e) {}

                if (typeof resetEncounterInstructionsForNewEncounter === 'function') {
                    resetEncounterInstructionsForNewEncounter();
                }

                document.getElementById('queueCard').classList.add('hidden');
                if (typeof updateCharacterCounter === 'function') {
                    updateCharacterCounter();
                }

                showToast('Cleared', 'All data cleared', 'success');
                focusCurrentSection();
            }
        }

        async function clearQueue() {
            if (!confirm('Clear ALL queued requests (all encounters + local-only items)?')) {
                return;
            }

            // Clear server queue if authenticated
            const token = getAuthToken();
            if (token) {
                try {
                    await apiFetch('/queue', {
                        method: 'DELETE',
                        headers: { 'Authorization': `Bearer ${token}` }
                    });
                } catch (error) {
                    console.warn('[Queue] Failed to clear server queue:', error);
                }
            }

            // Clear local-only queue and IndexedDB leftovers
            const keys = (app.requestQueue || [])
                .filter(r => r && (r.localOnly || r.data?.storageState === 'local'))
                .map(req => (req && req.data && req.data.fileKey) ? req.data.fileKey : null)
                .filter(Boolean);

            app.requestQueue = [];
            persistLocalQueueMeta();
            saveQueue();
            updateQueueDisplay();

            if (keys.length) {
                try {
                    await queueStorage.deleteMany(keys);
                } catch (error) {
                    console.warn('[QueueStorage] Failed to delete queued files:', error);
                }
            }
            // (a) Best-effort clear of the queue-meta key; blocked storage must not prevent
            // the requested queue clear (the showToast below is the user-facing success).
            try { localStorage.removeItem(_localQueueMetaKey()); } catch (e) {}

            showToast('Cleared', 'All queued requests cleared', 'success');
        }
        window.clearQueue = clearQueue;

        function copyNotePlain() {
            const text = typeof getGeneratedNoteMainText === 'function'
                ? getGeneratedNoteMainText()
                : (document.getElementById('generatedNote').value || '');

            if (!text || !String(text).trim()) {
                showToast('Error', 'No note to copy', 'error');
                return;
            }

            const cleanedText = finalizeNoteText(text);

            if (navigator.clipboard && window.isSecureContext) {
                navigator.clipboard.writeText(cleanedText)
                    .then(() => showToast('Copied', 'Plain text copied', 'success'))
                    .catch(() => fallbackCopy(cleanedText));
            } else {
                fallbackCopy(cleanedText);
            }
        }
        window.copyNotePlain = copyNotePlain;

        async function copyNoteFormatted() {
            const text = typeof getGeneratedNoteMainText === 'function'
                ? getGeneratedNoteMainText()
                : (document.getElementById('generatedNote').value || '');

            if (!text || !String(text).trim()) {
                showToast('Error', 'No note to copy', 'error');
                return;
            }

            const plain = finalizeNoteText(text);
            const canRich =
                window.CNGMarkdown &&
                typeof window.CNGMarkdown.renderMarkdownSimple === 'function' &&
                navigator.clipboard &&
                typeof navigator.clipboard.write === 'function' &&
                window.isSecureContext;

            if (!canRich) {
                if (navigator.clipboard && window.isSecureContext && typeof navigator.clipboard.writeText === 'function') {
                    try {
                        await navigator.clipboard.writeText(plain);
                        showToast('Copied', 'Note copied as plain text (rich copy unavailable here).', 'success');
                    } catch (e) {
                        console.warn('[copyNoteFormatted] plain write', e);
                        fallbackCopy(plain);
                    }
                } else {
                    fallbackCopy(plain);
                }
                return;
            }

            const htmlBody = window.CNGMarkdown.renderMarkdownSimple(text);
            const htmlDoc = '<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>' + htmlBody + '</body></html>';

            try {
                await navigator.clipboard.write([
                    new ClipboardItem({
                        'text/html': new Blob([htmlDoc], { type: 'text/html' }),
                        'text/plain': new Blob([plain], { type: 'text/plain' }),
                    }),
                ]);
                showToast('Copied', 'Note copied (rich paste in Word; plain in Notepad / EMR)', 'success');
            } catch (e) {
                console.warn('[copyNoteFormatted]', e);
                try {
                    await navigator.clipboard.writeText(plain);
                    showToast(
                        'Copied as plain text',
                        'Formatted clipboard is not available in this browser or context. Plain text was copied.',
                        'warning',
                    );
                } catch (e2) {
                    console.warn('[copyNoteFormatted] fallback', e2);
                    fallbackCopy(plain);
                }
            }
        }
        window.copyNoteFormatted = copyNoteFormatted;
        window.copyNote = copyNoteFormatted;

        window.openOrderRequestsModal = function() {
            const modal = document.getElementById('orderRequestsModal');
            if (!modal) return;
            if (modal.parentElement !== document.body) {
                document.body.appendChild(modal);
            }
            modal.classList.remove('hidden');
            modal.style.display = 'flex';
            renderOrderRequestsModal();
        }

        window.closeOrderRequestsModal = function() {
            const modal = document.getElementById('orderRequestsModal');
            if (!modal) return;
            modal.classList.add('hidden');
            modal.style.display = 'none';
        }

        function setOrderRequestsButtonVisible(visible, persist = true) {
            const btn = document.getElementById('openOrderRequestsBtn');
            if (!btn) return;
            if (visible) {
                btn.classList.remove('hidden');
            } else {
                btn.classList.add('hidden');
            }
            if (persist) {
                updateUiState({ showOrderRequestsButton: !!visible });
            }
        }

        function setEvidenceButtonVisible(visible, persist = true) {
            const btn = document.getElementById('openConsultCommentBtn');
            if (!btn) return;
            if (visible) {
                btn.classList.remove('hidden');
            } else {
                btn.classList.add('hidden');
            }
            if (persist) {
                updateUiState({ showEvidenceButton: !!visible });
            }
        }

        function handleEvidenceBasedCommentsClick() {
            const genId = window.lastGenerationId;
            if (!genId) {
                showToast('Error', 'Generate a note first.', 'error');
                return;
            }
            const state = ensureRagState();
            openConsultComment();

            if (state.status === 'loading' || state.status === 'error' || state.hasGenerated) {
                const retryBtn = document.getElementById('retryConsultComment');
                if (retryBtn) retryBtn.classList.remove('hidden');
                if (state.content) {
                    renderRagContent(state.content);
                } else if (state.status === 'error') {
                    const commentEl = document.getElementById('consultComment');
                    const refsEl = document.getElementById('consultRefs');
                    if (commentEl) setConsultComment('Evidence-based comment failed. Click Retry to regenerate.');
                    if (refsEl) refsEl.textContent = '';
                } else if (state.hasGenerated) {
                    const commentEl = document.getElementById('consultComment');
                    const refsEl = document.getElementById('consultRefs');
                    if (commentEl) setConsultComment('No cached evidence-based comment available. Click Retry to regenerate.');
                    if (refsEl) refsEl.textContent = '';
                }
                return;
            }

            startRagGeneration(genId, { force: false });
        }

        function openConsultComment() {
            const card = document.getElementById('consultCommentCard');
            if (card) {
                card.classList.remove('hidden');
                try { card.scrollIntoView({ behavior: 'smooth', block: 'start' }); } catch {}
            }
        }

        function goToGeneratedNoteCardActions() {
            if (window.CNGGenerateUI && typeof window.CNGGenerateUI.goToGeneratedNoteCardActions === 'function') {
                return window.CNGGenerateUI.goToGeneratedNoteCardActions();
            }
        }

        function isAudioCaptureActive() {
            if (window.CNGGenerateUI && typeof window.CNGGenerateUI.isAudioCaptureActive === 'function') {
                return window.CNGGenerateUI.isAudioCaptureActive();
            }
            return false;
        }

        function clearPendingGenerateAfterTranscription() {
            if (window.CNGGenerateUI && typeof window.CNGGenerateUI.clearPendingGenerateAfterTranscription === 'function') {
                return window.CNGGenerateUI.clearPendingGenerateAfterTranscription();
            }
        }

        function triggerGenerateNoteAndFocus() {
            if (window.CNGGenerateUI && typeof window.CNGGenerateUI.triggerGenerateNoteAndFocus === 'function') {
                return window.CNGGenerateUI.triggerGenerateNoteAndFocus();
            }
            generateNote();
        }

        function renderRagContent(content) {
            const commentEl = document.getElementById('consultComment');
            const refsEl = document.getElementById('consultRefs');
            const ragHintCard = document.getElementById('ragHint');
            if (!commentEl || !refsEl) return;

            const comment = (content && content.comment) ? String(content.comment).trim() : '';
            const structured = (content && content.structured) ? content.structured : null;

            if (structured) {
                renderStructuredComment(structured, comment);
            } else {
                setConsultComment(comment || 'No evidence-backed comment generated.');
            }

            // Build combined references (RAG + web)
            const rags = (content && content.references) ? content.references : [];
            const webs = (content && content.webRefs) ? content.webRefs : [];
            const allRefs = rags.map(r => ({...r, _kind: 'rag'})).concat(
                webs.map(w => ({...w, _kind: 'web'}))
            );
            const refs = deduplicateReferences(allRefs);

            if (refs.length) {
                const frag = document.createDocumentFragment();
                refs.forEach(r => {
                    const line = document.createElement('span');
                    line.className = 'ref-line';
                    const kindTag = r._kind === 'web' ? '🌐' : '📚';
                    const title = safeStr(r.title).trim() || 'Untitled';
                    const yearRaw = safeStr(r.year).trim();
                    const year = yearRaw ? ` (${escapeHtml(yearRaw)})` : '';
                    const url = safeStr(r.url || r.link || '').trim();
                    const srcRaw = safeStr(r.source || r._kind || '').trim();
                    const src = srcRaw ? ` [${escapeHtml(srcRaw)}]` : '';
                    if (url) {
                        line.innerHTML = `${kindTag} <a href="${escapeHtml(url)}" target="_blank" rel="noopener">${escapeHtml(title)}</a>${year}${src}`;
                    } else {
                        line.textContent = `${kindTag} ${title}${year}${src}`;
                    }
                    frag.appendChild(line);
                    frag.appendChild(document.createElement('br'));
                });
                refsEl.innerHTML = '';
                refsEl.appendChild(frag);
            } else {
                refsEl.textContent = 'No references available';
            }
            if (ragHintCard) ragHintCard.classList.add('hidden');

            // Show evidence quality summary if available
            updateEvidenceQualityBanner(structured);
        }

        // -------------------------------------------------------------------
        // Structured consult comment renderer (Phase 3)
        // -------------------------------------------------------------------

        function escapeHtml(s) {
            const el = document.createElement('div');
            el.textContent = s;
            return el.innerHTML;
        }

        function severityClass(severity) {
            const map = {
                'critical': 'sev-critical',
                'high': 'sev-high',
                'moderate': 'sev-moderate',
                'low': 'sev-low',
                'informational': 'sev-info',
                'stat': 'sev-critical',
                'urgent': 'sev-high',
                'routine': 'sev-moderate',
                'consider': 'sev-low',
            };
            return map[(severity || '').toLowerCase()] || 'sev-low';
        }

        function confidenceBadge(confidence) {
            const map = {
                'high': '✓ High confidence',
                'moderate': '~ Moderate',
                'low': '↓ Low',
                'speculative': '? Speculative'
            };
            return map[(confidence || '').toLowerCase()] || confidence || '';
        }

        function evidenceQualityLabel(q) {
            const map = {
                'strong': '🟢 Strong',
                'moderate': '🟡 Moderate',
                'limited': '🟠 Limited',
                'insufficient': '🔴 Insufficient'
            };
            return map[(q || '').toLowerCase()] || q || 'Unknown';
        }

        function renderStructuredComment(structured, rawComment) {
            const el = document.getElementById('consultComment');
            if (!el) return;
            el.innerHTML = '';
            el.dataset.raw = rawComment || '';
            el.style.minHeight = 'auto';

            const frag = document.createDocumentFragment();

            // ── SAFETY ALERTS (always first, always visible) ──
            const alerts = Array.isArray(structured.safety_alerts) ? structured.safety_alerts : [];
            if (alerts.length) {
                const sect = document.createElement('div');
                sect.className = 'ev-section ev-section-alerts';
                const title = document.createElement('h3');
                title.className = 'ev-section-title';
                title.textContent = '🚨 Safety Alerts';
                sect.appendChild(title);
                alerts.forEach(a => {
                    const row = document.createElement('div');
                    row.className = 'ev-item';
                    const badge = document.createElement('span');
                    badge.className = 'ev-badge ' + severityClass(a.severity);
                    badge.textContent = (a.severity || 'INFO').toUpperCase();
                    const body = document.createElement('span');
                    body.className = 'ev-body';
                    body.innerHTML = '<strong>' + escapeHtml(a.alert || '') + '</strong>';
                    if (a.action_required) {
                        body.innerHTML += '<br><em>' + escapeHtml(a.action_required) + '</em>';
                    }
                    if (a.evidence_source) {
                        body.innerHTML += ' <span class="ev-src">' + escapeHtml(a.evidence_source) + '</span>';
                    }
                    row.appendChild(badge);
                    row.appendChild(body);
                    sect.appendChild(row);
                });
                frag.appendChild(sect);
            }

            // ── DIFFERENTIAL DIAGNOSIS ──
            const diffs = Array.isArray(structured.differential_diagnosis) ? structured.differential_diagnosis : [];
            if (diffs.length) {
                const sect = createCollapsibleSection('🔬 Differential Diagnosis', diffs.map(d => ({
                    header: (d.rank ? '#' + d.rank + ' ' : '') + escapeHtml(d.condition || ''),
                    sub: confidenceBadge(d.confidence),
                    body: escapeHtml(d.rationale || '') + (d.evidence_source ? ' <span class="ev-src">' + escapeHtml(d.evidence_source) + '</span>' : ''),
                    cls: 'sev-' + ((d.confidence || '').toLowerCase() === 'speculative' ? 'low' : 'moderate')
                })));
                frag.appendChild(sect);
            }

            // ── RECOMMENDED WORKUP ──
            const workup = Array.isArray(structured.recommended_workup) ? structured.recommended_workup : [];
            if (workup.length) {
                const sect = createCollapsibleSection('🔬 Recommended Workup', workup.map(w => ({
                    header: escapeHtml(w.test || ''),
                    sub: (w.urgency || '').toUpperCase(),
                    body: escapeHtml(w.rationale || '') + (w.evidence_source ? ' <span class="ev-src">' + escapeHtml(w.evidence_source) + '</span>' : ''),
                    cls: severityClass(w.urgency)
                })));
                frag.appendChild(sect);
            }

            // ── MANAGEMENT ADJUSTMENTS ──
            const mgmt = Array.isArray(structured.management_adjustments) ? structured.management_adjustments : [];
            if (mgmt.length) {
                const sect = createCollapsibleSection('💊 Management Adjustments', mgmt.map(m => ({
                    header: escapeHtml(m.change || ''),
                    sub: (m.severity || '').toUpperCase(),
                    body: escapeHtml(m.rationale || '') + (m.evidence_source ? ' <span class="ev-src">' + escapeHtml(m.evidence_source) + '</span>' : ''),
                    cls: severityClass(m.severity)
                })));
                frag.appendChild(sect);
            }

            // ── EVIDENCE SUMMARY ──
            const ev = structured.evidence_summary || {};
            if (Object.keys(ev).length) {
                const sect = document.createElement('div');
                sect.className = 'ev-section ev-section-summary';
                const title = document.createElement('h3');
                title.className = 'ev-section-title';
                title.textContent = '📊 Evidence Summary';
                sect.appendChild(title);
                const row = document.createElement('div');
                row.className = 'ev-summary-grid';
                row.innerHTML =
                    '<div><span class="ev-summary-label">Sources</span><span class="ev-summary-val">' + escapeHtml(String(ev.sources_consulted || 0)) + '</span></div>' +
                    '<div><span class="ev-summary-label">Quality</span><span class="ev-summary-val">' + escapeHtml(evidenceQualityLabel(ev.evidence_quality)) + '</span></div>';
                if (ev.limitations) {
                    row.innerHTML += '<div class="ev-summary-full"><span class="ev-summary-label">Limitations</span><span class="ev-summary-val">' + escapeHtml(ev.limitations) + '</span></div>';
                }
                sect.appendChild(row);
                frag.appendChild(sect);
            }

            // Raw LLM output — intentionally hidden from end users (not for clinical display)
            // Stored in dataset for debugging if needed
            el.dataset.raw = rawComment || '';

            el.appendChild(frag);
        }

        function createCollapsibleSection(title, items) {
            const sect = document.createElement('div');
            sect.className = 'ev-section';

            const header = document.createElement('button');
            header.className = 'ev-collapse-header';
            header.onclick = function() {
                const body = this.nextElementSibling;
                const isOpen = !body.classList.contains('hidden');
                if (isOpen) {
                    body.classList.add('hidden');
                    this.classList.add('collapsed');
                } else {
                    body.classList.remove('hidden');
                    this.classList.remove('collapsed');
                }
            };
            header.innerHTML = escapeHtml(title) + ' <span class="ev-collapse-arrow">▼</span>';

            const body = document.createElement('div');
            body.className = 'ev-collapse-body';
            items.forEach(item => {
                const row = document.createElement('div');
                row.className = 'ev-item';
                if (item.cls) {
                    const badge = document.createElement('span');
                    badge.className = 'ev-badge ' + item.cls;
                    badge.textContent = item.sub || '';
                    row.appendChild(badge);
                }
                const content = document.createElement('span');
                content.className = 'ev-body';
                content.innerHTML = '<strong>' + escapeHtml(item.header) + '</strong>';
                if (item.body) {
                    // item.body is pre-escaped HTML (constructed by the caller with the
                    // untrusted parts already through escapeHtml() and the evidence-source
                    // <span> as real markup) -- escaping it again here would double-encode
                    // that span and render it as literal "<span...>" text.
                    content.innerHTML += '<br>' + item.body;
                }
                row.appendChild(content);
                body.appendChild(row);
            });

            sect.appendChild(header);
            sect.appendChild(body);
            return sect;
        }

        function updateEvidenceQualityBanner(structured) {
            const card = document.getElementById('consultCommentCard');
            if (!card) return;
            let existing = card.querySelector('.ev-quality-banner');
            if (existing) existing.remove();

            if (!structured || !structured.evidence_summary) return;
            const ev = structured.evidence_summary;
            const banner = document.createElement('div');
            banner.className = 'ev-quality-banner';
            const q = ev.evidence_quality || 'unknown';
            const colors = { strong: '#22c55e', moderate: '#eab308', limited: '#f97316', insufficient: '#ef4444' };
            const labels = { strong: 'Strong', moderate: 'Moderate', limited: 'Limited', insufficient: 'Insufficient' };
            banner.style.borderLeft = '4px solid ' + (colors[q] || '#6b7280');
            banner.innerHTML = 'Evidence quality: <strong>' + escapeHtml(labels[q] || q) + '</strong> · ' +
                (ev.sources_consulted || 0) + ' sources consulted';
            if (ev.limitations) {
                banner.innerHTML += ' · <em>' + escapeHtml(ev.limitations.slice(0, 100)) + '</em>';
            }
            card.querySelector('.card-header').after(banner);
        }

        function renderMarkdownSimple(text) {
            if (window.CNGMarkdown && typeof window.CNGMarkdown.renderMarkdownSimple === 'function') {
                return window.CNGMarkdown.renderMarkdownSimple(text);
            }
            const raw = text == null ? '' : String(text);
            return raw.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/\n/g, '<br>');
        }

        function setConsultComment(text) {
            const el = document.getElementById('consultComment');
            if (!el) return;
            const raw = text == null ? '' : String(text);
            el.dataset.raw = raw;
            el.innerHTML = renderMarkdownSimple(raw);
        }

        /** Maps backend consult_comment errors to a clear title/body (not always "RAG offline"). */
        function classifyConsultRagError(err) {
            const s = String(err || '').toLowerCase();
            if (!s.trim()) {
                return {
                    title: 'Evidence unavailable',
                    subtitle: 'No error detail from server. Check RAG service and config, or Retry.',
                    transportLikely: true
                };
            }
            if (s.includes('no evidence returned') || s.includes('no evidence')) {
                return {
                    title: 'No matching evidence in index',
                    subtitle: 'The retriever returned no chunks for this query. Try Retry, add Impression/Plan, or broaden the note.',
                    transportLikely: false
                };
            }
            if (s.includes('unable to derive focus')) {
                return {
                    title: 'Could not build a search focus',
                    subtitle: 'Add structured Impression/Plan or more clinical detail, then Retry.',
                    transportLikely: false
                };
            }
            if (s.includes('rag /query failed') || s.includes('cannot connect') || s.includes('connection refused') ||
                s.includes('timeout') || s.includes('timed out') || s.includes('failed: http') ||
                s.includes('clientconnectorerror') || s.includes('rag_url')) {
                return {
                    title: 'Evidence service unreachable',
                    subtitle: String(err).slice(0, 320),
                    transportLikely: true
                };
            }
            return {
                title: 'Evidence-backed comment failed',
                subtitle: String(err).slice(0, 320),
                transportLikely: false
            };
        }

        function applyConsultRagHint({ show, title, subtitle }) {
            const card = document.getElementById('ragHint');
            const titleEl = document.getElementById('ragHintTitle');
            const subEl = document.getElementById('ragHintSubtitle');
            if (!card) return;
            if (!show) {
                card.classList.add('hidden');
                return;
            }
            if (titleEl && title) titleEl.textContent = title;
            if (subEl) subEl.textContent = subtitle || 'Try Retry or check the evidence index.';
            card.classList.remove('hidden');
        }

        function startRagGeneration(genId, options = {}) {
            if (!genId) return;
            const force = !!options.force;
            const apiBase = getApiBase(app.settings.serverUrl || '/api');
            const token = getAuthToken();
            if (!token) {
                showToast('Error', 'Missing auth token', 'error');
                return;
            }
            // Tag this poll with the encounter it started for. The 'encounters-refresh'
            // handler clears window.ragPollInterval on switch, but that only stops the
            // NEXT tick -- a fetch already in flight at the moment of the switch still
            // resolves and, without this check, would write a different patient's
            // evidence comment into the (DOM-id-reused) consultComment/consultRefs
            // elements the new encounter is now showing.
            const pollEncounterId = String((app && app.activeEncounterId) || '');

            if (window.ragPollInterval) {
                clearInterval(window.ragPollInterval);
                window.ragPollInterval = null;
            }

            // Clear evidence quality banner — it should regenerate with new content
            const card = document.getElementById('consultCommentCard');
            if (card) {
                const banner = card.querySelector('.ev-quality-banner');
                if (banner) banner.remove();
            }

            const commentEl = document.getElementById('consultComment');
            const refsEl = document.getElementById('consultRefs');
            const retryBtn = document.getElementById('retryConsultComment');
            const ragHintCard = document.getElementById('ragHint');
            if (commentEl) {
                setConsultComment(
                    force
                        ? 'Retrying evidence-backed comment generation...'
                        : 'Generating evidence-backed comment...'
                );
            }
            if (refsEl) refsEl.textContent = '';
            if (retryBtn) retryBtn.classList.add('hidden');
            if (ragHintCard) ragHintCard.classList.add('hidden');
            openConsultComment();

            window.ragState = {
                ...ensureRagState(),
                status: 'loading',
                content: null,
                generationId: genId,
                hasGenerated: false,
                lastUpdated: Date.now()
            };
            persistRagState();

            let tries = 0;
            let maxRetries = 40; // 40 × 3s = 2 minutes
            let consultErrorHandled = false;

            const poll = setInterval(async () => {
                tries++;
                if (String((app && app.activeEncounterId) || '') !== pollEncounterId) {
                    clearInterval(poll);
                    return;
                }
                try {
                    debugLog(`[RAG] Polling consult comment (attempt ${tries}/${maxRetries})`);
                    const encQs = encounterQueryString();
                    let path = `/generation/${genId}/consult_comment`;
                    if (force && tries === 1) {
                        path += `?force=1&strategy=full_note${encQs ? `&${encQs}` : ''}`;
                    } else if (encQs) {
                        path += `?${encQs}`;
                    }
                    const r = await apiFetch(path, {
                        headers: { 'Authorization': `Bearer ${token}` }
                    });
                    // Re-check after the await: the user may have switched encounters
                    // while this specific fetch was in flight, past the top-of-tick
                    // check above.
                    if (String((app && app.activeEncounterId) || '') !== pollEncounterId) {
                        clearInterval(poll);
                        return;
                    }

                    if (r.status === 404) {
                        clearInterval(poll);
                        consultErrorHandled = true;
                        window.ragState = {
                            status: 'error',
                            content: null,
                            generationId: genId,
                            hasGenerated: false,
                            lastUpdated: Date.now()
                        };
                        persistRagState();
                        if (commentEl) {
                            setConsultComment('This generation session is no longer on the server. Generate the note again, then retry evidence comments.');
                        }
                        applyConsultRagHint({
                            show: true,
                            title: 'Session expired',
                            subtitle: 'Generation data was cleared or timed out. Regenerate the note to use evidence-backed comments.'
                        });
                        if (refsEl) refsEl.textContent = '';
                        if (retryBtn) retryBtn.classList.remove('hidden');
                        showToast('Notice', 'Evidence comment: session not found', 'warning');
                        return;
                    }

                    if (r.ok) {
                        const data = await r.json();
                        if (data.status === 'pending') {
                            window.orderRequestsState = {
                                ...window.orderRequestsState,
                                status: 'pending',
                                hasGenerated: false,
                                lastUpdated: Date.now()
                            };
                            persistOrderRequestsState();
                            renderOrderRequestsModal();
                            return;
                        }
                        if (data.status === 'done') {
                            clearInterval(poll);
                            const content = {
                                comment: (data.comment || '').trim(),
                                references: data.refs || [],
                                webRefs: data.web_refs || [],
                                structured: data.structured || null
                            };
                            window.ragState = {
                                status: 'ready',
                                content,
                                generationId: genId,
                                hasGenerated: true,
                                lastUpdated: Date.now()
                            };
                            persistRagState();
                            renderRagContent(content);
                            if (retryBtn) retryBtn.classList.remove('hidden');
                            showToast('Success', 'Evidence-backed comment generated!', 'success');
                            return;
                        }
                        if (data.status === 'error') {
                            clearInterval(poll);
                            consultErrorHandled = true;
                            const hint = classifyConsultRagError(data.error);
                            applyConsultRagHint({ show: true, title: hint.title, subtitle: hint.subtitle });
                            window.ragState = {
                                status: 'error',
                                content: null,
                                generationId: genId,
                                hasGenerated: false,
                                lastUpdated: Date.now()
                            };
                            persistRagState();
                            if (commentEl) {
                                setConsultComment(
                                    data.error
                                        ? `Evidence-based comment failed: ${data.error}`
                                        : 'Evidence-based comment failed.'
                                );
                            }
                            if (refsEl) refsEl.textContent = '';
                            if (retryBtn) retryBtn.classList.remove('hidden');
                            showToast('Error', hint.transportLikely ? 'Evidence service error' : 'Evidence-backed comment failed', 'error');
                            return;
                        }
                    } else if (r.status !== 404) {
                        console.warn(`[RAG] Unexpected response: ${r.status}`);
                    }
                } catch (err) {
                    console.error('[RAG] Polling error:', err);
                }

                if (tries >= maxRetries) {
                    clearInterval(poll);
                    if (!consultErrorHandled) {
                        window.ragState = {
                            status: 'error',
                            content: null,
                            generationId: genId,
                            hasGenerated: false,
                            lastUpdated: Date.now()
                        };
                        persistRagState();
                        if (commentEl) setConsultComment('Timeout waiting for evidence-backed comment. You can retry.');
                        applyConsultRagHint({
                            show: true,
                            title: 'Still working or stuck',
                            subtitle: 'No result after waiting. If RAG is slow, try Retry; if the index is down, start the RAG service.'
                        });
                        if (retryBtn) retryBtn.classList.remove('hidden');
                        showToast('Timeout', 'Evidence comment timeout. You can retry later.', 'warning');
                        console.warn('[RAG] Timeout after max retries');
                    }
                }
            }, 3000);

            window.ragPollInterval = poll;
        }

        function renderOrderRequestsModal() {
            const listEl = document.getElementById('orderRequestsList');
            const emptyEl = document.getElementById('orderRequestsEmpty');
            if (!listEl || !emptyEl) return;

            const state = window.orderRequestsState || { status: 'idle', items: [] };
            listEl.innerHTML = '';

            if (state.status === 'loading') {
                emptyEl.textContent = 'Generating requests...';
                emptyEl.classList.remove('hidden');
                return;
            }
            if (state.status === 'error') {
                const detail = (state.lastError && String(state.lastError).trim())
                    ? ` ${state.lastError}`
                    : '';
                emptyEl.textContent = 'Order requests unavailable. Click Retry to regenerate.' + detail;
                emptyEl.classList.remove('hidden');
                return;
            }

            if (state.status === 'pending') {
                emptyEl.textContent = 'Generating requests… (this can take a minute)';
                emptyEl.classList.remove('hidden');
                return;
            }

            const items = Array.isArray(state.items) ? state.items : [];
            if (!items.length) {
                emptyEl.textContent = 'No requests were detected.';
                emptyEl.classList.remove('hidden');
                return;
            }

            emptyEl.classList.add('hidden');

            items.forEach((item, idx) => {
                const wrapper = document.createElement('div');
                wrapper.className = 'request-item';

                const header = document.createElement('div');
                header.className = 'request-item-header';

                const title = document.createElement('div');
                title.className = 'request-title';
                title.textContent = item.title || `Request ${idx + 1}`;

                const category = document.createElement('div');
                category.className = 'request-category';
                category.textContent = item.category || 'Other';

                header.appendChild(title);
                header.appendChild(category);

                const contentDiv = document.createElement('div');
                contentDiv.className = 'request-text pm-content-body';
                contentDiv.innerHTML = window.CNGMarkdown.renderMarkdownSimple(item.request || '');

                const actions = document.createElement('div');
                actions.className = 'request-actions';

                const copyBtn = document.createElement('button');
                copyBtn.className = 'btn btn-outline';
                copyBtn.textContent = 'Copy';
                copyBtn.onclick = () => copyOrderRequest(contentDiv.innerText || '');

                actions.appendChild(copyBtn);

                wrapper.appendChild(header);
                wrapper.appendChild(contentDiv);
                wrapper.appendChild(actions);

                listEl.appendChild(wrapper);
            });
        }

        function copyOrderRequest(text) {
            const value = (text || '').trim();
            if (!value) {
                showToast('Error', 'Nothing to copy', 'error');
                return;
            }
            if (navigator.clipboard && window.isSecureContext) {
                navigator.clipboard.writeText(value)
                    .then(() => showToast('Copied', 'Request copied to clipboard', 'success'))
                    .catch(() => showToast('Error', 'Copy failed', 'error'));
            } else {
                const temp = document.createElement('textarea');
                temp.value = value;
                document.body.appendChild(temp);
                temp.select();
                try {
                    const success = document.execCommand('copy');
                    if (!success) throw new Error('execCommand copy failed');
                    showToast('Copied', 'Request copied to clipboard', 'success');
                } catch {
                    showToast('Error', 'Copy failed', 'error');
                } finally {
                    document.body.removeChild(temp);
                }
            }
        }

        function handleOrderRequestsClick() {
            const genId = window.lastGenerationId;
            if (!genId) {
                showToast('Error', 'Generate a note first.', 'error');
                return;
            }
            const state = ensureOrderRequestsState();
            window.openOrderRequestsModal();

            if (state.status === 'loading' || state.status === 'error' || state.hasGenerated || state.status === 'pending') {
                if (state.status === 'pending') {
                    // Backend autostart already in flight: (re)start the poll loop so
                    // the modal resolves when the pending run finishes.
                    startOrderRequestsGeneration(genId, { force: false, followPending: true });
                } else {
                    renderOrderRequestsModal();
                }
                return;
            }

            startOrderRequestsGeneration(genId, { force: false });
        }

        // --- Patient Materials ---

        function handlePatientMaterialsClick() {
            const genId = window.lastGenerationId;
            if (!genId) {
                // Mid-visit pre-note mode is allowed (Phase A): openPatientMaterialsModal
                // mints a provisional gen_id from live encounter data instead.
                if (typeof window.openPatientMaterialsModal !== 'function') {
                    console.error('[PM] openPatientMaterialsModal is not defined. patient_materials_ui.js may have failed to load.');
                    showToast('Error', 'Patient Materials module failed to load. Please refresh the page.', 'error');
                    return;
                }
                window.openPatientMaterialsModal(null);
                return;
            }
            if (typeof window.openPatientMaterialsModal !== 'function') {
                console.error('[PM] openPatientMaterialsModal is not defined. patient_materials_ui.js may have failed to load.');
                showToast('Error', 'Patient Materials module failed to load. Please refresh the page.', 'error');
                return;
            }
            window.openPatientMaterialsModal(genId);
        }
        window.handlePatientMaterialsClick = handlePatientMaterialsClick;

        function setPatientMaterialsButtonVisible(visible, persist = true) {
            const btn = document.getElementById('openPatientMaterialsBtn');
            if (btn) {
                if (visible) {
                    btn.classList.remove('hidden');
                } else {
                    btn.classList.add('hidden');
                }
            }
            if (persist) {
                updateUiState({ showPatientMaterialsButton: !!visible });
            }
        }

        // Ensure patient materials state exists
        function ensurePatientMaterialsState() {
            if (!window.patientMaterialsState) {
                window.patientMaterialsState = {
                    status: 'idle',
                    materials: {},
                    patientData: {},
                    generationId: null,
                    hasGenerated: false,
                    lastUpdated: null
                };
            }
            return window.patientMaterialsState;
        }

        // Utility: render markdown simple — delegate to the safe version defined above
        // (DO NOT duplicate — see renderMarkdownSimple at line ~4824)

        function startOrderRequestsGeneration(genId, options = {}) {
            if (!genId) return;
            const force = !!options.force;
            const apiBase = getApiBase(app.settings.serverUrl || '/api');
            const token = getAuthToken();
            if (!token) {
                showToast('Error', 'Missing auth token', 'error');
                return;
            }
            // See the matching comment in startRagGeneration: tag this poll with the
            // encounter it started for so a switch mid-poll can't apply a different
            // patient's order requests to the currently-shown modal.
            const pollEncounterId = String((app && app.activeEncounterId) || '');

            if (window.orderRequestsPollInterval) {
                clearInterval(window.orderRequestsPollInterval);
                window.orderRequestsPollInterval = null;
            }

            window.orderRequestsState = {
                ...ensureOrderRequestsState(),
                status: 'loading',
                items: [],
                generationId: genId,
                hasGenerated: false,
                lastUpdated: Date.now(),
                lastError: null
            };
            persistOrderRequestsState();
            renderOrderRequestsModal();

            let tries = 0;
            let maxRetries = 30; // ~90s at 3s interval
            const poll = setInterval(async () => {
                tries++;
                if (String((app && app.activeEncounterId) || '') !== pollEncounterId) {
                    clearInterval(poll);
                    return;
                }
                try {
                    const encQs = encounterQueryString();
                    let path = `/generation/${genId}/order_requests`;
                    if (force && tries === 1 && !options.followPending) {
                        path += `?force=1${encQs ? `&${encQs}` : ''}`;
                    } else if (encQs) {
                        path += `?${encQs}`;
                    }
                    const r = await apiFetch(path, {
                        headers: { 'Authorization': `Bearer ${token}` }
                    });
                    // Re-check after the await -- see the matching comment in
                    // startRagGeneration's poll.
                    if (String((app && app.activeEncounterId) || '') !== pollEncounterId) {
                        clearInterval(poll);
                        return;
                    }
                    if (r.ok) {
                        const data = await r.json();
                        if (data.status === 'pending') {
                            window.orderRequestsState = {
                                ...window.orderRequestsState,
                                status: 'pending',
                                hasGenerated: false,
                                lastUpdated: Date.now()
                            };
                            persistOrderRequestsState();
                            renderOrderRequestsModal();
                            return;
                        }
                        if (data.status === 'done') {
                            clearInterval(poll);
                            window.orderRequestsState = {
                                status: 'ready',
                                items: Array.isArray(data.items) ? data.items : [],
                                generationId: genId,
                                hasGenerated: true,
                                lastUpdated: Date.now()
                            };
                            persistOrderRequestsState();
                            renderOrderRequestsModal();
                            return;
                        }
                        if (data.status === 'error') {
                            clearInterval(poll);
                            window.orderRequestsState = {
                                status: 'error',
                                items: [],
                                generationId: genId,
                                hasGenerated: false,
                                lastUpdated: Date.now(),
                                lastError: (data.error && String(data.error).trim()) ? String(data.error).trim() : null
                            };
                            persistOrderRequestsState();
                            renderOrderRequestsModal();
                            showToast('Notice', 'Order requests not available', 'warning');
                            return;
                        }
                    }
                } catch (e) {
                    console.warn('[OrderRequests] polling error:', e);
                }
                if (tries >= maxRetries) {
                    clearInterval(poll);
                    window.orderRequestsState = {
                        status: 'error',
                        items: [],
                        generationId: genId,
                        hasGenerated: false,
                        lastUpdated: Date.now(),
                        lastError: 'Timed out waiting for order requests (still pending). Check Clinical API logs and LLM routing.'
                    };
                    persistOrderRequestsState();
                    renderOrderRequestsModal();
                }
            }, 3000);

            window.orderRequestsPollInterval = poll;
        }

        function retryOrderRequests() {
            const genId = window.lastGenerationId;
            if (!genId) {
                showToast('Error', 'Generate a note first.', 'error');
                return;
            }
            startOrderRequestsGeneration(genId, { force: true });
        }

        function fallbackCopy(text) {
            const textarea = document.createElement('textarea');
            textarea.value = text;
            textarea.setAttribute('readonly', '');

            textarea.style.position = 'absolute';
            textarea.style.left = '-9999px';
            textarea.style.fontSize = '16px';

            document.body.appendChild(textarea);

            try {
                textarea.focus();
                textarea.select();

                const success = document.execCommand('copy');

                if (success) {
                    showToast('Copied', 'Note copied to clipboard', 'success');
                } else {
                    throw new Error('execCommand copy failed');
                }

            } catch (error) {
                window.prompt('Copy your note manually:', text);
            } finally {
                document.body.removeChild(textarea);
            }
        }

        async function saveNote() {
            const noteText = typeof getMergedGeneratedNoteText === 'function' ? getMergedGeneratedNoteText() : (document.getElementById('generatedNote').value || '');

            if (!noteText) {
                showToast('Error', 'No note to save', 'error');
                return;
            }

            const timestamp = new Date().toISOString().replace(/[:.]/g, '-');
            const filename = `clinical-note-${timestamp}.txt`;
            const blob = new Blob([noteText], { type: 'text/plain' });

            const fsr = await tryFileSystemSave(blob, filename);
            if (fsr.ok) {
                markNoteSaved();
                return;
            }
            if (fsr.canceled) {
                showToast('Canceled', 'Save canceled', nothingToSaveWarning(noteText) ? 'warning' : 'info');
                return;
            }

            if (isMobileDevice()) {
                const ssr = await tryWebShareWithFiles(blob, filename, noteText);
                if (ssr.ok) {
                    markNoteSaved();
                    return;
                }
                if (ssr.canceled) {
                    showToast('Canceled', 'Share canceled', 'info');
                    return;
                }
            }

            tryDownloadFallback(noteText, filename);
            markNoteSaved();
        }

        function isMobileDevice() {
            return /Android|iPhone|iPad|iPod/i.test(navigator.userAgent);
        }

        function nothingToSaveWarning(text) {
            return !text || !text.trim();
        }

        async function tryWebShareWithFiles(blob, filename, noteText) {
            try {
                const file = new File([blob], filename, { type: 'text/plain' });
                if (navigator.canShare && navigator.canShare({ files: [file] })) {
                    await navigator.share({ files: [file], title: 'Clinical note', text: 'Saved from DreamCision' });
                    showToast('Saved', 'Note saved via Share', 'success');
                    return { ok: true, canceled: false };
                }
            } catch (error) {
                if (error && (error.name === 'AbortError' || error.name === 'NotAllowedError')) {
                    return { ok: false, canceled: true };
                }
                debugLog('Web Share failed:', error && error.message ? error.message : error);
            }
            return { ok: false, canceled: false };
        }

        async function tryFileSystemSave(blob, filename) {
            if (!window.showSaveFilePicker) {
                return { ok: false, canceled: false };
            }
            try {
                const handle = await window.showSaveFilePicker({
                    suggestedName: filename,
                    types: [{ description: 'Text File', accept: { 'text/plain': ['.txt'] } }]
                });
                const writable = await handle.createWritable();
                await writable.write(blob);
                await writable.close();
                showToast('Saved', 'Note saved to chosen folder', 'success');
                return { ok: true, canceled: false };
            } catch (e) {
                if (e && (e.name === 'AbortError' || e.name === 'SecurityError')) {
                    return { ok: false, canceled: true };
                }
                return { ok: false, canceled: false };
            }
        }

        function tryDownloadFallback(noteText, filename) {
            try {
                const dataUrl = 'data:text/plain;charset=utf-8,' + encodeURIComponent(noteText);

                const downloadLink = document.createElement('a');
                downloadLink.href = dataUrl;
                downloadLink.download = filename;

                document.body.appendChild(downloadLink);
                downloadLink.click();
                document.body.removeChild(downloadLink);

                showToast('Saved', 'Download initiated', 'success');

            } catch (error) {
                showToast('Error', 'Saving failed. Long-press to copy or use Share.', 'error');
                console.error('Save fallback failed:', error);
            }
        }

        function retryConsultComment() {
            const genId = window.lastGenerationId;
            if (!genId) {
                showToast('Error', 'Generate a note first.', 'error');
                return;
            }
            startRagGeneration(genId, { force: true });
        }

        function saveDraft() {
            const transcription = document.getElementById('transcriptionDisplay')?.value || '';
            const transcriptionNotes = document.getElementById('transcriptionData')?.value || '';
            const oldVisits = document.getElementById('oldVisitsData')?.value || '';
            const mixedOther = document.getElementById('mixedOtherData')?.value || '';

            if (!transcription && !oldVisits && !mixedOther) {
                showToast('Nothing to Save', 'No data to save', 'warning');
                return;
            }

            const draft = {
                transcription: transcription,
                transcriptionNotes: transcriptionNotes,
                oldVisits: oldVisits,
                mixedOther: mixedOther,
                noteType: document.getElementById('noteType')?.value || 'consult',
                timestamp: new Date().toISOString()
            };

            localStorage.setItem('notegen_draft', JSON.stringify(draft));
            showToast('Draft Saved', 'Draft saved to browser localStorage (persists until cleared)', 'success');
        }

        function saveToStorage() {
            const mergedNote = typeof getMergedGeneratedNoteText === 'function' ? getMergedGeneratedNoteText() : (document.getElementById('generatedNote').value || '');
            const data = {
                transcription: document.getElementById('transcriptionDisplay')?.value || '',
                transcriptionDisplay: document.getElementById('transcriptionDisplay')?.value || '',
                transcriptionNotes: document.getElementById('transcriptionData')?.value || '',
                oldVisits: document.getElementById('oldVisitsData')?.value || '',
                mixedOther: document.getElementById('mixedOtherData')?.value || '',
                generatedNote: mergedNote,
                noteType: document.getElementById('noteType').value
            };
            localStorage.setItem('clinicalNoteData', JSON.stringify(data));
        }

        function loadFromStorage() {
            const stored = localStorage.getItem('clinicalNoteData');
            if (stored) {
                try {
                    const data = JSON.parse(stored);
                    const oldVisits = data.oldVisits || data.chartData || '';
                    const transcriptDisplay = data.transcriptionDisplay || data.transcription || '';
                    const transcriptNotes = data.transcriptionNotes || '';
                    const notesValue = transcriptNotes;
                    setFieldValue('transcriptionData', notesValue);
                    const displayEl = document.getElementById('transcriptionDisplay');
                    if (displayEl) displayEl.value = transcriptDisplay;
                    setFieldValue('oldVisitsData', oldVisits);
                    setFieldValue('mixedOtherData', data.mixedOther || '');
                    if (typeof applyConflictsSplitFromFullNote === 'function') {
                        applyConflictsSplitFromFullNote(data.generatedNote || '');
                    } else {
                        document.getElementById('generatedNote').value = data.generatedNote || '';
                    }
                    const nextType = data.noteType || 'followup';
                    const noteTypeEl = document.getElementById('noteType');
                    const noteTypeMirrorEl = document.getElementById('noteTypeMirror');
                    if (noteTypeEl) noteTypeEl.value = nextType;
                    if (noteTypeMirrorEl) noteTypeMirrorEl.value = nextType;
                    updateGeneratedNoteEmptyState();
                    if (typeof updateCharacterCounter === 'function') {
                        updateCharacterCounter();
                    }
                } catch (error) {
                    console.error('Error loading from storage:', error);
                }
            }
            loadQueue();
        }

        function showProgress(containerId, barId) {
            document.getElementById(containerId).classList.remove('hidden');
            const bar = document.getElementById(barId);
            bar.style.width = '0%';

            let progress = 0;
            const interval = setInterval(() => {
                progress += Math.random() * 10;
                if (progress > 90) progress = 90;
                bar.style.width = progress + '%';
            }, 200);

            bar.dataset.interval = interval;
        }

        function hideProgress(containerId) {
            document.getElementById(containerId).classList.add('hidden');
            const bar = document.getElementById(containerId).querySelector('.progress-bar');
            if (bar && bar.dataset.interval) {
                clearInterval(Number(bar.dataset.interval));
            }
        }

        // Transcription display: allow explicit edit/save with double-confirmation.
        function toggleEditTranscription() {
            const tx = document.getElementById('transcriptionDisplay');
            const btn = document.getElementById('transcriptionEditBtn');
            if (!tx || !btn) return;

            const isReadonly = tx.hasAttribute('readonly');

            if (isReadonly) {
                const ok = window.confirm('Edit transcription? This changes the raw transcript text.');
                if (!ok) return;

                tx.removeAttribute('readonly');
                tx.classList.add('is-editable');
                if (window.AsrTranscriptView && typeof window.AsrTranscriptView.setEditMode === 'function') {
                    window.AsrTranscriptView.setEditMode(true);
                }
                btn.classList.add('is-saving');
                btn.textContent = '💾';
                btn.title = 'Save transcription edits';
                try { tx.focus(); } catch (e) {}
                return;
            }

            // Saving
            const ok2 = window.confirm('Save these transcription edits?');
            if (!ok2) return;

            tx.setAttribute('readonly', 'readonly');
            tx.classList.remove('is-editable');
            if (window.AsrTranscriptView && typeof window.AsrTranscriptView.setEditMode === 'function') {
                window.AsrTranscriptView.setEditMode(false);
            }
            if (window.AsrTranscriptView && typeof window.AsrTranscriptView.syncView === 'function') {
                window.AsrTranscriptView.syncView();
            }
            btn.classList.remove('is-saving');
            btn.textContent = '✎';
            btn.title = 'Edit transcription';

            // Persist via existing storage/workspace sync flows
            // (a) Best-effort persist + WS-sync after a transcription edit; storage/socket
            // hiccups must not surface as an error — the Saved toast below is enough.
            try { saveToStorage(); } catch (e) {}
            try {
                if (window.AuthWorkspace && typeof window.AuthWorkspace.queueSave === 'function') {
                    window.AuthWorkspace.queueSave();
                }
            } catch (e) {}

            // (a) Success toast is the user-facing surface; guard keeps a toast DOM
            // failure from bubbling out of this button handler.
            try { showToast('Saved', 'Transcription updated', 'success'); } catch (e) {}
        }


        function showToast(title, message, type = 'info') {
            const container = document.getElementById('toastContainer');
            const toast = document.createElement('div');
            toast.className = `toast ${type}`;
            toast.style.cursor = 'pointer';

            toast.innerHTML = `
                <div class="toast-title">${escapeHtml(title)}</div>
                <div class="toast-message">${escapeHtml(message)}</div>
            `;

            container.appendChild(toast);

            let autoDismissTimer = setTimeout(() => {
                if (toast.parentNode) {
                    toast.style.animation = 'slideIn 0.3s ease reverse';
                    setTimeout(() => container.removeChild(toast), 300);
                }
            }, 2500);

            // Dismiss on click
            toast.addEventListener('click', () => {
                if (!toast.parentNode) return;
                clearTimeout(autoDismissTimer);
                toast.style.animation = 'slideIn 0.3s ease reverse';
                setTimeout(() => container.removeChild(toast), 300);
            });
        }

        // Sidebar Functions (hover kept for compatibility; width is controlled by body.sidebar-rail-collapsed)
        function expandSidebar() {
            const el = document.getElementById('leftSidebar');
            if (el) el.classList.add('expanded');
        }
        function collapseSidebar() {
            const el = document.getElementById('leftSidebar');
            if (el) el.classList.remove('expanded');
        }

        function toggleSidebarRail() {
            if (!document.body.classList.contains('auth-ready')) return;
            document.body.classList.toggle('sidebar-rail-collapsed');
            const collapsed = document.body.classList.contains('sidebar-rail-collapsed');
            // (a) Best-effort persist of the sidebar-rail collapse state; blocked/quota
            // storage must not fail the user's explicit toggle action.
            try {
                localStorage.setItem('cng_sidebar_rail_collapsed', collapsed ? '1' : '0');
            } catch (e) {}
            const btn = document.getElementById('sidebarRailToggle');
            if (btn) {
                btn.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
                btn.title = collapsed ? 'Expand tool rail' : 'Collapse tool rail';
            }
        }

        function applySavedSidebarRailPreference() {
            if (!document.body.classList.contains('auth-ready')) return;
            // (a) Best-effort restore of the saved rail-preference on load: missing or
            // unreadable storage just keeps the default expanded rail — never fatal.
            try {
                if (localStorage.getItem('cng_sidebar_rail_collapsed') === '1') {
                    document.body.classList.add('sidebar-rail-collapsed');
                    const btn = document.getElementById('sidebarRailToggle');
                    if (btn) {
                        btn.setAttribute('aria-expanded', 'false');
                        btn.title = 'Expand tool rail';
                    }
                }
            } catch (e) {}
        }
        window.toggleSidebarRail = toggleSidebarRail;
        window.applySavedSidebarRailPreference = applySavedSidebarRailPreference;

        // Error Handling
        window.addEventListener('error', function(e) {
            console.error('JavaScript Error:', e.error);
            showToast('Error', 'An unexpected error occurred', 'error');
        });

        window.addEventListener('unhandledrejection', function(e) {
            console.error('Unhandled Promise Rejection:', e.reason);
            let errorMsg = 'Network or server error occurred';
            if (e.reason?.message) {
                errorMsg = `Error: ${e.reason.message}`;
            }
            showToast('Promise Rejection', errorMsg, 'error');
        });

        document.addEventListener('touchstart', function() {}, {passive: true});

        window.addEventListener('orientationchange', function() {
            setTimeout(() => {
                window.scrollTo(0, 0);
            }, 500);
        });

        window.addEventListener('click', function(event) {
            const cameraModal = document.getElementById('cameraModal');
            const promptModal = document.getElementById('promptModal');

            if (event.target === cameraModal) {
                closeCamera();
            }
            if (event.target === promptModal) {
                closePromptSettings();
            }
        });

        // Global state
        let app = {
            settings: {
                serverUrl: '/api',
                apiKey: ''
            },
            connection: {
                status: 'disconnected',
                lastCheck: null,
                /** Set from /api/health — queue pill must not overwrite "Queued" when jobs remain. */
                lastHealthOk: false
            },
            speechRecognition: null,
            isListening: false,
            /** idle | recording | stopping | transcribing | generating_note — Record/Stop UI */
            audioPipelinePhase: 'idle',
            suppressNextAsrTranscribedToast: false,
            pendingGenerateAfterTranscription: false,
            pendingGenerateTimer: null,
            /** Transcription box snapshot at recording start — appended with each ASR session, not replaced. */
            asrBaselineBeforeRecording: '',
            /** Last ASR state from universal_audio (_asrStatus): recording|processing|done|error|fallback|… */
            lastAsrState: 'idle',
            requestQueue: [],
            uiState: {
                lastGenerationId: null,
                showOrderRequestsButton: false,
                showEvidenceButton: false,
                ragHasGenerated: false,
                orderHasGenerated: false,
                ragContent: null,
                orderItems: [],
                ragLastUpdated: null,
                orderLastUpdated: null
            },
            templates: {
                progress: `History of Present Illness:

Physical Exam:

Investigations:

Impression:

Plan:`,
                followup: `Subjective:

Objective:

Assessment:

Plan:`,
                multi_issue_soap: `## Subjective

1. 

## Objective

1. 

## Assessment

1. 

## Plan

1. `,
                consult: `Chief Complaint:

History of Present Illness:

Past Medical History:

Medications:

Allergies:

Social History:

Review of Systems:

Physical Examination:

Investigations:

Impression and Plan:`,
                procedure: `Procedure:

Date:

Indication:

Technique:

Findings:

Complications:

Disposition:`,
                referral: `Reason for Referral:

Relevant History:

Current Treatment:

Specific Questions:`,
                admission: `Admission Date:

Reason for Admission:

Relevant History:

Past medical and surgical history:

Current Medications:

Allergies:

Social History:

Physical Examination:

Investigations:

Impression and Plan:`,
                discharge: `Admission Date:

Admission Diagnosis:

Hospital Course:

Procedures/Consults:

Investigations:

Medications at Discharge:

Follow-Up:

Discharge Instructions:`,
                transfer: `Transfer Date:

Transferring From:

Transferring To:

Reason for Transfer:

Current Diagnoses:

Hospital Course Summary:

Current Medications:

Pending Items:

Contact Information:`,
                pre_encounter_prep: `## Patient Pre-Encounter Summary

### Why the Patient Is Being Seen

### Past Medical History

### Medications

### Immediate Action Items (Top Priority)

### Active Clinical Issues and Follow-ups

### Pending Results and Orders

### Relevant History and Social Context

### Clinical Alerts and Flags

### For Today's Clinician

### For Other Providers / Co-management`,
                summarize: `Summary of Records:`,
                custom: ``
            },
            defaultPrompts: {},
            baselinePrompts: {},
            profileNoteTypesById: {},
            profileNoteTypesOrder: [],
            workspace: {
                state: null,
                version: 1
            },
            noteState: { edited: false, lastSavedHash: null }
        };
        window.app = app; // Expose shared app state for auth module
        // Emergency escape hatch: force-reset stuck recording state.
        // Call from browser console: forceResetRecording()
        window.forceResetRecording = function() {
            if (window.universalAudio && typeof window.universalAudio.forceResetRecording === 'function') {
                window.universalAudio.forceResetRecording();
                if (typeof showToast === 'function') showToast('Reset', 'Recording state force-reset.', 'info');
            }
        };
        window.updateGeneratedNoteEmptyState = updateGeneratedNoteEmptyState;
        window.syncGeneratedNotePreview = syncGeneratedNotePreview;
        window.getMergedGeneratedNoteText = getMergedGeneratedNoteText;
        window.getGeneratedNoteMainText = getGeneratedNoteMainText;
        window.setRecordingButtonsState = setRecordingButtonsState;
        window.setAudioPipelinePhase = setAudioPipelinePhase;
        window.syncRecordingUiFromPipeline = syncRecordingUiFromPipeline;
        window.syncEncounterRecordingStatus = syncEncounterRecordingStatus;
        window.applyConflictsSplitFromFullNote = applyConflictsSplitFromFullNote;
        window.clearConflictsPanel = clearConflictsPanel;

        function getAuthToken() {
            if (window.app?.settings?.apiKey) {
                return window.app.settings.apiKey;
            }
            try {
                // admin_workspace_token holds the same access_token under a
                // second key, written by auth_workspace.js when login redirects
                // to admin.html. Reading both here is what lets this be the
                // single definition rather than each consumer reimplementing
                // the lookup.
                return sessionStorage.getItem('auth_access_token')
                    || sessionStorage.getItem('admin_workspace_token')
                    || '';
            } catch (err) {
                console.warn('[Auth] unable to read auth token:', err);
                return '';
            }
        }
        window.getAuthToken = getAuthToken;

        /**
         * Report a client-side ASR incident to the operator incident store.
         *
         * The implementation is a method on the UniversalAudioHandler instance
         * (universal_audio_handler.js). Three call sites in this file invoked it
         * as a bare `_reportAsrIncident(...)`, where no such binding exists, so
         * each threw a ReferenceError. Every one of those calls sits inside
         * `try { ... } catch (_) {}`, so the throw was swallowed in silence and
         * the incident never left the browser.
         *
         * The three affected paths were the ones most worth reporting: chunk
         * worker error during finalization, merged-transcript fetch after retries
         * were exhausted, and drain POST timeout. Delegating here keeps those
         * sites unchanged and best-effort while making the call actually land.
         */
        function _reportAsrIncident(payload) {
            try {
                var handler = window.universalAudio;
                if (handler && typeof handler._reportAsrIncident === 'function') {
                    handler._reportAsrIncident(payload);
                }
            } catch (_) {}
        }
        window._reportAsrIncident = _reportAsrIncident;

        // Safe-Exit Guard functions
        function simpleHash(str) {
            let h = 0, i, chr;
            if (!str) return '0';
            for (i = 0; i < str.length; i++) {
                chr = str.charCodeAt(i);
                h = ((h << 5) - h) + chr;
                h |= 0;
            }
            return String(h);
        }

        function currentNoteText() {
            if (typeof getMergedGeneratedNoteText === 'function') {
                return (getMergedGeneratedNoteText() || '').trim();
            }
            return (document.getElementById('generatedNote')?.value || '').trim();
        }

        function hasUnsavedNote() {
            const text = currentNoteText();
            if (!text) return false;
            // P3-4: app.noteState.lastSavedHash only ever updates inside
            // saveNote() -- the LOCAL FILE export action (download/Web
            // Share/File System API), not the actual server auto-sync that
            // runs continuously in the background. Almost nobody manually
            // exports their note to a .txt file, so this hash comparison
            // was permanently stale for virtually every real session: the
            // "unsaved note" warning fired on notes that were already
            // safely synced to the server seconds after every edit. Defer
            // to the real sync state (AuthWorkspace's dirty-field tracking,
            // which covers the note/draft along with the rest of the
            // workspace) when it's available; fall back to the old
            // hash-based check only if this file is ever loaded standalone.
            if (window.AuthWorkspace && typeof window.AuthWorkspace.hasUnsavedLocalEdits === 'function') {
                return window.AuthWorkspace.hasUnsavedLocalEdits();
            }
            const currentHash = simpleHash(text);
            return currentHash !== app.noteState.lastSavedHash;
        }

        function markNoteDirty() {
            app.noteState.edited = true;
            saveToStorage();
        }

        function markNoteSaved() {
            const text = currentNoteText();
            app.noteState.lastSavedHash = simpleHash(text);
            app.noteState.edited = false;
            localStorage.setItem('clinicalNoteLastSaved', String(Date.now()));
        }

        function registerUnloadGuards() {
            window.addEventListener('beforeunload', (e) => {
                if (hasUnsavedNote()) {
                    e.preventDefault();
                    e.returnValue = '';
                }
            });

            window.addEventListener('pagehide', () => {
                if (hasUnsavedNote()) {
                    localStorage.setItem('clinicalNoteUnsavedExit', '1');
                }
                // P4-4 pilot instrumentation: report how much the generated
                // note was edited before the doctor moved on. Best-effort,
                // never blocks unload.
                try {
                    const baseline = window.__pilotEditBaseline;
                    if (baseline && baseline.genId && window.DreamCisionUsage) {
                        window.DreamCisionUsage.reportEditDistance(
                            baseline.genId, baseline.text, currentNoteText(), { beacon: true }
                        );
                    }
                } catch (_) {}
            });

            document.addEventListener('visibilitychange', () => {
                if (document.visibilityState === 'hidden' && hasUnsavedNote()) {
                    showToast('Heads-up', 'You have an unsaved note. Tap Save to keep a copy.', 'warning');
                }
            });
        }

        // Prompt Settings Functions
        async function syncProfileSpecialtyToBar() {
            const auth = window.AuthWorkspace;
            if (!auth || typeof auth.request !== 'function') return;
            try {
                const apiBase = getApiBase(app.settings.serverUrl || '/api');
                const resp = await auth.request(apiBase.replace(/\/?$/, '') + '/profile', { method: 'GET' });
                if (!resp.ok) return;
                const p = await resp.json();
                const el = document.getElementById('userSpeciality');
                if (!el) return;
                const def = (p.default_specialty || '').trim();
                if (!def) return;
                if (!(el.value || '').trim()) {
                    el.value = def;
                }
            } catch (e) {
                console.warn('[Profile] specialty sync', e);
            }
        }
        window.syncProfileSpecialtyToBar = syncProfileSpecialtyToBar;

        function toggleBaselinePrompt() {
            const wrap = document.getElementById('baselinePromptWrap');
            const btn = document.getElementById('baselineToggleBtn');
            if (!wrap || !btn) return;
            const hidden = wrap.style.display === 'none' || !wrap.style.display;
            if (hidden) {
                wrap.style.display = 'block';
                btn.textContent = 'Hide baseline template';
            } else {
                wrap.style.display = 'none';
                btn.textContent = 'Show baseline template';
            }
        }

        function hideBaselinePromptPanel() {
            const wrap = document.getElementById('baselinePromptWrap');
            const btn = document.getElementById('baselineToggleBtn');
            if (wrap) wrap.style.display = 'none';
            if (btn) btn.textContent = 'Show baseline template';
        }

        function hideAddCustomNoteTypeSection() {
            const wrap = document.getElementById('addCustomNoteTypeWrap');
            const btn = document.getElementById('addCustomNoteTypeToggleBtn');
            if (wrap) wrap.style.display = 'none';
            if (btn) btn.textContent = 'Add a new template';
        }

        function toggleAddCustomNoteTypeSection() {
            const wrap = document.getElementById('addCustomNoteTypeWrap');
            const btn = document.getElementById('addCustomNoteTypeToggleBtn');
            if (!wrap || !btn) return;
            const hidden = wrap.style.display === 'none' || !wrap.style.display;
            if (hidden) {
                wrap.style.display = 'block';
                btn.textContent = 'Hide add template form';
            } else {
                wrap.style.display = 'none';
                btn.textContent = 'Add a new template';
            }
        }

        function expandAddCustomNoteTypeSection() {
            // The form lives inside the Account templates tab — make sure it's showing.
            setPromptSettingsTab('templates');
            const wrap = document.getElementById('addCustomNoteTypeWrap');
            const btn = document.getElementById('addCustomNoteTypeToggleBtn');
            if (wrap) wrap.style.display = 'block';
            if (btn) btn.textContent = 'Hide add template form';
        }

        function openAddNewNoteTypeFromPrompts() {
            setPromptSettingsTab('templates');
            expandAddCustomNoteTypeSection();
            try {
                const wrap = document.getElementById('addCustomNoteTypeWrap');
                if (wrap && wrap.scrollIntoView) wrap.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
                const labelEl = document.getElementById('newNoteTypeLabel');
                if (labelEl) labelEl.focus();
            } catch (_) {}
        }
        window.openAddNewNoteTypeFromPrompts = openAddNewNoteTypeFromPrompts;

        function openPromptTemplateLibrary() {
            try {
                const url = new URL('prompt-template-picker.html', window.location.href).href;
                const feat = 'width=760,height=700,scrollbars=yes,resizable=yes';
                const w = window.open(url, 'promptTemplateLibrary', feat);
                if (!w) {
                    showToast('Popup blocked', 'Allow popups for this site to use the template library.', 'warning');
                }
            } catch (e) {
                console.warn(e);
                showToast('Error', 'Could not open template library.', 'error');
            }
        }
        window.openPromptTemplateLibrary = openPromptTemplateLibrary;

        function applyPromptTemplateFromPicker(title, promptText) {
            const label = String(title || '').trim();
            const p = String(promptText || '').trim();
            expandAddCustomNoteTypeSection();
            const labelEl = document.getElementById('newNoteTypeLabel');
            const ta = document.getElementById('newNoteTypeInitialPrompt');
            if (labelEl) labelEl.value = label;
            if (ta) ta.value = p;
            try {
                const wrap = document.getElementById('addCustomNoteTypeWrap');
                if (wrap && wrap.scrollIntoView) wrap.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
                if (ta) ta.focus();
            } catch (_) {}
            showToast('Template loaded', 'Review name and prompt — then click Add type.', 'info');
        }

        function setPromptSettingsTab(which) {
            const a = document.getElementById('promptTabTemplates');
            const c = document.getElementById('promptTabSettings');
            const btnA = document.getElementById('promptTabTemplatesBtn');
            const btnC = document.getElementById('promptTabSettingsBtn');
            const w = String(which || '').toLowerCase();
            const isTemplates = w !== 'settings'; // 'types' (legacy callers) now means templates
            const isSettings = w === 'settings';
            if (a) a.style.display = isTemplates ? 'block' : 'none';
            if (c) c.style.display = isSettings ? 'block' : 'none';
            if (btnA) btnA.className = isTemplates ? 'btn btn-primary' : 'btn btn-outline';
            if (btnC) btnC.className = isSettings ? 'btn btn-primary' : 'btn btn-outline';
            // Load settings when switching to settings tab
            if (isSettings) {
                loadNoteTypePreferences();
            }
        }
        window.setPromptSettingsTab = setPromptSettingsTab;

        // Load saved note-type preferences (default + visibility) at boot so
        // they survive page reloads, not just until the next click.
        async function loadNoteTypePrefsAtBoot() {
            const auth = window.AuthWorkspace;
            if (!auth || typeof auth.request !== 'function') return;
            try {
                const apiBase = getApiBase(app.settings.serverUrl || '/api');
                const resp = await auth.request(`${apiBase}/profile`, { method: 'GET' });
                if (!resp.ok) return;
                const p = await resp.json();
                app.visibleNoteTypes = (p.visible_note_types && p.visible_note_types.length) ? p.visible_note_types : null;
                app.defaultNoteType = p.default_note_type || null;
                syncNoteTypeDropdownsFromProfile();
                // Apply the default whenever the dropdown is still on its
                // untouched initial selection (page reload / fresh start).
                const sel = document.getElementById('noteType');
                if (sel && app.defaultNoteType) {
                    const cur = sel.value || 'consult';
                    const untouched = cur === 'consult' || !cur;
                    const has = [...sel.options].some((o) => o.value === app.defaultNoteType);
                    if (untouched && has) {
                        sel.value = app.defaultNoteType;
                        const mirror = document.getElementById('noteTypeMirror');
                        if (mirror) mirror.value = sel.value;
                    }
                }
            } catch (e) {
                console.warn('[Note type preferences] boot load failed:', e);
            }
        }

        async function loadNoteTypePreferences() {
            const auth = window.AuthWorkspace;
            if (!auth || typeof auth.request !== 'function') return;
            try {
                const apiBase = getApiBase(app.settings.serverUrl || '/api');
                const resp = await auth.request(`${apiBase}/profile`, { method: 'GET' });
                if (!resp.ok) return;
                const data = await resp.json();
                const defaultSelect = document.getElementById('defaultNoteTypeSelect');
                const checkboxesDiv = document.getElementById('visibleNoteTypesCheckboxes');
                if (!defaultSelect || !checkboxesDiv) return;
                // Build note type list from /note-types/all — the unfiltered list.
                // profileNoteTypesOrder comes from /note-types which already drops
                // hidden types; using it here would make hidden types unre-checkable.
                let noteTypes = app.profileNoteTypesOrder || [];
                try {
                    const allResp = await auth.request(`${apiBase}/note-types/all`, { method: 'GET' });
                    if (allResp.ok) {
                        const allData = await allResp.json();
                        if (Array.isArray(allData.note_types) && allData.note_types.length) {
                            noteTypes = allData.note_types;
                        }
                    }
                } catch (e) { /* fall back to filtered order */ }
                if (!noteTypes.length) return;
                // Populate default note type dropdown
                defaultSelect.innerHTML = '';
                for (const nt of noteTypes) {
                    if (!nt || !nt.id) continue;
                    const opt = document.createElement('option');
                    opt.value = nt.id;
                    opt.textContent = nt.label || nt.id;
                    defaultSelect.appendChild(opt);
                }
                defaultSelect.value = data.default_note_type || 'consult';
                // Populate checkboxes for visible note types
                checkboxesDiv.innerHTML = '';
                const visibleSet = data.visible_note_types ? new Set(data.visible_note_types) : null;
                for (const nt of noteTypes) {
                    if (!nt || !nt.id) continue;
                    const label = document.createElement('label');
                    label.className = 'note-type-checkbox-label';
                    const cb = document.createElement('input');
                    cb.type = 'checkbox';
                    cb.value = nt.id;
                    cb.checked = visibleSet === null || visibleSet.has(nt.id);
                    label.appendChild(cb);
                    label.appendChild(document.createTextNode(' ' + (nt.label || nt.id)));
                    checkboxesDiv.appendChild(label);
                }
            } catch (e) {
                console.warn('[Note type preferences] Failed to load:', e);
            }
        }
        window.loadNoteTypePreferences = loadNoteTypePreferences;

        async function saveNoteTypePreferences() {
            const auth = window.AuthWorkspace;
            if (!auth || typeof auth.request !== 'function') {
                showToast('Sign in', 'Sign in to save note type preferences.', 'warning');
                return;
            }
            const defaultSelect = document.getElementById('defaultNoteTypeSelect');
            const checkboxesDiv = document.getElementById('visibleNoteTypesCheckboxes');
            if (!defaultSelect || !checkboxesDiv) return;
            const defaultNoteType = defaultSelect.value;
            const checkboxes = checkboxesDiv.querySelectorAll('input[type="checkbox"]:checked');
            const visibleNoteTypes = Array.from(checkboxes).map(cb => cb.value);
            if (!visibleNoteTypes.length) {
                showToast('Error', 'At least one note type must be visible.', 'error');
                return;
            }
            try {
                const apiBase = getApiBase(app.settings.serverUrl || '/api');
                const resp = await auth.request(`${apiBase}/note-type-preferences`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        default_note_type: defaultNoteType,
                        visible_note_types: visibleNoteTypes,
                    }),
                });
                if (!resp.ok) {
                    const err = await resp.json().catch(() => ({}));
                    showToast('Error', err.detail || 'Failed to save preferences.', 'error');
                    return;
                }
                // Update local state
                app.defaultNoteType = defaultNoteType;
                app.visibleNoteTypes = visibleNoteTypes.length > 0 ? visibleNoteTypes : null;
                // Re-sync dropdowns
                await loadProfileNoteTypes();
                showToast('Saved', 'Note type preferences updated.', 'success');
            } catch (e) {
                console.warn('[Note type preferences] Failed to save:', e);
                showToast('Error', 'Failed to save preferences.', 'error');
            }
        }
        window.saveNoteTypePreferences = saveNoteTypePreferences;

        function selectAllNoteTypes() {
            const checkboxesDiv = document.getElementById('visibleNoteTypesCheckboxes');
            if (!checkboxesDiv) return;
            const checkboxes = checkboxesDiv.querySelectorAll('input[type="checkbox"]');
            checkboxes.forEach(cb => cb.checked = true);
        }
        window.selectAllNoteTypes = selectAllNoteTypes;

        function deselectAllNoteTypes() {
            const checkboxesDiv = document.getElementById('visibleNoteTypesCheckboxes');
            if (!checkboxesDiv) return;
            const checkboxes = checkboxesDiv.querySelectorAll('input[type="checkbox"]');
            checkboxes.forEach(cb => cb.checked = false);
        }
        window.deselectAllNoteTypes = deselectAllNoteTypes;

        /** Clears per-encounter UI fields when an encounter is closed. */
        function resetEncounterInstructionsForNewEncounter() {
            const ta = document.getElementById('encounterInstructionsInput');
            if (ta) ta.value = '';
        }
        window.resetEncounterInstructionsForNewEncounter = resetEncounterInstructionsForNewEncounter;

        async function showPromptSettings() {
            const modal = document.getElementById('promptModal');
            const select = document.getElementById('noteTypeSelect');
            if (!modal || !select) return;

            await loadProfileNoteTypes();

            if (!Object.keys(app.defaultPrompts || {}).length) {
                showToast('Warning', 'Default prompts not loaded yet. Check API connection.', 'warning');
            }

            const activeNoteType = document.getElementById('noteType')?.value || 'consult';
            select.value = activeNoteType;

            hideBaselinePromptPanel();
            hideAddCustomNoteTypeSection();
            updatePromptDisplay();
            updateCustomNoteTypeDeleteVisibility();
            syncCustomRenameUi();
            setPromptSettingsTab('templates');
            modal.classList.add('show');
        }

        function closePromptSettings() {
            const modal = document.getElementById('promptModal');
            if (modal) {
                modal.classList.remove('show');
            }
        }
        window.showPromptSettings = showPromptSettings;
        window.closePromptSettings = closePromptSettings;

        function _isOtherNoteTypeScope(noteType) {
            const meta = app.profileNoteTypesById && app.profileNoteTypesById[noteType];
            if (meta && meta.scope) return meta.scope === 'other';
            return ['referral', 'summarize', 'custom', 'procedure', 'pre_encounter_prep'].indexOf(noteType) >= 0;
        }

        function syncNoteTypeDropdownsFromProfile() {
            const order = app.profileNoteTypesOrder;
            if (!order || !order.length) return;
            const cur = (document.getElementById('noteType') && document.getElementById('noteType').value) || 'consult';
            const ids = ['noteType', 'noteTypeSelect', 'noteTypeMirror'];
            // Get visible note types filter (null = show all)
            const visibleSet = app.visibleNoteTypes ? new Set(app.visibleNoteTypes) : null;
            for (const sid of ids) {
                const sel = document.getElementById(sid);
                if (!sel || sel.tagName !== 'SELECT') continue;
                sel.innerHTML = '';
                for (const nt of order) {
                    if (!nt || !nt.id) continue;
                    // Filter by visible note types
                    if (visibleSet && !visibleSet.has(nt.id)) continue;
                    const opt = document.createElement('option');
                    opt.value = nt.id;
                    opt.textContent = nt.label || nt.id;
                    sel.appendChild(opt);
                }
                const has = [...sel.options].some((o) => o.value === cur);
                if (has) {
                    sel.value = cur;
                } else {
                    // Current choice is gone (hidden/deleted): prefer the user's
                    // default note type, then the first visible option.
                    const dflt = app.defaultNoteType;
                    const hasDflt = dflt && [...sel.options].some((o) => o.value === dflt);
                    sel.value = hasDflt ? dflt : (sel.options[0] ? sel.options[0].value : cur);
                }
            }
        }

        function updateCustomNoteTypeDeleteVisibility() {
            const sel = document.getElementById('noteTypeSelect');
            const wrap = document.getElementById('customNoteTypeDeleteWrap');
            if (!sel || !wrap) return;
            const nt = sel.value;
            const meta = app.profileNoteTypesById && app.profileNoteTypesById[nt];
            wrap.style.display = (meta && meta.kind === 'custom') ? 'block' : 'none';
        }

        function syncCustomRenameUi() {
            const sel = document.getElementById('noteTypeSelect');
            const wrap = document.getElementById('customRenameWrap');
            const inp = document.getElementById('renameCustomNoteTypeLabel');
            if (!sel || !wrap) return;
            const nt = sel.value || '';
            const meta = app.profileNoteTypesById && app.profileNoteTypesById[nt];
            const isCustom = !!(meta && meta.kind === 'custom');
            wrap.style.display = isCustom ? 'block' : 'none';
            if (inp) inp.value = isCustom ? (meta.label || '') : '';
        }
        window.syncCustomRenameUi = syncCustomRenameUi;

        async function createCustomNoteType() {
            const auth = window.AuthWorkspace;
            if (!auth || typeof auth.request !== 'function') {
                showToast('Sign in', 'Sign in to add custom note types.', 'warning');
                return;
            }
            const label = (document.getElementById('newNoteTypeLabel') && document.getElementById('newNoteTypeLabel').value || '').trim();
            const initial_prompt = (document.getElementById('newNoteTypeInitialPrompt') && document.getElementById('newNoteTypeInitialPrompt').value || '').trim();
            if (!label) {
                showToast('Name required', 'Enter a name for the new note type.', 'warning');
                return;
            }
            const apiBase = getApiBase(app.settings.serverUrl || '/api');
            const resp = await auth.request(`${apiBase}/note-types/custom`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ label, initial_prompt: initial_prompt || undefined }),
            });
            if (!resp.ok) {
                let msg = 'Could not create note type.';
                // (a) Best-effort error-detail parse: non-JSON body falls back to the
                // generic message shown in the Error toast below.
                try {
                    const err = await resp.json();
                    if (err.detail) msg = typeof err.detail === 'string' ? err.detail : JSON.stringify(err.detail);
                } catch (e) {}
                showToast('Error', msg, 'error');
                return;
            }
            document.getElementById('newNoteTypeLabel').value = '';
            document.getElementById('newNoteTypeInitialPrompt').value = '';
            await loadDefaultPrompts();
            await loadProfileNoteTypes();
            updatePromptDisplay();
            updateCustomNoteTypeDeleteVisibility();
            syncCustomRenameUi();
            // Auto-select the newly created custom type if we can infer it from the response.
            try {
                const data = await resp.json();
                const created = (data.note_types || []).find((x) => x && x.kind === 'custom' && (x.label || '') === label);
                if (created && created.id) {
                    const mainSel = document.getElementById('noteType');
                    if (mainSel) mainSel.value = created.id;
                    const mirror = document.getElementById('noteTypeMirror');
                    if (mirror) mirror.value = created.id;
                    const promptSel = document.getElementById('noteTypeSelect');
                    if (promptSel) promptSel.value = created.id;
                    syncNoteTypeDropdownsFromProfile();
                }
            } catch (_) {}
            // (a) Best-effort auto-select of the just-created type: if any of the select
            // elements are absent mid-refresh, the Added toast still confirms creation.
            showToast('Added', 'Custom note type created.', 'success');
        }

        async function renameCustomNoteType() {
            const sel = document.getElementById('noteTypeSelect');
            const inp = document.getElementById('renameCustomNoteTypeLabel');
            const auth = window.AuthWorkspace;
            if (!sel || !inp || !auth || typeof auth.request !== 'function') return;
            const noteType = sel.value || '';
            const meta = app.profileNoteTypesById && app.profileNoteTypesById[noteType];
            if (!meta || meta.kind !== 'custom') return;
            const newLabel = inp.value.trim();
            if (!newLabel) {
                showToast('Name required', 'Enter a new name.', 'warning');
                return;
            }
            const apiBase = getApiBase(app.settings.serverUrl || '/api');
            const resp = await auth.request(`${apiBase}/note-types/${encodeURIComponent(noteType)}`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ label: newLabel }),
            });
            if (!resp.ok) {
                let msg = 'Could not rename note type.';
                // (a) Best-effort error-detail parse: non-JSON body falls back to the
                // generic message shown in the Error toast below.
                try {
                    const err = await resp.json();
                    if (err.detail) msg = typeof err.detail === 'string' ? err.detail : JSON.stringify(err.detail);
                } catch (e) {}
                showToast('Error', msg, 'error');
                return;
            }
            await loadDefaultPrompts();
            await loadProfileNoteTypes();
            updatePromptDisplay();
            updateCustomNoteTypeDeleteVisibility();
            syncCustomRenameUi();
            showToast('Renamed', 'Custom note type renamed.', 'success');
        }
        window.renameCustomNoteType = renameCustomNoteType;

        async function deleteCustomNoteType() {
            const select = document.getElementById('noteTypeSelect');
            const auth = window.AuthWorkspace;
            if (!select || !auth || typeof auth.request !== 'function') return;
            const noteType = select.value || '';
            const meta = app.profileNoteTypesById && app.profileNoteTypesById[noteType];
            if (!meta || meta.kind !== 'custom') return;
            if (!confirm(`Delete custom note type "${meta.label || noteType}"? This cannot be undone.`)) return;
            const apiBase = getApiBase(app.settings.serverUrl || '/api');
            const resp = await auth.request(`${apiBase}/note-types/${encodeURIComponent(noteType)}`, { method: 'DELETE' });
            if (!resp.ok) {
                showToast('Error', 'Could not delete note type.', 'error');
                return;
            }
            await loadDefaultPrompts();
            await loadProfileNoteTypes();
            updatePromptDisplay();
            updateCustomNoteTypeDeleteVisibility();
            showToast('Deleted', 'Custom note type removed.', 'info');
        }

        async function revertAllBuiltinTemplates() {
            const auth = window.AuthWorkspace;
            if (!auth || typeof auth.request !== 'function') {
                showToast('Sign in', 'Sign in to reset templates.', 'warning');
                return;
            }
            if (!confirm('Reset every built-in note type to the server baseline? Your custom note types are kept.')) return;
            const apiBase = getApiBase(app.settings.serverUrl || '/api');
            const resp = await auth.request(`${apiBase}/note-types/revert-builtins`, { method: 'POST' });
            if (!resp.ok) {
                showToast('Error', 'Could not reset templates.', 'error');
                return;
            }
            await loadDefaultPrompts();
            await loadProfileNoteTypes();
            updatePromptDisplay();
            showToast('Reset', 'All built-in templates match the server baseline.', 'info');
        }
        window.revertAllBuiltinTemplates = revertAllBuiltinTemplates;

        function updatePromptDisplay() {
            const select = document.getElementById('noteTypeSelect');
            const defaultTextarea = document.getElementById('defaultPromptDisplay');
            const profileTa = document.getElementById('profileTemplateInput');
            const typeLbl = document.getElementById('promptModalNoteTypeLabel');
            if (!select || !defaultTextarea) {
                return;
            }

            const noteType = select.value || 'consult';
            if (typeLbl) {
                const opt = select.options[select.selectedIndex];
                typeLbl.textContent = (opt && opt.text) ? opt.text.trim() : noteType;
            }

            const baseLine = (app.baselinePrompts && app.baselinePrompts[noteType]) || '';
            const defaultPrompt = baseLine || (app.defaultPrompts && app.defaultPrompts[noteType]) || 'Baseline not loaded. Check connection and try again.';
            const meta = app.profileNoteTypesById && app.profileNoteTypesById[noteType];
            const profText = meta ? (meta.user_prompt || '') : '';

            defaultTextarea.value = defaultPrompt;
            if (profileTa) profileTa.value = profText;
        }

        async function loadProfileNoteTypes() {
            const auth = window.AuthWorkspace;
            if (!auth || typeof auth.request !== 'function') {
                app.profileNoteTypesById = {};
                app.profileNoteTypesOrder = [];
                return;
            }
            const apiBase = getApiBase(app.settings.serverUrl || '/api');
            try {
                const resp = await auth.request(`${apiBase}/note-types`, { method: 'GET' });
                if (!resp.ok) {
                    app.profileNoteTypesById = {};
                    app.profileNoteTypesOrder = [];
                    return;
                }
                const data = await resp.json();
                const map = {};
                app.profileNoteTypesOrder = data.note_types || [];
                for (const nt of data.note_types || []) {
                    if (nt && nt.id) map[nt.id] = nt;
                }
                app.profileNoteTypesById = map;
                syncNoteTypeDropdownsFromProfile();
            } catch (e) {
                console.warn('[Profile] note-types load failed', e);
                app.profileNoteTypesById = {};
                app.profileNoteTypesOrder = [];
            }
        }

        async function saveAccountNoteTemplate() {
            const select = document.getElementById('noteTypeSelect');
            const profileTa = document.getElementById('profileTemplateInput');
            if (!select || !profileTa) return;
            const auth = window.AuthWorkspace;
            if (!auth || typeof auth.request !== 'function') {
                showToast('Sign in', 'Sign in to save account templates.', 'warning');
                return;
            }
            const noteType = select.value || 'consult';
            const text = profileTa.value.trim();
            const apiBase = getApiBase(app.settings.serverUrl || '/api');
            const body = _isOtherNoteTypeScope(noteType)
                ? { templates_other: { [noteType]: text } }
                : { templates: { [noteType]: text } };
            const resp = await auth.request(`${apiBase}/note-types`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });
            if (!resp.ok) {
                showToast('Error', 'Could not save account template.', 'error');
                return;
            }
            await loadDefaultPrompts();
            await loadProfileNoteTypes();
            updatePromptDisplay();
            showToast('Saved', 'Account note-type template saved.', 'success');
        }

        async function revertAccountNoteTemplate() {
            const select = document.getElementById('noteTypeSelect');
            if (!select) return;
            const auth = window.AuthWorkspace;
            if (!auth || typeof auth.request !== 'function') {
                showToast('Sign in', 'Sign in to revert templates.', 'warning');
                return;
            }
            const noteType = select.value || 'consult';
            const apiBase = getApiBase(app.settings.serverUrl || '/api');
            const q = _isOtherNoteTypeScope(noteType) ? '?other=true' : '';
            const resp = await auth.request(`${apiBase}/note-types/${encodeURIComponent(noteType)}/revert${q}`, {
                method: 'POST',
            });
            if (!resp.ok) {
                showToast('Error', 'Could not revert template.', 'error');
                return;
            }
            await loadDefaultPrompts();
            await loadProfileNoteTypes();
            updatePromptDisplay();
            showToast('Reverted', 'Template reset to server baseline.', 'info');
        }

        // Backward-compat: encounter instructions UI moved out of the prompt modal.
        async function saveCustomPrompt() {
            const ta = document.getElementById('encounterInstructionsInput');
            if (!ta) return;
            showToast('Applied', 'Encounter instructions saved to this encounter only.', 'success');
        }

        async function clearCustomPrompt() {
            const ta = document.getElementById('encounterInstructionsInput');
            if (!ta) return;
            ta.value = '';
            try {
                ta.dispatchEvent(new Event('input', { bubbles: true }));
            } catch (_) {}
            showToast('Cleared', 'Encounter instructions cleared.', 'info');
        }

        let serviceErrorTimer = null;
        let lastServiceRetry = null;

        function showServiceErrorBanner(detail, retryFn) {
            const traceId =
                detail?.trace_id ||
                detail?.detail?.trace_id ||
                '';
            try {
                if (traceId) {
                    console.warn('[Service error]', traceId, detail);
                }
            } catch (e) {}

            const friendly =
                typeof summarizeServiceDetailForUser === 'function'
                    ? summarizeServiceDetailForUser(detail)
                    : 'This action could not be completed.';

            // Mobile UX: fixed banners can block interactions on small screens.
            // Use a non-blocking toast as the primary surface, and only fall back
            // to the banner UI on larger screens.
            try {
                showToast('Could not complete', friendly, 'warning');
            } catch (e) {}

            const isNarrow = (window.innerWidth || 9999) <= 640;
            if (isNarrow) {
                // On phones, don't show the blocking banner.
                lastServiceRetry = typeof retryFn === 'function' ? retryFn : null;
                return;
            }

            const banner = document.getElementById('serviceErrorBanner');
            const titleEl = document.getElementById('serviceErrorTitle');
            const detailsEl = document.getElementById('serviceErrorDetails');
            const retryBtn = document.getElementById('serviceErrorRetry');
            const closeBtn = document.getElementById('serviceErrorClose');
            if (!banner || !titleEl || !detailsEl || !retryBtn || !closeBtn) return;

            titleEl.textContent = 'Could not complete';
            detailsEl.textContent = friendly;

            if (serviceErrorTimer) clearTimeout(serviceErrorTimer);
            serviceErrorTimer = setTimeout(() => hideServiceErrorBanner(), 120000);

            lastServiceRetry = typeof retryFn === 'function' ? retryFn : null;
            retryBtn.disabled = !lastServiceRetry;
            retryBtn._retryCount = retryBtn._retryCount || 0;
            retryBtn.onclick = async () => {
                if (!lastServiceRetry) return;
                retryBtn._retryCount += 1;
                // On repeated failures, add delay with exponential backoff
                const attempt = retryBtn._retryCount;
                if (attempt > 1) {
                    const delaySec = Math.min(5 * Math.pow(2, attempt - 2), 120);
                    retryBtn.disabled = true;
                    retryBtn.textContent = `Retrying in ${delaySec}s…`;
                    await new Promise(r => setTimeout(r, delaySec * 1000));
                    retryBtn.textContent = 'Retry';
                }
                // If a queue job ID is attached, process it via the queue endpoint.
                const jobId = lastServiceRetry.jobId;
                if (jobId) {
                    const token = getAuthToken();
                    if (!token) { showToast('Sign In Required', 'Log in to retry.', 'warning'); return; }
                    const progressMap = { ocr: 'documentProgress' };
                    const barMap = { ocr: 'documentProgressBar' };
                    // Find request type from app.requestQueue
                    const qJob = app.requestQueue.find((r) => r && String(r.id) === String(jobId));
                    const jobType = qJob?.type || 'ocr';
                    if (isRemovedAudioQueueType(jobType)) {
                        showToast('Retry unavailable', 'Audio recordings use saved segments, not the queue.', 'warning');
                        return;
                    }
                    if (progressMap[jobType]) showProgress(progressMap[jobType], barMap[jobType]);
                    try {
                        const resp = await apiFetch(`/queue/${jobId}/process`, {
                            method: 'POST',
                            headers: { 'Authorization': `Bearer ${token}` }
                        });
                        if (!resp.ok) {
                            const errText = await resp.text();
                            const msg =
                                typeof formatApiErrorMessage === 'function'
                                    ? formatApiErrorMessage(errText, { httpStatus: resp.status })
                                    : `Could not process this job (${resp.status}).`;
                            throw new Error(msg);
                        }
                        const data = await resp.json();
                        if (jobType === 'ocr') {
                            // V7: OCR results go to the target field (default: oldVisitsData)
                            const targetFieldId = window.currentDropTargetField || window.lastInputFieldId || 'oldVisitsData';
                            const targetEl = document.getElementById(targetFieldId);
                            const existing = targetEl ? targetEl.value : '';
                            const addition = sanitizeClinicalText(data.text || '');
                            const block = addition || '[No text detected]';
                            const newVal = existing ? `${existing}\n\nExtracted Text:\n\n${block}` : `Extracted Text:\n\n${block}`;
                            if (targetEl) setFieldValue(targetFieldId, newVal);
                        }
                        // Remove from local queue
                        app.requestQueue = app.requestQueue.filter((r) => r && String(r.id) !== String(jobId));
                        saveQueue();
                        updateQueueDisplay();
                        saveToStorage();
                        hideServiceErrorBanner();
                        showToast('Processed', 'OCR complete', 'success');
                    } catch (error) {
                        console.error('[Ribbon Retry] Failed:', error);
                        const errorMsg =
                            typeof formatApiErrorMessage === 'function'
                                ? formatApiErrorMessage(error.message || '')
                                : error.message || 'Retry failed.';
                        showToast('Error', errorMsg, 'error');
                    } finally {
                        if (progressMap[jobType]) hideProgress(progressMap[jobType]);
                    }
                } else {
                    lastServiceRetry();
                }
            };
            closeBtn.onclick = () => hideServiceErrorBanner();

            banner.classList.remove('hidden');
        }

        function hideServiceErrorBanner() {
            const banner = document.getElementById('serviceErrorBanner');
            if (!banner) return;
            banner.classList.add('hidden');
            const retryBtn = document.getElementById('serviceErrorRetry');
            if (retryBtn) { retryBtn._retryCount = 0; }
        }

        function initPriorVisitsCollapsible() {
            const sec = document.getElementById('priorVisitsSection');
            const btn = document.getElementById('priorVisitsToggle');
            if (!sec || !btn || btn.dataset.bound === '1') return;
            btn.dataset.bound = '1';
            btn.addEventListener('click', (e) => {
                e.preventDefault();
                const nowCollapsed = sec.classList.toggle('collapsed');
                btn.setAttribute('aria-expanded', nowCollapsed ? 'false' : 'true');
            });
        }
        /** Sticky offset for Edit/Copy chrome so it stays under the pinned note header while scrolling. */
        function syncGeneratedNoteChromeStickyTop() {
            const stickyTop = document.querySelector('#noteCard .note-card-sticky-top');
            if (!stickyTop) return;
            const base = window.matchMedia('(min-width: 851px)').matches ? 88 : 0;
            const h = Math.ceil(stickyTop.getBoundingClientRect().height);
            document.documentElement.style.setProperty('--cng-note-chrome-sticky-top', `${base + h}px`);
        }

        /** Encounter instructions row: Add/Hide + Clear (when panel open). */
        function syncEncounterInstructionsToolbar() {
            const toolbar = document.getElementById('noteCardToolbarRow');
            const wrap = document.getElementById('encounterInstructionsInlineWrap');
            const clearBtn = document.getElementById('encounterInstructionsClearBtn');
            if (!toolbar || !wrap) return;
            const open = wrap.style.display !== 'none' && wrap.style.display !== '';
            toolbar.classList.toggle('note-card-toolbar-row--instructions-open', open);
            if (clearBtn) clearBtn.style.display = open ? '' : 'none';
            syncGeneratedNoteChromeStickyTop();
        }

        function toggleEncounterInstructionsInline() {
            const wrap = document.getElementById('encounterInstructionsInlineWrap');
            const btn = document.getElementById('encounterInstructionsInlineToggleBtn');
            if (!wrap || !btn) return;
            const hidden = wrap.style.display === 'none' || !wrap.style.display;
            if (hidden) {
                wrap.style.display = 'block';
                btn.textContent = 'Hide encounter instructions';
                try {
                    const ta = document.getElementById('encounterInstructionsInput');
                    if (ta) ta.focus();
                } catch (_) {}
            } else {
                wrap.style.display = 'none';
                btn.textContent = 'Add encounter instructions';
            }
            syncEncounterInstructionsToolbar();
        }
        window.toggleEncounterInstructionsInline = toggleEncounterInstructionsInline;
        window.syncEncounterInstructionsToolbar = syncEncounterInstructionsToolbar;
        window.syncGeneratedNoteChromeStickyTop = syncGeneratedNoteChromeStickyTop;
        window.syncCopyNoteButtonPlacement = syncEncounterInstructionsToolbar;


        // Workspace load (encounter instructions are in-memory only — not in extras.customPrompts)
        async function loadWorkspaceState() {
            const auth = window.AuthWorkspace;
            if (!auth || typeof auth.request !== 'function') return false;

            const apiBase = getApiBase(app.settings.serverUrl || '/api');
            const resp = await auth.request(`${apiBase}/workspace/`, { method: 'GET' });
            if (!resp.ok) return false;

            const data = await resp.json();
            app.workspace.state = data?.state || {
                settings: { theme: 'light', language: 'en' },
                documents: [],
                draft: null,
                extras: {}
            };
            app.workspace.version = data?.version || 1;

            return true;
        }

        async function loadDefaultPrompts() {
            try {
                const auth = window.AuthWorkspace;

                // If no auth workspace yet, clear and return (we will call again after login)
                if (!auth || typeof auth.request !== 'function') {
                    app.defaultPrompts = {};
                    app.baselinePrompts = {};
                    return false;
                }

                const apiBase = getApiBase(app.settings.serverUrl || '/api');
                const resp = await auth.request(`${apiBase}/note_prompts`, { method: 'GET' });

                if (!resp.ok) {
                    console.warn('[Prompts] Failed to load:', resp.status);
                    app.defaultPrompts = {};
                    app.baselinePrompts = {};
                    return false;
                }

                const data = await resp.json();

                // Backward compatibility
                const templates = data?.templates || data?.prompts || {};
                app.defaultPrompts = (templates && typeof templates === 'object') ? { ...templates } : {};
                const baselines = data?.templates_baseline || {};
                app.baselinePrompts = (baselines && typeof baselines === 'object') ? { ...baselines } : {};

                return true;
            } catch (e) {
                console.error('[Prompts] Error loading default prompts:', e);
                app.defaultPrompts = {};
                app.baselinePrompts = {};
                return false;
            }
        }

        // Settings Management
        function debugLog(...args) {
            if (window.DEBUG_MODE === true) {
                console.log(...args);
            }
        }

        function loadSettings() {
            const token = getAuthToken();
            app.settings = { serverUrl: '/api', apiKey: token };
            if (token) {
                checkConnection();
            }
        }

        function saveSettings() {
            const token = getAuthToken();
            app.settings = { serverUrl: '/api', apiKey: token };
            writeSettings({ serverUrl: '/api', apiKey: token });
            return app.settings;
        }

        function debugAudioCapabilities() {
            if (window.CNGAudioUI && typeof window.CNGAudioUI.debugAudioCapabilities === 'function') {
                return window.CNGAudioUI.debugAudioCapabilities();
            }
        }

        let connectionFailures = 0;
        const MAX_FAILURES = 3;

        async function checkConnection() {
            const token = getAuthToken();
            if (!token) return;
            app.settings.apiKey = token;
            if (app.isStreaming) {
                return;
            }

            const controller = new AbortController();
            const timeoutId = setTimeout(() => controller.abort(), 8000);

            try {
                const apiBase = getApiBase(app.settings.serverUrl || '/api');
                const response = await apiFetch('/health', {
                    method: 'GET',
                    headers: { 'Authorization': `Bearer ${token}` },
                    signal: controller.signal
                });

                clearTimeout(timeoutId);

                if (response.ok) {
                    app.connection.lastHealthOk = true;
                    connectionFailures = 0;
                    if (Array.isArray(app.requestQueue) && app.requestQueue.some((r) => isQueueJobAutoProcessable(r) && queueJobMatchesActiveEncounter(r))) {
                        try {
                            await processQueue();
                        } catch (e) {
                            console.warn('[Queue] processQueue after reconnect:', e);
                        }
                    }
                    syncQueueConnectionPill();
                } else {
                    throw new Error(`Server error: ${response.status}`);
                }
            } catch (error) {
                clearTimeout(timeoutId);
                connectionFailures++;
                app.connection.lastHealthOk = false;
                syncQueueConnectionPill();

                if (error.name === 'AbortError') {
                    debugLog('Connection timeout during health check');
                } else if (error.message.includes('Failed to fetch')) {
                    debugLog('Network error during health check');
                } else {
                    debugLog('Connection error:', error.message);
                }
            }
        }

        // Character Counter Logic (V7 API - 3 field system): 100k chars / section (~24k tokens heuristic).
        function updateCharacterCounter() {
            const MAX_CHART_CHARS = 100000;

            const oldVisitsEl = document.getElementById('oldVisitsData');
            const mixedOtherEl = document.getElementById('mixedOtherData');
            const transNotesEl = document.getElementById('transcriptionData');
            const transAsrEl = document.getElementById('transcriptionDisplay');

            const oldVisitsLen = oldVisitsEl?.value.length || 0;
            const mixedOtherLen = mixedOtherEl?.value.length || 0;
            const currentEncounterLen =
                (transNotesEl?.value.length || 0) + (transAsrEl?.value.length || 0);

            // Update old visits counter
            const oldVisitsCounter = document.getElementById('oldVisitsCounter');
            if (oldVisitsCounter) {
                const oldVisitsRemaining = MAX_CHART_CHARS - oldVisitsLen;
                oldVisitsCounter.textContent = `${oldVisitsLen.toLocaleString()} / ${MAX_CHART_CHARS.toLocaleString()}`;
                oldVisitsCounter.className =
                    'hidden ' + (oldVisitsRemaining < 0 ? 'text-danger' : 'text-muted');
                if (oldVisitsEl) {
                    oldVisitsEl.classList.toggle('is-invalid', oldVisitsRemaining < 0);
                }
            }

            // Update mixed other counter
            const mixedOtherCounter = document.getElementById('mixedOtherCounter');
            if (mixedOtherCounter) {
                const mixedOtherRemaining = MAX_CHART_CHARS - mixedOtherLen;
                mixedOtherCounter.textContent = `${mixedOtherLen.toLocaleString()} / ${MAX_CHART_CHARS.toLocaleString()}`;
                mixedOtherCounter.className =
                    'hidden ' + (mixedOtherRemaining < 0 ? 'text-danger' : 'text-muted');
                if (mixedOtherEl) {
                    mixedOtherEl.classList.toggle('is-invalid', mixedOtherRemaining < 0);
                }
            }

            // Show/hide global warning (any of the three chart channels oversized)
            const warningEl = document.getElementById('charLimitWarning');
            const messageEl = document.getElementById('charLimitMessage');
            if (warningEl && messageEl) {
                const oldVisitsOver = oldVisitsLen > MAX_CHART_CHARS;
                const mixedOtherOver = mixedOtherLen > MAX_CHART_CHARS;
                const currentOver = currentEncounterLen > MAX_CHART_CHARS;

                if (oldVisitsOver || mixedOtherOver || currentOver) {
                    warningEl.classList.remove('hidden');
                    const overFields = [];
                    if (currentOver) overFields.push('Current encounter (dictation + notes)');
                    if (oldVisitsOver) overFields.push('Prior Visits');
                    if (mixedOtherOver) overFields.push('Labs/Imaging/Consults');
                    messageEl.textContent =
                        `${overFields.join(', ')} exceed the recommended size (~24k tokens or 100,000 characters per section). ` +
                        'Content over ~24k tokens per section may be trimmed server-side after preprocessing. Reduce paste size for best results.';
                } else {
                    warningEl.classList.add('hidden');
                }
            }
        }

        // Attach character counter listeners to chart fields (P9: include current encounter text areas)
        function initCharacterCounters() {
            ['oldVisitsData', 'mixedOtherData', 'transcriptionData', 'transcriptionDisplay'].forEach(id => {
                const el = document.getElementById(id);
                if (el) {
                    el.addEventListener('input', updateCharacterCounter);
                    el.addEventListener('paste', () => setTimeout(updateCharacterCounter, 10));
                }
            });
            // Initial update
            updateCharacterCounter();
        }

        /** User-adjustable width between chart column and generated-note column (desktop). */
        function initWorkspaceSplit() {
            const wrap = document.getElementById('workspaceSplitWrap');
            const split = document.getElementById('workspaceSplitter');
            if (!wrap || !split) return;

            const KEY = 'workspaceSplitLeftPct';
            const minPct = 25;
            const maxPct = 75;

            let leftPct = 50;
            // (a) Best-effort restore of the saved split width: blocked/missing storage
            // just falls back to the default 50/50 split — never fatal.
            try {
                const s = parseFloat(localStorage.getItem(KEY) || '', 10);
                if (!isNaN(s)) leftPct = s;
            } catch {}

            function applyPct(pct) {
                leftPct = Math.min(maxPct, Math.max(minPct, pct));
                wrap.style.gridTemplateColumns = `minmax(240px, ${leftPct}%) 8px minmax(260px, 1fr)`;
                split.setAttribute('aria-valuenow', String(Math.round(leftPct)));
                // (a) Best-effort persist of the split width: blocked/quota storage must
                // not break an in-session drag-resize of the sidebar split.
                try {
                    localStorage.setItem(KEY, String(leftPct));
                } catch {}
            }

            applyPct(leftPct);

            const mq = window.matchMedia('(min-width: 851px)');
            let splitDragBound = false;
            function bindSplitDrag() {
                if (splitDragBound) return;
                splitDragBound = true;
                let dragging = false;

                function onMove(clientX) {
                    const rect = wrap.getBoundingClientRect();
                    if (rect.width <= 0) return;
                    const pct = ((clientX - rect.left) / rect.width) * 100;
                    applyPct(pct);
                }

                split.addEventListener('pointerdown', (e) => {
                    if (e.button !== 0) return;
                    dragging = true;
                    // (a) setPointerCapture can throw if the pointer isn't active for
                    // this element; a failure here just means pointermove deltas continue
                    // to resolve via the window listeners below — non-fatal.
                    try {
                        split.setPointerCapture(e.pointerId);
                    } catch {}
                    e.preventDefault();
                    document.body.style.cursor = 'col-resize';
                    document.body.style.userSelect = 'none';
                });
                split.addEventListener('pointermove', (e) => {
                    if (!dragging) return;
                    onMove(e.clientX);
                });
                split.addEventListener('pointerup', (e) => {
                    if (!dragging) return;
                    dragging = false;
                    try {
                        split.releasePointerCapture(e.pointerId);
                    } catch {}
                    document.body.style.cursor = '';
                    document.body.style.userSelect = '';
                });
                split.addEventListener('pointercancel', () => {
                    dragging = false;
                    document.body.style.cursor = '';
                    document.body.style.userSelect = '';
                });

                split.addEventListener('dblclick', () => applyPct(50));

                split.addEventListener('keydown', (e) => {
                    let next = leftPct;
                    if (e.key === 'ArrowLeft') next = leftPct - 2;
                    if (e.key === 'ArrowRight') next = leftPct + 2;
                    if (e.key === 'Home') next = minPct;
                    if (e.key === 'End') next = maxPct;
                    if (next !== leftPct) {
                        e.preventDefault();
                        applyPct(next);
                    }
                });
            }

            if (mq.matches) bindSplitDrag();
            mq.addEventListener('change', (ev) => {
                if (ev.matches) {
                    applyPct(leftPct);
                    bindSplitDrag();
                }
            });
        }

        // Initialize app
        document.addEventListener('DOMContentLoaded', async function() {
            loadSettings();
            await loadDefaultPrompts();
            await loadProfileNoteTypes();
            await loadNoteTypePrefsAtBoot();
            await syncProfileSpecialtyToBar();
            if (localStorage.getItem('clinicalNotePrompts')) {
                localStorage.removeItem('clinicalNotePrompts');
                showToast('Update', 'Prompt templates refreshed. Add custom instructions from Customize.', 'info');
            }

            initSpeechRecognition();
            syncEncounterRecordingStatus();
            if (window.AsrSettingsUi && typeof window.AsrSettingsUi.initAsrSettingsUi === 'function') {
                window.AsrSettingsUi.initAsrSettingsUi().catch(function () {});
            }
            initDragAndDrop();
            // Workspace (server) is source of truth when signed in.
            // Avoid resurrecting old cases from localStorage when auth is present.
            if (typeof getAuthToken === 'function' && getAuthToken()) {
                loadQueue();
            } else {
                loadFromStorage();
            }
            // Re-transcribe button reflects per-encounter state. Encounter switch and workspace
            // reload both fire encounters-refresh; reuse it to drop stale in-memory recordings
            // and re-evaluate whether the saved server recording exists for the new encounter.
            window.addEventListener('encounters-refresh', () => {
                // Clear stale RAG / order-request polls so they do not keep hitting
                // the old generation ID after the user switches encounters.
                if (window.ragPollInterval) {
                    clearInterval(window.ragPollInterval);
                    window.ragPollInterval = null;
                }
                if (window.orderRequestsPollInterval) {
                    clearInterval(window.orderRequestsPollInterval);
                    window.orderRequestsPollInterval = null;
                }
                // Clear evidence quality banner — it persists otherwise
                const card = document.getElementById('consultCommentCard');
                if (card) {
                    const banner = card.querySelector('.ev-quality-banner');
                    if (banner) banner.remove();
                }
                try {
                    if (typeof window._refreshRetranscribeUi === 'function') {
                        window._refreshRetranscribeUi();
                    }
                } catch (_) {}
                // (a) Best-effort init refresh; a DOM hiccup here just defers the panel
                // repair to the next explicit retranscribe-refresh call.
                try {
                    if (app && app.activeEncounterId) {
                        fetchAsrSegmentsForEncounter(String(app.activeEncounterId)).catch(() => {});
                    }
                } catch (_) {}
            });
            ensureRagState();
            ensureOrderRequestsState();
            // Best-effort: recover any completed recordings saved locally (crash/phone sleep) and queue them.
            recoverLocalRecordingsToServer();
            // (a) Best-effort init fetch of ASR segments for the open encounter; the
            // inner .catch already keeps it fire-and-forget, outer guard is defensive.
            try {
                if (app && app.activeEncounterId) {
                    fetchAsrSegmentsForEncounter(String(app.activeEncounterId)).catch(() => {});
                }
            } catch (_) {}

            // Auto-poll queued transcribe jobs so they get processed transparently.
            // Recursive setTimeout with exponential backoff: starts 60s, max 10 min, resets on empty.
            let _queuePollDelayMs = 60000;
            const _scheduleQueuePoll = () => {
                setTimeout(async () => {
                    const hasAuto = Array.isArray(app.requestQueue) && app.requestQueue.some(
                        (r) => isQueueJobAutoProcessable(r) && queueJobMatchesActiveEncounter(r)
                    );
                    if (!hasAuto) {
                        _queuePollDelayMs = 60000;
                        _scheduleQueuePoll();
                        return;
                    }
                    // (a) Best-effort background queue poll: a transient processing error
                    // must not kill the recursive poll loop that drains the queue.
                    try { await processQueue(); } catch (_) {}
                    const stillHasJobs = Array.isArray(app.requestQueue) && app.requestQueue.some(
                        (r) => isQueueJobAutoProcessable(r) && queueJobMatchesActiveEncounter(r)
                    );
                    _queuePollDelayMs = stillHasJobs ? Math.min(_queuePollDelayMs * 2, 600000) : 60000;
                    _scheduleQueuePoll();
                }, _queuePollDelayMs);
            };
            _scheduleQueuePoll();

            initPriorVisitsCollapsible();
            initNoteFormatToolbar();
            initGeneratedNoteEditToggle();
            if (typeof syncEncounterInstructionsToolbar === 'function') syncEncounterInstructionsToolbar();
            try {
                const stickyTop = document.querySelector('#noteCard .note-card-sticky-top');
                if (stickyTop && typeof ResizeObserver !== 'undefined') {
                    const ro = new ResizeObserver(() => syncGeneratedNoteChromeStickyTop());
                    ro.observe(stickyTop);
                }
            } catch (_) {}
            // (a) Best-effort ResizeObserver wiring for the sticky note chrome: if the
            // element or observer isn't available, the chrome just stays non-sticky.
            let _chromeStickyResizeTimer = null;
            window.addEventListener('resize', () => {
                if (_chromeStickyResizeTimer) clearTimeout(_chromeStickyResizeTimer);
                _chromeStickyResizeTimer = setTimeout(syncGeneratedNoteChromeStickyTop, 120);
            });

            const consultBtn = document.getElementById('openConsultCommentBtn');
            if (consultBtn) {
                consultBtn.addEventListener('click', () => handleEvidenceBasedCommentsClick());
            }

            const orderBtn = document.getElementById('openOrderRequestsBtn');
            if (orderBtn) {
                orderBtn.addEventListener('click', () => handleOrderRequestsClick());
            }

            const patientMaterialsBtn = document.getElementById('openPatientMaterialsBtn');
            if (patientMaterialsBtn) {
                patientMaterialsBtn.addEventListener('click', () => handlePatientMaterialsClick());
            }

            window.addEventListener('message', (event) => {
                if (event.origin !== window.location.origin) return;
                const d = event.data;
                if (!d || d.type !== 'DREAMCISION_PROMPT_TEMPLATE') return;
                const title = typeof d.title === 'string' ? d.title : '';
                const promptText = typeof d.prompt === 'string' ? d.prompt : '';
                applyPromptTemplateFromPicker(title, promptText);
            });

            document.getElementById('generatedNote').addEventListener('input', () => {
                markNoteDirty();
                updateGeneratedNoteEmptyState();
            });

            const noteTypeEl = document.getElementById('noteType');
            const noteTypeMirrorEl = document.getElementById('noteTypeMirror');
            function syncNoteType(value, source) {
                if (noteTypeEl && source !== 'main') noteTypeEl.value = value;
                if (noteTypeMirrorEl && source !== 'mirror') noteTypeMirrorEl.value = value;
                // Note type can change generate-while-recording eligibility — refresh button state.
                if (window.CNGAudioUI && typeof window.CNGAudioUI.syncGenerateButtonsForPipeline === 'function') {
                    window.CNGAudioUI.syncGenerateButtonsForPipeline(
                        (window.CNGGenerateUI && typeof window.CNGGenerateUI.isAudioCaptureActive === 'function'
                            && window.CNGGenerateUI.isAudioCaptureActive()) ? 'recording' : 'idle'
                    );
                }
                const promptSelect = document.getElementById('noteTypeSelect');
                if (promptSelect && promptSelect.value !== value) {
                    promptSelect.value = value;
                    const promptModal = document.getElementById('promptModal');
                    if (promptModal && promptModal.classList.contains('show')) {
                        updatePromptDisplay();
                    }
                }
            }
            if (noteTypeEl) {
                noteTypeEl.addEventListener('change', () => syncNoteType(noteTypeEl.value, 'main'));
            }
            if (noteTypeMirrorEl) {
                noteTypeMirrorEl.addEventListener('change', () => syncNoteType(noteTypeMirrorEl.value, 'mirror'));
            }
            if (noteTypeEl && noteTypeMirrorEl) {
                noteTypeMirrorEl.value = noteTypeEl.value;
            }

            // Sanitize text when user copies from the generated note textarea
            // This ensures clipboard content is EMR-compatible even if Unicode slips through
            document.getElementById('generatedNote').addEventListener('copy', (event) => {
                const textarea = event.target;
                if (textarea.selectionStart !== textarea.selectionEnd) {
                    // User is copying selected text
                    const selectedText = textarea.value.substring(
                        textarea.selectionStart,
                        textarea.selectionEnd
                    );
                    const sanitized = finalizeNoteText(selectedText);

                    // Put sanitized text in clipboard instead of original
                    event.clipboardData.setData('text/plain', sanitized);
                    event.preventDefault();

                    debugLog('[Copy] Sanitized clipboard text for EMR compatibility');
                }
            });

            const generatedNoteViewEl = document.getElementById('generatedNoteView');
            if (generatedNoteViewEl) {
                generatedNoteViewEl.addEventListener('copy', (event) => {
                    const sel = window.getSelection();
                    if (!sel || sel.rangeCount === 0 || sel.isCollapsed) return;
                    const anchorNode = sel.anchorNode;
                    if (!anchorNode || !generatedNoteViewEl.contains(anchorNode)) return;
                    const selectedText = sel.toString();
                    if (!selectedText) return;
                    const sanitized = finalizeNoteText(selectedText);
                    event.clipboardData.setData('text/plain', sanitized);
                    event.preventDefault();
                    debugLog('[Copy] Sanitized clipboard text from formatted view for EMR compatibility');
                });
            }

            const initial = currentNoteText();
            app.noteState.lastSavedHash = initial ? simpleHash(initial) : null;
            updateGeneratedNoteEmptyState();

            // Initialize V7 character counters for all 3 input fields
            initCharacterCounters();
            initWorkspaceSplit();

            registerUnloadGuards();

            if (localStorage.getItem('clinicalNoteUnsavedExit') === '1') {
                showToast('Unsaved note preserved', 'Your text is still here. Tap Save to keep a copy to Files.', 'warning');
                localStorage.removeItem('clinicalNoteUnsavedExit');
            }

            setInterval(checkConnection, 30000);
        });

        setInterval(() => {
            if (currentNoteText()) saveToStorage();
        }, 10000);
