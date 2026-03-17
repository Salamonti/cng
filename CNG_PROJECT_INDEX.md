# CNG (Clinical Note Generator) — Comprehensive Project Index

> **Generated:** 2026-03-17  
> **Purpose:** Project cleanup, maintenance, and developer onboarding reference.  
> **Root:** `/mnt/c/project-root/`

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [System Architecture & Data Flow](#2-system-architecture--data-flow)
3. [Complete File Index](#3-complete-file-index)
4. [Phase 2: Functions & API Endpoints](#4-phase-2-functions--api-endpoints)
   - [PCHost — Node.js Reverse Proxy](#pchost--nodejs-reverse-proxy)
   - [Clinical-Note-Generator — FastAPI Backend](#clinical-note-generator--fastapi-backend)
   - [RAG — Retrieval Pipeline](#rag--retrieval-pipeline)
5. [Phase 3: Deletable / Suspect Files](#5-phase-3-deletable--suspect-files)
6. [Phase 4: Redundant Code Blocks](#6-phase-4-redundant-code-blocks)
7. [Phase 5: Non-Git Files (Do NOT Commit)](#7-phase-5-non-git-files-do-not-commit)
8. [Environment Variables Reference](#8-environment-variables-reference)
9. [Database Schema](#9-database-schema)

---

## 1. Project Overview

CNG is a clinical note-generation web application for healthcare professionals. It accepts audio transcriptions and/or chart data, forwards them to a local large language model (llama-server), and streams back structured clinical notes. Secondary features include:

- **OCR** — extract text from uploaded PDFs/images via a vision-capable LLM.
- **ASR** — speech-to-text via whisper.cpp server (two-node round-robin with fallback).
- **RAG Q&A** — retrieval-augmented clinical Q&A using a ChromaDB vector store served by a dedicated FastAPI service.
- **Vision Q&A** — medical image analysis via vision-capable LLM.
- **Queue** — offline-resilient file queuing for OCR/ASR jobs.
- **De-identification** — regex + spaCy NER layer strips names/dates/MRNs before dataset logging.
- **Dataset logging** — every generated note and feedback event is appended to daily JSONL files for future fine-tuning.

### Technology Stack

| Layer | Technology |
|---|---|
| Reverse proxy / gateway | Node.js (Express) + http-proxy-middleware |
| Backend API | Python FastAPI + Uvicorn |
| LLM inference | llama-server (llama.cpp) — external process |
| Speech recognition | whisper.cpp server — external process |
| OCR | Same llama-server or separate vision LLM |
| RAG vector store | ChromaDB (persistent) |
| RAG embeddings | sentence-transformers (GIST-small) |
| Database | SQLite via SQLModel/SQLAlchemy |
| Web frontend | Vanilla JS + HTML/CSS (served as static files) |
| TLS | Let's Encrypt certs — served by Node.js PCHost |

---

## 2. System Architecture & Data Flow

```
Browser (HTTPS)
    │
    ▼
PCHost/server.js  (:3443 HTTPS / :3000 HTTP)
  ├─ Serves static files from PCHost/web/
  ├─ /api/*       → proxy → FastAPI :7860
  ├─ /admin/*     → proxy → FastAPI :7860
  ├─ /whisperx    → proxy → FastAPI :7860 /api/transcribe_diarized
  ├─ /ocr         → proxy → FastAPI :7860 /api/ocr
  ├─ /llama/generate → proxy → llama-gateway :7871
  └─ /health      → local response
    │
    ▼
Clinical-Note-Generator/server/app.py  (:7860)
  ├─ POST /api/generate_v8_stream   → note generation (streaming)
  ├─ POST /api/ocr                  → image/PDF OCR
  ├─ POST /api/transcribe_diarized  → ASR proxy
  ├─ GET  /api/generation/{id}/meta → note metadata + RAG refs
  ├─ GET  /api/generation/{id}/consult_comment  → RAG-based consult addendum
  ├─ GET  /api/generation/{id}/order_requests   → LLM-extracted order items
  ├─ POST /api/qa/chat              → Q&A with RAG + web search
  ├─ POST /api/qa/vision            → medical image Q&A (streaming)
  ├─ POST /api/auth/*               → JWT auth
  ├─ GET/PUT /api/workspace/        → user workspace sync
  ├─ POST/GET /api/queue/*          → offline job queue
  ├─ GET  /api/health               → health check
  ├─ GET  /api/performance          → Prometheus-style metrics
  └─ GET/POST /api/admin/*          → admin dashboard
    │           │
    ├───────────▼
    │     llama-server :8081
    │     (note generation, OCR, vision QA)
    │
    ├───────────▼
    │     whisper.cpp :8095 / :8096
    │     (ASR transcription)
    │
    └───────────▼
          RAG query_api.py :8007
          (ChromaDB vector store + BM25 hybrid retrieval)
```

### Request Flows

**Note Generation (primary path):**
1. Browser POSTs to `PCHost /api/generate_v8_stream` (JSON: transcription_text, old_visits_text, mixed_other_text, note_type).
2. PCHost proxies to FastAPI `/api/generate_v8_stream`.
3. FastAPI builds prompt via `core/prompt/builder.py::build_prompt_v8`.
4. FastAPI streams tokens from llama-server via `services/note_generator_clean.py::SimpleNoteGenerator.stream_completion`.
5. Chunks are cleaned by `clean_model_output_chunk` (Unicode → ASCII EMR safe).
6. After stream ends: background tasks fire for consult comment + order request extraction.
7. FastAPI returns `StreamingResponse` with `X-Generation-Id` header.
8. Browser uses generation ID to poll `/api/generation/{id}/consult_comment` and `/api/generation/{id}/order_requests`.

**ASR Transcription:**
1. Browser POSTs audio to `PCHost /whisperx`.
2. PCHost rewrites to FastAPI `/api/transcribe_diarized`.
3. FastAPI normalizes audio to WAV via ffmpeg (if enabled).
4. FastAPI round-robin proxies to whisper.cpp :8095 / :8096 with fallback/cooldown logic.
5. Returns plain-text transcript.

**OCR:**
1. Browser POSTs image/PDF to `PCHost /ocr`.
2. PCHost rewrites to FastAPI `/api/ocr`.
3. FastAPI renders PDF pages to PNG via PyMuPDF, or normalizes image.
4. Calls `services/ocr_llm_client.py::OCRLLMEngine.ocr_image_bytes`.
5. OCRLLMEngine posts to llama-server `/v1/chat/completions` with image in base64.

**RAG Q&A:**
1. Browser POSTs to `/api/qa/chat` with message + session_id.
2. FastAPI fires parallel tasks: RAG query (via `services/rag_http_client.py`) + SearXNG web search (via `services/qa_web_search.py`).
3. Combined evidence → prompt → LLM → answer.
4. Response includes source references and de-identification counts.

---

## 3. Complete File Index

### `Clinical-Note-Generator/` — Main Application

```
server/
├── app.py                              FastAPI application factory
├── metrics.py                          HTTP + GPU VRAM + note metrics
│
├── routes/
│   ├── notes.py                        Note generation endpoints + helpers (LARGE file)
│   ├── ocr.py                          OCR endpoint
│   ├── asr.py                          ASR proxy endpoint
│   ├── qa_chat.py                      Q&A chat endpoint (RAG + web search)
│   ├── qa_vision.py                    Medical image Q&A endpoint
│   ├── workspace.py                    User workspace CRUD
│   ├── queue.py                        Offline job queue management
│   ├── auth_users.py                   JWT auth (register/login/refresh/logout)
│   ├── admin.py                        Admin dashboard (config, services, models)
│   ├── admin_users.py                  Admin user management
│   ├── rag_updates.py                  RAG weekly summary + recent updates
│   ├── perf.py                         /health, /performance, /qa_config
│   └── version.py                      /version endpoint
│
├── services/
│   ├── note_generator_clean.py         SimpleNoteGenerator — llama-server async client
│   ├── asr_whisperx.py                 WhisperX in-process ASR engine (legacy/dev)
│   ├── ocr_llm_client.py               OCRLLMEngine — vision LLM client
│   ├── rag_http_client.py              RAGHttpClient — async RAG service client
│   ├── qa_web_search.py                SearXNG web search with domain allowlist
│   ├── vision_qa_client.py             VisionQAEngine — streaming medical image QA
│   └── clinical_text_normalizer.py     Number-word→numeral + RxNorm canonicalization
│
├── core/
│   ├── app.py → (N/A — entrypoint is server/app.py)
│   ├── config.py                       Settings (JWT, DB URL) via Pydantic
│   ├── db.py                           SQLite engine + init_db()
│   ├── security.py                     JWT encode/decode + PBKDF2 password hashing
│   ├── dependencies.py                 FastAPI deps: require_api_bearer, get_current_user
│   ├── env.py                          Load .env file (dotenv or fallback parser)
│   ├── baseline.py                     Default workspace state factory
│   │
│   ├── prompt/
│   │   └── builder.py                  build_prompt_v8, build_prompt_other, build_note_prompt_legacy
│   │
│   ├── stores/
│   │   ├── generation_store.py         TTLStore instances for generation cache/meta
│   │   └── ttl_store.py                Generic thread-safe TTL dict (24h default)
│   │
│   ├── streaming/
│   │   └── helpers.py                  _stream_response, _stream_response_v8, _stream_qa_response
│   │
│   ├── consult/
│   │   └── pipeline.py                 _generate_consult_comment — RAG-based addendum generator
│   │
│   ├── order/
│   │   └── pipeline.py                 _generate_order_requests — LLM order extraction
│   │
│   ├── deid/
│   │   ├── __init__.py
│   │   ├── v1.py                       Regex + spaCy NER de-identification
│   │   └── ner_spacy.py                spaCy PERSON entity redaction layer
│   │
│   ├── qa_rag/
│   │   └── helpers.py                  _qa_rewrite_with_rag — baseline + RAG rewrite logic
│   │
│   ├── logging/
│   │   ├── __init__.py
│   │   └── dataset_logger.py           log_case_record + log_case_event → JSONL files
│   │
│   └── preprocessing/
│       ├── __init__.py
│       ├── pipeline.py                 PreprocessingPipeline (boilerplate removal, dedup)
│       ├── constants.py                Regex patterns for preprocessing
│       └── truncation.py               TokenBudgetTruncator
│
├── models/
│   ├── user.py                         User SQLModel
│   ├── workspace.py                    UserWorkspace SQLModel (JSON state)
│   ├── refresh_token.py                RefreshToken SQLModel
│   └── queued_job.py                   QueuedJob SQLModel
│
├── schemas/
│   ├── auth.py                         Pydantic schemas: LoginRequest, RegisterRequest, TokenResponse
│   ├── workspace.py                    WorkspacePayload, WorkspaceResponse
│   └── queue.py                        QueuedJobCreate, QueuedJobResponse
│
└── tests/
    ├── conftest.py
    ├── test_smoke_auth.py
    ├── test_smoke_note_ocr_qa.py
    ├── test_smoke_queue.py
    ├── test_smoke_version.py
    ├── test_notes_modular.py
    ├── test_asr_proxy.py
    ├── test_clinical_text_normalizer.py
    ├── test_deid_ner_optional.py
    ├── test_phase1_dataset_logging.py
    ├── test_preprocessing.py
    ├── test_qa_chat_stream.py
    ├── test_stores.py
    ├── test_stores_integration.py
    └── test_ttl_perf.py

config/
└── config.json                         Runtime config (secrets — NOT in git)

data/
├── user_data.sqlite                    User/auth database (NOT in git)
└── datasets/
    └── cases_YYYY-MM-DD.jsonl          Daily de-identified note logs (NOT in git)

docs/
├── CNG_PROJECT_HANDOFF.md
├── ENV_VARIABLES.md
├── EXTERNAL_SERVERS_SETUP.md
├── INSTALL.md
├── changes_2026-02-06_order_requests.md
├── regression_checklist.md
└── prompt-optimization/
    ├── handbook/PROMPT_OPTIMIZATION_HANDBOOK.md
    └── prompts/
        ├── prompt_final.txt
        └── prompt_v8_stability.txt

scripts/
└── create_admin.py                     One-off admin user creation script

start_fastapi_server.bat                Windows batch launcher
start_fastapi_server_external.bat       External-binding batch launcher
requirements.txt
```

### `PCHost/` — Node.js Gateway

```
server.js                               Main Express server (proxy + static + TLS)
llama-gateway.js                        Separate llama-gateway process (port 7871)
openwebui-proxy.js                      OpenWebUI proxy (port 8443)
package.json
package-lock.json
New_Main_Server.bat                     Windows launcher batch
start-openwebui-proxy.bat

config/
├── server_config.json                  Runtime config (ports, domain, SSL paths, backend URL)
└── server_config.linux.json            Linux variant

web/
├── index.html                          Main note generation UI
├── admin.html                          Admin dashboard UI
├── qa.html                             Q&A UI
├── ocr.html                            OCR UI
├── auth_debug.html                     Auth debugging page
├── scripts.js                          OCR page JS class (OCRProcessor)
├── generate_ui_flow.js                 Note generation UI flow helpers (CNGGenerateUI)
├── auth_workspace.js                   Auth + workspace sync JS module
├── audio_ui_utils.js                   Audio recording UI utilities
├── universal_audio_handler.js          Cross-browser audio recording + streaming
├── markdown_renderer.js                Markdown → HTML renderer for notes
├── service_worker.js                   PWA service worker
├── manifest.json                       PWA manifest
└── styles.css                          Global styles
```

### `RAG/` — Retrieval Pipeline

```
query_api.py                            FastAPI /query endpoint + hybrid search
retriever.py                            Low-level hybrid search (dense + BM25)
store.py                                ChromaDB client factory (singleton)
embedder.py                             SentenceTransformer embedding engine
chunker.py                              Text chunking + basic de-identification
bm25_index.py                           BM25Helper (Okapi BM25 over collection)
chunking_pipeline.py                    Full chunking pipeline
embed_chunks.py                         Batch embedding script
update_index.py                         Upsert + prune ChromaDB index
vector_store_manager.py                 CLI for ingest/count/search
version_manager.py                      Document versioning + staleness management
sources_config.py                       Medical source domains + filter config
sources_config.yaml                     YAML export of sources config
fetch_sources.py                        Source fetching orchestration
guidelines_fetcher.py                   Guideline PDF fetching
pmc_fetcher.py                          PubMed Central article fetcher
composer.py                             Context composition utilities
process_clinical_corpus.py              Clinical corpus processing
summarize_recent_updates.py             Generate recent_updates.json for RAG dashboard
metrics.py                              RequestMetrics (CSV logging)
log_utils.py                            Logging utilities
utils_meta.py                           Metadata normalization utilities
settings.yaml                           RAG configuration (persist_directory, model, etc.)
requirements.txt
start_rag_service.bat

logs/
└── request_metrics.csv

scripts/
├── clean_corpus/
│   └── local_guidelines.clean.json
├── fetch_aasm_pdfs.py
├── fetch_site_pdfs.py
├── import_dpd.py
├── import_local_pdfs.py
├── import_spl.py
├── print_site_pages_to_pdf.py
├── process_spl_drugs.py
├── systemd/
│   ├── ragapi.service
│   └── ragapi.service.example
├── weekly_run.cmd
├── weekly_run.ps1
└── weekly_run.sh

server/services/
└── rag_client.py                       (Legacy?) RAG client used by older paths
```

### `certs/` — TLS Certificates (NOT in git)

```
certs/ieissa/
├── README
├── cert.pem
├── chain.pem
├── fullchain.pem
└── privkey.pem
```

### `startup/` — PowerShell Scripts

```
startup/
├── start-cloudflared.ps1               Start Cloudflare tunnel daemon
└── start-office-stack.ps1              Start full stack (llama-server, FastAPI, PCHost, RAG)
```

---

## 4. Phase 2: Functions & API Endpoints

### PCHost — Node.js Reverse Proxy

**File: `PCHost/server.js`**

| Symbol | Type | Purpose |
|---|---|---|
| `app` | Express app | Main HTTP/HTTPS server |
| `proxyCommon` | Config object | Proxy settings for FastAPI backend |
| `llamaProxyCommon` | Config object | Proxy settings for llama-gateway :7871 |
| `openwebuiProxyCommon` | Config object | Proxy settings for OpenWebUI |

**Routes:**

| Method | Path | Proxied To | Description |
|---|---|---|---|
| `GET` | `/health` | (local) | Node health check |
| `GET` | `/fastapi-check` | FastAPI `/api/health` | Direct connectivity test |
| `ALL` | `/whisperx` | FastAPI `/api/transcribe_diarized` | ASR proxy (path rewritten) |
| `ALL` | `/ocr` | FastAPI `/api/ocr` | OCR proxy (path rewritten) |
| `ALL` | `/llama/generate` | llama-gateway `/api/generate` | LLM generate proxy |
| `ALL` | `/llama/check` | llama-gateway `/api/check` | LLM health proxy |
| `ALL` | `/api/*` | FastAPI `/api/*` | Full API namespace proxy |
| `ALL` | `/admin/*` | FastAPI `/admin/*` | Admin namespace proxy |
| `GET` | `/` | (static) | Serves `index.html` |
| `GET` | `/qa` | (static) | Serves `qa.html` |
| `GET` | `*` | (static) | SPA fallback to `index.html` |

**File: `PCHost/llama-gateway.js`**

Separate process serving llama-gateway on port 7871. Routes `/api/generate` and `/api/check` to internal llama-server.

**File: `PCHost/openwebui-proxy.js`**

Proxies OpenWebUI on port 8443 with WebSocket support.

---

### Clinical-Note-Generator — FastAPI Backend

#### `server/app.py` — Application Factory

| Symbol | Purpose |
|---|---|
| `app` | FastAPI instance with CORS, HTTP logging middleware |
| `http_logger` | Middleware: records request duration, sizes, status codes to Metrics |
| `_load_cfg()` | Loads `config/config.json` to determine `web_dir` for static files |
| `startup_event()` | Calls `init_db()` on startup |
| `shutdown_event()` | Graceful shutdown log |
| `root()` | Redirects `/` to `/static/admin.html` |

**Router mounts:**

| Router | Prefix | Auth |
|---|---|---|
| `ocr_router` | `/api` | `require_api_bearer` |
| `asr_router` | `/api` | per-route `require_api_bearer` |
| `notes_router` | `/api` | `require_api_bearer` |
| `rag_router` | `/api` | `require_api_bearer` |
| `qa_chat_router` | `/api` | `require_api_bearer` |
| `qa_vision_router` | `/api` | `require_api_bearer` |
| `perf_router` | `/api` | Open |
| `version_router` | `/api` | Open |
| `auth_router` | (no prefix — uses `/api/auth` internally) | Open |
| `workspace_router` | (uses `/api/workspace` internally) | `get_current_user` |
| `admin_users_router` | (uses `/api/admin/users` internally) | `get_current_admin` |
| `admin_router` | (uses `/api/admin` internally) | `get_current_admin` |
| `queue_router` | (uses `/api/queue` internally) | `get_current_user` |

---

#### `server/routes/notes.py` — Note Generation

**Core API Endpoints:**

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/api/generate_v8_stream` | Bearer | Primary streaming note generation (3-field input) |
| `POST` | `/api/feedback` | Bearer | Record thumbs up/down for a generation |
| `GET` | `/api/note_prompts` | Bearer | Fetch default note templates from config |
| `GET` | `/api/generation/{gen_id}/meta` | Bearer | Generation metadata (RAG refs, QA status) |
| `GET` | `/api/generation/{gen_id}/consult_comment` | Bearer | RAG-grounded consult addendum |
| `GET` | `/api/generation/{gen_id}/order_requests` | Bearer | LLM-extracted order/referral items |

**Key Functions:**

| Function | Purpose |
|---|---|
| `generate_v8_stream()` | Main endpoint: builds prompt, streams from llama-server, fires background tasks |
| `generate_stream()` | **Legacy endpoint** — not routed (kept for compatibility), handles legacy 2-field input |
| `generate_v8()` | Non-streaming version of v8 (not routed — internal utility) |
| `build_prompt_v8()` | Wrapper → delegates to `core/prompt/builder.py` |
| `build_prompt_other()` | Wrapper for non-SOAP note types |
| `build_note_prompt_legacy()` | Wrapper for legacy 2-field prompt |
| `build_qa_prompt()` | QA-specific prompt builder (model knowledge only, no RAG) |
| `clean_model_output_chunk()` | Stream-safe cleaner: Unicode→ASCII, removes XML tags |
| `clean_model_output_final()` | Post-stream cleaner: removes think blocks, markdown, normalizes paragraphs |
| `_sanitize_chart_text()` | Removes control chars and format symbols from chart input |
| `_sanitize_transcription_text()` | Lighter sanitizer for transcription input |
| `_deid_fields()` | De-identify dict of text fields, aggregate redaction counts |
| `_log_case_completion()` | Append de-identified case record to daily JSONL |
| `_extract_actor()` | Extract user_id + user_email from JWT for logging |
| `_rag_client_from_cfg()` | Create RAGHttpClient from config/env |
| `_gather_rag_for_qa()` | Async RAG query for QA endpoint (with timeout) |
| `_generate_consult_comment()` | Thin wrapper → `core/consult/pipeline._generate_consult_comment_impl` |
| `_generate_order_requests()` | Thin wrapper → `core/order/pipeline._generate_order_requests_impl` |
| `_maybe_autostart_consult_comment()` | Background trigger for consult addendum after note generation |
| `_maybe_autostart_order_requests()` | Background trigger for order extraction after note generation |
| `_normalize_reference_items()` | Flatten RAG results into UI-friendly reference entries |
| `_chunk_text_for_stream()` | Split completed QA text into stream-safe chunks |
| `_extract_plan_section()` | Isolate Plan section from generated note |
| `_normalize_request_items()` | Validate and sanitize order item dicts |
| `_merge_medication_items()` | Collapse multiple medication items into one deduplicated block |
| `_dedupe_request_items()` | Remove duplicate order items; collapse lab lines |
| `truncate_to_context_length_tokens()` | Hard word-count cap on text (1:1 word:token) |
| `_model_meta()` | Describe active LLM endpoint/model for logging |

---

#### `server/routes/asr.py` — ASR Proxy

**Endpoints:**

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/api/transcribe_diarized` | Bearer | Proxy audio to whisper.cpp servers |
| `GET` | `/api/asr_engine` | Bearer | Probe ASR server info |

**Key Functions:**

| Function | Purpose |
|---|---|
| `_candidate_urls()` | Round-robin URL selection with primary-down cooldown |
| `_normalize_audio_to_wav()` | Convert any audio format to 16kHz mono WAV via ffmpeg |
| `_extract_whisper_text()` | Parse whisper.cpp JSON response (multiple field variants) |
| `_mark_primary_down()` | Set 20s cooldown on primary ASR after failure |
| `_infer_file_suffix()` | Detect audio format from content-type/filename |

---

#### `server/routes/ocr.py` — OCR

**Endpoints:**

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/api/ocr` | Bearer | Process image or multi-page PDF |

**Key Functions:**

| Function | Purpose |
|---|---|
| `ocr()` | Main handler: routes PDF vs image, calls OCRLLMEngine |
| `_pdf_first_page_to_png_bytes()` | Render first PDF page to PNG bytes via PyMuPDF |
| `_pdf_extract_text_first()` | Attempt selectable text extraction before rasterization |
| `_downscale_if_needed()` | Resize image to max dimension for LLM compatibility |
| `_get_ocr_client()` | Singleton factory for OCRLLMEngine (URL-based caching) |

---

#### `server/routes/auth_users.py` — Authentication

**Endpoints:**

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/api/auth/register` | Open | Register new user (unapproved) |
| `POST` | `/api/auth/login` | Open | Login, get JWT access + refresh tokens |
| `GET` | `/api/auth/me` | Bearer | Get current user profile |
| `POST` | `/api/auth/refresh` | Open | Refresh access token via refresh token |
| `POST` | `/api/auth/logout` | Open | Revoke current refresh token |
| `POST` | `/api/auth/logout_all` | Bearer | Revoke all refresh tokens for user |

---

#### `server/routes/workspace.py` — Workspace Sync

**Endpoints:**

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/api/workspace/` | Bearer | Get user workspace state |
| `PUT` | `/api/workspace/` | Bearer | Update workspace state (version-checked) |
| `POST` | `/api/workspace/clear` | Bearer | Clear all workspace content |

**State conflict resolution:** Version mismatch returns 409 with server state. Smart merge prevents stale client from wiping ASR results unless `transcriptionCleared` flag is set.

---

#### `server/routes/queue.py` — Offline Job Queue

**Endpoints:**

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/api/queue` | Bearer | Create queued job (upload file) |
| `GET` | `/api/queue` | Bearer | List user's queued jobs |
| `DELETE` | `/api/queue/{job_id}` | Bearer | Delete a specific job |
| `POST` | `/api/queue/{job_id}/retry` | Bearer | Reset failed job to pending |
| `GET` | `/api/queue/{job_id}/download` | Bearer | Download stored file |
| `POST` | `/api/queue/{job_id}/process` | Bearer | Process job server-side (OCR or transcribe) |
| `DELETE` | `/api/queue` | Bearer | Clear all user's queued jobs |

---

#### `server/routes/admin.py` — Admin Dashboard

**Endpoints (all require admin token):**

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/admin/logs/tail` | Tail server log file |
| `GET` | `/api/admin/models` | List available LLM + whisper models |
| `POST` | `/api/admin/models/select` | Set active LLM/whisper model in config |
| `POST` | `/api/admin/models/parameters` | Update model parameters in config |
| `GET` | `/api/admin/config` | Read full config.json |
| `POST` | `/api/admin/config/save` | Write full config.json |
| `GET` | `/api/admin/ocr/status` | Probe OCR server health + models |
| `GET` | `/api/admin/llama/status` | Probe llama-server health + active model |
| `GET` | `/api/admin/llama/health` | Internal manager health (returns "externalized") |
| `POST` | `/api/admin/llama/start` | Start llama (disabled — externalized) |
| `POST` | `/api/admin/llama/stop` | Stop llama (disabled — externalized) |
| `POST` | `/api/admin/llama/restart` | Restart llama (disabled — externalized) |
| `GET` | `/api/admin/services/status` | All services port + Windows service status |
| `GET` | `/api/admin/rag/status` | RAG service health check |

---

#### `server/routes/admin_users.py` — User Management

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/admin/users` | List all users |
| `PATCH` | `/api/admin/users/{id}/approve` | Approve user |
| `PATCH` | `/api/admin/users/{id}/reject` | Unapprove user |
| `DELETE` | `/api/admin/users/{id}` | Delete user (cascades tokens + workspace) |

---

#### `server/routes/qa_chat.py` — Q&A Chat

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/api/qa/chat` | Bearer | Single-turn Q&A with RAG + web search (non-streaming) |
| `POST` | `/api/qa/chat_stream` | Bearer | Streaming version; sends `__QA_META__` JSON at end |

**In-memory state:** `_QA_STATE` dict keyed by `(user_id, session_id)` holds conversation turns + summary (up to 12 turns).

---

#### `server/routes/qa_vision.py` — Vision Q&A

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/api/qa/vision` | Bearer | Upload image + question, stream medical analysis |

---

#### `server/routes/rag_updates.py` — RAG Status

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/api/rag/weekly_summary` | Bearer | RAG ingestion summary for last 7 days |
| `GET` | `/api/rag/recent_updates` | Bearer | Recent corpus updates (from `recent_updates.json`) |

---

#### `server/routes/perf.py` — Observability

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/api/health` | Open | Server uptime |
| `GET` | `/api/performance` | Open | Route stats, GPU VRAM, tokens/sec |
| `GET` | `/api/qa_config` | Open | QA max chars, default tokens |

---

#### `server/routes/version.py`

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/version` | Git commit hash, build timestamp, Python/FastAPI/Uvicorn versions |

---

#### `server/services/note_generator_clean.py` — LLM Client

**Class: `SimpleNoteGenerator`**

| Method | Purpose |
|---|---|
| `__init__()` | Load config, resolve URLs from env (NOTEGEN_URL_PRIMARY / FALLBACK) |
| `reload_config()` | Hot-reload config without restart |
| `stream_completion()` | Async generator: stream tokens from llama-server SSE |
| `collect_completion()` | Collect full response as string (with chat→completion fallback) |
| `_build_payload()` | Route to chat vs completion payload builder |
| `_build_chat_payload()` | Build `/v1/chat/completions` payload |
| `_build_completion_payload()` | Build `/completion` payload |
| `_candidate_urls()` | URL list with primary-down cooldown |
| `_mark_primary_down()` | Set 20s cooldown |
| `_reset_context()` | POST `/command {"cmd":"reset"}` to free KV cache |
| `_extract_stream_content()` | Parse content from various llama-server response shapes |

**Class: `ExternalServiceError`** — raised when all URL candidates fail; carries `service`, `primary_url`, `fallback_url`, `errors` list.

**Singleton:** `get_simple_note_generator()` returns cached `SimpleNoteGenerator` instance.

---

#### `server/services/asr_whisperx.py` — In-Process WhisperX Engine

> **Note:** This is the **old in-process ASR engine** using WhisperX directly with CUDA. The current production path uses the **whisper.cpp HTTP proxy** in `routes/asr.py`. This file is retained for local dev/testing.

**Class: `WhisperXASREngine`**

| Method | Purpose |
|---|---|
| `warmup()` | Load all models eagerly |
| `new_session()` | Create audio session |
| `append_chunk()` | Append audio bytes |
| `transcribe()` | Finalize and transcribe, return (text, confidence) |
| `transcribe_stream()` | Iterator of formatted transcript lines |
| `cleanup_session()` | Delete temp files |
| `get_info()` | Return engine metadata dict |

**Class: `PassthroughVAD`** — Replaces whisperx Pyannote VAD with a simple 30s chunker (avoids HuggingFace token requirement).

---

#### `server/services/ocr_llm_client.py` — OCR LLM Client

**Class: `OCRLLMEngine`**

| Method | Purpose |
|---|---|
| `ocr_image_bytes()` | POST image to llama-server `/v1/chat/completions`, return (text, confidence) |
| `_resolve_model_id()` | Auto-discover vision model from `/v1/models` |
| `_discover_vision_models()` | Query `/v1/models`, filter by vision-related keywords |
| `_estimate_confidence()` | Heuristic confidence from word count + medical content signals |
| `_flush_server_context()` | POST `/command {"cmd":"reset"}` after OCR |

---

#### `server/services/rag_http_client.py` — RAG Client

**Class: `RAGHttpClient`**

| Method | Purpose |
|---|---|
| `query()` | POST to `/query`, compose context, normalize metadata; auto-fallback broadens keywords if evidence is weak |
| `_compose_context()` | Build numbered snippet text block for LLM consumption |
| `_normalize_meta()` | Harmonize metadata field names (title, source, link, year, section) |
| `_snippet()` | Sentence-based text truncation |
| `_weak()` | Heuristic: evidence is weak if mean score < 0.12 or total text < 40 words |

---

#### `server/services/qa_web_search.py` — Web Search

**Function: `searx_search(query, limit=8)`**

- Tries SearXNG at: `localhost:8083/search` → `SEARXNG_URL` env → fallbacks.
- Filters results by domain allowlist (PubMed, NEJM, JAMA, Lancet, FDA, CDC, etc.).
- Falls back to semi-allowlist (`.gov`, `.edu`, `nih`, `pubmed`, etc.) if strict allowlist yields zero.

---

#### `server/services/vision_qa_client.py` — Vision QA

**Class: `VisionQAEngine`**

Similar structure to `OCRLLMEngine` but:
- Uses streaming (`stream=True`) via SSE.
- Builds a structured medical Q&A prompt with disclaimers.
- `stream_vision_answer()` — async generator yielding answer tokens.

---

#### `server/services/clinical_text_normalizer.py` — Text Normalization

| Symbol | Purpose |
|---|---|
| `normalize_numeric_units()` | Convert spelled-out dose numbers to numerals (e.g., "five mg" → "5 mg") |
| `canonicalize_medication_lines()` | Match medication names against RxNorm RXNCONSO.RRF (confidence-gated) |
| `normalize_clinical_note_output()` | Combined normalization returning `NormalizationResult` |
| `RxNormIndex` | Lazy loader for RxNorm terms; thread-safe; only active if `RXNORM_DIR` env is set |

---

#### `server/core/prompt/builder.py` — Prompt Construction

| Function | Purpose |
|---|---|
| `build_prompt_v8()` | Primary note prompt: structures 3 fields with XML section tags + templates from config |
| `build_prompt_other()` | Non-SOAP note types (referral, summarize, custom, procedure) |
| `build_note_prompt_legacy()` | Legacy 2-field format (chart_data + transcription) |
| `_apply_preprocessing()` | Apply `PreprocessingPipeline` + `TokenBudgetTruncator` when enabled in config |
| `_fill_template()` | String `{KEY}` replacement |
| `_cfg_text()` | Normalize config value (string/list → string) |

**Prompt Structure (v8):**
```
SYSTEM:
{system_prompt from config}

USER:
{note_type template from config}

PATIENT DATA:
<CURRENT_ENCOUNTER>
DATE: {today}
{transcription_text}
</CURRENT_ENCOUNTER>

<PRIOR_VISITS>
{old_visits_text}
</PRIOR_VISITS>

<LABS_IMAGING_OTHER>
{mixed_other_text}
</LABS_IMAGING_OTHER>

ADDITIONAL INSTRUCTIONS: (if custom_prompt)

STYLE REQUIREMENTS: (numeric unit instruction)

ASSISTANT:
```

---

#### `server/core/deid/v1.py` — De-identification

**Function: `deidentify_text(text)`** → returns `{"text": redacted, "redaction_counts": {...}, "leak_flags": {...}}`

Patterns applied (in order):
1. `name_comma_age` — "Lastname, 52-year-old"
2. `name_sentence_verb` — "Gregory reports/states/presents..."
3. `name_doctor` — "Dr. Smith"
4. `name_labeled` — "Patient: John Smith"
5. `date` — ISO/slash/spelled dates
6. `mrn` — MRN/HCN/PHN/Chart ID
7. `phone` — North American phone numbers
8. `email` — Email addresses
9. **NER layer** — spaCy `en_core_web_sm` PERSON entities (via `ner_spacy.py`)

---

#### `server/core/consult/pipeline.py` — Consult Addendum

**Function: `_generate_consult_comment(gen_id, note_text, cfg, ...)`**

Flow:
1. Extract Impression + Plan from note.
2. Identify confirmed + ruled-out statements via configurable markers.
3. Build RAG query (strategies: `sections` | `full_note` | `llm_query`).
4. POST to RAG service, get evidence context.
5. Generate structured comment with 5 required sections.
6. If structure missing, retry with explicit headers.
7. If "insufficient evidence" in output AND context available, retry without refusal.
8. Store result in `_consult_comment_store[gen_id]`.

---

#### `server/core/order/pipeline.py` — Order Extraction

**Function: `_generate_order_requests(gen_id, note_text, cfg, ...)`**

Flow:
1. Extract Plan section.
2. LLM call: detect order items as JSON `{items: [{category, title, need_full_note, use_referral_prompt}]}`.
3. For each item, LLM generates copy-ready requisition text (category-specific prompts).
4. Imaging items use structured requisition format; referrals use system/referral prompts from config.
5. De-duplicate + merge medications.
6. Store result in `_order_request_store[gen_id]`.

---

#### `server/core/stores/`

**`TTLStore`** — Generic thread-safe dict with 24h TTL. Dict-like interface (`__setitem__`, `__getitem__`, `__contains__`, `evict_expired()`).

**`generation_store.py`** — Creates four TTLStore instances:
- `_generation_cache` — prompt + output text for feedback
- `_generation_meta` — RAG refs, QA status, context
- `_consult_comment_store` — consult addendum result
- `_order_request_store` — order extraction result

---

#### `server/core/qa_rag/helpers.py` — QA RAG Rewrite

**Function: `_qa_rewrite_with_rag(baseline_text, ...)`**

1. Await RAG task result.
2. If RAG context sufficient (≥ `qa_rag_min_context_chars` chars), generate rewrite prompt.
3. Collect rewritten answer via LLM.
4. If rewrite differs from baseline → prepend enhancement label.
5. Log "missed question" if evidence is weak and no RAG results.

---

#### `server/core/logging/dataset_logger.py`

**Functions:**
- `log_case_record(record)` → appends to `data/datasets/cases_YYYY-MM-DD.jsonl`
- `log_case_event(event)` → appends to `data/datasets/case_events_YYYY-MM-DD.jsonl`

All writes are thread-safe via `_append_lock`.

---

#### `server/core/preprocessing/pipeline.py`

**Class: `PreprocessingPipeline`** (disabled by default via config `preprocessing.enabled: false`)

| Method | Purpose |
|---|---|
| `remove_boilerplate()` | Strip date-only lines, separator lines, known boilerplate patterns |
| `collapse_repeated_headers()` | Remove duplicate section headers within a window |
| `remove_junk_artifacts()` | Remove lines matching junk patterns |
| `deduplicate_near_identical_blocks()` | Remove near-identical paragraphs within a window |
| `normalize_whitespace()` | Normalize tabs, trailing spaces, multiple newlines |

---

#### `server/metrics.py`

**Class: `Metrics`**

| Method | Purpose |
|---|---|
| `record_http()` | Track per-route request stats + append to CSV |
| `record_note()` | Track note generation duration + tokens |
| `record_ocr()` | Track OCR duration + confidence |
| `snapshot()` | Return dict with uptime, route stats, GPU VRAM, tokens/sec |
| `inc_active() / dec_active()` | Track concurrent requests |
| `_collect_gpu_stats()` | Query NVML for GPU memory usage |

---

### RAG — Retrieval Pipeline

#### `RAG/query_api.py` — Query Service

**FastAPI app** running on port 8007.

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | ChromaDB connectivity check |
| `POST` | `/query` | Hybrid search with caching, filters, summarization |

**Key Functions:**

| Function | Purpose |
|---|---|
| `hybrid_search_filtered()` | Dense cosine + BM25 hybrid search with recency/authority boosts |
| `extract_keywords()` | Extract non-stopword keywords from query |
| `summarize_chunk()` | Extractive summarization of long chunks |
| `_package()` | Build context block + references list for downstream |
| `_summarize_hits()` | Aggregate stats (score mean/median, year range, specialty breakdown) |
| `_query_cache_key()` | Deterministic cache key (query, top_k, filters, corpus_version) |

**Scoring formula:**
```
sim = 1 - cosine_distance
hybrid = sim * (1 - hybrid_lambda) + bm25_norm * hybrid_lambda
final_score = hybrid * (1 - keyword_overlap_lambda) + overlap * keyword_overlap_lambda
final_score += recency_boost (≥2022: +0.025, ≥2018: +0.01)
final_score += authority_boost (+0.02 for guideline sources)
```

If `use_rrf: true` in settings, RRF (Reciprocal Rank Fusion) replaces the above.

---

#### `RAG/retriever.py` — Low-Level Search

Parallel execution of BM25 + vector search via `ThreadPoolExecutor`. Used by `VectorStoreManager` CLI. The `query_api.py` uses its own inline implementation.

---

#### `RAG/embedder.py` — Embedding Engine

**Class: `Embedder`**

- Uses `sentence-transformers` with model from `settings.yaml` (default: `avsolatorio/GIST-small-Embedding-v0`).
- Caches model in process memory.
- Single-text LRU cache for `_cached_single_embedding`.
- Supports Matryoshka dimension truncation via `EMBEDDER_DIM` env var.

---

#### `RAG/store.py` — ChromaDB Client

- `get_client(persist_directory)` — singleton `PersistentClient`; sets `BM25_PERSIST_DIR` env.
- `get_collection(client, name)` — singleton collection with cosine space.

---

#### `RAG/chunker.py` — Text Chunking

- `chunk_text(text, chunk_size=1800, overlap=200)` — simple character-based chunker.
- `read_corpus(corpus_dir)` — reads `.txt` files, applies basic de-id, extracts metadata, chunks.
- `deidentify()` — toy regex de-id (SSN, phone, dates, MRN, name patterns).

---

## 5. Phase 3: Deletable / Suspect Files

### ⚠️ Definitely Deletable

| File | Reason |
|---|---|
| `Clinical-Note-Generator/pytest-cache-files-m28ft4kr/` | Stale pytest cache directory with non-standard name; should be in `.gitignore`. Not `__pycache__` so may not be excluded. |
| `Clinical-Note-Generator/server/services/asr_whisperx.py` | **In-process WhisperX engine**. Current production ASR path uses `routes/asr.py` (HTTP proxy to whisper.cpp). This file has heavy CUDA dependencies (torch, whisperx, omegaconf) that inflate the import footprint. Only useful for local dev testing. Should be moved to `dev/` or removed if whisper.cpp HTTP path is stable. |
| `RAG/server/services/rag_client.py` | Lives in `RAG/server/services/` — a non-standard location. The active client is `Clinical-Note-Generator/server/services/rag_http_client.py`. This looks like a legacy artifact. Verify nothing imports it before deleting. |
| `RAG/test.py` | Generic test script with no clear integration. Likely dev scratch file. |
| `PCHost/web/auth_debug.html` | Debug page that should not be in production. |

### 🔍 Investigate Before Deleting

| File | Reason |
|---|---|
| `PCHost/llama-gateway.js` | Proxies `/api/generate` and `/api/check` to llama-server port 7871. The main note generation goes directly to llama-server port 8081 via FastAPI. Unclear if this gateway is actively used. Check if any frontend or PCHost route calls `/llama/generate`. |
| `PCHost/openwebui-proxy.js` | OpenWebUI proxy. The comment in `server.js` says it was "moved to dedicated server (openwebui-proxy.js) Running on port 8443". May still be needed if OpenWebUI is used. |
| `PCHost/start-openwebui-proxy.bat` | Only needed if OpenWebUI proxy is active. |
| `RAG/retriever.py` | Implements hybrid search but `query_api.py` has its own inline implementation. `retriever.py` is imported by `vector_store_manager.py` CLI. Low risk — keep for CLI use. |
| `Clinical-Note-Generator/server/core/deid/ner_spacy.py` | spaCy NER layer. If `spacy` / `en_core_web_sm` is not installed on the production system, this silently no-ops. Verify `spacy` is in `requirements.txt` (it is NOT listed). May be a dead dependency. |
| `server/routes/notes.py::generate_stream()` | **Legacy endpoint** (not routed). 700+ lines of dead code. Should be deleted or refactored into a dev-only module. |
| `server/routes/notes.py::generate_v8()` | Non-streaming v8 — also not routed. Dead code. |

---

## 6. Phase 4: Redundant Code Blocks

### `server/routes/notes.py`

1. **`generate_stream()` function (~200 lines)** — Not routed in `app.py`. Legacy endpoint. Dead code.
2. **`generate_v8()` function (~80 lines)** — Non-streaming v8, not routed. Dead code.
3. **`clean_model_output = clean_model_output_chunk` alias defined TWICE** — appears at line ~460 and again ~660. Remove one.
4. **`truncate_to_context_length()` and `truncate_to_context_length_tokens()`** — Two nearly identical functions. `truncate_to_context_length()` uses `int(max_tokens * 0.75)` word estimate; `_tokens` version uses 1:1. The 1:1 version is used in `generate_v8_stream`. `truncate_to_context_length()` appears unused. Remove or consolidate.
5. **`build_qa_prompt()` in notes.py** — Used only inside `generate_stream()` (which is unrouted). If `generate_stream()` is deleted, this becomes dead code.

### `server/routes/admin.py`

1. **`_configure_llama_service()` function** — Returns `(True, "skipped")` immediately. No-op placeholder. Safe to remove.
2. **`_service_names()`, `_service_status_win()`, `_service_action_win()`, `_run_cmd()`, `_nssm_bin()`** — Windows NSSM/SC service management. These work only on Windows with NSSM installed. The service management endpoints (`/services/status`) call these. If service management is fully externalized (not used), these can be trimmed.
3. **`cfg` variable in `llama_status()`** — Used as `cfg.get("llm_model", ...)` but is not locally defined within that function scope (it relies on module-level `_load_cfg()` being called earlier in the code flow). This is a latent bug — `cfg` is undefined in `llama_status()` as written. The function should call `cfg = _load_cfg()` explicitly.

### `server/services/asr_whisperx.py`

- Entire file is suspect (see Phase 3). If kept, the `PassthroughVAD` monkey-patch on line 60 (`whisperx_pyannote.Pyannote = _BypassPyannoteVAD`) has global side effects on import.

### `server/core/deid/v1.py`

- `_PATTERNS["name_labeled"]` regex can match medication names that follow the pattern `"Medication: Dose"` if the label word matches. The regex is `\b(?:patient|pt|name|doctor|dr\.?|provider)\s*[:\-]\s*` which is conservative enough, but worth monitoring for false positives.

### `RAG/query_api.py`

- `_load_settings()` is decorated with `@lru_cache(maxsize=1)` which means config changes require a process restart to take effect. This is intentional but should be documented.
- `_parse_date_any()` is duplicated from `version_manager.py`. Could be extracted to `utils_meta.py`.

### `RAG/retriever.py`

- `HYBRID_LAMBDA = 0.20` is hardcoded here, but `query_api.py` reads from `settings.yaml` (`hybrid_lambda: 0.10`). The two implementations use different defaults. `retriever.py` is only used by `vector_store_manager.py` CLI, so this inconsistency is low-risk but confusing.

---

## 7. Phase 5: Non-Git Files (Do NOT Commit)

| Path | Description | Why Excluded |
|---|---|---|
| `Clinical-Note-Generator/config/config.json` | Runtime config with JWT secrets, model paths, API keys | Contains secrets |
| `Clinical-Note-Generator/data/user_data.sqlite` | SQLite database (users, workspaces, refresh tokens, queued jobs) | User PII / auth data |
| `Clinical-Note-Generator/data/datasets/*.jsonl` | Daily de-identified case logs (`cases_YYYY-MM-DD.jsonl`, `case_events_YYYY-MM-DD.jsonl`) | Patient data (even if de-identified) |
| `Clinical-Note-Generator/data/queue_files/` | Uploaded files for offline queue (audio, images, PDFs) | Patient files |
| `Clinical-Note-Generator/data/RxNorm_full*/` | RxNorm drug database files (RXNCONSO.RRF, etc.) | Large data files (~300 MB+); not project code |
| `Clinical-Note-Generator/server/logs/` | HTTP request CSV logs and application logs | Runtime logs with request data |
| `Clinical-Note-Generator/server/temp-audio/` | Retained audio recordings from ASR | Patient audio |
| `RAG/chroma_store/` | ChromaDB vector store (embeddings + metadata) | Large binary data; regeneratable |
| `RAG/chroma_db/` | Alternative ChromaDB path | Same as above |
| `RAG/embeddings/` | Precomputed numpy embedding files (`.npy`, `.npz`) | Large binary data; regeneratable |
| `RAG/logs/request_metrics.csv` | Runtime metrics log | Runtime data |
| `RAG/current_corpus/` | Active document JSON files (version manager) | Large data; regeneratable |
| `RAG/archive/` | Archived prior document versions | Large data |
| `RAG/raw_docs/` | Raw downloaded PDFs/text (guidelines, PMC articles) | Large data; downloadable |
| `certs/ieissa/` | TLS certificate and private key | Security-sensitive |
| `PCHost/config/server_config.json` | Contains domain + SSL cert paths | Environment-specific config |

### Recommended `.gitignore` entries

```gitignore
# Config (secrets)
Clinical-Note-Generator/config/config.json
PCHost/config/server_config.json

# User data
Clinical-Note-Generator/data/user_data.sqlite
Clinical-Note-Generator/data/datasets/
Clinical-Note-Generator/data/queue_files/
Clinical-Note-Generator/data/RxNorm_full*/

# Logs
Clinical-Note-Generator/server/logs/
Clinical-Note-Generator/server/temp-audio/

# RAG data
RAG/chroma_store/
RAG/chroma_db/
RAG/embeddings/
RAG/logs/
RAG/current_corpus/
RAG/archive/
RAG/raw_docs/

# Certs
certs/

# Node
PCHost/node_modules/

# Python
**/__pycache__/
**/*.pyc
*.egg-info/
.pytest_cache/
pytest-cache-files-*/
venv/
cenv/
.env
```

---

## 8. Environment Variables Reference

| Variable | Component | Default | Description |
|---|---|---|---|
| `FASTAPI_PORT` | FastAPI | 7860 | FastAPI bind port |
| `NOTEGEN_URL_PRIMARY` | FastAPI | `http://127.0.0.1:8081` | Primary llama-server URL |
| `NOTEGEN_URL_FALLBACK` | FastAPI | None | Fallback llama-server URL |
| `ASR_URL` | FastAPI | None | Primary whisper.cpp URL (required for ASR) |
| `ASR_URL_FALLBACK` | FastAPI | None (auto-derived: 8095→8096) | Fallback ASR URL |
| `ASR_API_KEY` | FastAPI | `notegenadmin` | Bearer token for whisper.cpp |
| `ASR_NORMALIZE_TO_WAV` | FastAPI | `1` | Enable ffmpeg audio normalization |
| `ASR_WHISPERCPP_VAD` | FastAPI | `0` | Enable VAD in whisper.cpp |
| `ASR_WHISPERCPP_NO_SPEECH_THOLD` | FastAPI | `1.0` | No-speech threshold |
| `OCR_URL_PRIMARY` | FastAPI | `http://127.0.0.1:8090` | Primary OCR LLM server |
| `OCR_URL_FALLBACK` | FastAPI | None | Fallback OCR URL |
| `OCR_MODEL_NAME` | FastAPI | `nanonets-ocr-s` | OCR model identifier |
| `OCR_PDF_DPI` | FastAPI | `200` | PDF render DPI |
| `OCR_IMAGE_MAX_DIM` | FastAPI | `3200` | Max image dimension before downscale |
| `RAG_URL` | FastAPI | None (required for RAG) | RAG query service URL |
| `VISION_QA_URL` | FastAPI | Falls back to OCR_URL_PRIMARY | Vision QA LLM server |
| `SEARXNG_URL` | FastAPI | `https://ieissa.com:3443/searxng/search` | SearXNG search URL |
| `SEARXNG_API_KEY` | FastAPI | None | SearXNG API key |
| `FFMPEG_BIN` | FastAPI | auto-detected | Path to ffmpeg binary |
| `JWT_SECRET` | FastAPI | Required | JWT access token secret |
| `JWT_REFRESH_SECRET` | FastAPI | Required | JWT refresh token secret |
| `DATABASE_URL` | FastAPI | `sqlite:///data/user_data.sqlite` | SQLAlchemy DB URL |
| `RXNORM_DIR` | FastAPI | None | Directory containing `RXNCONSO.RRF` |
| `EMBEDDER_DEVICE` | RAG | auto (cuda if available) | Embedding model device |
| `EMBEDDER_DIM` | RAG | 0 (full dim) | Matryoshka output dimension |
| `HTTP_PORT` | PCHost | 3000 | PCHost HTTP port |
| `HTTPS_PORT` | PCHost | 3443 | PCHost HTTPS port |
| `FASTAPI_URL` | PCHost | `http://127.0.0.1:7860` | FastAPI target URL |
| `LLAMA_GATEWAY_URL` | PCHost | `http://127.0.0.1:7871` | Llama gateway target |
| `SSL_KEY_PATH` | PCHost | from server_config.json | TLS private key path |
| `SSL_CERT_PATH` | PCHost | from server_config.json | TLS certificate path |

---

## 9. Database Schema

### SQLite: `data/user_data.sqlite`

**Table: `user`**
| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | auto-generated |
| `email` | String UNIQUE | login identifier |
| `hashed_password` | String | PBKDF2-SHA256 |
| `is_active` | Bool | default True |
| `is_admin` | Bool | default False |
| `is_approved` | Bool | default False — admin must approve |
| `created_at` | DateTime | UTC |

**Table: `userworkspace`**
| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `user_id` | UUID FK → user | UNIQUE |
| `state_json` | JSON | Full workspace state (settings, extras, documents) |
| `version` | Int | Monotonic version for conflict detection |
| `created_at` | DateTime | UTC |
| `updated_at` | DateTime | UTC |

**Table: `refreshtoken`**
| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `user_id` | UUID FK → user | |
| `token_hash` | String | PBKDF2 hash of token |
| `user_agent` | String? | |
| `expires_at` | DateTime | |
| `revoked` | Bool | default False |
| `created_at` | DateTime | UTC |

**Table: `queued_jobs`**
| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `user_id` | UUID FK → user | |
| `type` | String | "ocr" or "transcribe" |
| `status` | String | "pending" / "processing" / "failed" |
| `created_at` | DateTime | UTC |
| `processed_at` | DateTime? | |
| `file_name` | String | Original filename |
| `mime_type` | String | |
| `file_size` | Int | bytes |
| `server_file_key` | String UNIQUE | Relative path in queue storage |
| `error` | Text? | Error message on failure |

---

*End of CNG Project Index.*
