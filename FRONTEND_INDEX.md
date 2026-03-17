# CNG Frontend & PCHost Proxy — Complete Index

**Generated:** 2026-03-17  
**Source path:** `/mnt/c/project-root/PCHost/`

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Data Flow Diagram](#data-flow-diagram)
3. [Server Files](#server-files)
   - [server.js](#serverjs)
   - [llama-gateway.js](#llama-gatewayjs)
   - [openwebui-proxy.js](#openwebui-proxyjs)
   - [package.json](#packagejson)
   - [config/server_config.json](#configserver_configjson)
   - [config/server_config.linux.json](#configserver_configlinuxjson)
   - [New_Main_Server.bat](#new_main_serverbat)
   - [start-openwebui-proxy.bat](#start-openwebui-proxybat)
4. [Web Files](#web-files)
   - [index.html](#indexhtml)
   - [scripts.js](#scriptsjs)
   - [styles.css](#stylescss)
   - [auth_workspace.js](#auth_workspacejs)
   - [audio_ui_utils.js](#audio_ui_utilsjs)
   - [generate_ui_flow.js](#generate_ui_flowjs)
   - [markdown_renderer.js](#markdown_rendererjs)
   - [universal_audio_handler.js](#universal_audio_handlerjs)
   - [service_worker.js](#service_workerjs)
   - [manifest.json](#manifestjson)
   - [admin.html](#adminhtml)
   - [ocr.html](#ocrhtml)
   - [qa.html](#qahtml)
   - [auth_debug.html](#auth_debughtml)
5. [Routes & Endpoints](#routes--endpoints)
6. [Dead / Commented-Out Code](#dead--commented-out-code)
7. [Unused / Legacy Files](#unused--legacy-files)

---

## Architecture Overview

```
Internet / LAN
      │
      ▼
PCHost (Node.js - server.js)
  HTTP :3000  ──redirect──▶  HTTPS :3443
      │
      ├─── /api/*         ──proxy──▶  FastAPI  (localhost:7860)
      ├─── /admin/*       ──proxy──▶  FastAPI  (localhost:7860)
      ├─── /whisperx      ──proxy──▶  FastAPI  (localhost:7860) → /api/transcribe_diarized
      ├─── /ocr           ──proxy──▶  FastAPI  (localhost:7860) → /api/ocr
      ├─── /llama/generate──proxy──▶  Llama Gateway (localhost:7871) → /api/generate
      ├─── /llama/check   ──proxy──▶  Llama Gateway (localhost:7871) → /api/check
      ├─── /health        ──local──▶  node health check (200 OK)
      ├─── /fastapi-check ──local──▶  direct HTTP probe of FastAPI /api/health
      ├─── /              ──static─▶  web/index.html
      ├─── /qa            ──static─▶  web/qa.html
      └─── /*             ──static─▶  web/ directory (SPA fallback → index.html)

Llama Gateway (llama-gateway.js)
  :7871  ──proxy──▶  llama-server  (localhost:8081 - WSL llama-server)

OpenWebUI Proxy (openwebui-proxy.js)  [SEPARATE PROCESS]
  HTTP :8013  ──redirect──▶  HTTPS :8443
  HTTPS :8443 ──proxy──▶  Open WebUI Docker (localhost:8035)
```

---

## Data Flow Diagram

### Note Generation Request
```
Browser
  → POST /api/generate_v8_stream  (+ Authorization: Bearer <token>)
  → server.js /api proxy
  → pathRewrite: prepend /api (Express strips prefix)
  → FastAPI localhost:7860 /api/generate_v8_stream
  → Streams LLM tokens back to browser via SSE/chunked transfer
```

### Audio Transcription
```
Browser
  → POST /api/transcribe_diarized  (FormData: audio file + Authorization)
  → server.js /api proxy  (OR shortcut: POST /whisperx → /api/transcribe_diarized)
  → FastAPI localhost:7860 /api/transcribe_diarized
  → Returns speaker-diarized transcript text
```

### OCR
```
Browser
  → POST /api/ocr  (FormData: image/pdf + Authorization)
  → server.js /api proxy  (OR shortcut: POST /ocr → /api/ocr)
  → FastAPI localhost:7860 /api/ocr
  → Returns JSON: { text, confidence, engine_used, processing_time }
```

### Workspace Sync
```
Browser (auth_workspace.js)
  → GET/PUT /api/workspace/  (Authorization: Bearer <token>)
  → server.js /api proxy
  → FastAPI localhost:7860 /api/workspace/
```

### Llama Direct (bypass FastAPI)
```
Browser or Internal
  → POST /llama/generate
  → server.js rewrites to /api/generate
  → llama-gateway.js :7871 → llama-server :8081 (WSL)
```

---

## Server Files

---

### `server.js`

**Purpose:** Main Node.js HTTPS reverse-proxy that serves static web files and proxies all API, admin, transcription, OCR, and Llama requests to backend services. Handles SSL, CORS, HTTP→HTTPS redirect, and health checks.

**Dependencies:** express, http-proxy-middleware, cors, https, http, fs, path

**Configuration loaded:** `./config/server_config.json`

**Backend targets:**
| Variable | Default | Description |
|---|---|---|
| `FASTAPI_URL` | `http://127.0.0.1:7860` | Python FastAPI backend |
| `GATEWAY_URL` | `http://127.0.0.1:7871` | Llama gateway process |
| `OPENWEBUI_URL` | `http://127.0.0.1:8035` | Open WebUI Docker (comment says moved to openwebui-proxy.js) |

**Key Middleware / Settings:**
- `app.set('trust proxy', true)` — trusts X-Forwarded-* headers
- `cors()` — allows origins: localhost:3000/3443, ieissa.com:3443, notes.ieissa.com:3443
- No-cache middleware for all dynamic responses
- SSL loaded from `config.ssl_cert_path` / `config.ssl_key_path` (Windows: `C:\certs\ieissa\`)
- HTTP→HTTPS redirect when SSL available
- `express.static()` serves `web/` directory

**Proxy configurations:**
```js
proxyCommon       → FastAPI :7860  (timeout 300s, followRedirects false)
llamaProxyCommon  → Gateway :7871  (timeout 300s)
openwebuiProxyCommon → OpenWebUI :8035  (ws:true, timeout 300s)  [defined but not used]
```

**Routes:**

| Route | Target | Path Rewrite | Notes |
|---|---|---|---|
| `GET /fastapi-check` | FastAPI /api/health | none (raw http.get) | Direct probe, bypasses proxy |
| `ANY /llama/generate` | Gateway :7871 | → `/api/generate` | Direct llama-server path |
| `ANY /llama/check` | Gateway :7871 | → `/api/check` | Llama health check |
| `ANY /whisperx` | FastAPI :7860 | → `/api/transcribe_diarized` | WhisperX shortcut |
| `ANY /ocr` | FastAPI :7860 | → `/api/ocr` | OCR shortcut |
| `ANY /api/*` | FastAPI :7860 | Strips then re-prepends `/api` | Main API namespace |
| `ANY /admin/*` | FastAPI :7860 | Strips then re-prepends `/admin` | Admin API namespace |
| `GET /health` | Local | — | Returns `{status:"OK", timestamp, fastapi_target}` |
| `GET /` | Static | — | Serves `web/index.html` |
| `GET /qa` | Static | — | Serves `web/qa.html` |
| `GET /*` | Static/SPA | — | Falls back to `web/index.html` for non-asset paths |

**Path rewrite note (CRITICAL):**  
Express strips the mount prefix before `pathRewrite` sees the path. So for `/api/auth/login`, `pathRewrite` receives `/auth/login` — the rewrite manually re-prepends `/api`. Same for `/admin`.

**Server startup:**
- `http.createServer(app).listen(HTTP_PORT, config.host)` — HTTP :3000
- `https.createServer(sslOptions, app).listen(HTTPS_PORT, config.host)` — HTTPS :3443 (if SSL loaded)

**Graceful shutdown:** Handles SIGTERM and SIGINT.

---

### `llama-gateway.js`

**Purpose:** Minimal 4-line Express proxy that forwards requests arriving at `:7871/api/*` to the WSL llama-server at `localhost:8081`. Acts as a network bridge between Windows-side Node.js and WSL-hosted llama-server.

**Functions:** None (just `app.use('/api', createProxyMiddleware({target:'http://localhost:8081'}))`).

**Listen:** `:7871` on `0.0.0.0`

**Usage:** `server.js` routes `/llama/generate` and `/llama/check` here.

---

### `openwebui-proxy.js`

**Purpose:** Dedicated HTTPS reverse proxy for Open WebUI Docker container, running independently on port `:8443` (HTTPS) and `:8013` (HTTP redirect). Enables WebSocket support for Open WebUI's real-time features.

**Key config:**
- HTTP_PORT: 8013 (redirect only)
- HTTPS_PORT: 8443
- Backend: `http://127.0.0.1:8035`
- SSL: `C:\certs\ieissa\privkey.pem` / `fullchain.pem` (hardcoded Windows path)

**Route:** `/*` → Open WebUI (all traffic including WebSockets, `ws: true`)

**Started by:** `start-openwebui-proxy.bat`  
**Note:** server.js has `openwebuiProxyCommon` defined but commented out with a note saying this moved to this dedicated file.

---

### `package.json`

**Purpose:** NPM package manifest. Defines the project as `home-pc-static-server v1.0.0`, entry point `server.js`.

**Scripts:**
- `start` / `dev` → `node server.js`
- `ssl-setup` → `node ssl-setup.js` (file not present in repo)
- `generate-ssl` → `node generate-ssl-cert.js` (file not present in repo)
- `check-ssl` → `node ssl-setup.js`

**Dependencies:**
- cors ^2.8.5
- express ^4.21.2
- fs ^0.0.1-security
- http-proxy-middleware ^3.0.5
- https ^1.0.0

---

### `config/server_config.json`

**Purpose:** Windows production configuration for server.js.

```json
{
  "host": "0.0.0.0",
  "https_port": 3443,
  "http_port": 3000,
  "backend_url": "http://127.0.0.1:7860",
  "backend_timeout": 300000,
  "ssl_cert_path": "C:\\certs\\ieissa\\fullchain.pem",
  "ssl_key_path": "C:\\certs\\ieissa\\privkey.pem",
  "web_directory": "web",
  "enable_gzip": true,
  "log_level": "INFO",
  "domain": "notes.ieissa.com",
  "custom_ports": { "http": 3000, "https": 3443, "reason": "Avoiding standard ports, port 80 reserved for QNAP NAS" }
}
```

**Note:** `enable_gzip` is defined but server.js does NOT implement gzip compression middleware. Dead config field.

---

### `config/server_config.linux.json`

**Purpose:** Linux/WSL variant of server config with different SSL cert paths.

**Differences from Windows config:**
- `ssl_cert_path`: `/etc/letsencrypt/live/ieissa/fullchain.pem`
- `ssl_key_path`: `/etc/letsencrypt/live/ieissa/privkey.pem`
- `reason`: "Linux WSL - non-privileged ports"

**Note:** server.js only loads `server_config.json` by name — this linux config is NOT auto-loaded. Must be manually swapped in or server.js modified to detect platform.

---

### `New_Main_Server.bat`

**Purpose:** Windows batch launcher for `server.js`. Used by NSSM (Non-Sucking Service Manager) for running as a Windows service and for manual startup. Performs 5-step pre-flight check.

**Steps:**
1. Verify Node.js installed and working
2. Verify `server.js`, `package.json`, `config\server_config.json`, `web\` directory exist
3. Run `npm install` if `node_modules` missing
4. Check ports 3443 and 3000 are not already in use (exits with error if they are, suggests running `Kill_Old_Node_Processes.bat`)
5. Execute `node server.js`

**Note:** References `Kill_Old_Node_Processes.bat` which is not in the repo.

---

### `start-openwebui-proxy.bat`

**Purpose:** Launcher for `openwebui-proxy.js`. Checks port availability (8443, 8013) and optionally checks if Open WebUI backend is reachable before starting.

**Steps:**
1. Check port 8443 not in use
2. Check port 8013 not in use
3. curl-probe `http://127.0.0.1:8035/` (warning only, non-fatal)
4. Execute `node openwebui-proxy.js`

---

## Web Files

---

### `web/index.html`

**Purpose:** Main single-page application (SPA) for the Clinical Note Generator v6.0. Handles the full clinical note workflow: patient data input, audio recording/transcription, OCR, note generation, workspace sync, and evidence-based comments.

**Architecture:** All application logic is inline `<script>` tags + external JS modules. No build system — pure vanilla JS.

**External JS modules loaded (in order):**
1. `universal_audio_handler.js` — Audio recording/speech
2. `markdown_renderer.js` — Markdown rendering
3. `audio_ui_utils.js` — Recording button state management
4. `generate_ui_flow.js` — Generate button flow
5. `auth_workspace.js` — Auth + server-side workspace sync

**Key Global State (`app` object):**
```js
app = {
  settings: { serverUrl: '/api', apiKey: '' },
  connection: { status: 'disconnected', lastCheck: null },
  isListening, pendingGenerateAfterTranscription,
  requestQueue: [],        // server-side queue (OCR/transcription jobs)
  uiState: {              // persisted to workspace
    lastGenerationId, showOrderRequestsButton, showEvidenceButton,
    ragHasGenerated, orderHasGenerated, ragContent, orderItems, ...
  },
  templates: { progress, followup, consult, procedure, referral, admission, discharge, transfer, summarize, custom },
  defaultPrompts: {},     // loaded from /api/note_prompts
  customPrompts: {},      // stored in workspace extras.customPrompts
  workspace: { state, version },
  noteState: { edited, lastSavedHash }
}
```

**Major Functions (inline scripts):**

| Function | Description |
|---|---|
| `getApiBase()` | Always returns `'/api'` |
| `safeStr(val)` | Null-safe string conversion |
| `cleanMarkdownFences(text)` | Strips markdown code fences and XML wrapper tags from generated notes |
| `stripBracketArtifacts(text)` | Removes lines that are only `[]{}` bracket noise |
| `updateGeneratedNoteEmptyState()` | Dims note textarea if empty or busy |
| `setGeneratedNoteBusy(bool)` | Enables/disables note textarea during generation |
| `updateNoteStatus(message)` | Shows/hides note status text |
| `sanitizeUserText(text)` | Strips non-ASCII control chars, trim |
| `cleanNoteChunk(chunk)` | Removes `__STREAM_END__` markers from streaming chunks |
| `finalizeNoteText(text)` | Comprehensive EMR sanitization: Unicode subscripts→ASCII, smart quotes, special spaces, paragraph spacing |
| `deduplicateReferences(refs)` | Deduplicates RAG references by source+title+section key |
| `updateConnectionStatus(status)` | Updates connection pill UI (connected/disconnected/queued) |
| `useFileCamera()` | Triggers file input with `capture=environment` for mobile camera |
| `setRecordingButtonsState(bool)` | Delegates to `CNGAudioUI.setRecordingButtonsState` |
| `initSpeechRecognition()` | Wires up `universalAudio` callbacks for transcription, ASR status, audio file |
| `toggleSpeechRecognition()` | Start/stop recording via universalAudio |
| `stopSpeechRecognition()` | Stops recording, resets buttons |
| `handleSelectedFiles(input)` | Routes files: audio→`transcribeAudio`, images/PDF→`processDocuments` |
| `handleAudioFile(input)` | Calls `transcribeAudio()` per file |
| `openCamera()` / `closeCamera()` | WebRTC camera modal |
| `capturePhoto()` | Canvas capture from video stream → processDocuments |
| `sanitizeClinicalText(rawText)` | Strips control chars, zero-width chars, markdown noise, removes boilerplate lab text |
| `appendToTextarea(el, text)` | Appends text respecting cursor position |
| `setFieldValue(fieldId, rawText, opts)` | Sanitizes and sets a field value, updates char counter |
| `setChartDataValue(rawText, opts)` | Calls `setFieldValue('chartData')` — legacy wrapper |
| `handleDrop(e, targetField)` | Global drop handler for text drops and file drops |
| `getFieldDisplayName(fieldId)` | Returns human-friendly name for field IDs |
| `initDragAndDrop()` | Attaches all drag/drop, paste, and focus listeners to the 3 input fields + drop zone |
| `appendChunkTranscript(tx, chunkText)` | Appends transcript chunk with overlap-dedup logic |
| `transcribeAudio(file, options)` | POSTs audio to `/api/transcribe_diarized`, appends to `transcriptionDisplay` |
| `ocrOnServer(file)` | POSTs file to `/api/ocr`, returns `{text, confidence, engine}` |
| `processDocuments(files)` | Runs OCR pipeline on dropped/uploaded documents, appends to target field |
| `generateNote()` | Entry point — validates input, checks online, calls `generateNoteOnline` |
| `stopGeneration()` | Aborts fetch via `generationAbortController.abort()` |
| `generateNoteOnline(chartData, noteType)` | Full streaming note generation: POSTs FormData to `/api/generate_v8_stream`, reads stream, finalizes text, triggers RAG + orders |
| `syncStreamingControlsVisibility()` | Shows/hides streaming controls bar based on `generationAbortController` |
| `queueStorage` object | IndexedDB helper (init, storeFile, getFile, deleteFile, deleteMany, clearAll) — **now mostly no-op since queue moved server-side** |
| `formatBytes(bytes)` | Human-readable byte sizes |
| `downloadQueuedFile(request)` | Downloads queued file via `/api/queue/<id>/download` |
| `queueRequest(type, data)` | Uploads file to server queue via POST `/api/queue`; handles offline with local download prompt |
| `sendFeedback(rating, suggestion, skipRatingEvent)` | POSTs feedback to `/api/feedback` |
| `openFeedbackSuggestionModal()` / `closeFeedbackSuggestionModal()` | Modal for thumbs-down text feedback |
| `submitFeedbackSuggestion()` | Submits suggestion text via `sendFeedback` |
| `processQueue()` | Retries queued jobs via `/api/queue/<id>/process` |
| `updateQueueDisplay()` | Renders queue card in UI |
| `saveQueue()` | No-op (queue now server-side) |
| `clearTranscription()` | Clears transcriptionDisplay and transcriptionData fields |
| `copyTranscription()` | Copies transcript to clipboard |
| `loadQueue()` | GETs server queue `/api/queue`, maps to local `app.requestQueue` |
| `openTemplateModal(template)` / `closeTemplateModal()` | Template replace/append confirmation |
| `applyTemplateReplace()` / `applyTemplateAppend()` | Apply pending template |
| `loadTemplate()` | Loads note-type template into chart field |
| `clearChartData()` | Legacy: calls `clearOldVisits()` |
| `defaultRagState()` / `defaultOrderRequestsState()` | Return default state objects |
| `ensureRagState()` / `ensureOrderRequestsState()` | Init-or-get window state |
| `persistRagState()` / `persistOrderRequestsState()` | Mirror state to `app.uiState` |
| `updateUiState(patch)` | Merges patch into `app.uiState`, triggers workspace queueSave |
| `applyUiStateFromWorkspace(ui)` | Restores UI state from workspace extras.ui |
| `clearOldVisits()` / `clearMixedOther()` | Clear respective input fields with confirm |
| `displayUncertainItems(internalData)` | Renders uncertain date items card from generation metadata |
| `getFactTypeIcon(factType)` / `formatFactSummary(item)` | Helpers for uncertain items rendering |
| `toggleUncertainItems()` | Expand/collapse uncertain items card |
| `focusCurrentSection()` / `goToGeneratedNoteCardActions()` / `isAudioCaptureActive()` | Delegates to `CNGGenerateUI` module |
| `clearGeneratedNote()` | Clears generated note with confirm |
| `clearAll(skipConfirm)` | Full reset: clears all fields, cancels audio, clears server queue, localStorage |
| `clearQueue()` | Clears server + local queue with confirm |
| `copyNote()` | Copies note to clipboard, strips `**` markdown bold |
| `openOrderRequestsModal()` / `closeOrderRequestsModal()` | Shows/hides order requests modal |
| `setOrderRequestsButtonVisible(visible)` / `setEvidenceButtonVisible(visible)` | Toggling feature buttons |
| `handleEvidenceBasedCommentsClick()` | Opens consult comment card, starts RAG generation if needed |
| `openConsultComment()` | Unhides consult comment card and scrolls to it |
| `startRagGeneration(genId, options)` | Polls `/api/generation/<id>/consult_comment` every 3s (up to 2min) for evidence-backed comment |
| `renderRagContent(content)` | Renders comment + references into UI |
| `renderMarkdownSimple(text)` | Delegates to `CNGMarkdown.renderMarkdownSimple` |
| `setConsultComment(text)` | Sets consult comment div innerHTML via renderMarkdownSimple |
| `renderOrderRequestsModal()` | Renders order/referral request items into modal |
| `copyOrderRequest(text)` | Copies single order request to clipboard |
| `handleOrderRequestsClick()` | Opens modal, starts order requests generation if needed |
| `startOrderRequestsGeneration(genId, options)` | Polls `/api/generation/<id>/order_requests` every 3s (up to 90s) |
| `retryOrderRequests()` | Force-restarts order requests generation |
| `fallbackCopy(text)` | Legacy clipboard copy via `document.execCommand` |
| `saveNote()` | Saves note via File System API (picker), Web Share (mobile), or download link |
| `isMobileDevice()` | UA detection for Android/iOS |
| `tryFileSystemSave(blob, filename)` | Uses `window.showSaveFilePicker` |
| `tryWebShareWithFiles(blob, filename, text)` | Uses `navigator.share` with files |
| `tryDownloadFallback(text, filename)` | Creates `<a download>` link |
| `retryConsultComment()` | Force-retries RAG with `force=1` |
| `saveDraft()` | Saves draft to localStorage `notegen_draft` (local backup) |
| `saveToStorage()` | Saves all fields + noteType to localStorage `clinicalNoteData` |
| `loadFromStorage()` | Restores fields from localStorage (only used when not authenticated) |
| `showProgress(containerId, barId)` / `hideProgress(containerId)` | Animates progress bars |
| `toggleEditTranscription()` | Toggles transcription display between readonly/editable with double-confirm |
| `showToast(title, message, type)` | Creates dismissible toast notification (2.5s auto-dismiss) |
| `expandSidebar()` / `collapseSidebar()` | Sidebar hover expand/collapse |
| `updateCharacterCounter()` | Updates 50k char counters for oldVisitsData and mixedOtherData |
| `initCharacterCounters()` | Attaches input/paste listeners for character counting |
| `loadSettings()` | Reads auth token, sets `app.settings`, calls `checkConnection` |
| `saveSettings()` | Updates app.settings, calls writeSettings |
| `checkConnection()` | GETs `/api/health` with auth token, updates connection status |
| `loadDefaultPrompts()` | GETs `/api/note_prompts`, populates `app.defaultPrompts` |
| `saveCustomPrompt()` / `clearCustomPrompt()` | Save/clear per-note-type custom prompt to workspace |
| `loadWorkspaceState()` | GETs `/api/workspace/`, merges custom prompts |
| `saveWorkspaceCustomPrompts()` | PUTs workspace with custom prompts in extras, handles 409 conflict |
| `loadCustomPromptsFromStorage()` | **DEPRECATED** — was localStorage, now workspace |
| `showServiceErrorBanner(detail, retryFn)` | Shows/hides service error banner with retry button |
| `hideServiceErrorBanner()` | Hides error banner |
| `refreshBadge()` | Re-runs `/api/health` to update connection status badge |
| `openSettingsDrawer()` / `closeSettingsDrawer()` | Side drawer for auth card + diagnostics |
| `openQAPanel()` / `closeQAPanel()` | Slide-in Q&A side panel (iframe to qa.html) |
| `openToolsSheet()` / `closeToolsSheet()` | Mobile bottom sheet for tools |
| `mobileNavSetActive(el)` / `mobileNavGo(which, el)` | Mobile bottom nav handlers |
| `placeAuthCard(signedIn)` | Moves auth card to settings drawer (signed in) or main content (signed out) |
| `toggleMobileTopBar()` | Collapses/expands mobile top bar |
| `_hasToken()` | Checks sessionStorage/localStorage/app.settings for auth token |
| `simpleHash(str)` | DJB2 hash for note change detection |
| `hasUnsavedNote()` / `markNoteDirty()` / `markNoteSaved()` | Note save-guard tracking |
| `registerUnloadGuards()` | Registers beforeunload, pagehide, visibilitychange handlers |
| `getAuthToken()` | Returns current Bearer token from app.settings or sessionStorage |
| `showPromptSettings()` / `closePromptSettings()` | Prompt customization modal |
| `updatePromptDisplay()` | Syncs selected note type with default/custom prompt textareas |
| `debugLog(...args)` | console.log only when `window.DEBUG_MODE === true` |
| `readSettings()` / `writeSettings(obj)` | Settings helpers; always use `/api` base URL |
| `apiFetch(path, opts)` | Fetch with auto-auth header |
| `getConnectionState()` | Probes `/api/health`, returns `{ok, reason}` |

**V7 3-Field Input System:**
- `transcriptionDisplay` — read-only transcript from ASR (shown in stacked box top)
- `transcriptionData` — editable current encounter notes (stacked box bottom)
- `oldVisitsData` — prior visit notes (50k char limit, red background)
- `mixedOtherData` — labs/imaging/consults (50k char limit, yellow background; auto-date-classified)

**Modals present:**
- `cameraModal` — WebRTC camera capture
- `promptModal` — AI prompt customization
- `templateModal` — Template replace/append confirmation
- `orderRequestsModal` — Order & referral requests
- `feedbackSuggestionModal` — Thumbs-down suggestion text

**PWA registration:** Service worker registered for non-localhost origins only.

---

### `web/scripts.js`

**Purpose:** Provides the `OCRProcessor` class for the standalone `ocr.html` page. Also exports global OCR utility functions (`escapeHtml`, `engineVariant`, `renderOcrBadge`, `addEngineBadge`) and simple helper functions for the OCR results UI.

**Note:** This file is loaded by `ocr.html` only — NOT by `index.html`. It defines its own `showToast` function which conflicts with the one in `index.html` (different signature and simpler implementation).

**Class: `OCRProcessor`**

| Method | Description |
|---|---|
| `constructor()` | Sets apiBase `/api`, initializes queue/retry state, calls `initializeEventListeners()` |
| `getAuthToken()` | Reads from `window.app.settings.apiKey` or sessionStorage |
| `initializeEventListeners()` | Wires upload area click, drag events, file input change, process button click |
| `handleDragOver(e)` | Adds `dragover` class |
| `handleDragLeave(e)` | Removes `dragover` class |
| `handleDrop(e)` | Extracts file from drag event, calls `selectFile` |
| `handleFileSelect(e)` | Extracts file from input change, calls `selectFile` |
| `selectFile(file)` | Validates type (PDF/PNG/JPEG/TIFF/BMP) and size (≤50MB), stores as `this.currentFile` |
| `processDocument()` | POSTs file to `/api/ocr` with auth header, shows results or queues for retry |
| `enqueueCurrentFile()` | Pushes current file to retry queue |
| `scheduleQueue()` | Sets timeout to call `processQueue` after `retryDelayMs` (5s base) |
| `processQueue()` | Dequeues and retries, backs off up to 60s, max 3 retries |
| `showProgress()` | Shows progress container, disables button, starts `animateProgress` |
| `hideProgress()` | Hides progress, re-enables button |
| `animateProgress()` | Fakes progress animation with cycling messages |
| `showResults(result)` | Populates `ocrResults` textarea, `resultsInfo` span; scrolls to results |
| `showError(message)` | Shows error container |
| `clearPreviousResults()` | Hides results and error containers |

**Global OCR functions:**
| Function | Description |
|---|---|
| `escapeHtml(s)` | HTML-escapes special chars |
| `engineVariant(engineRaw)` | Maps engine string to CSS class + label for badge |
| `renderOcrBadge(engine, conf)` | Returns HTML badge string |
| `addEngineBadge(filename, engine, conf)` | Appends badge to `ocrEngineList` element |
| `showToast(message)` | Simple green fixed toast (ocr.html only, different from index.html version) |
| `copyOcrText()` | Copies OCR result to clipboard |
| `downloadOcrText()` | Downloads OCR result as `.txt` file |
| `useInNotes()` | Saves OCR text to `localStorage.extracted_text`, scrolls to `#notes` section |
| `clearResults()` | Resets all OCR UI elements |
| `clearError()` | Hides error container |

**Initialized on DOMContentLoaded:** Creates `window.ocrProcessor = new OCRProcessor()` if `uploadArea` element exists; adds OCR nav link dynamically.

---

### `web/styles.css`

**Purpose:** CSS styles for `ocr.html` and `scripts.js` components. Provides upload area, OCR options, progress bar, results, error states, and engine badge styling.

**Key CSS classes:**
- `.upload-area`, `.upload-area.dragover` — dashed drop zone
- `.upload-area:hover` — blue hover state
- `.ocr-options` — flex row of mode options
- `.option-card`, `.option-card.selected` — selectable mode cards
- `.progress-container`, `.progress-fill` — upload progress
- `.results-container` — OCR results wrapper
- `.results-actions` — copy/download/use buttons row
- `.error-container` — red error display
- `.ocr-badge`, `.engine-mixed`, `.engine-printed`, `.engine-handwritten`, `.engine-unknown` — colored engine badges

**Note:** This file is NOT referenced in `index.html`. The main page styles are all inline in index.html's `<style>` block. `styles.css` is only loaded by `ocr.html`.

---

### `web/auth_workspace.js`

**Purpose:** Complete authentication and workspace synchronization module. Handles login/register, JWT token management (including decode + expiry/refresh), idle timeout, and bidirectional workspace sync with the server.

**Exposed global:** `window.AuthWorkspace`

**Module pattern:** IIFE (`(function(){...})()`)

**Constants:**
- `STORAGE_KEYS.ACCESS = 'auth_access_token'` (sessionStorage)
- `STORAGE_KEYS.API_BASE = 'auth_api_base'` (localStorage)

**State properties:**
```
apiBase, accessToken, user, workspaceVersion, profilePromise, workspacePromise,
saveTimer, initialized, syncTimer, syncIntervalMs(7000), lastLocalEditAt,
suppressAutoSaveUntil, idleTimer, idleWarningTimer, idleTimeoutMs(3600000=1hr),
tokenExpiryMs, tokenRefreshTimer, refreshLeadMs(60000), recordingKeepAliveTimer
```

**Methods:**

| Method | Description |
|---|---|
| `init()` | Entry point: caches DOM elements, binds events, checks existing session token |
| `cacheElements()` | Caches all DOM refs: authCard, loginForm, registerForm, syncPill, diagWorkspaceVersion, etc. |
| `bindEvents()` | Attaches form submit, logout, clear workspace, toggle register, apiBase change handlers; sets up field polling (500ms) for programmatic changes; `beforeunload` → sendBeacon; `visibilitychange` → save/pull |
| `updateStatus(text, type)` | Updates auth status text + CSS class |
| `showAuthForms()` | Shows login/register form, hides actions, clears auth-ready body class |
| `showAuthActions(profile)` | Hides forms, shows actions, adds auth-ready class, hides clear button |
| `showPendingApproval(profile)` | Shows pending approval state |
| `showApprovalNotice(message)` / `hideApprovalNotice()` | Approval message display |
| `updateWorkspaceMeta()` | Updates workspace version display in UI |
| `setSyncPill(level, text)` | Updates sync status pill (ok/warn/bad classes) |
| `toggleRegisterForm()` | Switches between login and register forms |
| `setAccessToken(token)` | Stores in sessionStorage, updates `app.settings.apiKey`, calls `updateTokenMetadata` |
| `clearSession()` | Clears token, stops sync loop, resets state |
| `login()` | POSTs to `/api/auth/login`, stores token, fetches profile, loads workspace |
| `register()` | POSTs to `/api/auth/register` |
| `showLoginError(message)` / `showRegisterInfo(message, type)` | Form error display |
| `fetchProfile()` | GETs `/api/auth/me`, sets `this.user`, shows actions or pending approval |
| `isAudioRecordingActive()` | Checks multiple audio state sources to prevent idle timeout during recording |
| `isWorkspaceReady()` | Returns `user && user.is_approved` |
| `loadWorkspace()` | GETs `/api/workspace/`, calls `applyWorkspaceState`, starts sync loop |
| `startWorkspaceSyncLoop()` | Sets 7s interval calling `pullWorkspaceIfNewer` |
| `stopWorkspaceSyncLoop()` | Clears sync interval |
| `pullWorkspaceIfNewer(force)` | GETs workspace; only applies if server version > local AND no recent edit (3s debounce) |
| `applyWorkspaceState(state)` | Restores all fields from workspace: generatedNote, transcriptionDisplay, transcriptionData (currentEncounter), oldVisitsData, mixedOtherData, userSpeciality, chartData (legacy), custom prompts, UI state |
| `collectWorkspaceState()` | Collects all field values into workspace payload structure |
| `queueSave()` | Debounces workspace save (1s), only on main page and if workspace ready |
| `saveWorkspace()` | PUTs `/api/workspace/` with version; handles 409 conflict (merge retry) |
| `clearWorkspace()` | Saves first, double-confirms, calls `clearAll(true)`, POSTs `/api/workspace/clear` |
| `logout(skipConfirm)` | Saves workspace, confirms, clears queue/session, POSTs `/api/auth/logout` |
| `tryRefresh()` | POSTs `/api/auth/refresh`, updates access token on success |
| `handleUnauthorized(message)` | Clears session, shows auth forms |
| `request(path, options, skipAuth)` | Authenticated fetch wrapper; auto-retries after token refresh on 401 |
| `enableIdleTracking()` / `disableIdleTracking()` | Attaches/detaches DOM activity listeners |
| `resetIdleTimer()` | Restarts idle countdown (warn at 30s before timeout) |
| `handleIdleTimeout()` | Auto-logout on idle (respects active recording) |
| `clearIdleTimers()` / `showIdleWarning()` / `hideIdleWarning()` | Idle warning management |
| `clearUiState()` | Resets auth UI chrome (NOT field data) on sign-out |
| `updateApiBaseInput()` | Syncs apiBase to hidden input |
| `decodeToken(token)` | Base64url decodes JWT payload |
| `updateTokenMetadata(token)` | Extracts exp/sub from JWT, schedules refresh |
| `scheduleTokenRefresh()` / `clearTokenRefreshTimer()` | Proactive token refresh 60s before expiry |
| `emitAuthChanged(signedIn)` | Fires `workspace-auth-changed` CustomEvent |
| `ensureFreshToken()` | Calls `tryRefresh()` if token within 60s of expiry |

**Workspace page guard:** `window.WORKSPACE_PAGE_TYPE === 'main'` must be set for field monitoring and save-on-unload to activate. (`index.html` sets it; `ocr.html`, `qa.html`, `admin.html` do not.)

**Events emitted:**
- `workspace-auth-changed` → `{ signedIn, token }` — listened by index.html for connection/queue refresh
- `workspace-synced` — emitted on successful sync pull

**Fields monitored for workspace sync (main page only):**
`generatedNote`, `transcriptionData`, `oldVisitsData`, `mixedOtherData`, `userSpeciality`, `chartData` (legacy)

---

### `web/audio_ui_utils.js`

**Purpose:** UI helper module that manages the visual state of all record buttons across the page. Exposed as `window.CNGAudioUI`.

**Module pattern:** IIFE

**Functions:**

| Function | Description |
|---|---|
| `isIconOnlyRecordButton(btn)` | Returns true for `recordBtnInlineRound` or `.record-round-btn` (icon-only, no text label) |
| `ensureRecLabel(btn)` | Gets or creates a `.rec-label` span inside non-icon-only buttons |
| `setRecordingButtonsState(isRecording)` | Applies `recording-active`/`recording-ready` classes and "Stop"/"Record" label text to ALL record buttons (by ID, by class selector, and by onclick selector) |
| `debugAudioCapabilities()` | Shows toast with audio capabilities summary (delegates to `universalAudio.getBrowserGuidance()`) |

**Exported:** `window.CNGAudioUI = { setRecordingButtonsState, debugAudioCapabilities }`

---

### `web/generate_ui_flow.js`

**Purpose:** UI flow module for generate button interactions. Handles the case where user clicks Generate while recording is active (auto-stops recording first, waits for transcription, then generates). Exposed as `window.CNGGenerateUI`.

**Module pattern:** IIFE

**Functions:**

| Function | Description |
|---|---|
| `focusCurrentSection()` | Scrolls to and focuses authCard or chartCard |
| `goToGeneratedNoteCardActions()` | Scrolls to noteCard, focuses Generate button |
| `isAudioCaptureActive()` | Checks `universalAudio` recording/listening state flags |
| `clearPendingGenerateAfterTranscription()` | Cancels pending auto-generate timer |
| `triggerGenerateNoteAndFocus()` | Main entry: if recording active → stop and set `pendingGenerateAfterTranscription=true` (45s timeout), else call `generateNote()` directly |

**Exported:** `window.CNGGenerateUI = { focusCurrentSection, goToGeneratedNoteCardActions, isAudioCaptureActive, clearPendingGenerateAfterTranscription, triggerGenerateNoteAndFocus }`

---

### `web/markdown_renderer.js`

**Purpose:** Lightweight markdown-to-HTML renderer for the evidence-based consult comments and Q&A chat messages. No external dependencies.

**Module pattern:** IIFE

**Functions:**

| Function | Description |
|---|---|
| `renderMarkdownSimple(text)` | Converts markdown to HTML: supports h1-h6, bold, italic, code, bullet lists, tables, horizontal rules. Handles table detection via separator row pattern. Uses `inlineFormat()` for inline elements. Properly closes `<ul>` and `<table>` tags. |

**Exported:** `window.CNGMarkdown = { renderMarkdownSimple }`

---

### `web/universal_audio_handler.js`

**Purpose:** Cross-browser audio recording handler that abstracts MediaRecorder API and browser-specific limitations (iOS Safari vs Chrome iOS vs Android Chrome). Provides both speech recognition path (native Web Speech API) and audio recording path (MediaRecorder → file upload).

**Architecture note:** The "speech recognition" path has been reimplemented to use full-upload mode (records locally, uploads complete audio on stop) rather than native SpeechRecognition API. `startSpeechRecognition()` actually calls `startAudioRecording()`.

**Class: `UniversalAudioHandler`**

| Method | Description |
|---|---|
| `constructor()` | Detects capabilities, initializes state |
| `_extensionFromMime(mimeType)` | Maps MIME type to file extension (webm/ogg/m4a/wav) |
| `setAsrStatusCallback(cb)` / `_asrStatus(state, detail)` | ASR status callback for UI |
| `detectBrowserCapabilities()` | Detects platform (Android/iOS), browser (Chrome/Firefox/Safari/Samsung/Edge/ChromeiOS), and feature availability (SpeechRecognition, getUserMedia, MediaRecorder) |
| `initSpeechRecognition()` | Sets up WebkitSpeechRecognition with continuous mode, result/error/end handlers; auto-restarts on end; returns `{available, method}` |
| `initAudioRecording()` | Returns `{available, method}` based on capability detection |
| `startSpeechRecognition()` | **Currently = full-upload mode**: calls `startAudioRecording()` |
| `stopSpeechRecognition()` | Calls `stopAudioRecording()`, emits ASR status 'processing' |
| `startAudioRecording()` | getUserMedia → MediaRecorder (prefers `audio/webm;codecs=opus`) → collects chunks → on stop: creates File → calls `onAudioFileCallback` → restarts if `shouldKeepRecording` |
| `stopAudioRecording()` | Stops MediaRecorder, sets `shouldKeepRecording=false` |
| `getBrowserGuidance()` | Returns `{speechAvailable, recordingAvailable, message, recommendations}` for current browser/platform |
| `onSpeechStart/Stop/Error` | Stub event handlers (overridden by index.html setup) |
| `onRecordingStart/Stop/Error` | Stub event handlers (overridden by index.html setup) |
| `getSpeechUnavailableReason()` | Returns human-readable reason string |
| `getRecordingUnavailableReason()` | Returns human-readable reason string |
| `setTranscriptionCallback(cb)` | Sets `onTranscriptionCallback` |
| `setAudioFileCallback(cb)` | Sets `onAudioFileCallback` |
| `_requestWakeLock()` / `_releaseWakeLock()` / `_reacquireWakeLock()` | Screen wake lock management (prototype methods added at bottom) |

**Global function:** `initUniversalAudio()` — creates and returns `window.universalAudio = new UniversalAudioHandler()`

**MIME handling:** Forces WebM/Opus; WAV fallback was removed due to invalid browser-generated files.

---

### `web/service_worker.js`

**Purpose:** PWA service worker providing offline caching for static assets. Uses a cache-version strategy (`clinical-notes-ocr-v15`) to force cache invalidation on updates.

**Cache name:** `clinical-notes-ocr-v15` (increment to force update)
**Pre-cache URLs:** Empty array `[]` — no assets are pre-cached at install

**NEVER_CACHE list (always network-fetch):**
- `/static/index.html`, `/index.html`, `/index111.html`
- `auth_workspace.js` (CRITICAL — never cache)
- `universal_audio_handler.js` (must always load fresh)

**Install handler:** Iterates PRECACHE_URLS (empty), calls `skipWaiting()`
**Activate handler:** Deletes old caches, calls `clients.claim()`
**Message handler:** `SKIP_WAITING` message type support

**Fetch handler logic (priority order):**
1. If request has `Authorization` header → pass through directly (CRITICAL FIX)
2. If URL in NEVER_CACHE → pass through
3. If path starts with `/api` → bypass cache (pass through)
4. If HTML navigation request → network-first with cache fallback
5. If GET request → cache-first with network fallback; caches fresh copies
6. Everything else → pass through

**Note in code:** Install handler log message says "v14" but CACHE_NAME is `v15` — minor inconsistency.

---

### `web/manifest.json`

**Purpose:** PWA Web App Manifest. Minimal configuration — no icons defined.

```json
{
  "name": "Clinical Note Generator",
  "short_name": "CNG",
  "start_url": "/static/index.html",
  "scope": "/static/",
  "display": "standalone",
  "background_color": "#ffffff",
  "theme_color": "#0b5fff",
  "icons": []
}
```

**Issue:** `start_url` and `scope` use `/static/` path prefix, but `server.js` serves files from `/` (no `/static/` prefix). This means the PWA install/offline behavior may not match the actual URL structure if the app is accessed at `https://ieissa.com:3443/`.

---

### `web/admin.html`

**Purpose:** Admin console page providing system information, user management, and server diagnostics. Loaded at `/admin.html` path (static file serve).

**Structure (first 100 lines visible — file is 1771+ lines):**
- Admin console layout with nav links
- Navigation includes links: `admin.html`, `index.html`, `qa.html` — **link to `ocr.html` is CSS-hidden (`display:none !important`)**
- Cards layout with `row`/`col` structure
- Info panels with table display for system stats
- `updates-list` section for server update logs

**Auth:** Loads `auth_workspace.js` (not directly seen in first 100 lines but structure implies it).

---

### `web/ocr.html`

**Purpose:** Standalone OCR document scanning page. Provides file upload interface for image/PDF OCR, using the `OCRProcessor` class from `scripts.js`.

**Key elements:**
- File upload area with drag-and-drop (`uploadArea`, `documentInput`)
- OCR mode selector (`ocrMode`)
- Progress bar (`progressContainer`, `progressFill`, `progressText`)
- Results textarea (`ocrResults`) with engine info (`resultsInfo`)
- Error container (`errorContainer`, `errorMessage`)

**Scripts loaded:** `scripts.js` (OCRProcessor + helpers)

**Auth:** Includes inline auth card (same login form as index.html) and loads `auth_workspace.js`.  
**Note:** Sets `window.WORKSPACE_PAGE_TYPE` is NOT set, so workspace sync is disabled on this page.

---

### `web/qa.html`

**Purpose:** Medical Q&A chat interface. Renders as an iframe within `index.html`'s side panel (or standalone). Streams answers from the backend with support for image attachments (vision Q&A).

**Key elements:**
- `#chat` — message feed (user/assistant bubbles)
- `#question` — resizable textarea
- `#sendBtn` — send button (disabled during streaming)
- `#error` — error display
- Image attachment support with thumbnail preview

**Backend endpoint:** `/api/qa` (POST, streaming with `Transfer-Encoding: chunked` or EventSource)

**Features:**
- Markdown rendering via `renderMarkdownSimple` (inline or from `markdown_renderer.js`)
- Reference display with clickable links
- Image attachment via file input (vision Q&A)
- Streaming cursor animation
- Token counter display

---

### `web/auth_debug.html`

**Purpose:** Developer diagnostic tool for testing auth_workspace.js loading and functionality. Dark terminal-style UI with color-coded log output.

**Tests performed:**
1. sessionStorage availability
2. localStorage availability  
3. Loads `auth_workspace.js` dynamically
4. Checks `window.AuthWorkspace` exists and logs its properties

**Classification:** Development/debug tool — not linked from main app navigation (directly accessed by URL).

---

## Routes & Endpoints

### All Proxied Routes (server.js)

| External URL | Method | Proxied To | Notes |
|---|---|---|---|
| `GET /fastapi-check` | GET | `http://127.0.0.1:7860/api/health` | Direct http.get, not proxy middleware |
| `/llama/generate` | ANY | `http://127.0.0.1:7871/api/generate` | Llama gateway path |
| `/llama/check` | ANY | `http://127.0.0.1:7871/api/check` | Llama health |
| `/whisperx` | ANY | `http://127.0.0.1:7860/api/transcribe_diarized` | WhisperX shortcut |
| `/ocr` | ANY | `http://127.0.0.1:7860/api/ocr` | OCR shortcut |
| `/api/*` | ANY | `http://127.0.0.1:7860/api/*` | Main FastAPI API |
| `/admin/*` | ANY | `http://127.0.0.1:7860/admin/*` | Admin FastAPI |
| `GET /health` | GET | Local (Express) | Node proxy health |
| `GET /` | GET | Static: `web/index.html` | Main app |
| `GET /qa` | GET | Static: `web/qa.html` | Q&A page |
| `GET /*` | GET | Static/SPA fallback | Non-asset paths → index.html |

### Known FastAPI Backend Routes (called by frontend)

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/health` | GET | Backend health check |
| `/api/auth/login` | POST | User login → access_token |
| `/api/auth/register` | POST | User registration |
| `/api/auth/me` | GET | Get current user profile |
| `/api/auth/logout` | POST | Logout |
| `/api/auth/refresh` | POST | Refresh JWT token |
| `/api/workspace/` | GET | Load workspace state |
| `/api/workspace/` | PUT | Save workspace state |
| `/api/workspace/clear` | POST | Reset workspace to baseline |
| `/api/transcribe_diarized` | POST | WhisperX speech-to-text (FormData: audio) |
| `/api/ocr` | POST | OCR image/PDF (FormData: file) |
| `/api/generate_v8_stream` | POST | Stream clinical note generation (FormData) |
| `/api/generation/<id>/meta` | GET | Generation metadata (V7 uncertain items) |
| `/api/generation/<id>/consult_comment` | GET | Evidence-backed comment (poll) |
| `/api/generation/<id>/order_requests` | GET | Order/referral requests (poll) |
| `/api/feedback` | POST | Submit note rating/suggestion |
| `/api/note_prompts` | GET | Load default prompt templates |
| `/api/queue` | GET | List queued jobs |
| `/api/queue` | POST | Add file to queue (FormData) |
| `/api/queue` | DELETE | Clear all queued jobs |
| `/api/queue/<id>/process` | POST | Process a queued job |
| `/api/queue/<id>/download` | GET | Download queued file |
| `/api/version` | GET | App version info (commit hash + build timestamp) |
| `/api/qa` | POST | Medical Q&A (streaming) |

---

## Dead / Commented-Out Code

### `server.js`
1. **`openwebuiProxyCommon`** — Defined but never used in any `app.use()`. Open WebUI was moved to `openwebui-proxy.js`. The variable just sits there.
2. **Comment block:** `// Note: Open WebUI proxy moved to dedicated server (openwebui-proxy.js)` — clarifies the above.

### `index.html`
1. **`saveCustomPromptsToStorage()`** — Commented out function body preserved at bottom: `// DEPRECATED: Custom prompts now stored in workspace, not localStorage`. The call sites also preserve the comment.
2. **`window.saveCustomPromptsToStorage`** inside `applyWorkspaceState` — Called but commented out: `// saveCustomPromptsToStorage(); // DEPRECATED - workspace is source of truth`
3. **`loadCustomPromptsFromStorage()`** call in DOMContentLoaded — commented out: `// loadCustomPromptsFromStorage(); // DEPRECATED - now using workspace`
4. **localStorage `clinicalNotePrompts` cleanup** — Code checks and removes old key if present, logs toast.
5. **`debugLog`** — All debug logging gated on `window.DEBUG_MODE === true` which is never set in production — effectively dead code for production.
6. **`chartData` element references** — Multiple functions reference `document.getElementById('chartData')` which does not exist in the current HTML (V7 removed this field). These calls silently return null and are effectively no-ops.
7. **`setChartDataValue()`** — Legacy wrapper calling `setFieldValue('chartData')` on a non-existent element; kept for backward compatibility but does nothing visible.
8. **`clearChartData()`** — Legacy function that calls `clearOldVisits()`. The original chart clearing is dead.
9. **`useInNotes()`** in scripts.js — Saves to `localStorage.extracted_text` and scrolls to `#notes` section that doesn't exist in any page.
10. **`noteActions-vertical` CSS class** — Defined as `display: none; /* deprecated */` in the CSS.

### `service_worker.js`
1. **Console log message mismatch** — Install handler logs "Service Worker v14 installed" but cache is named `v15`. Minor inconsistency.
2. **`PRECACHE_URLS = []`** — Pre-caching list is empty; the install loop does nothing.

### `auth_workspace.js`
1. **`saveCustomPromptsToStorage` reference** in `applyWorkspaceState` — wrapped in `if (typeof saveCustomPromptsToStorage === 'function')` check with comment `// DEPRECATED`.
2. **`window.saveSettings` wrapper** — Intercepts and re-saves workspace on settings save. But `saveSettings()` in index.html never modifies workspace-relevant data, so this is a no-op wrapper.

### `config/server_config.json` (both variants)
1. **`enable_gzip: true`** — Config key defined but no gzip middleware exists in `server.js`.
2. **`log_level: "INFO"`** — Config key defined but never read by `server.js`.

### `package.json`
1. **`ssl-setup` and `generate-ssl` scripts** — Reference `ssl-setup.js` and `generate-ssl-cert.js` which don't exist in the repo.

---

## Unused / Legacy Files

### `web/styles.css`
**Status: Only used by `ocr.html`**  
Not loaded by `index.html` (which has all styles inline). Not loaded by `admin.html` or `qa.html`. Solely serves `ocr.html`'s OCR-specific UI styling.

### `web/auth_debug.html`
**Status: Development/debug tool — not linked from navigation**  
Never linked from `index.html`, `admin.html`, or `qa.html`. Access requires direct URL navigation. Used only during development for testing auth module loading.

### `web/scripts.js`
**Status: Only used by `ocr.html`**  
Not loaded by `index.html`. The OCRProcessor class and global OCR helpers are only for the standalone OCR page. The `showToast` function in this file conflicts with (and is shadowed by) the more feature-rich `showToast` in `index.html`.

### `config/server_config.linux.json`
**Status: Not auto-loaded — requires manual swap**  
`server.js` only loads `./config/server_config.json` by name. This Linux variant must be manually renamed or server.js must be modified to detect platform. It exists as a reference config but is inert unless manually applied.

### `New_Main_Server.bat` references
**Status: References missing scripts**  
Mentions `Kill_Old_Node_Processes.bat` which is not in the repo.

### `package.json` references
**Status: References missing scripts**  
`ssl-setup.js` and `generate-ssl-cert.js` are in package.json scripts but not in the repo.

---

*End of FRONTEND_INDEX.md*
