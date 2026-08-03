/* P10b — RAG / order-request UI state sync with AuthWorkspace (after workspace_app defines window.app). */
(function () {
    'use strict';

    function defaultRagState() {
        return {
            status: 'idle',
            content: null,
            generationId: null,
            hasGenerated: false,
            lastUpdated: null
        };
    }

    function defaultOrderRequestsState() {
        return {
            status: 'idle',
            items: [],
            generationId: null,
            hasGenerated: false,
            lastUpdated: null,
            lastError: null
        };
    }

    function ensureRagState() {
        if (!window.ragState || typeof window.ragState !== 'object') {
            window.ragState = defaultRagState();
        }
        return window.ragState;
    }

    function ensureOrderRequestsState() {
        if (!window.orderRequestsState || typeof window.orderRequestsState !== 'object') {
            window.orderRequestsState = defaultOrderRequestsState();
        }
        return window.orderRequestsState;
    }

    function persistRagState() {
        const st = ensureRagState();
        updateUiState({
            ragHasGenerated: !!st.hasGenerated,
            ragContent: st.content || null,
            ragLastUpdated: st.lastUpdated || null
        });
    }

    function persistOrderRequestsState() {
        const st = ensureOrderRequestsState();
        updateUiState({
            orderHasGenerated: !!st.hasGenerated,
            orderItems: Array.isArray(st.items) ? st.items : [],
            orderLastUpdated: st.lastUpdated || null
        });
    }

    function updateUiState(patch) {
        if (!window.app) return;
        if (!window.app.uiState) window.app.uiState = {};
        Object.assign(window.app.uiState, patch || {});
        if (window.AuthWorkspace && typeof window.AuthWorkspace.queueSave === 'function') {
            window.AuthWorkspace.queueSave();
        }
    }

    function applyUiStateFromWorkspace(ui) {
        if (!window.app) return;
        const next = (ui && typeof ui === 'object') ? ui : {};
        window.app.uiState = { ...(window.app.uiState || {}), ...next };
        {
            const lg = window.app.uiState.lastGenerationId;
            window.lastGenerationId = (lg === undefined || lg === null || lg === '') ? null : lg;
        }
        const ragContent = (window.app.uiState.ragContent && typeof window.app.uiState.ragContent === 'object')
            ? window.app.uiState.ragContent
            : null;
        const orderItems = Array.isArray(window.app.uiState.orderItems) ? window.app.uiState.orderItems : [];
        window.ragState = {
            status: window.app.uiState.ragHasGenerated ? 'ready' : 'idle',
            content: ragContent,
            generationId: window.app.uiState.lastGenerationId || window.lastGenerationId || null,
            hasGenerated: !!window.app.uiState.ragHasGenerated,
            lastUpdated: window.app.uiState.ragLastUpdated || null
        };
        window.orderRequestsState = {
            status: window.app.uiState.orderHasGenerated ? 'ready' : 'idle',
            items: orderItems,
            generationId: window.app.uiState.lastGenerationId || window.lastGenerationId || null,
            hasGenerated: !!window.app.uiState.orderHasGenerated,
            lastUpdated: window.app.uiState.orderLastUpdated || null,
            lastError: null
        };
        if (typeof setOrderRequestsButtonVisible === 'function') {
            setOrderRequestsButtonVisible(!!window.app.uiState.showOrderRequestsButton, false);
        }
        // Patient Materials button is always visible like Order & Evidence buttons
        if (typeof setPatientMaterialsButtonVisible === 'function') {
            setPatientMaterialsButtonVisible(true, false);
        }
        if (typeof setEvidenceButtonVisible === 'function') {
            setEvidenceButtonVisible(!!window.app.uiState.showEvidenceButton, false);
        }
    }

    window.defaultRagState = defaultRagState;
    window.defaultOrderRequestsState = defaultOrderRequestsState;
    window.ensureRagState = ensureRagState;
    window.ensureOrderRequestsState = ensureOrderRequestsState;
    window.persistRagState = persistRagState;
    window.persistOrderRequestsState = persistOrderRequestsState;
    window.updateUiState = updateUiState;
    window.applyUiStateFromWorkspace = applyUiStateFromWorkspace;
})();
