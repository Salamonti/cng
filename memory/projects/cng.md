# CNG Project Knowledge Base

Last updated: 2026-02-24 (UTC)
Owner/Operator: Islam
Primary repo: `Salamonti/cng` (branch: `main`)

## 1) System Topology

CNG stack is split across 3 main folders/components:

- `Clinical-Note-Generator` → FastAPI backend + note generation/ASR/OCR APIs.
- `PCHost` → reverse proxy/web host that serves frontend and forwards API traffic.
- `RAG` → retrieval pipeline/data preparation/indexing artifacts.

### Runtime deployment context

- Production app runs on a **Windows workstation** (office machine).
- Reverse proxy runs via `PCHost/server.js`.
- Confirmed reverse proxy ports: **3000, 3443**.
- Confirmed backend target in PCHost config: `http://127.0.0.1:7860`.
- FastAPI runs on port **7860**.

Important operational point:
- GitHub push alone does not update workstation runtime. Workstation files must be patched/pulled and service restarted.

---

## 2) API and Route Wiring (FastAPI)

FastAPI app file: `Clinical-Note-Generator/server/app.py`

Routers actively included:
- OCR router under `/api`
- ASR router under `/api`
- Notes router under `/api`
- RAG updates router under `/api`
- Perf router under `/api`
- Auth router (`/api/auth/...`)
- Workspace router (`/api/workspace/...`)
- Admin users router (`/api/admin/users/...`)
- Admin router (`/api/admin/...`)

Known not active:
- `server/routes/services.py` router include is commented out in app.

### Key endpoints validated in recent operations

- `POST /api/generate_v8`
- `POST /api/generate_v8_stream`
- `POST /api/transcribe_diarized`
- `GET /api/asr_engine`
- `POST /api/auth/login`

Authenticated healthcheck run (latest):
- `/api/auth/login` OK
- `/api/asr_engine` OK
- `/api/generate_v8` OK
- `/api/transcribe_diarized` skipped due to no sample file

---

## 3) Important Bug Fixes Already Applied

### A) generate_v8 crash fix
- Problem: `name 'user_speciality' is not defined`.
- Fix location: `Clinical-Note-Generator/server/routes/notes.py`.
- Fix: extract/read `user_speciality` from request payload before prompt construction.

### B) ASR probe false-negative fix
- Problem: ASR probe treated `GET /inference` 404 as hard failure on some whisper.cpp builds.
- Fix location: `Clinical-Note-Generator/server/routes/asr.py`.
- Fix behavior: 404 probe no longer marks ASR down by itself.

### C) Low-GPU fallback behavior
- File: `Clinical-Note-Generator/start_fastapi_server_external.bat`.
- Change: fallback URLs blank by default so extra local fallbacks are not auto-enabled.

Related commits (historical from previous session):
- `3485c9d` (notes/asr fixes)
- `f78f682` (fallback behavior)
- `11a93d2` (cleanup/hygiene)

---

## 4) Frontend/Proxy Notes

PCHost web files under `PCHost/web` include:
- `index.html`, `qa.html`, `ocr.html`, `admin.html`
- plus JS assets such as `auth_workspace.js`, `universal_audio_handler.js`, `service_worker.js`

Known active behavior:
- PCHost routes web entry pages and forwards backend API traffic to FastAPI on 7860.

---

## 5) RAG Pipeline Notes

RAG has heavy data folders (`snapshots`, `embeddings`, `raw_docs`, etc.).

Important:
- In GitHub main, canonical chunking script is `RAG/chunking_pipeline.py`.
- If local `RAG/scripts/chunking_pipeline.py` exists, treat as local duplicate drift (not canonical in main).

Snapshot policy chosen:
- Keep last **2** snapshots.

---

## 6) Cleanup and Maintenance History (Recent)

### Completed
- CNG phase-1 cleanup executed with dry-run then live run.
  - Cleared temp/cache/log artifacts.
  - Some active logs remained locked (expected).
- CNG phase-2 conservative cleanup executed.
  - Removed older deploy backup while keeping newest.
- RAG phase-1 cleanup executed.
  - Reclaimed space from snapshots/log/cache artifacts.
  - Active RAG logs locked and retained (expected).

### Locked files behavior
- Windows log files in use by running services can’t be deleted.
- This is normal; they regenerate and/or can be removed when services are stopped.

---

## 7) Git/Repo Hygiene Notes

Observed workflow reality:
- Working deployment path can differ from git root.
- Main git aggregation path used: `C:\project-root` with subfolders for `Clinical-Note-Generator`, `RAG`, `PCHost`.

Common noise files to avoid in commits:
- runtime DB (`data/user_data.sqlite`)
- logs (`server/logs/*.log`, `*.csv`)
- temp audio
- deploy backup folders
- runtime-generated RAG logs

---

## 8) Operational Playbook (Condensed)

When changing production behavior:
1. Patch or pull code on workstation runtime path.
2. Restart FastAPI/service.
3. Run authenticated health checks.
4. Confirm through app flow.

When cleaning:
1. Dry-run first.
2. Backup/manifest first.
3. Delete only known-safe artifacts.
4. Keep retention for snapshots and backups.

---

## 9) Pending / Future Work

- Build robust code-level duplicate/legacy detector that is dependency-aware (not timestamp-based) for `.py/.html/.js`.
- Keep this document updated after every significant CNG change (fix, deploy, cleanup, endpoint change, topology change).

---

## 10) Update Rule for Future Sessions

For any future CNG request:
- Load this file first.
- Append a dated “Recent Actions” section entry after each completed task.
- If topology/endpoints/config behavior changes, update corresponding sections immediately.
