// js/client_error_reporter.js
// P2-8: browser JS errors and unhandled promise rejections are otherwise
// invisible -- a doctor hitting a broken button today leaves no trace
// anywhere anyone would notice. POSTs to /api/client_errors (no auth
// required there on purpose -- an expired session is exactly one of the
// things worth reporting). Loaded first, before every other script, so it
// can catch errors thrown while those scripts themselves initialize.
(function () {
    'use strict';

    var MAX_REPORTS_PER_LOAD = 20;
    var DEDUPE_WINDOW_MS = 10000;
    var reportCount = 0;
    var recentFingerprints = new Map(); // fingerprint -> last-sent timestamp

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

    function fingerprint(message, stack) {
        return (message || '') + '|' + (stack || '').slice(0, 200);
    }

    function report(kind, message, stack) {
        try {
            if (reportCount >= MAX_REPORTS_PER_LOAD) return;
            var fp = fingerprint(message, stack);
            var now = Date.now();
            var last = recentFingerprints.get(fp);
            if (last && now - last < DEDUPE_WINDOW_MS) return;
            recentFingerprints.set(fp, now);
            reportCount++;

            var body = JSON.stringify({
                kind: String(kind || 'error').slice(0, 32),
                message: String(message || '').slice(0, 2000),
                stack: String(stack || '').slice(0, 4000),
                url: String((window.location && window.location.href) || '').slice(0, 500),
            });

            var headers = { 'Content-Type': 'application/json' };
            var token = authToken();
            if (token) headers['Authorization'] = 'Bearer ' + token;

            // Best-effort, fire-and-forget: a failed error report must never
            // itself throw (that would re-trigger this same handler).
            fetch(apiBase() + '/client_errors', {
                method: 'POST',
                headers: headers,
                body: body,
                keepalive: true,
            }).catch(function () {});
        } catch (_) {
            // Swallow -- this handler exists to catch problems, not cause them.
        }
    }

    // Explicit reporting path for errors that are CAUGHT.
    //
    // The two listeners below only ever see errors that escape to the top --
    // that is what those events mean. This app catches almost everything: 175
    // of its 331 catch blocks swallow the error entirely. So the listeners were
    // installed correctly, POSTed correctly, and the backend recorded correctly,
    // and still not one real browser error had been captured -- the only
    // client_error records in a 10,279-record incident store were an auditor's
    // own probes.
    //
    // Both of the UI bugs found today were caught-and-displayed, never uncaught,
    // so neither would have produced a report no matter how well this file
    // worked. A catch block that has decided something went wrong should say so
    // here rather than rely on the error escaping, which it never will.
    //
    // Deliberately tolerant of its arguments: a reporting call must never be the
    // thing that throws inside a catch block.
    window.reportClientError = function (message, stack, kind) {
        try {
            report(kind || 'caught', message, stack);
        } catch (_) {}
    };

    window.addEventListener('error', function (event) {
        if (!event) return;
        var err = event.error;
        report(
            'error',
            (err && err.message) || event.message || 'Unknown error',
            err && err.stack
        );
    });

    window.addEventListener('unhandledrejection', function (event) {
        if (!event) return;
        var reason = event.reason;
        var message = (reason && (reason.message || String(reason))) || 'Unhandled promise rejection';
        var stack = reason && reason.stack;
        report('unhandledrejection', message, stack);
    });
})();
