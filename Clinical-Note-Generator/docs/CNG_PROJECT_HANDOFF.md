# CNG Project Handoff & Continuity Document

Last updated: 2026-03-07 (UTC)
Owner: Islam Eissa
Maintainer context: Albert (OpenClaw on VPS)

---

## 1) What CNG Is

**CNG (Clinical Note Generator)** is a full-stack clinical documentation system built for pulmonology and general medical practice. It takes clinician-provided inputs (transcribed speech, prior visit records, lab/imaging data, and free-form notes) and generates structured clinical notes using a local LLM (llama-server).

### Core capabilities
- **Note generation** — consult, follow-up, referral, admission, discharge, transfer, procedure, summarize, custom
- **OCR** — extract text from uploaded images/PDFs (scanned documents, handwritten notes) using a local multimodal LLM
- **ASR** — real-time speech-to-text transcription via WhisperX / whisper.cpp
- **QA** — evidence-based clinical question answering (text and vision modes) with optional RAG
- **RAG** — retrieval-augmented generation from a curated clinical corpus (guidelines, drug data, PubMed)
- **Workspace** — per-user persistent workspace with multi-case queue management

### Design principles
- **Local-first**: all LLM inference runs on the workstation (no cloud API calls for clinical data)
- **Streaming**: all generation endpoints stream responses for real-time UX
- **Queue resilience**: if a service is down, files are queued server-side for later processing
- **Multi-user**: JWT-based auth with per-user workspaces and admin approval flow

---

## 2) System Architecture

### Components (3 main codebases + external services)

```
┌─────────────────────────────────────────────────────────┐
│  PCHost (Node.js)                                       │
│  ├── server.js — HTTPS reverse proxy + static hosting   │
│  ├── openwebui-proxy.js — Open WebUI HTTPS proxy        │
│  └── web/ — Frontend UI (index.html + JS modules)       │
├─────────────────────────────────────────────────────────┤
│  Clinical-Note-Generator (Python/FastAPI)               │
│  ├── server/app.py — FastAPI application                │
│  ├── server/routes/ — API endpoints                     │
│  ├── server/services/ — LLM clients, OCR, ASR, RAG     │
│  ├── server/models/ — SQLModel ORM (users, jobs, etc.)  │
│  ├── server/core/ — auth, config, DB, security          │
│  ├── asr/ — WhisperX/whisper.cpp ASR services           │
│  ├── config/config.json — app configuration             │
│  └── prompts/ — clinical prompt templates               │
├─────────────────────────────────────────────────────────┤
│  RAG (Python)                                           │
│  ├── query_api.py — FastAPI RAG service                 │
│  ├── retriever.py — hybrid BM25 + vector search         │
│  ├── chunker.py / embedder.py — corpus processing       │
│  └── scripts/ — data ingestion (guidelines, drugs, PMC) │
└─────────────────────────────────────────────────────────┘

External services (all local, managed via NSSM / batch / manual):
  • llama-server (note gen/QA) — port 8081 (primary), 8036 (fallback)
  • llama-server (OCR multimodal) — port 8090 (primary), 8091 (fallback)
  • whisper.cpp / WhisperX (ASR) — port 8095 (primary), 8096 (fallback/CPU)
  • RAG service — port 8007
  • Open WebUI (Docker) — port 8035 (internal), proxied on 8443
  • FastAPI app — port 7860
```

### Request flow (note generation)
1. User fills input fields in UI (transcription, prior visits, labs/imaging/other)
2. UI sends POST to `/api/generate_v8_stream` with bearer token
3. FastAPI builds prompt (system + user sections) using note-type-specific template
4. Optional: RAG retrieval for evidence context
5. Prompt sent to llama-server via `/v1/chat/completions` (streaming)
6. Streamed chunks forwarded to UI as `text/plain`
7. Generation ID returned via `X-Generation-Id` header
8. Meta (note type, timing, etc.) available via `/api/generation/{id}/meta`

---

## 3) Deployment Context

### Current state (as of 2026-03-07)
- **Workstation**: Windows machine with NVIDIA RTX 5090 32GB + RTX 5060 Ti 16GB, Intel Ultra 9, 128GB RAM
- **VPS**: Ubuntu Linux (OpenClaw agent, orchestration, monitoring)
- **Repo**: `C:\project-root` on workstation (git-managed, GitHub remote: `Salamonti/cng`)
- **Live runtime**: symlinks from `C:\PCHost` and `C:\Clinical-Note-Generator` to repo subdirectories
- **Known issue**: repo vs live path drift can cause confusion; see Phase 5 in master plan

### Service URLs (all env-driven)
| Service | Env var | Default |
|---------|---------|---------|
| Note gen (primary) | `NOTEGEN_URL_PRIMARY` | `http://127.0.0.1:8081` |
| Note gen (fallback) | `NOTEGEN_URL_FALLBACK` | `http://127.0.0.1:8036` |
| OCR (primary) | `OCR_URL_PRIMARY` | `http://127.0.0.1:8090` |
| OCR (fallback) | `OCR_URL_FALLBACK` | `http://127.0.0.1:8091` |
| RAG | `RAG_URL` | `http://127.0.0.1:8007` |
| ASR (primary) | `ASR_URL` | `http://127.0.0.1:8095` |
| ASR (fallback) | `ASR_URL_FALLBACK` | `http://127.0.0.1:8096` |
| ASR API key | `ASR_API_KEY` | `notegenadmin` |

### Network exposure (current)
- PCHost (HTTPS proxy): `notes.ieissa.com` via port 3443 (router port-forward)
- Open WebUI: `ieissa.com:8443` (router port-forward)
- QNAP NAS: `office.ieissa.ca` via port 443 (router port-forward, No-IP DNS)
- Synology NAS: `home.eissa.ca` (router port-forward, No-IP DNS)
- DNS: managed via No-IP, domains registered at GoDaddy

---

## 4) Key Files Reference

### Backend (Clinical-Note-Generator/server/)
| File | Purpose |
|------|---------|
| `app.py` | FastAPI app, CORS, router mounting |
| `auth.py` | Bearer token auth (user + admin) |
| `routes/notes.py` | Note generation (v8 streaming), QA rewrite, consult comments, order requests, feedback, caching |
| `routes/ocr.py` | OCR endpoint (PDF multi-page + image) |
| `routes/asr.py` | ASR/transcription endpoint |
| `routes/queue.py` | Server-side job queue (file storage + retry) |
| `routes/qa_chat.py` | QA text streaming |
| `routes/qa_vision.py` | QA vision streaming |
| `routes/workspace.py` | Per-user workspace CRUD |
| `routes/auth_users.py` | User registration, login, token refresh |
| `routes/admin_users.py` | Admin user management |
| `routes/admin.py` | Admin config/prompt management |
| `routes/rag_updates.py` | RAG corpus update triggers |
| `routes/services.py` | Service health checks |
| `services/note_generator_clean.py` | LLM client (streaming + collect, primary/fallback) |
| `services/ocr_llm_client.py` | OCR LLM client (multimodal vision) |
| `services/rag_http_client.py` | RAG service client |
| `services/qa_deid.py` | De-identification (regex-based) |
| `services/clinical_text_normalizer.py` | Text cleanup/normalization |
| `services/vision_qa_client.py` | Vision QA client |
| `core/db.py` | SQLite database (SQLModel) |
| `core/security.py` | JWT token encode/decode |
| `core/dependencies.py` | FastAPI dependency injection |
| `models/user.py` | User ORM model |
| `models/workspace.py` | Workspace ORM model |
| `models/queued_job.py` | Queue job ORM model |
| `metrics.py` | Performance metrics collection |

### Frontend (PCHost/web/)
| File | Purpose |
|------|---------|
| `index.html` | Main UI (large monolith, partially modularized) |
| `scripts.js` | OCR queue manager class |
| `auth_workspace.js` | Workspace sync, auth flows |
| `markdown_renderer.js` | Markdown-to-HTML for clinical output |
| `generate_ui_flow.js` | Generation orchestration UI logic |
| `audio_ui_utils.js` | Audio/recording UI helpers |
| `universal_audio_handler.js` | Cross-browser audio recording |
| `service_worker.js` | PWA service worker |
| `styles.css` | Extracted styles |
| `admin.html` | Admin panel |
| `qa.html` | QA interface |
| `ocr.html` | Standalone OCR page |

### Proxy (PCHost/)
| File | Purpose |
|------|---------|
| `server.js` | Main HTTPS proxy (TLS termination, static files, API proxy to FastAPI) |
| `openwebui-proxy.js` | Dedicated Open WebUI HTTPS proxy with WebSocket support |
| `config/server_config.json` | Proxy configuration (ports, certs, backend URL) |

### RAG (RAG/)
| File | Purpose |
|------|---------|
| `query_api.py` | FastAPI RAG query service |
| `retriever.py` | Hybrid search (BM25 + vector similarity) |
| `chunker.py` | Document chunking |
| `embedder.py` | Embedding generation |
| `ingest.py` | Corpus ingestion pipeline |
| `scripts/` | Data source fetchers (guidelines, drugs, PMC articles) |

### Prompt optimization (docs/prompt-optimization/)
| Directory | Purpose |
|-----------|---------|
| `followup/` | Follow-up note prompt iterations + evaluation reports |
| `referral/` | Referral note prompt iterations + evaluation reports |
| `prompts/` | Final/production prompt versions |
| `reports/` | Cross-type optimization summaries |
| `tools/` | Optimization automation scripts |
| `handbook/` | Prompt optimization methodology guide |

---

## 5) Current Known Issues & Technical Debt

### Architecture
1. **`notes.py` is overloaded** — combines prompt building, streaming, QA rewrite, RAG, logging, caching, consult comments, and order extraction in one file (~1500+ lines)
2. **Two deployment contexts** — repo at `C:\project-root` vs live symlinks at `C:\PCHost` / `C:\Clinical-Note-Generator`; causes drift and risky updates
3. **Hardcoded Windows paths** — model paths, whisper model path, cert paths are hardcoded in several files
4. **In-memory caches have no TTL/eviction** — `_generation_cache`, `_generation_meta`, `_consult_comment_store`, `_order_request_store` grow unbounded

### Security & Compliance
5. **CORS is overly permissive** — `allow_origins=["*"]` with `allow_credentials=True` in FastAPI
6. **PHI logging risk** — feedback CSV logs full prompts/outputs; debug prints in OCR route; note generator logs full payloads
7. **Open router ports** — multiple services exposed via port-forward (443, 8080, 80, 8081, 20/21, 13131, 22 on QNAP alone)
8. **No MFA** on any service

### Code Quality
9. **Two truncation implementations** with different token-to-word ratios
10. **No automated tests** beyond one normalizer test
11. **Legacy/unused code files** not yet inventoried for cleanup

---

## 6) Improvement Roadmap (Master Plan)

The full phased improvement plan with hypotheses, deliverables, and verification criteria is maintained at:

**`/home/solom/.openclaw/workspace/memory/projects/cng-master-plan.md`** (VPS)

### Phase summary (in order)

| Phase | Goal | Status |
|-------|------|--------|
| **0** | Baseline + instrumentation (regression checklist, smoke tests, deployment stamp) | Not started |
| **1** | PHI-safe training dataset logging (JSONL + de-ID + feedback events) | Not started |
| **2** | Memory safety (TTL caches) + modularize notes.py | Not started |
| **3** | Smart preprocessing + smart truncation (quality + speed) | Not started |
| **4** | Repo hygiene (remove legacy, align GitHub, installation guide) | Not started |
| **5** | Eliminate symlinks — decision gate: **5A** (Windows consolidation) or **5B** (Ubuntu replatform) | Decision pending after Phase 4 |
| **6** | Security hardening: Cloudflare Tunnel, close router ports, WAF (can start after Phase 0) | Not started |

### Key decisions already made (2026-03-07)
- Training dataset is **global across all users** with `user_id` per record
- Thumbs-down opens optional **suggestion modal** (rating captured even if dismissed)
- In-memory cache TTL: **24 hours**, no persistence
- CORS: restrict to `*.ieissa.com` + localhost
- NAS auth: **Option A** (Cloudflare Tunnel without Access gate; rely on QTS/DSM login + WAF/rate limits)
- DNS migration: **No-IP → Cloudflare** (zones: `ieissa.ca`, `ieissa.com`, `eissa.ca`; registrar: GoDaddy)
- Workstation OS migration to **Ubuntu is under consideration** (Phase 5B)
- Remote desktop: **Apache Guacamole** behind Cloudflare Tunnel (browser-based, no client install)

---

## 7) Infrastructure Details

### Domains & DNS (current state)

**ieissa.com** (CNG app + workstation services)
| Record | Type | Target |
|--------|------|--------|
| `@` | A | `24.215.121.228` |
| `www` | A | `24.215.121.228` |
| `notes` | A | `24.215.121.228` |
| `*` | A | `24.215.121.228` |
| `app` | URL redirect | `https://ieissa.com:3443/index.html` |

**ieissa.ca** (office NAS + hospital)
| Record | Type | Target |
|--------|------|--------|
| `office` | A | `24.215.121.228` |
| `hospital` | A | `24.215.121.228` |
| `@` | A | `1.1.1.1` (placeholder) |
| SRV records | SRV | Skype/Teams federation |
| TXT records | TXT | MS verification + SPF |
| MX | (implicit via O365) | — |

**eissa.ca** (home NAS + email)
| Record | Type | Target |
|--------|------|--------|
| `home` | A | `38.162.253.186` |
| `@` | A | `1.1.1.1` (placeholder) |
| `www` | A | `1.1.1.1` (placeholder) |
| `@` | MX | `ieissa-ca.mail.protection.outlook.com` |

### QNAP NAS (office.ieissa.ca)
- LAN IP: `192.168.0.210`
- QTS HTTPS port: `443`
- 6 external companies access via individual QNAP user accounts (per-folder permissions)
- Current exposed ports: 443, 8080, 80, 8081, 20/21, 13131, 22

### Synology NAS (home.eissa.ca)
- Used as personal cloud storage
- Web UI access (same pattern as QNAP)

### Workstation services
| Service | Port | GPU |
|---------|------|-----|
| FastAPI (CNG backend) | 7860 | — |
| llama-server (note gen) | 8081 | GPU 0 (RTX 5090) |
| llama-server (fallback/vision) | 8036 | GPU 1 (RTX 5060 Ti) |
| llama-server (OCR) | 8090 | GPU 0 |
| whisper.cpp ASR | 8095 | GPU |
| whisper.cpp ASR (CPU fallback) | 8096 | CPU |
| RAG service | 8007 | — |
| Open WebUI (Docker) | 8035 | — |
| PCHost HTTPS proxy | 3443 | — |
| OpenWebUI HTTPS proxy | 8443 | — |

### VPS (OpenClaw)
- Hosts Albert (OpenClaw agent)
- Connected to workstation via Tailscale (node: WORKSTATION)
- Manages monitoring, heartbeats, email checks, morning briefings
- Search via SearXNG proxy at `https://ieissa.com:3443/searxng/search`

---

## 8) Startup Commands Reference

### FastAPI (main app)
```batch
set ASR_API_KEY=notegenadmin && C:\project-root\Clinical-Note-Generator\start_fastapi_server_external.bat
```
(The batch file sets all `*_URL_PRIMARY`, `*_URL_FALLBACK`, `RAG_URL`, `ASR_*` env vars)

### llama-server (note generation — primary)
```batch
set CUDA_VISIBLE_DEVICES=0 && llama-server -m "<model>.gguf" -c 51200 --jinja --host 0.0.0.0 --port 8081 -ctk q8_0 -ctv q8_0
```

### llama-server (OCR — multimodal)
```batch
set CUDA_VISIBLE_DEVICES=0 && llama-server -m "<ocr_model>.gguf" --mmproj "<mmproj>.gguf" --jinja --host 0.0.0.0 --port 8090 -c 4096 -ctk q8_0 -ctv q8_0
```

### ASR (WhisperX)
```powershell
cd C:\project-root\Clinical-Note-Generator
.\.venv\Scripts\Activate.ps1
$env:ASR_API_KEY="notegenadmin"
uvicorn asr.asr_service:app --host 0.0.0.0 --port 8095
```

### Health checks
```
FastAPI:  http://localhost:7860/api/health
llama:   http://localhost:8081/health
OCR:     http://localhost:8090/health
ASR:     http://localhost:8095/asr_engine
RAG:     http://localhost:8007/health
```

---

## 9) Prompt Pipeline

### Current architecture
- Note-type-specific prompts are built in `notes.py` (`build_prompt_v8()`)
- System prompt contains formatting rules, clinical constraints, conflicts policy
- User prompt contains tagged clinical input sections:
  - `<CURRENT_ENCOUNTER>` — live transcription + notes
  - `<PRIOR_VISITS>` — historical records
  - `<LABS_IMAGING_OTHER>` — labs, imaging, mixed data
- Specialty and custom user instructions are injected into the system prompt

### Prompt optimization history
- Consult, follow-up, and referral prompts have been through multi-iteration optimization
- Evaluation used automated scoring against gold-standard notes
- Final optimized prompts are in `docs/prompt-optimization/prompts/` and `docs/prompt-optimization/{followup,referral}/final-*`
- Methodology documented in `docs/prompt-optimization/handbook/PROMPT_OPTIMIZATION_HANDBOOK.md`

### Known prompt issues
- Two different truncation heuristics (1 token ≈ 1 word vs 1 token ≈ 0.75 words)
- No smart truncation (currently head-chops regardless of clinical importance)
- Historical/chart data contains OCR artifacts, repeated headers, boilerplate that wastes context

---

## 10) OCR Queue & Resilience Model

### How it works
1. UI attempts server OCR via `/api/ocr`
2. On failure (5xx, timeout, network error):
   - If connected: file uploaded to `/api/queue` (stored on disk per-user)
   - If disconnected: user prompted to download file locally
3. When connection restores, UI calls `/api/queue/{id}/process` to retry
4. On success: server deletes stored file and returns result
5. On failure: job marked as failed, file preserved for manual retry

### Server-side storage
- Files stored at `Clinical-Note-Generator/data/queue_files/<user_id>/<job_id>.<ext>`
- Job metadata in SQLite (`QueuedJob` model)
- Queue cleared on "New Case" / logout

---

## 11) Authentication Model

- **User auth**: JWT bearer tokens (access + refresh)
- **Admin auth**: separate admin API key (env `ADMIN_API_KEY` or config `admin_api_key`)
- **User approval**: new users require admin approval (`is_approved` flag)
- **Token storage**: client stores JWT in `localStorage`
- **Logout**: clears localStorage including `clinicalNoteQueue` and `requestQueue`

---

## 12) Quick Restart Checklist (New Session)

1. Read this document
2. Read `memory/projects/cng-master-plan.md` for current phase status and decisions
3. Confirm WORKSTATION node is connected (if remote operations needed)
4. Check current deployed commit: `git -C C:\project-root log --oneline -1`
5. Check service health endpoints
6. Pick next task from master plan phase list

---

## 13) Related Files

| Path | Purpose |
|------|---------|
| `memory/projects/cng-master-plan.md` | Master improvement roadmap (phases, decisions, daily log) |
| `TOOLS.md` | Runtime notes (node access, endpoints, credentials paths) |
| `MEMORY.md` | Curated long-term memory |
| `memory/procedures/email-check-procedure.md` | Email checking procedure |
| `memory/projects/cng-prompt-optimization/` | Prompt optimization workspace |
| `memory/projects/llm-routing-rules.md` | Model selection policy |
| `memory/projects/searxng-openclaw-setup.md` | SearXNG setup runbook |
