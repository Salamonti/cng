# External Services Migration Plan (Notes/OCR/ASR/RAG)

Date: 2026-01-27
Owner: Clinical-Note-Generator
Scope: Move note generation (llama-server), OCR, and ASR to external servers started from CMD. Keep RAG service at 8007 (no LLM). Remove conversation normalizer entirely. All endpoints resolved from environment variables only (no config fallback).

---

## Goals
1) **No Python-managed llama-server or OCR server processes.**
2) **Environment variables are the only configuration source** (no config fallback). Config may contain commented reference values only.
3) **Primary + fallback endpoints** for note-gen + OCR with simple LB/failover on connection errors, timeouts, or HTTP 5xx.
4) **User-facing banner** for service errors including exact error details; manual retry clears banner; auto-dismiss after 2 minutes.
5) **Remove Conv Normalizer completely** (code + references).
6) **RAG stays on single endpoint (8007)**, no fallback.
7) **ASR externalized now** as a separate FastAPI app on its own port (e.g., 8095) so it can be used remotely by other apps.

---

## Required Environment Variables (Single Source of Truth)
### Note Gen / Q&A (llama-server)
- `NOTEGEN_URL_PRIMARY` (example: http://127.0.0.1:8081)
- `NOTEGEN_URL_FALLBACK` (example: http://127.0.0.1:8036)

### OCR (llama-server multimodal)
- `OCR_URL_PRIMARY` (example: http://127.0.0.1:8090)
- `OCR_URL_FALLBACK` (example: http://127.0.0.1:8091)
- `OCR_MODEL_NAME` (optional, used to match /v1/models)

### RAG (single endpoint, no fallback)
- `RAG_URL` (example: http://127.0.0.1:8007)

### ASR (externalized)
- `ASR_URL` (example: http://127.0.0.1:8095)
- `ASR_ENABLE_DIARIZATION` (default: 1; set to 0 to disable diarization if it causes issues)

---

## Current Call Paths (Observed)
- **Note Gen / Q&A:** `server/routes/notes.py` -> `SimpleNoteGenerator` (`server/services/note_generator_clean.py`) -> llama-server `/v1/chat/completions` or `/completion`.
- **OCR:** `server/routes/ocr.py` -> `OCRLLMEngine` (`server/services/ocr_llm_client.py`) -> llama-server multimodal `/v1/chat/completions`.
- **ASR:** `server/routes/asr.py` -> `WhisperXASREngine` (`server/services/asr_whisperx.py`) (in-process, Python only).
- **RAG:** `server/services/rag_http_client.py` uses `rag_service_url` (to be replaced with RAG_URL env).
- **Conv Normalizer:** `server/services/conv_normalizer.py` (unused; to be removed).

---

## ASR Externalization Details (Port 8095)
We will create a **separate ASR FastAPI app** that exposes the same endpoints you already use:
- `POST /transcribe_diarized` (multipart/form-data with field `audio`)
- `GET /asr_engine`

This makes ASR callable from **any other app** via URL + port, just like OCR/NoteGen. It is still Python-based (WhisperX requirement), but fully decoupled from the main app process.

### Diarization notes
- Current diarization uses pyannote via WhisperX. This can be heavy (model size, HF token needs, GPU load).
- If diarization causes issues, we can disable it with `ASR_ENABLE_DIARIZATION=0`. The service will return a single-speaker transcript instead.

### Startup example
- `uvicorn asr_service:app --host 0.0.0.0 --port 8095`

---

## Proposed Changes (No Code Yet)
### A) Endpoint Resolution (No Config Fallback)
- Update service clients to resolve URLs **only** from env vars:
  - NoteGen/Q&A: `NOTEGEN_URL_PRIMARY` + `NOTEGEN_URL_FALLBACK`
  - OCR: `OCR_URL_PRIMARY` + `OCR_URL_FALLBACK`
  - RAG: `RAG_URL` only
  - ASR: `ASR_URL` only (main app becomes client; ASR service runs separately)

### B) Remove Auto-Start and Internal Managers
- **Note Gen:** remove any internal llama-server manager usage (no auto-start logic, no hints to enable auto-manage).
- **OCR:** remove `_ensure_server_running()` and any reference to `get_ocr_server_manager()`.

### C) Remove Conversation Normalizer Completely
- Delete `server/services/conv_normalizer.py`.
- Remove any imports or references to it.

### D) Failover / Simple LB Strategy
- If primary fails (timeout, connection error, HTTP 5xx), try fallback.
- Cooldown for primary: **20 seconds** before retrying it.
- Request timeout: **90 seconds** (override existing 120s).

### E) UI Error Banner
- Add persistent banner on UI when a service fails after both primary+fallback (or only primary for RAG/ASR).
- Banner should include:
  - Service name
  - Primary/fallback URL
  - Error message/HTTP status
  - “Please report this issue” text
- Banner hides on next successful request or auto-dismisses after **2 minutes**.

---

## llama-server CMD Checklist (Note Gen + Q&A)
Use llama.cpp server, started manually from CMD:

Required args:
- `llama-server.exe`
- `--model <path_to.gguf>`
- `--host 0.0.0.0`
- `--port 8081`
- `--ctx-size <context_length>` (example 64000)
- `--n-gpu-layers <N>`
- `--ubatch-size <N>`
- `--threads <N>`
- `--batch-size <N>`
- `--parallel <N>`

Optional args (use as needed):
- `--presence-penalty <val>`
- `-ctk <val>`
- `-ctv <val>`
- `--log-disable`
- `--no-context-shift`
- `--cont-batching`
- `-fa <on|off|...>`
- `--no-mmap`
- `--chat-template <template>`
- `--jinja`

Optional envs:
- `CUDA_VISIBLE_DEVICES`
- `GGML_CUDA_FORCE_CUBLAS`
- `GGML_CUDA_MMQ_ENABLE`
- `GGML_CUDA_DISABLE`

(Plan will keep a commented reference block of these in config.json for convenience, but code will not read them.)

---

## OCR llama-server CMD Checklist (Multimodal)
Required args:
- `--model <ocr_model.gguf>`
- `--mmproj <mmproj.gguf>`
- `--host 0.0.0.0`
- `--port 8090`
- `--ctx-size <ocr_ctx_size>`
- `--n-gpu-layers <N>`
- `--threads <N>`
- `--batch-size <N>`
- `--parallel <N>`

Optional envs:
- `OCR_MODEL_NAME`
- `CUDA_VISIBLE_DEVICES` or `OCR_CUDA_VISIBLE_DEVICES`

---

## Proposed File Changes (No Code Yet)
1) `server/services/note_generator_clean.py`
   - Read NOTEGEN_URL_PRIMARY/FALLBACK from env only.
   - Add primary/fallback failover with 20s cooldown.
   - Set timeout to 90s.

2) `server/services/ocr_llm_client.py`
   - Remove OCR server auto-start logic.
   - Resolve OCR URL via env primary/fallback only.
   - Add failover with 20s cooldown.
   - Set timeout to 90s.

3) `server/services/rag_http_client.py`
   - Use `RAG_URL` env only (no fallback).
   - Set timeout to 90s.

4) `server/services/conv_normalizer.py`
   - Delete file; remove all references.

5) `server/routes/ocr.py` and `server/routes/notes.py`
   - When endpoints fail, return structured error to UI for banner.

6) `web/index.html`
   - Add a banner for service failures with details and retry action.

7) `server/asr_service.py` (new)
   - External ASR service (FastAPI), same endpoints as current ASR route.
   - Respect `ASR_ENABLE_DIARIZATION` to disable diarization if needed.

8) `server/routes/asr.py`
   - Convert into a client that forwards to `ASR_URL` instead of in-process WhisperX.

---

## Testing Plan (Manual)
1) **Note Gen**: set NOTEGEN_URL_PRIMARY to 8081, generate a consult note.
2) **Failover**: stop 8081, set NOTEGEN_URL_FALLBACK to 8036, verify generation and banner behavior.
3) **OCR**: upload image/PDF with OCR_URL_PRIMARY on 8090; stop it, verify fallback on 8091.
4) **RAG**: verify consult comment uses only RAG_URL (8007).
5) **ASR**: run `uvicorn` ASR service on 8095 and confirm `/transcribe_diarized` works and is reachable remotely.
6) **Banner**: force failures and verify banner shows, disappears on success or after 2 minutes.

---

## Remaining Questions
1) Confirm ASR port: use **8095**?
2) Any specific path/naming for the new ASR service file? (default: `server/asr_service.py`)

