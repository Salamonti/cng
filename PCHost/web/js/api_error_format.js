/**
 * Convert FastAPI / fetch error bodies into short, user-facing strings.
 * Loaded before workspace_app.js and auth_workspace.js as window.formatApiErrorMessage / summarizeServiceDetailForUser.
 */
(function (global) {
    const DEFAULT_FALLBACK = 'Something went wrong. Please try again.';

    function looksLikeJson(s) {
        const t = String(s || '').trim();
        return (t.startsWith('{') && t.endsWith('}')) || (t.startsWith('[') && t.endsWith(']'));
    }

    function polishPlainError(s, httpStatus, fallback) {
        let t = String(s || '').trim();
        if (!t) return fallback;
        const lower = t.toLowerCase();

        if (lower.includes('invalid credentials')) return 'Incorrect email or password.';
        if (lower.includes('awaiting approval')) return 'Your account is pending approval.';
        if (lower.includes('email already registered')) return 'This email is already registered.';
        if (
            lower.includes('password must include') ||
            (lower.includes('value error') && lower.includes('password'))
        ) {
            return 'Password must be at least 12 characters and include upper and lower case letters, a number, and a symbol.';
        }
        if (/ensure this value has at least/i.test(t) && /12/i.test(t)) {
            return 'Password must be at least 12 characters and include upper and lower case letters, a number, and a symbol.';
        }
        if (/string should have at least/i.test(lower) && /12/i.test(t)) {
            return 'Password must be at least 12 characters and include upper and lower case letters, a number, and a symbol.';
        }

        if (httpStatus === 401 && t.length < 200) return 'Incorrect email or password.';
        if (httpStatus === 403 && t.length < 200 && !lower.includes('{')) return t;
        if (httpStatus === 404) return 'The requested item was not found.';
        if (httpStatus === 422) return 'Please check your input and try again.';
        if (httpStatus >= 500) return 'The server had a problem. Please try again later.';

        if (t.startsWith('{') || t.startsWith('[')) return fallback;
        if (t.length > 280) return fallback;
        return t;
    }

    function formatFromDetailObject(obj, opts) {
        const fallback = opts.fallback || DEFAULT_FALLBACK;
        const httpStatus = opts.httpStatus;
        if (!obj || typeof obj !== 'object') return fallback;

        let d = obj.detail !== undefined ? obj.detail : obj;

        if (typeof d === 'string') {
            return polishPlainError(d, httpStatus, fallback);
        }

        if (Array.isArray(d)) {
            const parts = [];
            for (const item of d) {
                if (!item) continue;
                if (typeof item === 'string') {
                    parts.push(polishPlainError(item, httpStatus, fallback));
                    continue;
                }
                if (typeof item === 'object') {
                    const msg =
                        item.msg != null
                            ? String(item.msg)
                            : item.message != null
                              ? String(item.message)
                              : '';
                    if (msg) parts.push(polishPlainError(msg, httpStatus, fallback));
                }
            }
            const joined = parts.filter(Boolean).join(' ');
            return joined || fallback;
        }

        if (d && typeof d === 'object') {
            if (typeof d.msg === 'string') return polishPlainError(d.msg, httpStatus, fallback);
            if (typeof d.message === 'string') return polishPlainError(d.message, httpStatus, fallback);
            if (Array.isArray(d.errors) && d.errors.length) {
                return formatFromDetailObject({ detail: d.errors }, opts);
            }
        }

        return fallback;
    }

    function formatApiErrorMessage(raw, options) {
        const opts = options || {};
        const fallback = opts.fallback || DEFAULT_FALLBACK;
        const httpStatus = opts.httpStatus;

        if (raw == null || raw === '') {
            if (httpStatus === 401) return 'Incorrect email or password.';
            if (httpStatus === 403) return 'You do not have permission to do this.';
            if (httpStatus === 404) return 'The requested item was not found.';
            if (httpStatus === 422) return 'Please check your input and try again.';
            if (httpStatus >= 500) return 'The server had a problem. Please try again later.';
            return fallback;
        }

        if (typeof raw === 'object') {
            return formatFromDetailObject(raw, opts);
        }

        let s = String(raw).trim();
        if (!s) return fallback;

        if (looksLikeJson(s)) {
            try {
                return formatFromDetailObject(JSON.parse(s), opts);
            } catch (e) {
                /* fall through */
            }
        }

        return polishPlainError(s, httpStatus, fallback);
    }

    /** Structured LLM/service errors from showServiceErrorBanner callers. */
    function summarizeServiceDetailForUser(detail) {
        if (detail == null || detail === '') return 'Please try again.';
        if (typeof detail === 'string') return formatApiErrorMessage(detail);

        if (typeof detail !== 'object') return DEFAULT_FALLBACK;

        const svcRaw = detail.service || detail.detail?.service;
        const svc = svcRaw ? String(svcRaw).replace(/_/g, ' ') : '';
        const errors = detail.errors || detail.detail?.errors;

        if (Array.isArray(errors) && errors.length) {
            const parts = errors
                .map((e) => {
                    if (typeof e === 'string') return formatApiErrorMessage(e);
                    if (e && typeof e === 'object') return formatApiErrorMessage(e);
                    return '';
                })
                .filter(Boolean);
            const head = svc ? svc + ': ' : '';
            return parts.length ? head + parts.join(' · ') : DEFAULT_FALLBACK;
        }

        const prim = detail.primary || detail.detail?.primary;
        if (prim) return formatApiErrorMessage(String(prim));

        return formatApiErrorMessage(detail);
    }

    global.formatApiErrorMessage = formatApiErrorMessage;
    global.summarizeServiceDetailForUser = summarizeServiceDetailForUser;

    try {
        if (typeof module !== 'undefined' && module.exports) {
            module.exports = { formatApiErrorMessage, summarizeServiceDetailForUser };
        }
    } catch (e) {
        // (a) Best-effort CommonJS export; fails only in odd bundler/host contexts
        // where the browser-global assignment above has already made it available.
    }
})(typeof globalThis !== 'undefined' ? globalThis : this);
