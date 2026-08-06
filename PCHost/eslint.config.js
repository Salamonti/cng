// ESLint flat config for the PCHost browser app.
//
// Purpose: `no-undef`. Four production bugs in one day were all in error-handling
// paths, and two of them were an undeclared identifier inside a branch that only
// runs on failure -- so the code responsible for DISPLAYING an error was the code
// that crashed. Those branches are almost never exercised in manual testing, so a
// linter is the only thing that reliably catches them.
//
// The scripts here are classic non-module browser scripts loaded via <script> tags
// in index.html; they share one global scope and call across files freely. ESLint
// analyses each file in isolation, so every legitimate cross-file call looks
// undefined. The APP_GLOBALS list below re-states that implicit contract.
//
// Each entry was verified to have a real top-level declaration or an explicit
// global assignment -- it is NOT a blanket suppression list. Adding a name here
// without checking would re-hide exactly the bug class this config exists to
// catch. If lint fails on a new name, confirm it is genuinely defined and loaded
// before adding it; the friction is deliberate, because every entry is a piece of
// undeclared coupling between files.
const globals = require("globals");

// name -> file that defines it (kept as a comment trail so the next reader can
// verify rather than trust this list).
const APP_GLOBALS = [
  // js/workspace_app.js (top-level declarations; the file has no IIFE wrapper)
  "apiFetch",
  "app",
  "applySavedSidebarRailPreference",
  "checkConnection",
  "clearAll",
  "debugLog",
  "getApiBase",
  "getAuthToken",
  "loadDefaultPrompts",
  "loadProfileNoteTypes",
  "loadQueue",
  "loadSettings",
  "loadWorkspaceState",
  "processDocuments",
  "saveToStorage",
  "setChartDataValue",
  "setEvidenceButtonVisible",
  "setFieldValue",
  "setOrderRequestsButtonVisible",
  "setPatientMaterialsButtonVisible",
  "showToast",
  "syncProfileSpecialtyToBar",
  "syncQueueConnectionPill",
  "updateConnectionStatus",
  "updateQueueDisplay",
  "closeOrderRequestsModal",
  // js/workspace_ui_state.js
  "defaultOrderRequestsState",
  "defaultRagState",
  "ensureOrderRequestsState",
  "ensureRagState",
  "persistOrderRequestsState",
  "persistRagState",
  "updateUiState",
  // js/workspace_file_camera.js
  "closeCamera",
  "getFieldDisplayName",
  "initDragAndDrop",
  // js/api_error_format.js
  "formatApiErrorMessage",
  "summarizeServiceDetailForUser",
  // universal_audio_handler.js
  "initUniversalAudio",
  // js/settings_connection.js
  "writeSettings",
  // literature_ui.js
  "refreshLiteratureList",
];

const appGlobals = Object.fromEntries(APP_GLOBALS.map((n) => [n, "writable"]));

module.exports = [
  {
    // Never lint dependencies or build output.
    ignores: ["node_modules/**", "web/**/*.min.js", "**/dist/**"],
  },
  {
    // The browser app.
    files: ["web/*.js", "web/js/**/*.js"],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "script",
      globals: { ...globals.browser, ...appGlobals },
    },
    rules: {
      "no-undef": "error",
    },
  },
  {
    // Files that double as CommonJS modules for the node test suite. Each guards
    // the export with `typeof module !== 'undefined'`, so the reference is safe
    // in a browser; the globals are declared to keep that footer lint-clean.
    files: [
      "web/js/api_error_format.js",
      "web/js/recording_durability.js",
      "web/js/recording_recovery.js",
      "web/universal_audio_handler.js",
    ],
    languageOptions: {
      globals: { module: "readonly", exports: "writable", require: "readonly" },
    },
  },
  {
    // The service worker has its own global scope (no window/document).
    files: ["web/service_worker.js"],
    languageOptions: {
      globals: { ...globals.serviceworker },
    },
  },
  {
    // Node-side, CommonJS: the server and the .cjs test files.
    files: ["server.js", "web/js/**/*.test.cjs"],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "commonjs",
      globals: { ...globals.node },
    },
    rules: {
      "no-undef": "error",
    },
  },
  {
    // Node-side, ES modules: the .mjs test files.
    files: ["tests/**/*.mjs"],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "module",
      globals: { ...globals.node },
    },
    rules: {
      "no-undef": "error",
    },
  },
];
