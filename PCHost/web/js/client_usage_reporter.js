// js/client_usage_reporter.js
// P4-4: pilot instrumentation -- feature usage + note edit-distance.
//
// Two signals nothing in the app currently captures:
// 1. Feature usage: which parts of the app doctors actually touch (Tools
//    sheet items, patient-material categories, bottom-nav targets).
// 2. Note edit distance: how much a doctor edits the generated note
//    before it's done with, a proxy for note quality. Computed HERE, in
//    the browser, so raw note text -- before or after edits -- never
//    leaves the client for this metric; only a 0-1 ratio and the two
//    text lengths are sent.
//
// POSTs to /api/client_usage, same open-registration + rate-limit shape
// as client_error_reporter.js's /api/client_errors.
(function () {
    'use strict';

    function apiBase() {
        try {
            if (window.getApiBase && window.app && window.app.settings) {
                return window.getApiBase(window.app.settings.serverUrl || '/api');
            }
        } catch (_) {}
        return '/api';
    }

    function authToken() {
        try {
            if (typeof window.getAuthToken === 'function') return window.getAuthToken();
        } catch (_) {}
        return null;
    }

    function send(events, useBeacon) {
        try {
            const body = JSON.stringify({ events: events });
            const url = apiBase() + '/client_usage';
            if (useBeacon && navigator.sendBeacon) {
                // Page-unload path: fetch (even with keepalive) can be dropped
                // by the browser mid-navigation; sendBeacon is built for
                // exactly this "fire this before the page goes away" case.
                // It only supports text/blob bodies with no custom headers,
                // so the auth token can't ride along here -- the route
                // doesn't require one anyway (same as client_errors).
                navigator.sendBeacon(url, new Blob([body], { type: 'application/json' }));
                return;
            }
            const headers = { 'Content-Type': 'application/json' };
            const token = authToken();
            if (token) headers['Authorization'] = 'Bearer ' + token;
            fetch(url, { method: 'POST', headers: headers, body: body, keepalive: true }).catch(function () {});
        } catch (_) {
            // Telemetry must never itself throw.
        }
    }

    function reportEvent(kind, opts) {
        opts = opts || {};
        send([{ kind: String(kind || ''), value: opts.value, meta: opts.meta, case_id: opts.caseId }], !!opts.beacon);
    }

    // Iterative Levenshtein distance with O(min(n,m)) space -- fine for a
    // one-time, save-time computation on note-length text (a few thousand
    // chars), not something run per-keystroke.
    function editDistanceRatio(a, b) {
        a = a || '';
        b = b || '';
        if (a === b) return 1;
        if (!a.length || !b.length) return 0;
        if (a.length > b.length) { const t = a; a = b; b = t; }
        let prev = new Array(a.length + 1);
        for (let i = 0; i <= a.length; i++) prev[i] = i;
        for (let j = 1; j <= b.length; j++) {
            const cur = new Array(a.length + 1);
            cur[0] = j;
            for (let i = 1; i <= a.length; i++) {
                const cost = a[i - 1] === b[j - 1] ? 0 : 1;
                cur[i] = Math.min(prev[i] + 1, cur[i - 1] + 1, prev[i - 1] + cost);
            }
            prev = cur;
        }
        const distance = prev[a.length];
        return 1 - distance / Math.max(a.length, b.length);
    }

    const reportedGenerationIds = new Set();

    function reportEditDistance(generationId, beforeText, afterText, opts) {
        try {
            if (!generationId || reportedGenerationIds.has(generationId)) return;
            if (!beforeText || !afterText || beforeText === afterText) {
                // Nothing edited -- still worth one ratio=1 sample so the
                // aggregate isn't skewed toward only-edited notes, but only
                // once per generation.
                if (beforeText && afterText) {
                    reportedGenerationIds.add(generationId);
                    reportEvent('note_edit_distance', { value: 1, caseId: generationId, beacon: (opts || {}).beacon });
                }
                return;
            }
            reportedGenerationIds.add(generationId);
            const ratio = editDistanceRatio(beforeText, afterText);
            reportEvent('note_edit_distance', { value: ratio, caseId: generationId, beacon: (opts || {}).beacon });
        } catch (_) {}
    }

    window.DreamCisionUsage = {
        reportEvent: reportEvent,
        reportEditDistance: reportEditDistance,
        editDistanceRatio: editDistanceRatio,
    };

    // Delegated click tracking for the fixed vocabulary of tagged elements
    // (data-usage on Tools-sheet items, data-target on bottom-nav items,
    // data-category on patient-material cards) -- one listener instead of
    // wiring a call into every feature's own onclick handler.
    document.addEventListener('click', function (e) {
        const usageEl = e.target.closest('[data-usage]');
        if (usageEl) {
            reportEvent(usageEl.getAttribute('data-usage'));
            return;
        }
        const navEl = e.target.closest('.mobile-bottom-nav .nav-item[data-target]');
        if (navEl) {
            reportEvent('nav_' + navEl.getAttribute('data-target'));
            return;
        }
        const pmEl = e.target.closest('.pm-category-card[data-category]');
        if (pmEl) {
            reportEvent('patient_materials_generate', { meta: pmEl.getAttribute('data-category') });
        }
    });
})();
