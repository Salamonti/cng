# CNG Backend Index — FastAPI + Services (Live)

> **Generated:** 2026-03-18 · **Source:** `Clinical-Note-Generator/server/`
>
> Scope: FastAPI application (`app.py`), routers under `/api`, supporting services, persistence, and ops touchpoints needed for restructuring.

## 1. Application Entry Point
- **`server/app.py`** wires the FastAPI instance, CORS, middleware logging, metrics, and mounts every router. Static admin assets (`/static/admin.html`) are served via `config/config.json`. Startup hooks call `core.db.init_db()`; shutdown just logs.
- **Metrics:** `server/metrics.py` exposes a CSV-backed collector referenced by routes (`metrics.record_note`, etc.) and the `/api/performance` router.

## 2. Configuration & Secrets
- **Primary config:** `Clinical-Note-Generator/config/config.json` — stores JWT secrets, SQLite paths, llama/OCR/RAG URLs, sampler defaults, and prompt templates. Environment variables override external endpoints (e.g., `NOTEGEN_URL_PRIMARY`, `ASR_URL`, `RAG_URL`).
- **Prompt + preprocessing controls:** `server/core/prompt/builder.py` and `core/preprocessing/*` load `config.json` to build v8 prompts, optionally run regex/whitespace cleanup, and cap sections via `TokenBudgetTruncator`. Placeholders include **`{USER_LOCATION}`**, **`{USER_DISPLAY_NAME}`**, **`{USER_EMAIL}`**; generation may append **`[Profile author]`** to SYSTEM when profile identity is set (`notes.py` + builder).
- **Workspace baseline:** `core/baseline.py` seeds empty `settings/documents/draft/extras` for new users.

## 3. Router Map
| Router File | Prefix | Key Endpoints |
| --- | --- | --- |
| `routes/notes.py` | `/api` | `POST /api/generate_v8_stream` (`#L1448`), `GET /api/generation/{id}/meta`, consult/order background kickoffs, `/api/note_prompts`, `/api/feedback` (dataset logging + consult/order pollers). Uses `_generation_cache` and autostarts consult/order tasks (`notes.py#L1229`).
| `routes/ocr.py` | `/api` | `POST /api/ocr` — forwards images/PDFs to llama OCR client with ffmpeg conversion fallback, enforces auth via `require_api_bearer`.
| `routes/asr.py` | `/api` | `POST /api/transcribe_diarized` (`asr.py#L201`) proxies to whisper.cpp URLs with round-robin/fallback, optional ffmpeg normalization, GET `/api/asr_engine` for health metadata.
| `routes/qa_chat.py` | `/api/qa` | `POST /api/qa/chat` + `/api/qa/chat_stream` integrate RAG (`services/rag_http_client.py`), SearX (`services/qa_web_search.py`), llama completions, and stateful summaries.
| `routes/qa_vision.py` | `/api/qa` | `POST /api/qa/vision` streams medical image answers via `services/vision_qa_client.py`.
| `routes/workspace.py` | `/api/workspace` | GET/PUT `/api/workspace/` (optimistic concurrency) and `POST /api/workspace/clear`. **P5:** workspace row is a **shell** with `extras.activeEncounterId`; clinical state is read/written on the **active** `UserEncounter` (`core/encounter_workspace.py`).
| `routes/encounters.py` | `/api/encounters` | **P5:** list, create, get, patch label, `POST …/{id}/activate`, `DELETE …/{id}` with `{ "confirm": true }`; deletes queued jobs tied to that encounter.
| `routes/queue.py` | `/api/queue` | Upload/download/delete OCR/ASR jobs, plus `/process` to replay them server-side. Uses `data/queue_files/<user>/<job>.bin` storage and deletes server/local queue entries together (`queue.py#L34-L125`).
| `routes/profile.py` | `/api` | `GET/PUT /api/profile`; `GET/PUT /api/note-types`; `POST /api/note-types/custom`; `DELETE /api/note-types/{id}` (custom only); `POST /api/note-types/revert-builtins`, `revert-bulk`; `POST /api/note-types/{id}/revert`. Backed by `core/profile_service.py`, `models/user_preferences.py`. |
| `routes/auth_users.py` | `/api/auth` | Register, login, refresh, me, logout/all. Approvals enforced via `User.is_approved`.
| `routes/admin_users.py` | `/api/admin/users` | Approve/reject/delete users (admin token required).
| `routes/admin.py` | `/api/admin` | Model selection, llama/OCR status checks, config save (`/models/select`, `/llama/status`, `/config/save`).
| `routes/perf.py` | `/api` | `/api/health`, `/api/performance`, `/api/qa_config`.
| `routes/version.py` | `/api` | `/api/version` with git hash/package data.
| `routes/rag_updates.py` | `/api` | `/api/weekly_summary`, `/api/recent_updates` (reads `RAG/recent_updates.json`).

## 4. Service Clients & Background Tasks
- **LLM client:** `services/note_generator_clean.py` manages llama-server streaming, SSE decoding, fallback logic, and context resets (`SimpleNoteGenerator.stream_completion`). Used by notes + QA.
- **RAG client:** `services/rag_http_client.py` builds snippet summaries, normalizes metadata, enforces word caps, and retries with keyword expansion when scores are weak.
- **QA web search:** `services/qa_web_search.py` calls SearXNG and formats results for QA prompt contexts.
- **Vision QA:** `services/vision_qa_client.py` wraps a multimodal llama endpoint for `/api/qa/vision`.
- **OCR client:** `services/ocr_llm_client.py` posts base64 images to a llama-server configured with multimodal weights.
- **Clinical normalizer:** `services/clinical_text_normalizer.py` (numeric normalization) is called post-generation.
- **Consult/order pipelines:** `core/consult/pipeline.py` and `core/order/pipeline.py` run after note streaming via `_maybe_autostart_*` hooks (`notes.py#L1229-L1267`) and persist to `_consult_comment_store` / `_order_request_store` for later polling.

## 5. Persistence & Models
- **Database:** SQLite via SQLModel (`core/db.py`). Tables include `User` (profile columns + auth flags), `UserPreferences` (note template JSON per user), `UserWorkspace`, **`UserEncounter` (P5)**, `RefreshToken`, and `QueuedJob` (with optional **`encounter_id`**). DB path defaults to `data/user_data.sqlite` (`config/config.json`).
- **Workspaces & encounters (P5):** One `UserWorkspace` row per user holds a **shell** `state_json` (pointer `extras.activeEncounterId`). Per-thread clinical state lives in **`user_encounter.state_json`**. Payload size limited to 2 MB per PUT; optimistic lock via workspace `version`. Migration: first login seeds **“Default”** from legacy workspace blob.
- **Queue files:** Binary payloads stored on disk under `data/queue_files/<user>/<job>`; metadata in `QueuedJob` SQLModel; new jobs record **`encounter_id`** for the active encounter.
- **Dataset logging:** `core/logging/dataset_logger.py` appends generation records/events to `data/datasets/cases_YYYY-MM-DD.jsonl` and is invoked at the end of `/api/generate_v8_stream` (`notes.py#L1614-L1665`).

## 6. Observations & Current Constraints
- **Multi-encounter (P5 API + P6 UI):** Server persists **`UserEncounter`** rows and queue **`encounter_id`**. **`PCHost/web/encounters_ui.js`** lists/switches threads; signed-in users use **Encounters** instead of legacy **New Case**.
- **Profile vs workspace:** **`User`** holds **`display_name`**, **`default_specialty`**, **`default_location`**; **`UserPreferences`** holds per-user note template overrides (`templates` / `templates_other`). Workspace **`state_json.extras`** still holds encounter-scoped UI and extra prompt text alongside server-backed defaults.
- **Queue + ASR:** `/api/queue` already supports both OCR and transcription job types and the Node UI exposes retry/clear actions. Streaming ASR would need new endpoints because the current ASR router accepts whole files only.

## 7. Operations & Scripts
- `scripts/create_admin.py` — CLI to insert an admin user into SQLite.
- `start_fastapi_server*.bat` — Windows launchers for uvicorn with/without external binding.
- Logs: `server/logs/*` collects HTTP CSVs, note generator logs, RAG miss logs (`notes.py` writes to `logs/rag_missed_questions.jsonl`).

Refer to `ARCHITECTURE_OPTIONS_MORNING_BRIEF.md` for pending design work (multi-patient workspaces, further prompt-builder unification / P4, ASR streaming) derived from these modules.
