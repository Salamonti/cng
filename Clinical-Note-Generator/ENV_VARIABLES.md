# Environment Variables Reference

All environment variables used by the Clinical Note Generator FastAPI server.
Variables are grouped by subsystem. Most have sensible defaults and are optional.

---

## Server

| Variable | Default | Description |
|---|---|---|
| `FASTAPI_PORT` | `7860` | Port for the FastAPI/Uvicorn server |
| `ENV` | _(unset)_ | Environment label shown in `/api/version` (e.g., `production`, `staging`) |

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
| `ASR_URL` | `http://127.0.0.1:8095` | Primary ASR endpoint |
| `ASR_URL_FALLBACK` | _(empty)_ | Fallback ASR endpoint (optional) |
| `ASR_API_KEY` | `notegenadmin` | API key for ASR service |
| `ASR_ENABLE_DIARIZATION` | `0` | Enable speaker diarization (`1`/`0`) |
| `ASR_ENABLE_ALIGNMENT` | `0` | Enable word-level alignment (`1`/`0`) |
| `ASR_COMPUTE_TYPE` | `float16` | Compute type for WhisperX model |
| `ASR_INITIAL_PROMPT` | _(empty)_ | Initial prompt for Whisper decoding |
| `ASR_MODEL_PATH` | _(auto)_ | Path to local Whisper model |
| `ASR_TRANSCRIBE_BATCH_SIZE` | `16` | Batch size for transcription |
| `ASR_WHISPERCPP_VAD` | _(unset)_ | Enable VAD for whisper.cpp |
| `ASR_WHISPERCPP_NO_SPEECH_THOLD` | _(unset)_ | No-speech threshold for whisper.cpp |
| `ASR_NORMALIZE_TO_WAV` | `0` | Convert audio to WAV before transcription |
| `FFMPEG_BIN` | _(system PATH)_ | Path to ffmpeg binary |
| `HF_TOKEN` / `HUGGINGFACE_TOKEN` | _(unset)_ | HuggingFace token for model downloads |
| `CUDA_VISIBLE_DEVICES` | _(system)_ | GPU device(s) for ASR |

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
      "prior_visits_budget_tokens": 4096,
      "labs_imaging_other_budget_tokens": 6144,
      "current_encounter_budget_tokens": 4096
    }
  }
}
```

### Token budgets

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
