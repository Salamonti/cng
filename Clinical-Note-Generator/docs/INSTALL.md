# Clinical Note Generator — Quick Start Guide

> **For complete installation instructions, see the comprehensive [INSTALLATION_GUIDE.md](../../INSTALLATION_GUIDE.md)**

This document provides minimal instructions to get started quickly.

## Quick Start (Windows)

### 1. Clone and Setup
```powershell
git clone https://github.com/your-org/cng.git
cd cng

# Python environment
python -m venv .venv
.venv\Scripts\activate
pip install -r Clinical-Note-Generator\requirements.txt

# Node.js dependencies
cd PCHost
npm install
cd ..
```

### 2. Configure
```powershell
# Set JWT secret
$env:JWT_SECRET="your-secure-jwt-secret-here"

# Edit config if needed
notepad Clinical-Note-Generator\config\config.json
```

### 3. Start Services
```powershell
# Terminal 1: Backend
cd Clinical-Note-Generator
start_fastapi_server.bat

# Terminal 2: Frontend
cd PCHost
node server.js
```

### 4. Access
- **UI**: http://localhost:3000
- **API**: http://localhost:7860/docs
- **Health**: http://localhost:7860/api/health

## Components

| Component | Port | Description |
|-----------|------|-------------|
| **PCHost** | 3000 | UI + proxy layer |
| **FastAPI** | 7860 | Main backend API |
| **llama.cpp** | 8081 | LLM inference |
| **OCR Service** | 8082 | Document processing |
| **ASR Service** | 9000 | Speech recognition |
| **RAG Service** | 8000 | Retrieval service |

## Next Steps
1. Read the full [INSTALLATION_GUIDE.md](../../INSTALLATION_GUIDE.md)
2. Configure external services (OCR, ASR, RAG)
3. Set up production deployment
4. Configure monitoring and backups

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
