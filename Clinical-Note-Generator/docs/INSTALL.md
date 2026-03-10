# Clinical Note Generator — Installation & Operations (Windows-first)

This document is the minimal runbook to install and run the system from this repo.

## 1) Components

- **PCHost** (Node/Express) — UI + proxy layer (default: http://127.0.0.1:3000)
- **Clinical-Note-Generator** (FastAPI) — main API (default: http://127.0.0.1:7860)
- **RAG** (separate service) — retrieval endpoint (default: http://127.0.0.1:8007)
- **OCR** service (separate) — multimodal OCR endpoint (default: http://127.0.0.1:8090)
- **ASR** service (separate) — transcription endpoint (default: http://127.0.0.1:8095)
- **NoteGen (LLM)** — llama-server compatible endpoint (default: http://127.0.0.1:8081)

## 2) Prerequisites

- Python 3.11+ (Windows)
- Node.js (for PCHost)
- FFmpeg installed (or set `FFMPEG_BIN` / config `ffmpeg_path`)
- GPU drivers + CUDA (if using GPU-backed services)

## 3) Repo layout

- `./PCHost/` — UI + proxy
- `./Clinical-Note-Generator/` — FastAPI backend
- `./RAG/` — RAG service

## 4) Configuration

### 4.1 Backend config file

Main config: `Clinical-Note-Generator/config/config.json`

### 4.2 Environment variables

Complete reference (defaults + meanings):
- `Clinical-Note-Generator/ENV_VARIABLES.md`

**Required (auth):**
- `JWT_SECRET`
- `JWT_REFRESH_SECRET`

## 5) Start order (typical)

1) Start external services (as applicable): NoteGen (8081), OCR (8090), ASR (8095), RAG (8007)
2) Start FastAPI
3) Start PCHost

## 6) Start FastAPI (External services mode)

From `Clinical-Note-Generator/`:

### PowerShell
```powershell
# Required
$env:JWT_SECRET = "..."
$env:JWT_REFRESH_SECRET = "..."

# Optional
$env:FASTAPI_PORT = "7860"  # override when port conflicts

.\start_fastapi_server_external.bat
```

FastAPI health/version:
- `GET /api/health`
- `GET /api/version`

## 7) Start PCHost

From `PCHost/`:

```powershell
npm install
node server.js
```

UI:
- http://127.0.0.1:3000

> If you change the FastAPI port, update `PCHost/config/server_config.json` (`backend_url`) or set `FASTAPI_URL` when starting PCHost.

## 8) Common ports

- PCHost UI: `3000`
- FastAPI: `7860` (or `FASTAPI_PORT` override)
- NoteGen/LLM: `8081`
- OCR: `8090`
- ASR: `8095`
- RAG: `8007`

## 9) Troubleshooting

- 404 on login: ensure UI is proxying to the correct FastAPI port.
- 401 from API routes: ensure you are logged in and passing bearer token; tests override auth.
- If preprocessing hides facts: increase token budgets in `config.json` (per-section caps).

---

This file is intentionally short. Keep deeper operational details in `docs/` as they stabilize.
