# CNG Backend Index

**Generated:** 2026-03-17  
**Source:** `/mnt/c/project-root/Clinical-Note-Generator/server/`  
**Config:** `config/config.json`

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [External Services & Ports](#external-services--ports)
3. [Configuration Summary](#configuration-summary)
4. [app.py — Application Entry Point](#apppy--application-entry-point)
5. [metrics.py — Metrics Singleton](#metricspy--metrics-singleton)
6. [core/ — Core Modules](#core--core-modules)
   - [core/config.py](#coreconfigpy)
   - [core/env.py](#coreenvpy)
   - [core/db.py](#coredbpy)
   - [core/security.py](#coresecuritypy)
   - [core/dependencies.py](#coredependenciespy)
   - [core/baseline.py](#corebaselinepy)
   - [core/deid/](#coredeid)
   - [core/logging/](#corelogging)
   - [core/preprocessing/](#corepreprocessing)
   - [core/prompt/builder.py](#corepromptbuilderpy)
   - [core/qa_rag/helpers.py](#coreqa_raghelperspey)
   - [core/streaming/helpers.py](#corestreaminghelperspy)
   - [core/stores/](#corestores)
   - [core/consult/pipeline.py](#coreconsultpipelinepy)
   - [core/order/pipeline.py](#coreorderpipelinepy)
7. [models/ — SQLModel Database Models](#models--sqlmodel-database-models)
8. [schemas/ — Pydantic Request/Response Schemas](#schemas--pydantic-requestresponse-schemas)
9. [routes/ — API Endpoints](#routes--api-endpoints)
   - [routes/app.py registration](#routesapp-registration)
   - [routes/auth_users.py](#routesauth_userspy)
   - [routes/admin_users.py](#routesadmin_userspy)
   - [routes/admin.py](#routesadminpy)
   - [routes/workspace.py](#routesworkspacepy)
   - [routes/notes.py](#routesnotespy)
   - [routes/ocr.py](#routesocrpy)
   - [routes/asr.py](#routesasrpy)
   - [routes/qa_chat.py](#routesqa_chatpy)
   - [routes/qa_vision.py](#routesqa_visionpy)
   - [routes/perf.py](#routesperfpy)
   - [routes/queue.py](#routesqueuepy)
   - [routes/rag_updates.py](#routesrag_updatespy)
   - [routes/version.py](#routesversionpy)
10. [services/ — External Service Clients](#services--external-service-clients)
    - [services/note_generator_clean.py](#servicesnote_generator_cleanpy)
    - [services/ocr_llm_client.py](#servicesocr_llm_clientpy)
    - [services/rag_http_client.py](#servicesrag_http_clientpy)
    - [services/asr_whisperx.py](#servicesasr_whisperxpy)
    - [services/clinical_text_normalizer.py](#servicesclinical_text_normalizerpy)
    - [services/qa_web_search.py](#servicesqa_web_searchpy)
    - [services/vision_qa_client.py](#servicesvision_qa_clientpy)
11. [scripts/create_admin.py](#scriptscreate_adminpy)
12. [Dead / Commented-Out Code](#dead--commented-out-code)
13. [API Endpoint Quick Reference](#api-endpoint-quick-reference)

---

## Architecture Overview

CNG is a FastAPI server that:
- Accepts clinical data (transcription + chart/lab/prior-visit text) from the web UI
- Builds structured prompts and streams clinical notes via an external llama-server (llama.cpp HTTP API)
- Provides OCR (image → text), ASR (audio → text), RAG Q&A, vision Q&A, and queue-based background processing
- Manages users, workspaces, and queued jobs via SQLite (SQLModel/SQLAlchemy)
- Tracks HTTP metrics and logs de-identified case records for dataset building

All primary API routes are mounted under `/api`. Static web UI is served from `config.json → web_dir` (default `C:/PCHost/web`).

---

## External Services & Ports

| Service | Default URL/Port | Config Key / Env Var | Purpose |
|---|---|---|---|
| llama-server (note gen) | `http://127.0.0.1:8081` | `NOTEGEN_URL_PRIMARY` / `NOTEGEN_URL_FALLBACK` | LLM inference for notes, QA, consult comments, order requests |
| OCR llama-server | `http://127.0.0.1:8090` | `OCR_URL_PRIMARY` / `OCR_URL_FALLBACK` | Multimodal OCR (Nanonets-OCR2 model) |
| RAG service | `http://127.0.0.1:8007` | `RAG_URL` / `rag_service_url` | Evidence retrieval for notes and QA |
| RAG comment LLM | `http://127.0.0.1:8036` | `rag_comment_llm_url` | LLM used for consult addendum generation |
| Order request LLM | `http://127.0.0.1:8081` | `order_request_llm_url` | LLM for order/referral extraction |
| whisper.cpp ASR | env `ASR_URL` (e.g. :8095) | `ASR_URL` / `ASR_URL_FALLBACK` | Audio transcription proxy |
| SearXNG | `http://127.0.0.1:8083/search` | `SEARXNG_URL` / `SEARXNG_API_KEY` | Web search for QA chat |
| FastAPI itself | port 7860 | `FASTAPI_PORT` | This server |

---

## Configuration Summary

**File:** `config/config.json`

Key sections (separated by `__section_*__` comment keys):

| Section | Key Fields |
|---|---|
| **AUTH** | `admin_api_key`, `auth_database_url` (SQLite path), `jwt_secret`, `jwt_refresh_secret`, `auth_access_token_exp_minutes` (600), `auth_refresh_token_exp_days` (30) |
| **PATHS_WEB** | `web_dir` (C:\PCHost\web), `ffmpeg_path`, `models_dir` |
| **LLAMA_SERVER** | `llm_model` (GGUF path), `use_llama_server` (true), `llama_server_port` (8081), `context_length` (64000), sampler defaults (`default_note_temperature` 0.2, `default_top_k` 20, `default_top_p` 0.92, `default_min_p` 0.06, `default_note_max_tokens` 6144) |
| **OCR** | `ocr_server_url` (127.0.0.1:8090), `ocr_model` (Nanonets GGUF), `ocr_cuda_visible_devices` ("1") |
| **RAG** | `rag_service_url` (:8007), `rag_comment_llm_url` (:8036), `rag_top_k` (16), `rag_min_score` (0.05), `rag_timeout_ms` (25000), `rag_max_context_words` (2400) |
| **NOTE_DEFAULTS** | Per-note-type system prompts (`default_note_system_prompt`, `default_note_system_prompt_other`) and user prompts (`default_note_user_prompts` for consult/progress/followup/admission/discharge/transfer; `default_note_user_prompts_other` for referral/summarize/custom/procedure) |
| **ASR_AUDIO** | `asr_model_path`, `asr_device` (cuda), `asr_compute_type` (float16), `audio_retention_days` (60), `asr_chunk_seconds` (12), `beam_size` (5) |

`llama_auto_manage` is **true** in config, but at runtime the admin routes stub this out as "externalized" — llama-server is started manually.

---

## app.py — Application Entry Point

**Purpose:** Creates the FastAPI app, wires CORS, HTTP logging middleware, metrics, route registration, and static file serving.

### Classes / Functions

| Name | Signature | Description |
|---|---|---|
| `http_logger` | `async (request, call_next) → response` | Middleware: times requests, records to `Metrics`, logs HTTP events |
| `_load_cfg` | `() → dict` | Loads `config/config.json` relative to `app.py` location |
| `root` | `GET /` | Redirects to `/static/admin.html` |
| `startup_event` | `on_event("startup")` | Calls `init_db()`; logs that llama/OCR servers must be started manually |
| `shutdown_event` | `on_event("shutdown")` | Logs shutdown |

### Route Registration

```
/api  +  ocr_router         (auth: require_api_bearer)
/api  +  asr_router         (per-endpoint auth, see routes/asr.py)
/api  +  notes_router       (auth: require_api_bearer)
/api  +  rag_router         (auth: require_api_bearer)
/api  +  qa_chat_router     (auth: require_api_bearer)
/api  +  qa_vision_router   (auth: require_api_bearer)
/api  +  perf_router        (open: /api/health)
/api  +  version_router     (open)
       auth_router          (no prefix override; prefix set in router: /api/auth)
       workspace_router     (prefix set in router: /api/workspace)
       admin_users_router   (prefix: /api/admin/users)
       admin_router         (prefix: /api/admin)
       queue_router         (prefix: /api/queue)
```

### Commented-Out Code

```python
#from server.routes.services import router as services_router  # noqa: E402
#app.include_router(services_router)
```
A `services` router was planned but never implemented / was removed.

### Static Files

Web UI served from `config.json → web_dir`. Falls back to `C:/PCHost/web`, then `./web`.

---

## metrics.py — Metrics Singleton

**Purpose:** Thread-safe HTTP metrics collector and GPU VRAM reporter. Appends HTTP request rows to `server/logs/http_requests.csv`.

### Classes

#### `RouteStats`
Dataclass tracking per-route: total, 2xx, 4xx, 5xx counts, and a rolling deque of last 500 latencies.

| Method | Signature | Description |
|---|---|---|
| `record` | `(status: int, ms: float) → None` | Increments counters |
| `snapshot` | `() → Dict` | Returns totals + p50/p95 latency |

#### `Metrics`

| Method | Signature | Description |
|---|---|---|
| `__init__` | `(logs_dir: str)` | Creates CSV file, initializes route dict and concurrency counters |
| `inc_active` / `dec_active` | `() → None` | Thread-safe active request counter |
| `record_http` | `(method, path, status, ms, in_bytes, out_bytes) → None` | Records per-route stats + appends CSV row |
| `record_note` | `(duration_sec, tokens, model) → None` | Records note generation latency/tokens |
| `record_ocr` | `(duration_sec, confidence) → None` | Records OCR job latency |
| `snapshot` | `() → Dict` | Returns full metrics snapshot including GPU VRAM (via pynvml if available) |
| `_collect_gpu_stats` | `() → Optional[List[Dict]]` | Queries NVML for per-GPU VRAM usage |

**Global singleton:** `metrics: Optional[Metrics] = None` — set by `app.py` after import.

---

## core/ — Core Modules

### core/config.py

**Purpose:** Loads and caches app settings from `config.json` and environment variables; provides typed `Settings` model.

#### `Settings` (Pydantic BaseModel)
Fields: `database_url`, `jwt_secret`, `jwt_refresh_secret`, `access_token_exp_minutes` (600), `refresh_token_exp_days` (30).

#### Functions

| Name | Signature | Description |
|---|---|---|
| `_config_path` | `() → Path` | Returns path to `config/config.json` |
| `_default_db_url` | `() → str` | Returns SQLite path under `data/user_data.sqlite` |
| `_load_config` | `() → Dict` | Reads config.json |
| `get_settings` | `() → Settings` (lru_cache) | Merges env + config.json; raises if JWT secrets missing |

**Inter-file deps:** `core/env.py → load_env_file`

---

### core/env.py

**Purpose:** Loads `.env` file from repo root once (cached). Falls back to a lightweight parser if `python-dotenv` is unavailable.

| Function | Description |
|---|---|
| `load_env_file() → Path` (lru_cache) | Loads `.env` without overriding existing env vars |

---

### core/db.py

**Purpose:** Creates the SQLAlchemy engine and `init_db()` for SQLModel schema creation.

| Function | Description |
|---|---|
| `init_db() → None` | Imports all model modules and calls `SQLModel.metadata.create_all(engine)` |
| `get_session()` | FastAPI dependency yielding a SQLModel `Session` |

**Inter-file deps:** `core/config.py`, `models/refresh_token`, `models/user`, `models/workspace`, `models/queued_job`

---

### core/security.py

**Purpose:** Password hashing (PBKDF2-SHA256) and JWT token creation/decoding.

| Function | Signature | Description |
|---|---|---|
| `hash_password` | `(password: str) → str` | PBKDF2-SHA256 hash |
| `verify_password` | `(password, hashed) → bool` | Constant-time compare |
| `create_access_token` | `(subject, claims?) → str` | HS256 JWT, expires per settings |
| `create_refresh_token` | `(subject) → str` | HS256 JWT, expires per settings |
| `decode_access_token` | `(token) → Dict` | Decodes/verifies with `jwt_secret` |
| `decode_refresh_token` | `(token) → Dict` | Decodes/verifies with `jwt_refresh_secret` |

**Inter-file deps:** `core/config.py`

---

### core/dependencies.py

**Purpose:** FastAPI dependency functions for route-level authentication.

| Function | Signature | Description |
|---|---|---|
| `require_api_bearer` | `(creds, session) → bool` | Validates JWT bearer token; checks user is active and approved |
| `get_current_user` | `(token, session) → User` | Full user lookup from JWT |
| `get_current_admin` | `(current_user) → User` | Extends `get_current_user`; raises 403 if not admin |

**Inter-file deps:** `core/db.py`, `core/security.py`, `models/user.py`

---

### core/baseline.py

**Purpose:** Returns the default empty workspace state dictionary.

| Function | Signature | Description |
|---|---|---|
| `get_baseline_workspace` | `() → Dict` | Returns `{settings: {theme, language}, documents: [], draft: None, extras: {}}` |

---

### core/deid/

#### `core/deid/__init__.py`
Empty module docstring. No exports.

#### `core/deid/v1.py`

**Purpose:** Regex + optional spaCy NER de-identification for PHI (names, dates, MRNs, phones, emails).

**Patterns defined:** `name_labeled`, `name_comma_age`, `name_sentence_verb`, `name_doctor`, `date`, `mrn`, `phone`, `email`.

| Function | Signature | Description |
|---|---|---|
| `deidentify_text` | `(text: str) → Dict[str, Any]` | Applies all regex redaction passes in order, then calls spaCy NER layer. Returns `{text, redaction_counts, leak_flags}` |

**Inter-file deps:** `core/deid/ner_spacy.py`

#### `core/deid/ner_spacy.py`

**Purpose:** Optional spaCy PERSON-entity NER layer, enabled by default. Disable via `CNG_DEID_NER=0`.

| Function | Signature | Description |
|---|---|---|
| `ner_enabled` | `() → bool` | Checks `CNG_DEID_NER` env var |
| `_load_nlp` | `() → spacy.Language` (lru_cache) | Loads spaCy model (`CNG_DEID_SPACY_MODEL`, default `en_core_web_sm`) |
| `redact_person_entities` | `(text: str) → Tuple[str, Dict]` | Runs NER PERSON detection; falls back to consecutive PROPN sequences if no entities found |

**External service:** spaCy model (`en_core_web_sm` or override) — loaded from disk.

---

### core/logging/

#### `core/logging/__init__.py`
Empty docstring.

#### `core/logging/dataset_logger.py`

**Purpose:** Thread-safe JSONL append-only logging of case records and feedback events for dataset building.

| Function | Signature | Description |
|---|---|---|
| `log_case_record` | `(record: Dict) → str` | Appends to `data/datasets/cases_YYYY-MM-DD.jsonl` |
| `log_case_event` | `(event: Dict) → str` | Appends to `data/datasets/case_events_YYYY-MM-DD.jsonl` |

Dataset directory overridable via `CNG_DATASET_DIR` env var.

---

### core/preprocessing/

#### `core/preprocessing/__init__.py`
Re-exports `PreprocessingPipeline` and `TokenBudgetTruncator`.

#### `core/preprocessing/constants.py`
Defines regex patterns for preprocessing: `BOILERPLATE_LINE_PATTERNS`, `HEADER_CANDIDATE_PATTERNS`, `JUNK_LINE_PATTERNS`, `DATE_STAMP_ONLY`, `MEDICAL_TERMS` set, `DATE_PATTERNS` dict (ymd, mdy, dmy_mon, mon_y).

#### `core/preprocessing/pipeline.py`

**Purpose:** Multi-step text cleaning pipeline for chart data before LLM prompt construction.

**Class: `PreprocessingPipeline`**

Constructor reads `cfg["preprocessing"]["enabled"]` and `cfg["preprocessing"]["steps"]`.

| Method | Description |
|---|---|
| `process(text)` | Runs enabled steps in order: remove_boilerplate → collapse_repeated_headers → remove_junk_artifacts → deduplicate_near_identical_blocks → normalize_whitespace |
| `normalize_whitespace` | Collapses tabs/spaces; reduces 3+ newlines to 2 |
| `remove_boilerplate` | Removes date-stamp-only lines, boilerplate patterns, separator lines (`---`, `===`, etc.) |
| `collapse_repeated_headers` | Deduplicates repeated header-like lines within a sliding window of 10 |
| `remove_junk_artifacts` | Removes page numbers, time-only lines, non-alphanumeric lines |
| `deduplicate_near_identical_blocks` | Deduplicates paragraph blocks by first-80-char key within a window of 5 |

**Inter-file deps:** `core/preprocessing/constants.py`

#### `core/preprocessing/truncation.py`

**Purpose:** Token-budget-based truncation for the three input sections (prior_visits, labs_imaging_other, current_encounter).

**Class: `TokenBudgetTruncator`**

| Method | Description |
|---|---|
| `__init__(cfg)` | Reads budget from `cfg["preprocessing"]["truncation"]` |
| `estimate_tokens(text) → int` | `ceil(word_count × 1.3)` |
| `truncate_section(text, section) → str` | Greedy paragraph selection by score; safety override if >80% would be removed |
| `_score_paragraph(para) → int` | Scores by: date recency (ordinal × 1B), numeric hits, unit hits, medical term hits, low-info penalty |
| `_latest_date_ordinal(text) → int` | Extracts max date across all patterns; returns `toordinal()` |
| `_clip_text_to_budget(text, budget) → str` | Line-level hard truncation fallback |

**Debug mode:** `CNG_TRUNCATION_DEBUG=1` env var enables detailed logging.

---

### core/prompt/builder.py

**Purpose:** Builds the final LLM prompts for note generation from config templates and patient data.

**Imports config from:** `config/config.json` (read directly via `CONFIG_PATH`).

**Inter-file deps:** `core/preprocessing/` (PreprocessingPipeline, TokenBudgetTruncator)

| Function | Signature | Description |
|---|---|---|
| `load_config` | `() → Dict` | Reads config.json |
| `_has_minimum_signal` | `(text, min_alnum) → bool` | Sanity check on cleaned text |
| `_sanitize_chart_text` | `(text) → str` | Removes control chars and format symbols from chart text |
| `_sanitize_transcription_text` | `(text) → str` | Lighter sanitize for transcription |
| `_fill_template` | `(tpl, values) → str` | Simple `{KEY}` substitution |
| `_cfg_text` | `(val) → str` | Joins list-of-strings or returns string from config value |
| `_apply_preprocessing` | `(cfg, trans, old, mixed) → tuple[str,str,str]` | Sanitizes + optionally preprocesses/truncates each of the 3 input sections |
| `build_prompt_v8` | `(transcription_text, old_visits_text, mixed_other_text, note_type, custom_prompt?, user_speciality?) → str` | **Primary prompt builder.** Wraps inputs in `<CURRENT_ENCOUNTER>`, `<PRIOR_VISITS>`, `<LABS_IMAGING_OTHER>` tags; injects system prompt and note-type user prompt from config; appends numeric style instruction and `END_OF_NOTE` stop token instruction |
| `build_prompt_other` | `(same args) → str` | Variant for "other" note types (referral, summarize, custom, procedure) — uses `default_note_system_prompt_other` and `default_note_user_prompts_other` from config; does NOT wrap in section tags |
| `build_note_prompt_legacy` | `(chart_data, transcription, note_type, ...) → str` | Legacy 2-field builder (chart + transcription, no section tags). Still referenced in `routes/notes.py` legacy path |

---

### core/qa_rag/helpers.py

**Purpose:** Async helpers for QA RAG retrieval and rewrite pipeline.

| Function | Signature | Description |
|---|---|---|
| `_call_rag` | `(rag_task) → Dict` | Awaits a RAG coroutine task; returns `{}` on error |
| `_qa_rewrite_with_rag` | `(baseline_text, question, cfg, max_tokens, rag_task, ...) → Dict` | Full RAG rewrite flow: awaits RAG, checks evidence quality, optionally rewrites baseline answer; returns `{final_text, rewrite_used, used_filters, norm_refs, full_chunks, rag_context_aug, rag_error}` |

---

### core/streaming/helpers.py

**Purpose:** Async generator helpers for streaming completions.

| Function | Description |
|---|---|
| `_stream_response(note_gen, prompt, temperature, max_tokens, stop_tokens, clean_chunk)` | Collects completion non-streaming, yields as single chunk (used in legacy path) |
| `_stream_response_v8(note_gen, prompt, temperature, max_tokens, stop_tokens, clean_chunk)` | True streaming via `note_gen.stream_completion()`; yields cleaned chunks |
| `_stream_qa_response(final_text, chunker, clean_chunk)` | Chunks a fully-generated string and yields segment by segment |

---

### core/stores/

#### `core/stores/ttl_store.py`

**Purpose:** Generic thread-safe in-memory TTL cache with dict-like interface.

**Class: `TTLStore[K, V]`**

| Method | Description |
|---|---|
| `put(key, value)` | Stores with current timestamp |
| `get(key, default?)` | Returns value if not expired; evicts if expired |
| `delete(key)` | Removes entry |
| `evict_expired() → int` | Removes all expired entries; returns count |
| `__contains__`, `__setitem__`, `__getitem__`, `__delitem__`, `__len__`, `clear` | Dict-compatibility shims |

TTL default: 86400 seconds (24h).

#### `core/stores/generation_store.py`

**Purpose:** Module-level singleton stores for generation state, shared between `routes/notes.py` and the pipeline modules.

```python
_generation_cache: TTLStore[str, Dict[str, str]]     # prompt + output text per gen_id
_generation_meta: TTLStore[str, Dict[str, Any]]      # RAG refs, QA status per gen_id
_consult_comment_store: TTLStore[str, Dict[str, Any]] # consult addendum status/result
_order_request_store: TTLStore[str, Dict[str, Any]]  # order extraction status/result
```

All TTL = 86400s.

---

### core/consult/pipeline.py

**Purpose:** Async background pipeline that generates a RAG-grounded consult addendum for a completed note.

| Function | Signature | Description |
|---|---|---|
| `_extract_references` | `(raw_refs, cap, normalize_reference_items) → List[Dict]` | Normalizes and caps RAG reference list |
| `_generate_consult_comment` | `(gen_id, note_text, cfg, *, strategy, consult_store, generation_meta, extract_marker_sentences, extract_focus_sections, fallback_focus_from_note, rag_tail_window, rag_client_from_cfg, get_rag_comment_llm, normalize_reference_items, clean_model_output_final) → None` | Full pipeline: extracts impression/plan → derives RAG focus → queries RAG → generates evidence-grounded addendum with structure validation and retry → stores in `consult_store[gen_id]`. Strategies: `"sections"`, `"full_note"`, `"llm_query"`. |

**External calls:** RAG service (via `rag_client_from_cfg`), LLM (via `get_rag_comment_llm` → `rag_comment_llm_url` port 8036).

**Addendum sections enforced:** Differential to Consider, Workup to Add Now, Management Adjustments, Safety/Red Flags, What Is Already Appropriate.

---

### core/order/pipeline.py

**Purpose:** Async background pipeline that extracts clinical orders/referrals from a note's Plan section and generates requisition text.

| Function | Signature | Description |
|---|---|---|
| `_parse_order_items` | `(detected_items, focus_text, max_items) → List[Dict]` | Parses LLM JSON output or falls back to regex-based detection of medications/labs from the plan text |
| `_generate_order_requests` | `(gen_id, note_text, cfg, *, order_store, ...) → None` | Extracts plan section → sends LLM detect prompt → for each item generates category-specific requisition (Imaging / Lab / Medication / Referral / Other) → deduplicates and merges medication items → stores in `order_store[gen_id]` |

**External calls:** LLM at `order_request_llm_url` (port 8081).

---

## models/ — SQLModel Database Models

All stored in SQLite at `data/user_data.sqlite`.

### `models/user.py` — `User`

| Field | Type | Description |
|---|---|---|
| `id` | UUID (PK) | Auto-generated |
| `email` | str (unique, indexed) | Login identifier |
| `hashed_password` | str | PBKDF2-SHA256 hash |
| `is_active` | bool (default True) | Account enabled |
| `is_admin` | bool (default False) | Admin privileges |
| `is_approved` | bool (default False) | Must be approved before login |
| `created_at` | datetime | UTC creation time |

### `models/refresh_token.py` — `RefreshToken`

| Field | Type | Description |
|---|---|---|
| `id` | UUID (PK) | |
| `user_id` | UUID (FK→user.id) | |
| `token_hash` | str | PBKDF2 hash of refresh token |
| `user_agent` | str? | |
| `expires_at` | datetime | |
| `revoked` | bool (default False) | |
| `created_at` | datetime | |

### `models/workspace.py` — `UserWorkspace`

| Field | Type | Description |
|---|---|---|
| `id` | UUID (PK) | |
| `user_id` | UUID (FK→user.id, unique) | One workspace per user |
| `state_json` | JSON | Arbitrary workspace blob (settings, documents, extras, draft) |
| `version` | int (default 1) | Optimistic concurrency control |
| `created_at` / `updated_at` | datetime | |

### `models/queued_job.py` — `QueuedJob`

| Field | Type | Description |
|---|---|---|
| `id` | UUID (PK) | |
| `user_id` | UUID (FK→user.id, indexed) | |
| `type` | str (indexed) | `"ocr"` or `"transcribe"` |
| `status` | str (indexed) | `pending` / `processing` / `failed` / `done` |
| `created_at` | datetime (indexed) | |
| `processed_at` | datetime? | |
| `file_name` | str | Original filename |
| `mime_type` | str | |
| `file_size` | int | Bytes |
| `server_file_key` | str (indexed, unique) | Relative path under `data/queue_files/` |
| `error` | str? (Text column) | Error message on failure |

---

## schemas/ — Pydantic Request/Response Schemas

### `schemas/auth.py`

| Schema | Fields | Notes |
|---|---|---|
| `RegisterRequest` | `email: EmailStr`, `password: str(12-128)` | Password validator: must have upper+lower+digit+symbol |
| `LoginRequest` | `email: EmailStr`, `password: str` | |
| `TokenResponse` | `access_token`, `token_type="bearer"`, `expires_in`, `refresh_token?` | |
| `RefreshRequest` | `refresh_token?` | Also accepted from cookie |
| `UserProfile` | `id: str`, `email`, `is_admin`, `is_approved`, `created_at` | Response model |

### `schemas/queue.py`

| Schema | Fields |
|---|---|
| `QueuedJobCreate` | `type: str` |
| `QueuedJobResponse` | All `QueuedJob` fields as Pydantic; `from_attributes = True` |

### `schemas/workspace.py`

| Schema | Fields |
|---|---|
| `WorkspaceDocument` | `id`, `title`, `summary?` |
| `WorkspaceSettings` | `theme: "light"|"dark"`, `language: str` |
| `WorkspaceState` | `settings`, `documents: List[WorkspaceDocument]`, `draft?`, `extras: Dict` |
| `WorkspacePayload` | `state: WorkspaceState`, `version: int` |
| `WorkspaceResponse` | `state`, `version`, `updated_at` |

---

## routes/ — API Endpoints

### routes/app registration

```
Notes router:   no prefix on router itself → mounted at /api in app.py
OCR router:     no prefix → /api/ocr
ASR router:     no prefix → /api/transcribe_diarized, /api/asr_engine
Auth router:    prefix /api/auth (set in router)
Admin router:   prefix /api/admin (set in router)
Admin users:    prefix /api/admin/users (set in router)
Workspace:      prefix /api/workspace (set in router)
Queue:          prefix /api/queue (set in router)
QA Chat:        prefix /qa → /api/qa/chat, /api/qa/chat_stream
QA Vision:      prefix /qa → /api/qa/vision
Perf/health:    no prefix → /api/health, /api/performance, /api/qa_config
Version:        no prefix → /api/version
RAG:            prefix /rag → /api/rag/weekly_summary, /api/rag/recent_updates
```

---

### routes/auth_users.py

**Auth prefix:** `/api/auth`  
**Auth required:** None on register/login/refresh; JWT (`get_current_user`) on `/me`, `/logout_all`

| Method | Path | Auth | Description |
|---|---|---|---|
| POST | `/api/auth/register` | None | Create new user (unapproved); returns UserProfile |
| POST | `/api/auth/login` | None | Verify credentials; returns JWT tokens + sets refresh cookie |
| GET | `/api/auth/me` | JWT | Returns current user profile |
| POST | `/api/auth/refresh` | None (token in body or cookie) | Rotates refresh token; issues new access token |
| POST | `/api/auth/logout` | None | Revokes refresh token, clears cookie |
| POST | `/api/auth/logout_all` | JWT | Revokes all refresh tokens for user |

**Helper:** `_issue_tokens(user, session, response)` — creates both tokens, persists refresh token hash, sets httponly cookie.

**Inter-file deps:** `core/db`, `core/security`, `core/dependencies`, `models/refresh_token`, `models/user`, `schemas/auth`

---

### routes/admin_users.py

**Auth prefix:** `/api/admin/users`  
**Auth required:** `get_current_admin` on all routes

| Method | Path | Description |
|---|---|---|
| GET | `/api/admin/users` | List all users |
| PATCH | `/api/admin/users/{user_id}/approve` | Set `is_approved=True` |
| PATCH | `/api/admin/users/{user_id}/reject` | Set `is_approved=False` |
| DELETE | `/api/admin/users/{user_id}` | Delete user (and tokens, workspace); blocks admin deletion |

**Inter-file deps:** `core/db`, `core/dependencies`, `models/refresh_token`, `models/user`, `models/workspace`, `schemas/auth`

---

### routes/admin.py

**Auth prefix:** `/api/admin`  
**Auth required:** `get_current_admin` on all routes (set at router level)

| Method | Path | Description |
|---|---|---|
| GET | `/api/admin/logs/tail` | Tail last N lines from `server/logs/` (default 200) |
| GET | `/api/admin/models` | List `.gguf` files under `models/llama/` and whisper models |
| POST | `/api/admin/models/select` | Update `llm_model` or `whisper_model` in config.json |
| POST | `/api/admin/models/parameters` | Update LLM/QA/server parameters in config.json |
| GET | `/api/admin/ocr/status` | TCP probe + HTTP health + `/v1/models` check on OCR server |
| GET | `/api/admin/llama/status` | TCP probe + health + `/v1/models` on llama-server; model sync check |
| GET | `/api/admin/llama/health` | Returns `{"running": false, "note": "externalized"}` (stubbed) |
| POST | `/api/admin/llama/start` | Returns `{"ok": false, "note": "externalized"}` (stubbed) |
| POST | `/api/admin/llama/stop` | Returns `{"ok": false, "note": "externalized"}` (stubbed) |
| POST | `/api/admin/llama/restart` | Returns `{"ok": false, "note": "externalized"}` (stubbed) |
| GET | `/api/admin/config` | Returns full config.json |
| POST | `/api/admin/config/save` | Saves full config.json blob |
| GET | `/api/admin/services/status` | NSSM/sc service status + TCP reachability for all 4 services |
| GET | `/api/admin/rag/status` | TCP probe + `/health` check on RAG service |

**Service control helpers:** `_service_status_win(name)`, `_service_action_win(name, action)` — uses NSSM then SC then taskkill. `_run_cmd(args)` wraps `subprocess.run`.

**External calls:** `requests.get` to llama-server `/health`, `/v1/models`; OCR server `/health`, `/v1/models`; RAG `/health`.

**Inter-file deps:** `core/dependencies`

---

### routes/workspace.py

**Auth prefix:** `/api/workspace`  
**Auth required:** `get_current_user`

| Method | Path | Description |
|---|---|---|
| GET | `/api/workspace/` | Get (or create) user workspace; returns WorkspaceResponse |
| PUT | `/api/workspace/` | Update workspace; optimistic versioning (409 on mismatch); size limit 2MB; merges ASR protection logic |
| POST | `/api/workspace/clear` | Reset workspace to baseline with cleared fields and updated `clearedAt` marker |

**Workspace merge logic (PUT):** Prevents stale client from wiping ASR transcription results unless `transcriptionCleared` flag is set. Respects cross-device `clearedAt` ordering.

**Inter-file deps:** `core/baseline`, `core/db`, `core/dependencies`, `models/workspace`, `schemas/workspace`

---

### routes/notes.py

**Auth:** Applied at app.py level (`require_api_bearer`). No prefix on router (mounted at `/api`).

This is the largest and most complex route file. It contains two note generation pipelines plus generation metadata/feedback endpoints.

#### Endpoints

| Method | Path | Description |
|---|---|---|
| POST | `/api/generate_v8_stream` | **Primary streaming note generation.** Accepts JSON or form-data with `transcription_text`, `old_visits_text`, `mixed_other_text`, `note_type`, `custom_prompt`, `user_speciality`. Streams text chunks; auto-starts consult comment + order request extraction in background after completion. Returns `X-Generation-Id` header. |
| GET | `/api/generation/{gen_id}/meta` | Returns generation metadata (RAG refs, QA status, pipeline) |
| GET | `/api/generation/{gen_id}/consult_comment` | Returns consult addendum status/result; triggers generation if not started. `?force=1` retries; `?strategy=` selects RAG focus strategy |
| GET | `/api/generation/{gen_id}/order_requests` | Returns order extraction status/items; triggers if not started. `?force=1` retries |
| GET | `/api/note_prompts` | Returns all note system/user prompt templates from config.json |
| POST | `/api/feedback` | Records thumbs up/down rating and optional suggestion for a generation ID |

**Legacy unrouted function:** `generate_stream()` — the old single-stream endpoint with QA + note generation in one flow. **Not mounted to any route in app.py** (no `@router.post` decorator, called as a function only). Used internally if called directly but not exposed as an HTTP endpoint.

**Unrouted function:** `generate_v8()` — non-streaming JSON version of v8. Also **not mounted** — no `@router.post` decorator.

#### Key Helper Functions

| Function | Description |
|---|---|
| `load_config()` | Reads config.json |
| `clean_model_output_chunk(chunk)` | Stream-safe EMR compatibility cleaner: removes NUL, converts Unicode subscripts/superscripts/dashes/quotes/spaces to ASCII |
| `clean_model_output_final(text)` | Post-stream cleanup: strips markdown, think blocks, XML wrappers, note markers, normalizes paragraphs, calls `normalize_clinical_note_output` |
| `_strip_note_end_marker(text)` | Removes `END_OF_NOTE` and everything after it |
| `_chunk_text_for_stream(text, max_chars)` | Splits pre-generated text into 600-char chunks for pseudo-streaming |
| `truncate_to_context_length_tokens(text, max_tokens)` | Hard word-count truncation with ellipsis |
| `_normalize_note_type(note_type)` | Normalizes aliases (e.g., `progress_note → progress`, `follow-up → followup`) |
| `_extract_actor(request, session)` | Extracts `user_id` + `user_email` from JWT for logging |
| `_split_prompt(prompt)` | Splits prompt into system/user parts for de-identified logging |
| `_deid_fields(fields)` | Runs `deidentify_text` on each input field; aggregates counts |
| `_log_case_completion(...)` | De-identifies prompt+output+inputs and appends to dataset JSONL |
| `_normalize_reference_items(raw_refs, cap?, sort_key?)` | Flattens RAG result metadata into UI-friendly reference objects + returns full_chunks |
| `_maybe_autostart_order_requests(gen_id, note_text, cfg)` | Background asyncio task if `order_request_autostart` config is truthy |
| `_maybe_autostart_consult_comment(gen_id, note_text, cfg, note_type)` | Background asyncio task if note_type is consult and `consult_comment_autostart` is truthy |
| `_rag_client_from_cfg(cfg)` | Instantiates `RAGHttpClient` from `RAG_URL` env |
| `_get_rag_comment_llm(cfg)` / `_get_order_request_llm(cfg)` | Instantiates `SimpleNoteGenerator` for specific LLM URLs |
| `_gather_rag_for_qa(question, cfg)` | Async: queries RAG + normalizes results; returns context, refs, weak_evidence flag |
| Various `_extract_*`, `_format_*`, `_merge_*`, `_dedupe_*` | Plan section extraction, imaging requisition formatting, medication line merging, order deduplication |

**Inter-file deps:** `services/note_generator_clean`, `services/rag_http_client`, `services/clinical_text_normalizer`, `core/db`, `core/deid/v1`, `core/logging/dataset_logger`, `core/security`, `core/stores/generation_store`, `core/prompt/builder`, `core/streaming/helpers`, `core/consult/pipeline`, `core/order/pipeline`, `core/qa_rag/helpers`, `models/user`, `metrics`

**External calls:** llama-server via `SimpleNoteGenerator`; RAG service via `RAGHttpClient`.

---

### routes/ocr.py

**Auth:** Applied at app.py level (`require_api_bearer`). No prefix on router.

| Method | Path | Auth | Description |
|---|---|---|---|
| POST | `/api/ocr` | Bearer | Upload image or PDF; returns `{success, text, confidence, engine_used, processing_time, pages_processed?}` |

**PDF handling:** Uses PyMuPDF (`fitz`) to render pages at `OCR_PDF_DPI` (200 DPI default) → PNG → OCR. Optional text-first extraction (`OCR_TEXT_FIRST=1`). Optional parallel pages (`OCR_PARALLEL_PAGES=1`, up to 4 workers).

**Image handling:** PIL downscale to `OCR_IMAGE_MAX_DIM` (3200px) → PNG normalization.

**HEIC/HEIF support:** Optional via `pillow_heif` if installed.

**External calls:** OCR llama-server at `OCR_URL_PRIMARY` (default :8090) via `OCRLLMEngine`.

**Inter-file deps:** `services/ocr_llm_client`, `metrics`

---

### routes/asr.py

**Auth:** `require_api_bearer` per-endpoint.

| Method | Path | Auth | Description |
|---|---|---|---|
| POST | `/api/transcribe_diarized` | Bearer | Proxy audio to whisper.cpp server; normalizes audio to 16kHz mono WAV via ffmpeg; round-robin across ASR_URL/ASR_URL_FALLBACK; returns plain text transcription |
| GET | `/api/asr_engine` | Bearer | Returns whisper.cpp engine info from upstream |

**ASR URL routing:** Round-robin + cooldown: primary marked down for 20s on failure; auto-derives fallback `:8096` if primary is `:8095`.

**FFmpeg:** Used to normalize audio. Resolved from `FFMPEG_BIN`, then `config.json → ffmpeg_path`, then `shutil.which("ffmpeg")`.

**External calls:** whisper.cpp `/inference` endpoint (POST multipart); `aiohttp` async client.

**Environment variables:** `ASR_URL`, `ASR_URL_FALLBACK`, `ASR_API_KEY` (default `"notegenadmin"`), `ASR_NORMALIZE_TO_WAV` (default `"1"`), `ASR_WHISPERCPP_VAD` (default `"0"`), `ASR_WHISPERCPP_NO_SPEECH_THOLD` (default `"1.0"`).

---

### routes/qa_chat.py

**Auth:** `HTTPBearer` per-endpoint (JWT decoded manually from `decode_access_token`). Prefix `/qa`.

| Method | Path | Auth | Description |
|---|---|---|---|
| POST | `/api/qa/chat` | Bearer | Non-streaming clinical Q&A; de-ids question → parallel RAG + web search → builds prompt → LLM → returns `QAChatResponse` |
| POST | `/api/qa/chat_stream` | Bearer | Streaming variant; yields tokens then emits `__QA_META__<json>` trailer |

**Session state:** In-memory `_QA_STATE: Dict[(user_id, session_id), Dict]`. Keeps last 12 turns + rolling summary (240 tokens, compressed after 8 turns).

**Evidence sources:**
1. RAG at `cfg["rag_service_url"]` (via `RAGHttpClient`)
2. SearXNG web search via `services/qa_web_search.py`

**Knowledge fallback:** If evidence is weak AND question matches dosing patterns (e.g., "what is the dose of…"), appends a knowledge-fallback disclaimer.

**Inter-file deps:** `core/security`, `services/note_generator_clean`, `core/deid/v1`, `services/qa_web_search`, `services/rag_http_client`

---

### routes/qa_vision.py

**Auth:** `HTTPBearer` per-endpoint (JWT decoded manually). Prefix `/qa`.

| Method | Path | Auth | Description |
|---|---|---|---|
| POST | `/api/qa/vision` | Bearer | Upload image + question; streams answer from vision LLM; no OCR fallback |

**Image limits:** Max 10MB; allowed types: jpeg/png/webp/bmp/gif.

**External calls:** Vision LLM at `VISION_QA_URL` or `OCR_URL_PRIMARY` (default :8081) via `VisionQAEngine`.

**Inter-file deps:** `core/security`, `services/vision_qa_client`

---

### routes/perf.py

**Auth:** None (all open).

| Method | Path | Description |
|---|---|---|
| GET | `/api/health` | Returns `{status: "ok", uptime_sec: N}` |
| GET | `/api/performance` | Returns full `Metrics.snapshot()` |
| GET | `/api/qa_config` | Returns QA config subset: `qa_max_user_chars`, `default_qa_max_tokens`, `qa_context_length` |

**Inter-file deps:** `metrics`

---

### routes/queue.py

**Auth:** `get_current_user` on all routes. Prefix `/api/queue`.

| Method | Path | Description |
|---|---|---|
| POST | `/api/queue` | Upload file and create queued job (`ocr` or `transcribe`); stores file to `data/queue_files/{user_id}/` |
| GET | `/api/queue` | List all queued jobs for current user (newest first) |
| DELETE | `/api/queue/{job_id}` | Delete job and its stored file |
| POST | `/api/queue/{job_id}/retry` | Reset job status to `pending` |
| GET | `/api/queue/{job_id}/download` | Download the stored file |
| POST | `/api/queue/{job_id}/process` | Process job server-side: calls `ocr_endpoint()` or POSTs to whisper.cpp synchronously; deletes file on success; stores error on failure |
| DELETE | `/api/queue` | Delete all queued jobs for current user (called on New Case / Clear) |

**File size limit:** 100MB.

**Inter-file deps:** `core/db`, `core/dependencies`, `models/queued_job`, `models/user`, `schemas/queue`, `routes/ocr (ocr_endpoint)`, `routes/asr (transcribe_diarized, helpers)`

---

### routes/rag_updates.py

**Auth:** None (open). Prefix `/rag`.

| Method | Path | Description |
|---|---|---|
| GET | `/api/rag/weekly_summary` | Reads `C:\RAG\fetch_log.jsonl` (or configured path); parses last 7 days of RAG ingestion runs; returns aggregated summary + per-source doc counts. Cached 15 minutes. |
| GET | `/api/rag/recent_updates` | Returns pre-generated `recent_updates.json` from RAG root; raises 503 if file is >7 days old |

**Config keys used:** `rag_root_dir`, `rag_fetch_log_path`, `rag_raw_docs_dir`, `rag_recent_updates_path`.

**Default paths:** All under `C:\RAG\`.

---

### routes/version.py

**Auth:** None (open).

| Method | Path | Description |
|---|---|---|
| GET | `/api/version` | Returns `{commit_hash, build_timestamp_utc, versions: {python, fastapi, uvicorn}, environment}` |

`COMMIT_HASH` resolved via `git rev-parse HEAD` at import time.

---

## services/ — External Service Clients

### services/note_generator_clean.py

**Purpose:** Async aiohttp client for llama-server, supporting both `/v1/chat/completions` (chat API) and `/completion` (legacy raw prompt) endpoints. Singleton pattern.

**Inter-file deps:** None (standalone).

**External calls:** `NOTEGEN_URL_PRIMARY` and `NOTEGEN_URL_FALLBACK` llama-server endpoints.

#### `class SimpleNoteGenerator`

| Method | Description |
|---|---|
| `__init__()` | Loads config, resolves URLs from env, sets cooldown state |
| `reload_config()` | Re-reads config.json (called after admin config changes) |
| `stream_completion(prompt, temperature, max_tokens, stop?)` | Async generator: sends streaming SSE request; yields content chunks; falls back to fallback URL on failure |
| `collect_completion(prompt, temperature, max_tokens, stop?)` | Awaits full response as string; retries with `/completion` if chat API returns empty |
| `_build_payload(prompt, temperature, max_tokens, stream, stop, force_chat?)` | Returns (payload, endpoint, used_chat) |
| `_build_chat_payload(...)` | OpenAI-format `/v1/chat/completions` payload |
| `_build_completion_payload(...)` | llama.cpp `/completion` payload |
| `_sampler_params(temperature, max_tokens, stream)` | Reads `default_repeat_penalty`, `default_top_p`, `default_top_k`, `default_min_p`, `default_seed` from config |
| `_extract_stream_content(data)` | Handles `content`, `text`, `choices[0].delta.content`, `choices[0].message.content`, `message.content` response formats |
| `_reset_context(base_url)` | Best-effort POST `/command {"cmd": "reset"}` to release KV cache |
| `_candidate_urls()` | Returns [primary] or [primary, fallback] depending on cooldown |
| `_mark_primary_down()` | Sets 20s cooldown on primary URL |

#### `class ExternalServiceError(RuntimeError)`
Fields: `service`, `primary_url`, `fallback_url`, `errors`.

#### `get_simple_note_generator() → SimpleNoteGenerator`
Module-level singleton factory.

---

### services/ocr_llm_client.py

**Purpose:** Synchronous (requests) client for OCR llama-server using vision chat completions API.

**External calls:** `OCR_URL_PRIMARY` / `OCR_URL_FALLBACK` (default :8090) via `requests.Session`.

#### `class OCRLLMEngine`

| Method | Description |
|---|---|
| `__init__(url, timeout, server_url?)` | Resolves URLs; loads model name from `OCR_MODEL_NAME` env or default |
| `check_server()` | GET `/health` probe |
| `_discover_vision_models()` | GET `/v1/models`; returns IDs matching vision keywords |
| `_resolve_model_id()` | Picks best matching model ID from discovered models |
| `ocr_image_bytes(image_bytes, mime_type?, _attempt?)` | Base64-encodes image → POST `/v1/chat/completions` with vision payload → extracts text → strips think blocks/XML artifacts → estimates confidence |
| `_estimate_confidence(text)` | Heuristic based on word count + medical indicators - artifact penalties |
| `_candidate_urls()` | Primary-first with 20s cooldown |
| `_mark_primary_down()` / `_flush_server_context(base_url)` | Cooldown + best-effort POST `/command reset` |

**OCR prompt:** `"Extract all visible text from this image... Output only the transcribed text without any commentary or explanation."`

**Note:** Contains `print("[DEBUG] OCR request...")` debug lines that appear in server logs.

#### `class ExternalServiceError(RuntimeError)` — same pattern as note_generator_clean.

---

### services/rag_http_client.py

**Purpose:** Async aiohttp client for the RAG service's `/query` endpoint. Composes context from snippets with metadata headers and enforces word-count caps.

**External calls:** `RAGHttpClient.base_url` (e.g. `http://127.0.0.1:8007`) → `/query` POST.

#### `class RAGHttpClient`

| Method | Description |
|---|---|
| `__init__(base_url, timeout)` | Default timeout 30s (passed as milliseconds) |
| `query(query, top_k, include_keywords?, date_from?, date_to?, specialty?)` | Async: POSTs to `/query`; if weak evidence (mean score <0.12 or <40 words), retries with expanded keyword set up to `top_k×2`; normalizes metadata; composes context string capped at `rag_max_context_words` (config, default 2400) |
| `_normalize_meta(md)` | Extracts `title`, `source`, `link`, `section`, `year` from various possible metadata key names |
| `_compose_context(items, max_words_total)` | Formats numbered snippet blocks with headers; enforces total word cap |
| `_snippet(text, max_words, max_sentences)` | First N sentences up to max_words |
| `_sentences(text)` | Simple regex sentence splitter |

---

### services/asr_whisperx.py

**Purpose:** WhisperX-based local ASR engine. **NOT currently used by the HTTP routes** (routes/asr.py proxies to an external whisper.cpp server). This module is a self-contained in-process WhisperX engine, likely used for local/batch ASR or as the original implementation.

**External dependencies:** `torch`, `whisperx`, `whisperx.diarize`, `whisperx.vads.pyannote`, `omegaconf`.

**Key design decisions:**
- `PassthroughVAD`: Custom VAD that splits audio into 30s chunks without speech detection — bypasses the pyannote VAD model entirely to avoid GPU contention
- `_BypassPyannoteVAD`: Monkey-patches `whisperx_pyannote.Pyannote` at import time
- Alignment on CPU (`align_device="cpu"`) and diarization on CPU (`diar_device="cpu"`) to avoid GPU contention with llama.cpp
- `torch.load` patched during diarization init to set `weights_only=False` for PyTorch 2.6+ compatibility

#### `class PassthroughVAD(Vad)`
Divides audio into 30-second `SegmentX` chunks; no silence detection.

#### `class ASRSession`
Accumulates audio chunks; detects format from magic bytes; writes to temp file on `finalize_to_file()`.

#### `class WhisperXASREngine`

| Method | Description |
|---|---|
| `__init__()` | Loads config; resolves model path, devices, compute type, initial prompt; creates temp-audio dir |
| `warmup()` | Calls `_ensure_models()` to pre-load models |
| `new_session(initial_prompt?, file_suffix?)` | Creates `ASRSession` |
| `append_chunk(session_id, data)` | Adds bytes to session |
| `transcribe_stream(session_id)` | Finalizes file → FFmpeg convert → `_transcribe_internal()` → yields formatted segment lines |
| `transcribe(session_id)` | Non-streaming; returns `(text, confidence)` |
| `_transcribe_internal(wav_path)` | WhisperX pipeline: load audio → transcribe → align (CPU) → diarize (CPU optional) → flush CUDA cache |
| `_format_segments(segments)` | Formats `[SPEAKER] text` per segment |
| `_retain_audio_copy(wav_path)` | Saves copy to `temp-audio/` dir; keeps last N |
| `get_info()` | Returns engine state dict |

---

### services/clinical_text_normalizer.py

**Purpose:** Post-processing normalizer for generated clinical note text: converts number-words to numerals + unit abbreviations; optionally canonicalizes medication names via RxNorm.

#### `class RxNormIndex`
Lazy loader for RxNorm `RXNCONSO.RRF` file. Reads via `RXNORM_TERMS_FILE` or `RXNORM_DIR` env vars. Filters to `SAB=RXNORM, TTY in {IN, PIN, BN, SCD, SBD}`. Caches deduplicated term list.

| Method | Description |
|---|---|
| `best_match(query, min_confidence)` | `SequenceMatcher` ratio comparison; returns match if `≥ min_confidence` (default 0.93) |

#### Functions

| Function | Signature | Description |
|---|---|---|
| `_parse_number_words` | `(words: str) → Optional[int]` | Converts English number words (up to thousands) to int |
| `_replace_dose_words` | regex match handler | Used in `_DOSE_WORDS_RE.sub()` |
| `normalize_numeric_units` | `(text: str) → Tuple[str, int]` | Regex substitution: spelled-out numbers + units → compact numeric form; also normalizes unit spacing |
| `canonicalize_medication_lines` | `(text, min_confidence) → Tuple[str, int]` | Per-line RxNorm name matching and replacement for medication lines |
| `normalize_clinical_note_output` | `(text: str) → NormalizationResult` | Runs both passes; returns `NormalizationResult(text, unit_conversions, rxnorm_replacements)` |

**RxNorm is optional:** if file not found, `_RXNORM.terms()` returns `[]` and canonicalization is a no-op.

---

### services/qa_web_search.py

**Purpose:** Async SearXNG web search with allowlisted medical/regulatory domains.

**External calls:** SearXNG at (in order): `http://127.0.0.1:8083/search`, `SEARXNG_URL`, `http://127.0.0.1:8083/searxng/search`, `http://127.0.0.1:3443/searxng/search`.

| Function | Signature | Description |
|---|---|---|
| `_allowed(url)` | `(url: str) → bool` | Checks against strict allowlist (PubMed, NEJM, JAMA, Lancet, BMJ, WHO, CDC, NICE, FDA, EMA, etc.) |
| `searx_search` | `async (query, limit=8) → List[Dict]` | Tries each SearXNG base URL with/without API key; filters to allowed domains; falls back to semi-allowed (gov/edu/nih) if strict allowlist returns empty |

**Result format:** `{title, url, snippet (≤500 chars), source: "web"}`.

---

### services/vision_qa_client.py

**Purpose:** Async aiohttp streaming client for vision-capable LLM (uses same pattern as `SimpleNoteGenerator`).

**External calls:** `VISION_QA_URL` or `OCR_URL_PRIMARY` (default :8081) → `/v1/chat/completions`.

#### `class VisionQAEngine`

| Method | Description |
|---|---|
| `__init__(url, timeout, model_name)` | Resolves URL from env; defaults to `:8081` |
| `_discover_vision_models()` | Async GET `/v1/models`; matches vision keywords |
| `_resolve_model_id()` | Picks best model name match |
| `_build_vision_payload(image_b64, mime_type, question, stream)` | OpenAI vision chat payload with medical analysis prompt |
| `stream_vision_answer(image_bytes, mime_type, question)` | Async generator: base64-encodes → resolves model → streams SSE chunks |
| `_reset_context(base_url)` | Best-effort POST `/command reset` |

**Medical prompt:** Asks for visual findings, differential, safety red flags, recommended next steps, with explicit uncertainty disclaimers.

**Inter-file deps:** `services/note_generator_clean.ExternalServiceError`

---

## scripts/create_admin.py

**Purpose:** CLI script to create the initial admin user in the database.

**Usage:** `python scripts/create_admin.py`

**Functions:**

| Function | Description |
|---|---|
| `prompt(prompt_text, default)` | Interactive input with default |
| `create_admin_user(email, password)` | Creates user with `is_admin=True, is_approved=True, is_active=True` via SQLModel session. Skips if email already exists. |
| `main()` | Calls `init_db()`, prompts for email and password, auto-generates password if blank |

**Inter-file deps:** `server/core/db`, `server/core/security`, `server/models/user`

---

## Dead / Commented-Out Code

| Location | Code | Notes |
|---|---|---|
| `app.py` | `#from server.routes.services import router as services_router` | A `services` router was planned but never implemented |
| `app.py` | `#app.include_router(services_router)` | Same — service management was moved to `admin.py` |
| `routes/notes.py` | `generate_stream()` function | Legacy all-in-one streaming endpoint; NOT mounted to any route (no `@router.post` decorator); code still present and functional if called directly |
| `routes/notes.py` | `generate_v8()` function | Non-streaming v8 endpoint; NOT mounted (no decorator); used as internal helper |
| `routes/notes.py` | `truncate_to_context_length()` (second definition) | Near-duplicate of `truncate_to_context_length_tokens()` using `max_tokens * 0.75` scaling — the first definition (1:1 ratio) is preferred and both coexist |
| `routes/notes.py` | `clean_model_output = clean_model_output_chunk` (appears twice) | Backward-compat alias defined twice in same file |
| `routes/admin.py` | llama start/stop/restart/health endpoints | All return stubbed `{"ok": false, "note": "externalized"}` — llama management was externalized |
| `routes/admin.py` | `_configure_llama_service()` | Returns `(True, "skipped")` — Windows service configuration was removed |
| `services/asr_whisperx.py` | Entire `WhisperXASREngine` class | Not referenced by any active HTTP route; `routes/asr.py` proxies to external whisper.cpp |
| `routes/ocr.py` | `OCR_ENABLE_TEXT_FIRST = os.environ.get("OCR_TEXT_FIRST", "0") == "1"` | PDF text-first extraction disabled by default |
| `routes/ocr.py` | `OCR_ENABLE_PARALLEL_PAGES = os.environ.get("OCR_PARALLEL_PAGES", "0") == "1"` | Parallel PDF page processing disabled by default |

---

## API Endpoint Quick Reference

### Open (No Auth)

| Method | Path | Description |
|---|---|---|
| GET | `/api/health` | Health check |
| GET | `/api/version` | Server version info |
| GET | `/api/performance` | Metrics snapshot |
| GET | `/api/qa_config` | QA config subset |
| GET | `/api/rag/weekly_summary` | RAG ingestion summary |
| GET | `/api/rag/recent_updates` | RAG recent updates cache |
| POST | `/api/auth/register` | Register new user |
| POST | `/api/auth/login` | Login → tokens |
| POST | `/api/auth/refresh` | Refresh access token |
| POST | `/api/auth/logout` | Logout (revoke refresh token) |
| GET | `/` | Redirect to `/static/admin.html` |

### Bearer Token Required (User JWT)

| Method | Path | Description |
|---|---|---|
| GET | `/api/auth/me` | Current user profile |
| POST | `/api/auth/logout_all` | Revoke all sessions |
| GET | `/api/workspace/` | Get workspace |
| PUT | `/api/workspace/` | Update workspace |
| POST | `/api/workspace/clear` | Clear workspace |
| POST | `/api/generate_v8_stream` | Stream clinical note generation |
| GET | `/api/generation/{id}/meta` | Generation metadata |
| GET | `/api/generation/{id}/consult_comment` | Consult addendum |
| GET | `/api/generation/{id}/order_requests` | Order extraction |
| GET | `/api/note_prompts` | Note prompt templates |
| POST | `/api/feedback` | Rate a generation |
| POST | `/api/ocr` | OCR image/PDF |
| POST | `/api/transcribe_diarized` | ASR audio → text |
| GET | `/api/asr_engine` | ASR engine info |
| POST | `/api/qa/chat` | Clinical Q&A (non-streaming) |
| POST | `/api/qa/chat_stream` | Clinical Q&A (streaming) |
| POST | `/api/qa/vision` | Vision Q&A (streaming) |
| POST | `/api/queue` | Create queued job |
| GET | `/api/queue` | List queued jobs |
| DELETE | `/api/queue` | Clear all queued jobs |
| DELETE | `/api/queue/{id}` | Delete specific queued job |
| POST | `/api/queue/{id}/retry` | Retry queued job |
| GET | `/api/queue/{id}/download` | Download queued file |
| POST | `/api/queue/{id}/process` | Process queued job server-side |

### Admin JWT Required

| Method | Path | Description |
|---|---|---|
| GET | `/api/admin/users` | List all users |
| PATCH | `/api/admin/users/{id}/approve` | Approve user |
| PATCH | `/api/admin/users/{id}/reject` | Reject user |
| DELETE | `/api/admin/users/{id}` | Delete user |
| GET | `/api/admin/logs/tail` | Tail server logs |
| GET | `/api/admin/models` | List model files |
| POST | `/api/admin/models/select` | Set active model |
| POST | `/api/admin/models/parameters` | Update model parameters |
| GET | `/api/admin/ocr/status` | OCR server status |
| GET | `/api/admin/llama/status` | LLaMA server status |
| GET | `/api/admin/llama/health` | LLaMA health (stubbed) |
| POST | `/api/admin/llama/start` | LLaMA start (stubbed) |
| POST | `/api/admin/llama/stop` | LLaMA stop (stubbed) |
| POST | `/api/admin/llama/restart` | LLaMA restart (stubbed) |
| GET | `/api/admin/config` | Get full config |
| POST | `/api/admin/config/save` | Save full config |
| GET | `/api/admin/services/status` | All services status |
| GET | `/api/admin/rag/status` | RAG service status |
