# CNG Frontend Index — Node Proxy + Web UI

> **Updated:** 2026-04-08 · **Source path:** `PCHost/web/`
>
> Covers the Express proxy (`server.js`), llama gateway, static web assets, and the main SPA (`index.html` + `js/workspace_app.js` and satellite modules). **P10 (ship):** see **`MODULARIZATION_PLAN.md`**; deep split of `workspace_app.js` = optional **P10b**.

## 1. Proxy Processes & Config
- **`server.js`** — Express HTTPS server with `http-proxy-middleware`. Proxies:
  - `/api/*` → FastAPI (`FASTAPI_URL`), rewrites paths to re-prepend `/api` because Express strips the mount prefix.
  - `/admin/*` → FastAPI admin routes.
  - `/whisperx` → `/api/transcribe_diarized` (ASR shortcut), `/ocr` → `/api/ocr`.
  - `/llama/generate` + `/llama/check` → `llama-gateway.js` (bridges to WSL llama-server on :8081).
  - `/health` responds locally; `/fastapi-check` bypasses proxy for debugging.
- **TLS/ports:** `PCHost/config/server_config.json` defines host/port, backend target, TLS cert/key, and HTTP→HTTPS redirect behavior.
- **Llama bridge:** `llama-gateway.js` exposes `/api` and proxies to llama-server. `openwebui-proxy.js` is a separate HTTPS redirect used when OpenWebUI is running.
- **Scripts:** `New_Main_Server.bat` and `start-openwebui-proxy.bat` launch the proxy processes; logs live under `PCHost/logs/`.

## 2. `web/` Contents (live snapshot)
| File | Purpose |
| --- | --- |
| `index.html` | Primary SPA shell: layout + small boot scripts. **P10 (ship):** linked **`css/workspace.css`**; **`js/workspace_app.js`** (core logic + **`WORKSPACE_PAGE_TYPE`**), **`settings_connection.js`**, **`qa_side_panel.js`**, **`mobile_tools.js`**, **`settings_drawer.js`** (profile, auth card, mobile bar), **`version_badge.js`**. Also **`auth_workspace.js`**, **`encounters_ui.js`**, **`literature_ui.js`**. |
| `generate_ui_flow.js` | Scroll/focus helpers triggering `window.generateNote`. Used for accessibility cues post-generation.
| `auth_workspace.js` | Session handling (login/register/logout), token refresh, auto-save to `/api/workspace/` (**active encounter** state, P5+), idle timers, **`isClinicalBusy()`** (P6), and queue save throttling. |
| `encounters_ui.js` | **P6:** slide-over Encounters panel — **`GET/POST/PATCH/DELETE/activate`** via **`AuthWorkspace.request`**; **`openEncountersPanel`**, **`encountersNewThread`**, **`closeCurrentEncounterContent`**. |
| `universal_audio_handler.js` | Cross-browser audio recorder. Records WebM chunks and uploads entire files on stop; does **not** support live streaming yet.
| `audio_ui_utils.js` | UI helpers for audio controls (playback, file pickers) shared across pages.
| `scripts.js` | OCR upload/queue helpers (used where embedded in the SPA).
| `markdown_renderer.js` | Converts Markdown in generated notes to sanitized HTML (used in admin/QA views).
| `service_worker.js`, `manifest.json` | Offline/PWA plumbing (caches static assets, surfaces install prompts).
| `styles.css` | Shared styles where used on older paths; main SPA uses **`css/workspace.css`**.
| `admin.html`, `qa.html` | Task-specific pages that reuse auth/audio helpers. (Standalone **`ocr.html`** removed; OCR flows live in **`index.html`**.)

## 3. Main workspace anatomy
- **Layout & styling:** **`css/workspace.css`** (+ **`dreamcision-tokens.css`**). Shared **`styles.css`** may still be used on legacy paths where applicable.
- **Top command bar:** `#userSpeciality` syncs with profile defaults and workspace extras (see **`auth_workspace.js`** `collectWorkspaceState` / `applyWorkspaceState`).
- **Global state:** `window.app` is declared in **`js/workspace_app.js`** (`settings`, `uiState`, note templates, prompts, queue metadata). Most handlers live in that file.
- **Storage:** `saveToStorage()` / `loadFromStorage()` use `localStorage` (`clinicalNoteData`); **`AuthWorkspace`** mirrors encounter fields to **`/api/workspace/`**.
- **Workspace integration:** Custom prompts under `extras.customPrompts` via helpers in **`workspace_app.js`** / **`AuthWorkspace`**. Clear/reset uses **`AuthWorkspace.clearWorkspace`** / **`/api/workspace/clear`** where applicable.
- **Account profile & templates (P3 / P3b):** Settings drawer calls **`GET/PUT /api/profile`**; the note prompt modal saves per–note-type **account** templates via **`PUT /api/note-types`** / **`POST …/revert`** and refreshes with **`loadProfileNoteTypes`** alongside **`GET /api/note_prompts`** (effective vs baseline maps; **no** system prompt body in that response after P4). **P3b:** **`POST /api/note-types/custom`**, **`DELETE …/{id}`** for custom types, **`POST …/revert-builtins`**; **`noteType`** / **`noteTypeSelect`** options sync from **`GET /api/note-types`**.
- **P4 prompt composition:** **Location** comes from **profile** default plus optional **encounter** field (**`encounterLocation`** in workspace / generate payload). **`POST /generate_v8_stream`** accepts **`use_account_template`** (checkbox in AI Prompts modal); when off, the server uses config baseline only for merged templates. **Encounter instructions** (workspace **`custom_prompt`**) merge in the USER block with the account template. The backend may inject **author context** into SYSTEM (**`{USER_DISPLAY_NAME}`** / **`{USER_EMAIL}`** from profile; optional **`[Profile author]`** line) so the model knows who is writing the note.
- **Note generation flow:** `generateNoteOnline()` in **`workspace_app.js`** streams **`/api/generate_v8_stream`**; consult/order polling uses generation id headers.
- **Queue + clear:** `clearAll()` / `clearQueue()` in **`workspace_app.js`** coordinate server queue + IndexedDB; see **`AuthWorkspace`** for workspace clears.
- **RAG/consult UI:** Evidence and order-request flows live in **`workspace_app.js`** (state + polling).
- **Auth card & status:** **`settings_drawer.js`** places the auth card; **`auth_workspace.js`** emits **`workspace-auth-changed`** / sync events.

## 4. Supporting Modules
- **`auth_workspace.js`** — Implements login/registration, token refresh, idle timers, and the workspace auto-save queue. `collectWorkspaceState()` (`auth_workspace.js#L692-L722`) captures note/transcription/oldVisits/mixedOther plus speciality and custom prompts; `queueSave()` throttles PUT `/api/workspace/`.
- **`universal_audio_handler.js`** — Checks browser capabilities, starts `MediaRecorder`, and concatenates blobs before calling `onAudioFileCallback`. Streaming ASR would require chunk callbacks instead of whole-file uploads (`universal_audio_handler.js#L208-L286`).
- **`audio_ui_utils.js`** — Handles file uploads for audio transcription, queue persistence, and toast notifications throughout the SPA.
- **`generate_ui_flow.js`** — Focus helper for `Generate` and clipboard actions; ensures note cards scroll into view after streaming.
- **`markdown_renderer.js`** — Sanitizes markdown for preview panes (admin/QA) but the main SPA currently operates on raw text.
- **`service_worker.js`** — Caches static assets and handles offline detection; ensures `/` falls back to cached `index.html` when offline.

## 5. Other Pages
- **`qa.html`** — Thin shell embedding the QA chat component; relies on the same `AuthWorkspace` module and fetches `/api/qa/chat`.
- **OCR** — Camera/file/queue flows are embedded in **`index.html`** (not a separate `ocr.html` page).
- **`admin.html`** — Admin dashboard hitting `/api/admin/*` endpoints for model selection and status checks.

## 6. Optional next steps (P10b)
- **`workspace_app.js`** remains large; optional split into ES modules or a bundler (**Vite/Rollup**) — see **`MODULARIZATION_PLAN.md`** and **`ARCHITECTURE_OPTIONS_MORNING_BRIEF.md`**.

Use this index together with **`BACKEND_INDEX.md`** and **`docs/MASTER_PLAN_DREAMCISION.md`** when planning UI work.
