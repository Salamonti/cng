/**
 * Pure helpers for deciding whether a recording has any durable copy after Stop:
 * verified server segment upload or successful segment transcription.
 * Loaded before workspace_app.js; exposed as window.RecordingDurability.
 */
(function (global) {
    /**
     * @param {object} o
     * @param {boolean} [o.persistLayerOk] — verified server segment upload succeeded
     * @param {boolean} [o.transcribeOk] — server segment transcription succeeded
     */
    function computeRecordingDurable(o) {
        if (!o || typeof o !== 'object') return false;
        if (o.persistLayerOk === true) return true;
        if (o.transcribeOk === true) return true;
        return false;
    }

    const api = { computeRecordingDurable };
    try {
        global.RecordingDurability = api;
    } catch (e) {}
    if (typeof module !== 'undefined' && module.exports) {
        module.exports = api;
    }
})(typeof globalThis !== 'undefined' ? globalThis : this);
