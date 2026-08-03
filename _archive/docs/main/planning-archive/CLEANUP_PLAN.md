# CNG Cleanup Plan

> **Generated:** 2026-03-17
> **Source:** CNG_PROJECT_INDEX.md, BACKEND_INDEX.md, FRONTEND_INDEX.md, RAG_INDEX.md
> **Location:** `C:\project-root` (WORKSTATION) / `~/workspace/cng` (WSL2 mirror)

---

## Phase 1 — Safe, High-Impact (No Risk)

### 1.1 Delete Dead Files

| File | Reason | Risk |
|---|---|---|
| `Clinical-Note-Generator/server/services/asr_whisperx.py` | In-process WhisperX engine — not used; ASR goes through HTTP proxy to whisper.cpp. Heavy CUDA imports. | LOW |
| `RAG/server/services/rag_client.py` | Legacy RAG client in non-standard location. Active client is `server/services/rag_http_client.py`. | LOW |
| `RAG/test.py` | Stub test loading wrong model (Jina, not GIST-small). Not a real test. | LOW |
| `PCHost/web/auth_debug.html` | Debug page, never linked from navigation. Should not be in production. | LOW |
| `PCHost/server_config_simple.json` | Untracked simplified config variant. Not auto-loaded. | LOW |

### 1.2 Delete Dead Code Blocks

| Location | What | Reason |
|---|---|---|
| `server/routes/notes.py` | `generate_stream()` function (~200 lines) | Not routed — no `@router.post` decorator. Legacy. |
| `server/routes/notes.py` | `generate_v8()` function (~80 lines) | Not routed — no decorator. Dead code. |
| `server/routes/notes.py` | `build_qa_prompt()` | Only used inside `generate_stream()` which is dead. |
| `server/routes/notes.py` | `truncate_to_context_length()` (word-estimate version) | Near-duplicate of `truncate_to_context_length_tokens()`. The tokens version is used. |
| `server/routes/notes.py` | Second `clean_model_output = clean_model_output_chunk` alias | Duplicate definition in same file. |
| `server/routes/admin.py` | `_configure_llama_service()` | Returns `(True, "skipped")` — no-op placeholder. |
| `server/routes/admin.py` | llama start/stop/restart/health endpoints | All return `{"ok": false, "note": "externalized"}`. |
| `RAG/query_api.py` | `_cosine()` function | Defined but never called. |
| `RAG/retriever.py` | `_cosine()` function | Defined but never called. |
| `RAG/query_api.py` | `from dataclasses import asdict` | Unused import. |
| `RAG/composer.py` | `_extracts` variable in `build_cited_opinion` | Computed but never used. |

### 1.3 Clean Up Frontend Dead Code

| Location | What | Reason |
|---|---|---|
| `PCHost/web/index.html` | `saveCustomPromptsToStorage()` commented-out body | Marked DEPRECATED — workspace is source of truth. |
| `PCHost/web/index.html` | `setChartDataValue()`, `clearChartData()` | Legacy wrappers for removed `chartData` element — do nothing. |
| `PCHost/web/index.html` | All `document.getElementById('chartData')` references | Element doesn't exist since V7. Silent no-ops. |
| `PCHost/web/scripts.js` | `useInNotes()` | Saves to localStorage and scrolls to non-existent `#notes` section. |
| `PCHost/web/service_worker.js` | Fix version mismatch: install logs "v14", cache named "v15". | Minor bug. |
| `PCHost/web/service_worker.js` | `PRECACHE_URLS = []` | Empty array — install loop does nothing. Either populate or remove. |
| `PCHost/server.js` | `openwebuiProxyCommon` variable | Defined but never used in `app.use()`. |
| `PCHost/web/styles.css` | `noteActions-vertical` class | `display: none; /* deprecated */`. |

### 1.4 Fix `.gitignore`

Add these entries:

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
.venv/
venv/
cenv/
.env

# Index docs (generated, not source)
*_INDEX.md
CLEANUP_PLAN.md
```

---

## Phase 2 — Moderate Risk (Verify Before Deleting)

### 2.1 Investigate Then Delete

| File | Question | Action |
|---|---|---|
| `PCHost/openwebui-proxy.js` | Is OpenWebUI still used? | If no → delete + delete `start-openwebui-proxy.bat` |
| `PCHost/config/server_config.linux.json` | Is it used on any deploy? | If no → delete (it's never auto-loaded) |
| `RAG/chunker.py` | Is `guidelines_fetcher.py --emit-txt` still used? | If no → delete (replaced by `chunking_pipeline.py`) |
| `RAG/scripts/import_dpd.py` | Calls missing `etl/dpd_etl.py` | Broken — either add ETL or delete script |
| `RAG/scripts/import_spl.py` | Calls missing `etl/spl_etl.py` | Broken — either add ETL or delete script |
| `server/core/deid/ner_spacy.py` | `spacy` not in `requirements.txt` | Verify if spaCy is installed; if not, NER is a silent no-op. Either add to requirements or document as optional. |

### 2.2 Config Cleanup

| Item | Issue | Fix |
|---|---|---|
| `server_config.json` → `enable_gzip: true` | No gzip middleware in server.js | Remove key or add middleware |
| `server_config.json` → `log_level: "INFO"` | Never read by server.js | Remove key or implement |
| `package.json` → `ssl-setup`, `generate-ssl` scripts | Reference `ssl-setup.js` and `generate-ssl-cert.js` which don't exist | Remove scripts |
| `PCHost/New_Main_Server.bat` | References `Kill_Old_Node_Processes.bat` which doesn't exist | Fix or remove |
| `RAG/summarize_recent_updates.py` → `RAG_ROOT` | Hardcoded `C:\RAG` Windows path | Make configurable via env var |
| `RAG/retriever.py` → `HYBRID_LAMBDA = 0.20` | Inconsistent with `settings.yaml` `hybrid_lambda: 0.10` | Align defaults |

### 2.3 Fix Latent Bugs

| Location | Bug | Fix |
|---|---|---|
| `server/routes/admin.py` → `llama_status()` | `cfg` variable not defined in function scope (relies on module-level) | Add `cfg = _load_cfg()` |
| `RAG/summarize_recent_updates.py` | Entire module body duplicated (second half redefines all classes/functions) | Remove duplicate block |

---

## Phase 3 — Refactoring (Lower Priority)

### 3.1 Code Consolidation

| Item | Current State | Suggested Change |
|---|---|---|
| `routes/notes.py` | Monolithic ~700+ lines after dead code removal | Split into `routes/notes_generate.py`, `routes/notes_feedback.py`, `routes/notes_helpers.py` |
| `RAG/query_api.py` inline hybrid search | Duplicates logic from `retriever.py` | Extract shared search logic to `retriever.py`, import in both |
| `_parse_date_any()` | Duplicated in `query_api.py` and `version_manager.py` | Move to `utils_meta.py` |
| `debugLog` calls in frontend | Gated on `window.DEBUG_MODE` which is never set | Either add a toggle or strip for production |

### 3.2 Dependency Cleanup

| Package | Status | Action |
|---|---|---|
| `spacy` + `en_core_web_sm` | Used by `ner_spacy.py` but not in `requirements.txt` | Add to requirements or make explicitly optional |
| `whisperx`, `torch`, `omegaconf` | Only needed by dead `asr_whisperx.py` | Remove if file is deleted |

### 3.3 Structural Improvements

| Item | Suggestion |
|---|---|
| `RAG/server/services/` directory | Non-standard nested path. Move `rag_client.py` to main `RAG/` or delete (Phase 1). |
| Prompts scattered in config JSON | Extract to dedicated `prompts/` directory with versioned `.txt` files |
| `.bat` files at repo root | Move to `scripts/` or `bin/` directory |

---

## Non-Git Files Inventory

These files exist at runtime but must NOT be committed:

| Path | Type | Size Est. |
|---|---|---|
| `Clinical-Note-Generator/data/user_data.sqlite` | User DB | Small |
| `Clinical-Note-Generator/data/datasets/*.jsonl` | De-identified case logs | Growing |
| `Clinical-Note-Generator/data/queue_files/` | Patient uploads | Variable |
| `Clinical-Note-Generator/data/RxNorm_full*/` | Drug DB | ~300MB+ |
| `Clinical-Note-Generator/server/logs/` | HTTP/app logs | Growing |
| `Clinical-Note-Generator/server/temp-audio/` | ASR audio files | Variable |
| `Clinical-Note-Generator/config/config.json` | Secrets (JWT keys, API keys) | Small |
| `RAG/chroma_store/` or `RAG/chroma_db/` | Vector store | ~500MB+ |
| `RAG/embeddings/` | Precomputed embeddings | Variable |
| `RAG/current_corpus/` | Active document JSONs | Variable |
| `RAG/archive/` | Prior versions | Variable |
| `RAG/raw_docs/` | Downloaded PDFs | Variable |
| `certs/` | TLS certs + private keys | Small |
| `PCHost/config/server_config.json` | Domain + cert paths | Small |

---

## Execution Order

1. **Git branch**: Create `cleanup/phase1` branch
2. **Phase 1.4**: Update `.gitignore` first
3. **Phase 1.1**: Delete dead files (5 files)
4. **Phase 1.2**: Remove dead backend code blocks
5. **Phase 1.3**: Remove dead frontend code
6. **Commit + test**: Run existing tests, verify app starts
7. **Phase 2**: Address one item at a time with verification
8. **Phase 3**: Tackle during feature work or dedicated refactor sprint

---

## Estimated Impact

- **Files deleted**: 5 (Phase 1) + up to 5 more (Phase 2)
- **Dead code removed**: ~500-700 lines
- **Risk**: LOW for Phase 1 (nothing deleted is imported or routed)
- **Testing**: Run `pytest` in `Clinical-Note-Generator/` after Phase 1
