# Clinical Note Generator (CNG) — Live Repository Index

> **Generated:** 2026-03-18 · **Root:** `/mnt/c/project-root`
>
> Audience: overnight architecture/restructure briefing. Use this index to navigate the repo and hand off deeper dives to the refreshed backend/frontend indices.

## Legacy Index Audit
- `CNG_PROJECT_INDEX.md` (previous snapshot) — **stale/inaccurate.** It was dated 2026-03-17 yet referenced files that no longer exist (`services/asr_whisperx.py`, `web/auth_debug.html`) and overstated features such as llama auto-management that are now manual.
- `BACKEND_INDEX.md` — **stale/partial.** Route coverage stopped at the v7 pipeline and still mentions non-existent modules (e.g., `services/asr_whisperx.py`) even though the live FastAPI stack only exposes `note_generator_clean.py`, `ocr_llm_client.py`, `rag_http_client.py`, `vision_qa_client.py`, and `qa_web_search.py` under `Clinical-Note-Generator/server/services`.
- `FRONTEND_INDEX.md` — **stale/incorrect.** It documents `web/auth_debug.html` and a split SPA that no longer exist; `/mnt/c/project-root/PCHost/web` now contains only `index.html`, `ocr.html`, `qa.html`, `admin.html`, and the shared JS/CSS assets.

## Repo Layout (quick map)
| Path | Purpose |
| --- | --- |
| `Clinical-Note-Generator/` | FastAPI backend (`server/app.py`, routes, services, SQLModel tables) plus Windows launch scripts and config under `config/config.json`.
| `PCHost/` | Node/Express reverse proxy (`server.js`), llama-gateway bridge, `web/` static UI (index/admin/ocr/qa), audio helpers, and service worker.
| `RAG/` | Stand-alone FastAPI retrieval service (`query_api.py`), Chroma store, BM25 cache, ingestion scripts, and `settings.yaml`.
| `startup/` | PowerShell automation for launching the whole stack (`start-office-stack.ps1`) and Cloudflared tunnel helper scripts.
| `certs/` | TLS materials referenced by `PCHost/config/server_config.json` (not versioned).
| `CLEANUP_PLAN.md`, `INSTALLATION_GUIDE.md`, `RAG_INDEX.md` | Supporting documentation.

## System Overview & Data Flow
1. **Client → Node proxy.** `PCHost/server.js` serves `web/index.html` and proxies `/api/*`, `/admin/*`, `/ocr`, `/whisperx`, and `/llama/*` to FastAPI or the llama-gateway (`PCHost/llama-gateway.js`).
2. **FastAPI backend.** `Clinical-Note-Generator/server/app.py` wires in routers under `/api` for note generation, OCR, ASR proxy, QA chat/vision, workspaces, queueing, admin, and health/version endpoints. Streaming note generation lives in `server/routes/notes.py#L1448`.
3. **External services.** llama-server endpoints are accessed via `services/note_generator_clean.py`, RAG via `services/rag_http_client.py`, OCR via `services/ocr_llm_client.py`, QA web search via `services/qa_web_search.py`, and optional vision QA via `services/vision_qa_client.py`.
4. **RAG/Q&A.** The backend hits the RAG FastAPI (`RAG/query_api.py`) and SearXNG via `server/routes/qa_chat.py` before reusing the llama client for answers.
5. **Logging & queueing.** Dataset logs are appended through `core/logging/dataset_logger.py`, and ASR/OCR retries use `/api/queue` (`server/routes/queue.py`).

## Backend Surfaces (see `BACKEND_INDEX.md` for detail)
- **Entry point:** `server/app.py` (middleware, router registration, static admin host).
- **Routes:**
  - Notes & streaming generation `server/routes/notes.py` (POST `/api/generate_v8_stream`, GET `/api/generation/{id}/*`).
  - OCR `server/routes/ocr.py`, ASR proxy `server/routes/asr.py` (POST `/api/transcribe_diarized`, GET `/api/asr_engine`).
  - QA chat/vision `server/routes/qa_chat.py`, `server/routes/qa_vision.py`.
  - Workspace sync `server/routes/workspace.py` (GET/PUT/POST `/api/workspace/*`).
  - Queue operations `server/routes/queue.py` (upload/process/delete ASR/OCR jobs).
  - Auth/admin/version/perf/rag updates under their respective modules.
- **Core config/prompting:** `core/config.py`, `core/prompt/builder.py`, `core/preprocessing/*`, `core/stores/generation_store.py`, `core/consult` and `core/order` pipelines.

## Frontend & Proxy Surfaces (see `FRONTEND_INDEX.md` for detail)
- **Proxy:** Express app in `PCHost/server.js` plus TLS config `PCHost/config/server_config.json`.
- **Single-page UI:** `PCHost/web/index.html` (shell + small inline boot scripts) loads **`css/workspace.css`**, **`js/workspace_app.js`**, and shared scripts (`auth_workspace.js`, etc.) for authentication, note entry, queue handling, audio capture, RAG/consult cards, and workspace sync (`window.app` in `workspace_app.js`).
- **Shared JS modules:** `generate_ui_flow.js` (focus helpers), `auth_workspace.js` (session + workspace persistence), `audio_ui_utils.js`, `universal_audio_handler.js` (record/upload pipeline), `markdown_renderer.js`, `service_worker.js`.
- **Other surfaces:** `qa.html`, `ocr.html`, `admin.html` reuse the shared JS/CSS bundle.

## Prompts, Models, and Config Touchpoints
- **Runtime config:** `Clinical-Note-Generator/config/config.json` stores LLM model paths, llama temperature/top-p defaults, OCR/QA/ASR URLs, and prompt templates under `default_note_system_prompt` / `default_note_user_prompts`.
- **Prompt builders:** `core/prompt/builder.py` constructs v8 prompts with the three-field layout and optional `custom_prompt` / `user_speciality` inputs.
- **Model client:** `services/note_generator_clean.py` streams completions via llama-server with primary/fallback URLs (env `NOTEGEN_URL_PRIMARY`, `NOTEGEN_URL_FALLBACK`).
- **RAG settings:** `RAG/settings.yaml` + `config.json` (`rag_service_url`, `rag_comment_llm_url`) define retrieval behavior for `/api/qa/chat` and consult/order add-ons.

## RAG / QA Paths
- **Backend client:** `server/services/rag_http_client.py` builds snippets, enforces context budgets, and is used by notes (consult/order post-processing) and QA chat.
- **Dedicated service:** `RAG/query_api.py` exposes `/query`, uses `store.py` for Chroma, `retriever.py` for hybrid scoring, and writes metrics to `RAG/logs/request_metrics.csv`.
- **QA web search:** `server/services/qa_web_search.py` hits SearXNG (config `SEARXNG_URL`), merged in `server/routes/qa_chat.py` before the llama call.

## Startup / Runtime Tooling
- **Windows batch launchers:** `Clinical-Note-Generator/start_fastapi_server*.bat`, `PCHost/New_Main_Server.bat`, `RAG/start_rag_service.bat`.
- **PowerShell orchestrator:** `startup/start-office-stack.ps1` controls all ports (3000/3443/7860/7871/8007) with managed processes and cleanup hooks.
- **Cloudflared & misc:** `startup/start-cloudflared.ps1`, `start-openclaw.ps1`, plus `PCHost/start-openwebui-proxy.bat` for the separate OpenWebUI proxy.

## Document Map
- `BACKEND_INDEX.md` — up-to-date FastAPI routes, services, data stores.
- `FRONTEND_INDEX.md` — Node proxy + SPA anatomy and splitting guidance.
- `ARCHITECTURE_OPTIONS_MORNING_BRIEF.md` — option analyses for UI splitting, React migration, multi-patient workflow, prompt/profile configuration, and ASR streaming.
