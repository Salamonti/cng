# Environment Variables Reference

All environment variables used by the Clinical Note Generator FastAPI server.
Variables are grouped by subsystem. Most have sensible defaults and are optional.

---

## Server

| Variable | Default | Description |
|---|---|---|
| `FASTAPI_PORT` | `7860` | Port for the FastAPI/Uvicorn server |
| `ENV` | _(unset)_ | Environment label shown in `/api/version` (e.g., `production`, `staging`) |
| `DREAMCISION_GIT_COMMIT` | _(unset)_ | Optional full git SHA for `/api/version` when `git` is not on `PATH` or `.git` is missing (same idea as `GIT_COMMIT_HASH`) |
| `GIT_COMMIT_HASH` | _(unset)_ | Same as `DREAMCISION_GIT_COMMIT` (either may be set) |
| `CORS_ALLOW_ALL` | _(unset)_ | If `1`/`true`, allow any origin with `Access-Control-Allow-Origin: *` and **disable** credentialed cookies (dev only). |
| `CORS_ALLOWED_ORIGINS` | _(empty)_ | Comma-separated explicit origins (e.g. `https://notes.ieissa.com:3443`) — combined with regex when set. |
| `CORS_ALLOW_ORIGIN_REGEX` | _(built-in)_ | Regex for allowed `Origin` headers; default allows `localhost` / `127.0.0.1` and `*.ieissa.com` / `*.ieissa.ca` / `*.eissa.ca`. Override only if you know the pattern. |
| `GENERATION_STORE_TTL_SECONDS` | `86400` | TTL for in-memory generation / consult / order caches (`core.stores.generation_store`). |

## Feature flags (Grand Plan G0)

Source of truth: `server/core/feature_flags.py`. **Policy** (see [GRAND_PLAN Feature flag policy](../../docs/GRAND_PLAN.md#feature-flag-policy-authoritative--supersedes-any-all-flags-off-wording-below)): the five **safety/sync fixes default ON** because each fixes silent data loss or auth breakage; the **new streaming capability defaults OFF** until Phase 4 parity sign-off. To roll a safety fix back to legacy behavior, set that single flag `=0`. (Older plan text saying "deploy all flags OFF" is the conservative alternative only — it requires explicitly exporting each flag `=0`; a fresh process with no env override uses the defaults below.)

| Variable | Default | Class | Description |
|---|---|---|---|
| `SYNC_BEACON_FIX` | `true` | safety | Authenticated keepalive PUT on tab unload |
| `SYNC_AUTH_FETCH` | `true` | safety | Unified authFetch + cold refresh cookie path |
| `SYNC_ANTI_STOMP` | `true` | safety | Block server pull/409 from overwriting unsaved local edits |
| `ASR_RECORDING_RECONCILE` | `true` | safety | `has_encounter_recording` on workspace GET/version |
| `ASR_PIPELINE_REFRESH` | `true` | safety | Proactive token refresh during clinical busy phases |
| `STREAMING_ASR_ENABLED` | `false` | capability | Live streaming ASR (Phase 4); never default-on before parity sign-off |

## Authentication & Database

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `sqlite:///data/user_data.sqlite` | SQLAlchemy database URL for user auth |
| `JWT_SECRET` | _(required)_ | Secret key for signing access tokens |
| `JWT_REFRESH_SECRET` | _(required)_ | Secret key for signing refresh tokens |
| `JWT_ACCESS_TOKEN_EXP_MINUTES` | `600` | Access token expiry (minutes) |
| `JWT_REFRESH_TOKEN_EXP_DAYS` | `30` | Refresh token expiry (days) |

> Auth values can also be set in `config/config.json`. Env vars take precedence.

## Note Generation (LLM)

| Variable | Default | Description |
|---|---|---|
| `NOTEGEN_URL_PRIMARY` | `http://127.0.0.1:8081` | Primary LLM endpoint for note generation |
| `NOTEGEN_URL_FALLBACK` | _(empty)_ | Fallback LLM endpoint (optional) |
| `LLAMA_SERVER` | _(unset)_ | Path to llama-server binary (for auto-managed mode) |

### LLM routing (per feature) — **P2**

Set **`LLM_*`** variables to point each surface at a different `llama-server` (or leave unset to use **`NOTEGEN_URL_*`**). All values are **base URLs** (no `/v1/...` path).

| Variable | Falls back to | Used for |
|---|---|---|
| `LLM_NOTE_GEN_URL` | `NOTEGEN_URL_PRIMARY` | Clinical note streaming (`/generate_v8_stream`, etc.) |
| `LLM_NOTE_GEN_URL_FALLBACK` | `NOTEGEN_URL_FALLBACK` | Same |
| `LLM_QA_TEXT_URL` | `NOTEGEN_URL_PRIMARY` | QA chat text (`/api/qa/chat`, `/chat_stream`) |
| `LLM_QA_TEXT_URL_FALLBACK` | `NOTEGEN_URL_FALLBACK` | Same |
| `LLM_OCR_URL` | `OCR_URL_PRIMARY` → `http://127.0.0.1:8090` | Document/camera OCR |
| `LLM_OCR_URL_FALLBACK` | `OCR_URL_FALLBACK` | Same |
| `LLM_QA_VISION_URL` | `VISION_QA_URL` → `OCR_URL_PRIMARY` → `http://127.0.0.1:8081` | QA with image |
| `LLM_QA_VISION_URL_FALLBACK` | `VISION_QA_URL_FALLBACK` | Same |
| `LLM_RAG_COMMENT_URL` | `rag_comment_llm_url` in `config.json`, then `NOTEGEN_URL_PRIMARY` | RAG consult comment |
| `LLM_RAG_COMMENT_URL_FALLBACK` | _(only when primary set from env)_ | Same |
| `LLM_ORDER_REQUEST_URL` | `order_request_llm_url` in `config.json`, then `NOTEGEN_URL_PRIMARY` | Order/imaging extraction |
| `LLM_ORDER_REQUEST_URL_FALLBACK` | _(only when primary set from env)_ | Same |

Startup logs list **resolved host:port** per row (no paths, no secrets).

**Where to set ports for QA vs notes:** edit **`start_fastapi_server_external.bat`** — see the **“Per-feature LLM”** comment block — add lines such as `set LLM_QA_TEXT_URL=http://127.0.0.1:9080` (port is in the URL). If `LLM_QA_TEXT_URL` is not set, QA uses `NOTEGEN_URL_PRIMARY` (same server/port as note generation).

## OCR

| Variable | Default | Description |
|---|---|---|
| `OCR_URL_PRIMARY` | `http://127.0.0.1:8090` | Primary OCR LLM endpoint |
| `OCR_URL_FALLBACK` | _(empty)_ | Fallback OCR endpoint (optional) |
| `OCR_PDF_DPI` | `200` | DPI for PDF-to-image conversion |
| `OCR_IMAGE_MAX_DIM` | `2048` | Max image dimension (pixels) before resize |
| `OCR_TEXT_FIRST` | `0` | If `1`, try text extraction before OCR |
| `OCR_PARALLEL_PAGES` | `1` | Number of pages to OCR in parallel |
| `OCR_MODEL` | _(from config)_ | Path to OCR GGUF model |
| `MMPROJ_MODEL` | _(from config)_ | Path to OCR multimodal projector model |
| `OCR_MODEL_NAME` | _(from config)_ | Display name for OCR model |
| `OCR_CHAT_MODEL` | _(from config)_ | Chat model name for OCR endpoint |
| `OCR_CUDA_VISIBLE_DEVICES` | `0` | GPU device(s) for OCR server |
| `DEBUG_OCR_ERRORS` | `0` | If `1`, log full OCR error details to console |

## ASR (Speech-to-Text)

| Variable | Default | Description |
|---|---|---|
| `ASR_URLS` | _(empty)_ | Optional comma/semicolon-separated ASR endpoint list (overrides/adds to `ASR_URL` pool order). When `service_endpoints.json` contains multiple `whisper_instances`, `service_endpoints_sync.py` derives this automatically. |
| `ASR_URL` | `http://127.0.0.1:8095` | Primary ASR endpoint |
| `ASR_URL_FALLBACK` | _(empty)_ | Fallback ASR endpoint (optional) |
| `ASR_API_KEY` | `notegenadmin` | API key for ASR service |
| `ASR_WHISPERCPP_VAD` | _(unset)_ | Enable VAD for whisper.cpp |
| `ASR_WHISPERCPP_NO_SPEECH_THOLD` | _(unset)_ | No-speech threshold for whisper.cpp |
| `ASR_NORMALIZE_TO_WAV` | `1` | Convert audio to WAV before transcription |
| `ASR_QUEUE_TIMEOUT_SEC` | `300` | Queue transcription per-attempt timeout (seconds) |
| `FFMPEG_BIN` | _(system PATH)_ | Path to ffmpeg binary |
| `HF_TOKEN` / `HUGGINGFACE_TOKEN` | _(unset)_ | HuggingFace token for model downloads |
| `CUDA_VISIBLE_DEVICES` | _(system)_ | GPU device(s) for ASR |

> Current ASR routing is whisper.cpp / `whisper-server` through `/api/transcribe_diarized`. Older WhisperX-style knobs such as `ASR_ENABLE_ALIGNMENT`, `ASR_COMPUTE_TYPE`, `ASR_MODEL_PATH`, and `ASR_TRANSCRIBE_BATCH_SIZE` are not read by this path.

## RAG (Retrieval-Augmented Generation)

| Variable | Default | Description |
|---|---|---|
| `RAG_URL` | `http://127.0.0.1:8007` | RAG service endpoint |
| `SEARXNG_URL` | _(unset)_ | SearXNG search endpoint for QA web search |
| `SEARXNG_API_KEY` | _(unset)_ | API key for SearXNG |

## Vision QA

| Variable | Default | Description |
|---|---|---|
| `VISION_QA_URL` | _(falls back to OCR_URL_PRIMARY)_ | Vision QA endpoint |
| `VISION_QA_URL_FALLBACK` | _(empty)_ | Fallback vision QA endpoint |
| `VISION_QA_MODEL` | _(falls back to OCR_MODEL_NAME)_ | Model name for vision QA |

## Admin UI & process control

Gates for **`admin.html`** and **`/api/admin/*`**. Operational layout: repo [`docs/OPERATOR_RUNBOOK_WINDOWS.md`](../../docs/OPERATOR_RUNBOOK_WINDOWS.md).

| Variable | Default (launcher) | Description |
|---|---|---|
| `ADMIN_PROCESS_CONTROL_ENABLED` | **`0`** in `start_fastapi_server_external.bat` (set **`1`** on trusted dev machines) | Master switch: **office stack** (Node/FastAPI/RAG) + **AI** (`llama-server` / `whisper-server`) start/stop from admin. |
| `ADMIN_AI_PROCESS_CONTROL_ENABLED` | _unset_ | Legacy: AI-only if set without the master flag above. |
| `ADMIN_OFFICE_STACK_PROCESS_CONTROL_ENABLED` | _unset_ | Legacy: office-only if set without the master flag. |
| `ADMIN_SERVICE_CONTROL_ENABLED` | _unset_ (off) | **Windows NSSM / `sc` control** for allowlisted service names from `service_endpoints.json`. |
| `ADMIN_MUTATIONS_LOCALHOST_ONLY` | _unset_ | If `1` / `true` / `yes` / `on`, **POST/PUT/PATCH/DELETE** under `/api/admin` return **403** unless the ASGI client address is loopback (`127.0.0.1` or `::1`). Use when FastAPI binds `0.0.0.0`. |

Whisper **`launch.arguments`** examples: [`WHISPER_LAUNCH_ARGUMENTS.md`](./WHISPER_LAUNCH_ARGUMENTS.md).

## Conversational Normalizer

| Variable | Default | Description |
|---|---|---|
| `CONV_NORMALIZER_URL` / `LLM_BASE_URL` | _(unset)_ | LLM endpoint for conversational normalization |
| `LLM_TIMEOUT` | `60` | Timeout (seconds) for normalizer LLM calls |
| `LLM_API_KEY` | _(empty)_ | API key for normalizer LLM |
| `LLM_MODEL_ID` | _(auto)_ | Model ID for normalizer |
| `NORMALIZER_DEBUG` | `0` | Enable normalizer debug logging |

## Clinical Text Normalizer

| Variable | Default | Description |
|---|---|---|
| `RXNORM_TERMS_FILE` | _(unset)_ | Path to RxNorm terms file |
| `RXNORM_DIR` | _(unset)_ | Path to RxNorm data directory |

## De-Identification (PHI Redaction)

| Variable | Default | Description |
|---|---|---|
| `CNG_DEID_NER` | `1` (ON) | Enable spaCy NER for name redaction. Set to `0` to disable. |
| `CNG_DEID_SPACY_MODEL` | `en_core_web_sm` | spaCy model to use for NER |

> NER requires `spacy` + model installed. If missing, it silently falls back to regex-only.

## Dataset Logging

| Variable | Default | Description |
|---|---|---|
| `CNG_DATASET_DIR` | `data/datasets/` | Directory for JSONL dataset logs |

## Preprocessing & Truncation

| Variable | Default | Description |
|---|---|---|
| `CNG_TRUNCATION_DEBUG` | `0` (OFF) | Enable truncation debug logging to server console. Shows per-paragraph scores, kept/dropped decisions, and token counts. |

> Preprocessing settings (enabled, steps, token budgets) are configured in `config/config.json` under the `"preprocessing"` key, not via env vars.

### Preprocessing config example (`config/config.json`)

```json
{
  "preprocessing": {
    "enabled": true,
    "steps": {
      "remove_boilerplate": true,
      "collapse_repeated_headers": true,
      "remove_junk_artifacts": true,
      "deduplicate_blocks": true,
      "normalize_whitespace": true
    },
    "truncation": {
      "enabled": true,
      "prior_visits_budget_tokens": 24576,
      "labs_imaging_other_budget_tokens": 24576,
      "current_encounter_budget_tokens": 24576
    }
  }
}
```

### Token budgets

Defaults (when keys are omitted): **24,576 tokens** (`24 × 1024`) per section for prior visits, labs/imaging/other, and current encounter — up to **~72k tokens** of chart input combined. Preprocessing **cleaning is on by default**; truncation runs **only when** a section still exceeds its budget after cleaning.

Token budgets are **per-section maximums**. If a section is smaller than its budget, no truncation occurs. When truncation is needed, paragraphs are scored by:

1. **Date recency** (newer = higher priority)
2. **Clinical signal** (numeric values, units, medical terms)
3. **Low-info penalty** (short paragraphs with no clinical content)

The highest-scoring paragraphs are kept (in original order) until the budget is filled.

---

## Quick Start (PowerShell)

```powershell
# Required
$env:JWT_SECRET="your-secret-here"
$env:JWT_REFRESH_SECRET="your-refresh-secret-here"

# Optional overrides
$env:FASTAPI_PORT="7860"
$env:CNG_DEID_NER="1"
$env:CNG_TRUNCATION_DEBUG="1"

# Start
.\start_fastapi_server_external.bat
```

## Quick Start (cmd.exe)

```bat
REM Required
set JWT_SECRET=your-secret-here
set JWT_REFRESH_SECRET=your-refresh-secret-here

REM Optional overrides
set FASTAPI_PORT=7860
set CNG_DEID_NER=1
set CNG_TRUNCATION_DEBUG=1

REM Start
start_fastapi_server_external.bat
```
