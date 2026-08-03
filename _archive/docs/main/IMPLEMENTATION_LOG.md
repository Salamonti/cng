# DreamCision — Implementation log

**Purpose:** Running record of **what was done**, **how**, and **where to continue**, so anyone (including AI assistants) can **resume without re-reading the whole repo**.

**Companion docs**

| Document | Role |
|----------|------|
| [`planning-archive/README.md`](./planning-archive/README.md) | **Closed-pass** roadmaps, indices, and handoff docs (single folder) |
| [`planning-archive/FUTURE_PLAN_BACKLOG.md`](./planning-archive/FUTURE_PLAN_BACKLOG.md) | **Next plan** — seed backlog (Whisper chunking, splits, RAG, EMR) |
| [`MASTER_PLAN_DREAMCISION.md`](./planning-archive/MASTER_PLAN_DREAMCISION.md) | Full roadmap (archived snapshot), phases P0–P14, testing expectations |
| **This file** | Chronological work log + handoff notes |

**How to update (every meaningful chunk of work)**

1. Add a new **Log entry** at the **top** (newest first) using the template below.
2. If a phase status changes, update the **Phase status** table.
3. Optionally note **git commit** or **PR** — never paste secrets.

---

## Phase status (high level)

Update this table when a phase starts / completes / is blocked.

| Phase | Name | Status | Last updated |
|-------|------|--------|--------------|
| P0 | Hygiene & dead ends | Done | 2026-04-05 |
| P1 | Admin session model | Done | 2026-04-05 |
| P2 | Config & LLM routing | Done | 2026-04-05 |
| P3 | Profile & preferences | Done | 2026-04-05 — see log entry below |
| P4 | Prompt builder refactor | Done | 2026-04-05 |
| P3b | Custom note types CRUD | Done | 2026-04-06 |
| P5 | Encounter data model & API | Done | 2026-04-06 — see log |
| P6 | Encounter UI | Done | 2026-04-07 — see log |
| P7 | Queue & unified failure UX | Done | 2026-04-07 — see log |
| P8 | Literature + QA UX | Done (v1) | 2026-04-07 |
| P9 | Tokens, parsing, orders | Done (v1) | 2026-04-07 |
| P10 | Modularize frontend | **Done (ship)** | 2026-04-08 |
| P11 | DreamCision rebrand | **Done (ship)** | 2026-04-08 |
| P12 | Whisper overlap experiment | Deferred | — |
| P13 | Operator stack & LLM instances (admin) | **Done (v1 + P13b)** | 2026-04-09 |
| P14 | Operator notebook (last) | **Done (v1)** | 2026-04-09 |
| Grand Plan **Phase 1** (G0–G5a–G5) | Sync/auth/ASR hardening on Windows | **VERIFIED ✅ — deployed + smoke pass + committed `fb4f135`** | 2026-06-12 |
| Grand Plan **Phase 1b** (Net-P0/P1) | Tunnel HTTP origin optimization | **VERIFIED ✅ — TTFB 224→186 ms (2026-06-12 night)** | 2026-06-12 |
| Grand Plan **Phase 2** | userver migration (whisper.cpp + vLLM Gemma) | **Planned — audit done + runbook written** (`PHASE2_USERVER_RUNBOOK.md`); not executed | 2026-06-12 |
| Grand Plan **Phase 3–4** | Streaming diarization (G6–G8) | Not started | 2026-06-12 |

> Status labels follow the Grand Plan legend: **CODE** (in repo) / **DEPLOYED** (running service) / **VERIFIED** (smoke + sign-off logged). See [`docs/GRAND_PLAN.md`](./GRAND_PLAN.md#status--execution-truth-read-first).

**Status values:** `Not started` · `In progress` · `Blocked` · `Done` (for that phase’s *current* scope)

---

## Log entries (newest first)

### 2026-06-12 (night, later) — Phase 2 prep: **read-only userver audit + execution runbook**

- **Type:** read-only audit (SSH `eissa@100.72.189.26`) + new doc `docs/PHASE2_USERVER_RUNBOOK.md`. No changes made on userver.
- **Audit findings:** Ubuntu 24.04.4, 24 cores, 122 GB RAM, 3.3 TB free, 2× RTX PRO 6000 Blackwell (96 GB ea), CUDA 13.2, passwordless sudo.
  - **AI stack already running as systemd:** Gemma `gemma4-26b-awq` `:8081` (`vllm-26b-card1.service`), Qwen `qwen3.6-27b-awq` `:8000` (`vllm-27b-card0.service`). **whisper.cpp built + running** `~/whisper.cpp` (med-finetuned-large-v3turbo-q5_1) on `:8097`, but as a **login-shell process** (`bash -lic …`), not systemd → fragile.
  - **Missing:** Node.js, system `pip` (venv OK). cloudflared 2026.5.2 installed but **unconfigured**. git/cmake/gcc/g++/make/ffmpeg/curl/jq/docker present. Ports 3000/3443/7860/8007/8037/8095/8096 free.
  - **Prior conflicting plan found:** `~/DreamCision/` holds another agent's migration program (`migration_plan_final_v4.md`, kickoff was 2026-06-19) — **Docker/compose**, far bigger scope (NAS backup, **wipe Windows→Ubuntu**, hot-standby failover via dual cloudflared, Thunderbolt). The earlier `migration_tracker_2026-05-28.md` used systemd.
- **Decisions locked (asked operator; proceeded on recommended defaults):**
  1. **Native systemd, NOT Docker** — matches the already-running vLLM/whisper units + the `127.0.0.1` design; Docker adds loopback friction for no GPU/latency gain. The Docker v4 plan is **superseded**/parked.
  2. **Minimal scope** — clone + run app on userver (systemd), **reuse existing Gemma/Qwen vLLM + whisper.cpp**, tunnel cutover, keep Windows stopped-but-intact 48 h. **No** wipe / failover / Thunderbolt.
  3. This runbook + GRAND_PLAN are **source of truth**.
- **Port reconciliation:** Windows fallback LLM `8037` → userver **`8000`** (existing Qwen). Set `LLM_*_FALLBACK=http://127.0.0.1:8000`.
- **Runbook contents (`PHASE2_USERVER_RUNBOOK.md`):** pre-flight (push GitHub `Salamonti/cng`, clean WAL checkpoint + DB copy); install Node 22 + venv; clone to `/opt/dreamcision`; Linux venvs + `npm ci`; Linuxize `service_endpoints.json`; systemd units for pchost/fastapi/rag mirroring the vLLM-unit style (FastAPI `server.app:app` **with `--proxy-headers --forwarded-allow-ips`** — confirmed + matters for R6 class); whisper.cpp `whisper-server@.service` template (8095/8096, bind 127.0.0.1, GPU pinned); RAG-weekly ported from `weekly_run.ps1` to a 6-step `weekly_run.sh` + systemd timer; cloudflared systemd cutover (single-tunnel ordering: stop Windows `cloudflare` first; real tunnel `451a4852-…`, ingress confirmed); ufw (backends loopback-only); Net-P2 split DNS + cert deferred; T25–T30 smoke + reboot test; full rollback table.
- **Verified against repo (not guessed):** `server.app:app`, `query_api:app`, requirements paths, RAG weekly pipeline steps, real cloudflared tunnel ID + ingress.
- **Open items for executor:** real secrets into `/etc/dreamcision.env`; confirm clean Linux `pip install` (no Windows-only wheels); prune cloudflared ingress hostnames without a userver service; Net-P2 cert source.

### 2026-06-12 (night) — Phase 1b **Net-P0 + Net-P1 VERIFIED** (tunnel HTTP origin)

- **Type:** cloudflared config-only change (no app code). Net-P0 baseline + Net-P1 origin optimization.
- **Net-P0 baseline TTFB:** FastAPI direct `:7860` 4.8 ms · PCHost https `:3443` 14.3 ms · PCHost http `:3000` (w/ `X-Forwarded-Proto: https`) 7.4 ms · remote tunnel `notes.ieissa.com` **224 ms**.
- **Net-P1 change:** live config `C:\Windows\System32\config\systemprofile\.cloudflared\config.yml` (tunnel `office-prod`). Switched `notes.ieissa.com` **and** `app.ieissa.com` origin `https://127.0.0.1:3443` → `http://127.0.0.1:3000`; added HTTP/1.1 keepalive (`connectTimeout 10s`, `tcpKeepAlive 30s`, `keepAliveConnections 100`, `keepAliveTimeout 90s`). **Omitted `http2Origin`** (the plan's snippet had it) — PCHost is HTTP/1.1 Express, no h2c; forcing it would break the origin. Backed up to `startup/cloudflared.config.bak.20260612-214037.yml` first.
- **Supervision:** cloudflared is run by **NSSM service `cloudflare`** (lowercase; chain `cloudflared → powershell wrapper → nssm.exe`). The capital-`Cloudflared` Windows service is a disabled leftover. Restart = `Restart-Service cloudflare -Force` (elevated). Operator restarted; tunnel reconnected.
- **Why it's safe:** browser still sees Cloudflare HTTPS; PCHost skips its http→https redirect because cloudflared forwards `X-Forwarded-Proto: https` (verified: `:3000` returns 200, not 301, with that header).
- **Verification:** `cloudflared tunnel ingress validate` → OK; `notes.ieissa.com` rule → `http://127.0.0.1:3000`. After restart: remote TTFB **224 → 186 ms** (~38 ms faster, in the plan's 20–50 ms target); `GET https://notes.ieissa.com/` → 200; `/api/workspace/version` → 401; operator confirms "connects ok".
- **Rollback:** restore the `.bak` over `config.yml`, `Restart-Service cloudflare -Force`.
- **Follow-ups:** Net-P2 (split-horizon DNS, on-site tunnel bypass) is Phase 2. Re-apply Net-P1 on userver after Phase 2 cutover (origin becomes userver loopback). Recommended final spot-check: remote in-app login + generate via `https://notes.ieissa.com`.

### 2026-06-12 (evening) — Grand Plan **Phase 1 DEPLOYED + VERIFIED** (G0–G5a–G5)

- **Type:** Deploy window executed (operator ran elevated `Restart-Service OfficeStack -Force`) + full smoke pass + one regression fix + one durability hardening.
- **Branch/commit:** verified on `main` working tree (parent `40feaec`); committed as **`fb4f135`** — "Phase 1: deploy + verify Sync/auth/ASR hardening (G0-G5a-G5)".
- **Feature flag state (live):** safety flags **ON** by default — `SYNC_BEACON_FIX`, `SYNC_AUTH_FETCH`, `SYNC_LOCAL_DRAFT` (new), plus server-side anti-stomp; `STREAMING_ASR_ENABLED` **OFF**. Per `GRAND_PLAN.md` Feature flag policy.

**Pre-deploy fixes (code):**
- `PCHost/web/auth_workspace.js`: removed duplicate `deferredSaveTimer` key.
- `Clinical-Note-Generator/server/routes/workspace.py`: added missing `from server.core.baseline import get_baseline_workspace` (latent `NameError` in `clear_workspace` else-branch).
- DB backup taken → `Clinical-Note-Generator/data/backups/user_data.20260612-171701.sqlite` (+ `-wal`/`-shm`).

**Deploy blocker found & fixed (the actual "failed to fetch" on note generation):**
- **Symptom:** after the restart, note generation failed with `failed to fetch`.
- **Root cause:** the **new G5 dedicated PCHost stream proxy** for `/api/generate_v8_stream` rewrote the path with a **trailing slash** (`app.use` mount remainder = `/`). FastAPI 307-redirected to the canonical path, and because the proxy sets `xfwd: true` (`X-Forwarded-Proto: https`), the redirect `location` became `https://127.0.0.1:7860/...` — but FastAPI speaks **http** on 7860, so the browser couldn't follow it → `failed to fetch`. (Worked before the restart only because generate used to flow through the generic `/api` proxy, which doesn't add the slash.) Confirmed via `curl`: `307 … location: https://127.0.0.1:7860/api/generate_v8_stream … connection: close`.
- **Fix:** `PCHost/server.js` — both dedicated stream routes (`generate_v8_stream`, `transcribe_diarized`) now use a `streamPathRewrite()` helper that never appends a lone trailing slash. After PCHost reload: `POST /api/generate_v8_stream` → **401** (was 307). New regression guard candidate: **R6 — dedicated stream proxy must not introduce a trailing slash / https self-redirect.**

**Durability hardening (G5a) — local draft survival on abrupt tab close:**
- During smoke, the 2-tab test revealed that a **hard close within ~1s of typing** could lose the last edit (keepalive unload request dropped during teardown; no local fallback existed).
- Added a **localStorage draft mirror** in `auth_workspace.js`: synchronous persist in `flushUnloadSave()` + `queueSave()`; **recover** on `loadWorkspace()` (same user + same encounter, ≤24h, server version still == draft base version, content differs); **clear** on confirmed save (200 + 409-merge) and on sign-out. Feature flag `window.SYNC_LOCAL_DRAFT` (default ON). Non-clobber guard prevents overwriting a newer edit from another device.
- Service worker `CACHE_NAME` bumped **v80 → v82** to force fresh client JS.

**Smoke results (all PASS):**
- Login + generate note ✅ (after the proxy fix)
- **G5a** edit + reload persists (no self-stomp) ✅
- **G5a** cross-tab: edit + refresh A → refresh B shows it (both directions) ✅; **abrupt-close recovery** ✅ (after the localStorage-draft hardening)
- **G5** rename encounter → version bumps, no data loss ✅
- **G1′** record audio → transcript attaches to encounter ✅
- **G1** leave page mid-edit → unload save fires ✅
- **G4** long generation → no 401 mid-gen (token refresh) ✅
- **G2** live DB: `journal_mode=wal`, `busy_timeout=5000`, active WAL file ✅
- Live route probes (via PCHost `:3443`, unauth): `workspace/version` 401, `generate_v8_stream` 401, `transcribe_diarized` 401 — all routes live ✅
- Backend chain healthy: LLM `:8081` 200 + `/v1/models` 200, FastAPI `:7860` `/docs` 200, RAG `:8007`, ASR `:8095` ✅

**Follow-ups:**
- ✅ Committed the working-tree Phase 1 changes as `fb4f135`.
- ✅ Added **R6** to `GRAND_PLAN.md` Regression guards.
- Verify the daily `purge_audio` scheduled task is registered on the host (last open compliance item).
- Next: **Phase 1b (Net-P1, Cloudflared origin)**, then **Phase 2** (userver migration).

### 2026-06-12 — Plan harmonization pass (no code changes) + Grand Plan Phase 1 status truth

- **Type:** Documentation review + harmonization across the plan set. **No application code changed** in this pass.
- **Files:** `docs/GRAND_PLAN.md`, `Clinical-Note-Generator/docs/ASR_PIPELINE_RESILIENCE_PLAN.md`, `Clinical-Note-Generator/docs/ENV_VARIABLES.md`, `docs/IMPLEMENTATION_LOG.md`, `ASR_Recording_Status_Report.md`.
- **What changed:**
  - Added a **Status & execution truth** section to `GRAND_PLAN.md` with a strict **CODE / DEPLOYED / VERIFIED** legend, a measured 2026-06-12 reality snapshot, and a "definition of done". This is now the single status source.
  - Resolved contradictions: removed the "Phase 1 NOT deploy-ready" vs "READY" conflict; G5a **AO-F1–F4** corrected from stale "NOT STARTED" to **CODE done / VERIFIED pending** (verified present in `auth_workspace.js`: `deferredSaveTimer`, `suppressAutoSaveUntil`, `applyWorkspaceState({force})`, field-level `isDirty()` 409 merge; and `encounters_ui.js` `force:true`).
  - Set an authoritative **Feature flag policy** (five safety flags default **ON** matching `feature_flags.py` + `ENV_VARIABLES.md`; `STREAMING_ASR_ENABLED` **OFF**). Superseded the contradictory "deploy all flags OFF" wording.
  - Added **Regression guards R1–R5**, including the `apiFetch` / `generate_v8_stream` 404 ("the stream_v8 thing") root cause: a duplicate `apiFetch` in `settings_connection.js` dropped the `/api` prefix — front-end script-order bug, not a missing backend route. Fix is single `window.apiFetch` owned by `workspace_app.js`. Smoke = **T8b**.
- **Measured Phase 1 deploy state (2026-06-12):** code merged + pytest-green; **FastAPI not restarted** → `GET /api/workspace/version` returns **404** live on `:7860`, `:3443`, `notes.ieissa.com` (client falls back to `/api/workspace/`, app works); live DB confirmed `PRAGMA journal_mode=wal`. **No VERIFIED smoke pass recorded.** One blocker: elevated `Restart-Service OfficeStack -Force`, then confirm version endpoint returns 401.
- **userver:** reachable as `eissa@100.72.189.26` (Tailscale); 2× RTX PRO 6000 Blackwell, vLLM on `:8081`; no Dreamcision clone (Phase 2 not started — expected).
- **Compliance note:** the 2026-05-22 `ASR_Recording_Status_Report.md` P0/P1 items are now addressed in code — `tools/purge_audio.py` import fixed + orphan sweep added; `admin.py` `audio_retention_days` is **7** (was 60); `tools/run_purge_audio.bat` exists. **Verify the daily scheduled task is actually registered on the host** (the only remaining open item).
- **How verified:** `pytest` subsets green (workspace_version, encounters_p5, asr_proxy, cors_config, service_endpoints_sync, profile_p3, token_limits — 51 passed in one run); live HTTP probes; SSH to userver.
- **Follow-ups:** Run the Phase 1 deploy window (restart + T1–T22b + sign-off) to move milestones to VERIFIED; then update the snapshot table.

### 2026-04-09 — Planning docs consolidated + pass complete

- **Docs:** Moved roadmap and index markdown into **`docs/planning-archive/`** (`MASTER_PLAN_DREAMCISION`, `ROADMAP_AUTHORITY`, `MODULARIZATION_PLAN`, multi-encounter design, architecture brief, cleanup plans, RAG/CNG/FE/BE indices, `CNG_PROJECT_HANDOFF`). Added **`planning-archive/README.md`**, **`FUTURE_PLAN_BACKLOG.md`** (next-plan seeds: Whisper chunking, `notes.py` / `workspace_app.js` splits, RAG, EMR). Updated links in **`IMPLEMENTATION_LOG.md`**, **`INSTALLATION_GUIDE.md`**, **`OPERATOR_RUNBOOK_WINDOWS.md`**. Ongoing log and operator runbook stay under **`docs/`** (not archived).

### 2026-04-09 — P10b slice + technical debt batch

- **P10b:** New `PCHost/web/js/workspace_ui_state.js` (RAG/order UI state, `updateUiState`, `applyUiStateFromWorkspace`) and `workspace_file_camera.js` (camera, file handling, drag/drop); trimmed `workspace_app.js`; `index.html` + `service_worker.js` (cache v34) updated.
- **Backend:** Shared `cache_lock` + env `GENERATION_STORE_TTL_SECONDS` in `core/stores/generation_store.py`; `POST /api/feedback` moved to `server/routes/feedback.py`; `extract_request_actor` in `core/http_actor.py`; CORS tightened via `core/cors_config.py` (env: `CORS_ALLOW_ALL`, `CORS_ALLOWED_ORIGINS`, `CORS_ALLOW_ORIGIN_REGEX`).
- **Hardening:** OCR debug path uses `logger.exception` (no `print`); `truncation.py` module doc points to `token_limits` for the other truncation path; `docs/DEPLOYMENT_PATHS.md` documents repo vs symlink drift; `ENV_VARIABLES.md` documents new env vars.
- **Tests:** `tests/test_cors_config.py`. Full server pytest: 84 passed (1 skipped).
- **Not automated here:** MFA, router port exposure, Cloudflare/WAF, full `notes.py` decomposition beyond feedback, legacy file inventory — remain operator/product follow-ups.

### 2026-04-09 — P13b (admin LLM forms + models preset; remove legacy Model Parameters)

- **Admin (`admin.html`):** Llama instance cards — `bind_host`, `-ctk`/`-ctv`, flash / mmap / direct-io checkboxes, `chat_template_kwargs`, `launch.working_dir`, `extra_llama_args`, **models ▸ llama** on model+mmproj; Whisper — `threads`, `whisper_processors`, `launch.working_dir`, `whisper_extra_args`, **models ▸ whisper**. Removed hidden **Model Parameters** card and `/admin/config` loaders for it.
- **API:** `GET /api/admin/fs/browse?preset=llama_models|whisper_models` resolves first existing `models/<llama|whisper>` under allowlisted roots.
- **Launcher:** `bind_host`; `mmap` off → `--no-mmap`; `direct_io` off omits `--direct-io`.
- **Tests:** `test_ai_process_launcher` (mmap/host); `test_admin_fs_browse` (preset). **Plan:** `MASTER_PLAN_DREAMCISION.md` §4 P13 — P13b delivered; legacy item **removed** (not deferred).

### 2026-04-09 — P14 closure (operator runbook v1)

- **Phase:** **P14** — **Done (v1)** per [`MASTER_PLAN_DREAMCISION.md`](./planning-archive/MASTER_PLAN_DREAMCISION.md) §4.
- **Artifact:** [`OPERATOR_RUNBOOK_WINDOWS.md`](./OPERATOR_RUNBOOK_WINDOWS.md) — NSSM **AppEnvironmentExtra** production table (JWT, bootstrap, `ADMIN_*`, `ADMIN_MUTATIONS_LOCALHOST_ONLY`); environment checklist; service layout / ports; bootstrap admin steps; **password rotation** (UI / bootstrap limits / DB / clearing NSSM secrets); backups & recovery; failure restart order; related doc index.
- **Follow-ups (optional):** dedicated CLI to hash admin password; P12 Whisper overlap experiment if desired.

### 2026-04-09 — Operator runbook, admin UX, prod defaults, roadmap authority

- **Docs:** [`docs/OPERATOR_RUNBOOK_WINDOWS.md`](./OPERATOR_RUNBOOK_WINDOWS.md) (NSSM supervisor row, FastAPI alt, three control planes, `ADMIN_*` recommendations, Whisper doc link); [`docs/planning-archive/ROADMAP_AUTHORITY.md`](./planning-archive/ROADMAP_AUTHORITY.md) (master plan vs `MULTI_ENCOUNTER_DESIGN.md` vs `MODULARIZATION_PLAN.md`); [`Clinical-Note-Generator/docs/WHISPER_LAUNCH_ARGUMENTS.md`](../Clinical-Note-Generator/docs/WHISPER_LAUNCH_ARGUMENTS.md); [`tools/argv_to_json.py`](../tools/argv_to_json.py). `ENV_VARIABLES.md` extended for admin gates + `ADMIN_MUTATIONS_LOCALHOST_ONLY`. `INSTALLATION_GUIDE.md` links to runbook + roadmap authority. `MASTER_PLAN_DREAMCISION.md` §P14 points at runbook.
- **Launcher:** `start_fastapi_server_external.bat` defaults `ADMIN_PROCESS_CONTROL_ENABLED` to **0** (production-safe); set `1` on trusted dev consoles.
- **Backend:** `ADMIN_MUTATIONS_LOCALHOST_ONLY` middleware in `server/app.py` blocks POST/PUT/PATCH/DELETE under `/api/admin` from non-loopback when enabled.
- **Admin UI:** “Three control mechanisms” collapsible; **Refresh all health** (services + office + AI); browser `confirm()` on destructive actions (office stop all, office stop, AI stop, Windows stop/restart).
- **Follow-ups:** P12 Whisper overlap (optional); **P14** password rotation is documented in runbook §11.

### 2026-04-09 — P13 v1 closure (operator stack & admin instances)

- **Phase:** **P13** — **Done (v1)** for the scope below; **optional P13b** = full per-binary form fields + disk model picker (see `MASTER_PLAN_DREAMCISION.md` §4 P13 deferrals).
- **What shipped:** **`service_endpoints.json`** (repo root) as SOT for URLs, **`llama_instances`**, **`feature_routing`**, **`whisper_instances`**, office stack process lists/order, Windows services, cleanup ports, AI binary defaults. Admin **GET/PUT** `/api/admin/service_endpoints` with env sync; **`admin.html`** JSON + structured instance forms + office stack + AI start/stop + existing service table. Python **`server.core.office_stack_supervisor`** (NSSM entry), **`ai_process_launcher`**, **`office_stack_launcher`**, **`service_endpoints_sync`**; **`startup/start-office-stack.ps1`** thin wrapper. Gates: **`ADMIN_PROCESS_CONTROL_ENABLED`**, **`ADMIN_SERVICE_CONTROL_ENABLED`**, etc.
- **Tests:** `test_service_endpoints_sync.py`, `test_ai_process_launcher.py`, `test_office_stack_launcher.py` (and related admin/process coverage as in tree).
- **How verified:** Pytest green; operator smoke on admin save + process controls when gates enabled.
- **Next roadmap (user order):** **P12** optional Whisper overlap; optional **P13b** stretch items. **P14 v1** done — see log entry “P14 closure”.

### 2026-04-08 — P10 / P11 closure for roadmap (ship scope)

- **Phase(s):** **P10** and **P11** — **Done (ship)** for the master-plan scope; at the time, next items were **P12** (optional), **P13**, **P14** — **P13 v1** closed 2026-04-09 (see log entry above).
- **P10 summary:** Main SPA shell is **markup + boot**; modules include **`js/workspace_app.js`** (sets **`WORKSPACE_PAGE_TYPE`**), **`settings_connection.js`**, **`qa_side_panel.js`**, **`mobile_tools.js`**, **`settings_drawer.js`** (profile + auth card + mobile bar), **`version_badge.js`**. **`encounters_ui.js`** clears panel on load. **`service_worker.js`** never-cache list + cache **v33** for new JS. **Optional later (P10b):** split **`workspace_app.js`** per **`MODULARIZATION_PLAN.md`** — not blocking.
- **P11 summary:** DreamCision across **index / qa / admin / privacy / licenses**; **inline** header feather + wordmark on main app; PNG wordmarks elsewhere; **`manifest.json`** icons **192/512**; **`theme-color`**. Operator can re-sync PNGs from **`D:\dream`** via **`tools/sync_dream_logos.py`**.
- **How verified:** Static review + prior smoke; operator re-runs full UI smoke when convenient.
- **Follow-ups:** **P12** Whisper overlap experiment (optional); **P13** and **P14** closed v1 2026-04-09 (see newer log entries).

### 2026-04-07 — P11: DreamCision rebrand (v1.0), privacy & licenses pages

- **Phase(s):** **P11** — **Done** (v1).
- **Summary:** Rebranded **DreamCision** across **`PCHost/web/index.html`**, **`qa.html`**, **`admin.html`**; **Version 1.0** in titles, header, footer, and dynamic version line (`DreamCision · v1.0 · <commit> · <utc>`). **Operator logos** from **`D:\\dream`** (`no-star-logo.png` → wordmark PNGs, `No-star-1.png` → **`icon-192/512`**, **`favicon-48`**); regenerate via **`tools/sync_dream_logos.py`**. **`manifest.json`** PNG icons; **`theme-color`** meta. **`privacy.html`** / **`licenses.html`**; **`css/legal-pages.css`**. Footer links; QA legal links; admin nav. **`service_worker.js`** cache **v25** + never-cache for brand assets. **`workspace.css`** wordmark header layout. Share sheet text in **`workspace_app.js`**.
- **How verified:** Static review; paths work under `/` and `/static/` mirrors via existing SW rules.
- **Follow-ups:** Re-run **`sync_dream_logos.py`** after swapping sources in **`D:\\dream`**.
- **Blockers:** None.

### 2026-04-07 — P10: `index.html` split + unified QA session (text + vision)

- **Phase(s):** **P10** — **Done** (v1: CSS + major JS extractions). **Optional:** unified QA session across text and image QA.
- **Summary (P10):** Extracted **`PCHost/web/css/workspace.css`** (~2k lines) from **`index.html`**; replaced inline `<style>` with `<link rel="stylesheet" href="css/workspace.css">`. Extracted **`js/workspace_app.js`** (main SPA logic), **`js/settings_connection.js`**, **`js/qa_side_panel.js`**, **`js/mobile_tools.js`**. **`index.html`** reduced to ~900 lines (markup + small inline boot scripts). **`service_worker.js`** cache **v23**; added **NEVER_CACHE** for new CSS/JS paths (root + `/static/`).
- **Summary (QA unify):** **`qa_chat.py`** — turns include optional **`channel`** (`text` | `vision`); prompts/summary use **`_format_turn_for_prompt`**. **`qa_vision.py`** — removed **`_VISION_QA_STATE`**; vision follow-ups and text QA share **`_QA_STATE`** via **`append_qa_turn_for_session`** / **`get_qa_session_state`** / **`format_prior_turns_for_vision`**; after each vision reply, **`_update_summary`** runs like text QA.
- **Docs:** **`docs/QA_SMOKE_CHECKLIST.md`** — manual smoke steps for QA (text + vision + mixed session).
- **How verified:** `pytest Clinical-Note-Generator/server/tests` (69 passed, 1 skipped).
- **Follow-ups:** Further splits from **`MODULARIZATION_PLAN.md`** (`appState.js`, `noteGeneration.js`, …); optional ES modules + build step if desired.
- **Blockers:** None.

### 2026-04-07 — P8: Literature panel + QA session, limits, layout

- **Phase(s):** **P8** — **Done** (v1).
- **Summary (8a):** **`PCHost/web/literature_ui.js`** — slide-over **Literature** panel (teal header) loads **`GET /api/rag/recent_updates`** via `AuthWorkspace.request`; lists `documents` (title, source, summary, optional link); **Refresh**, backdrop/Escape close; **desktop** purple rail button + **mobile Tools** row. Sign-out closes panel. **`service_worker.js`** never-cache + cache **v22**.
- **Summary (8b):** **`qa.html`** — **`New topic`** (new `session_id` UUID) vs **Clear chat** (same session); requests send **`session_id: qaSessionId`**; taller composer (`min-height` 100px, `max-height` min(40vh,420px)); safe-area padding. **`qa_chat.py`** — message **`max_length` 16000**; default completion **`qa_chat_max_tokens` 1400**; **`qa_chat_summary_max_tokens`** / **`qa_chat_summary_convo_max_chars`**; rolling context **6** recent turns / **10** in summary; softer prompt (sections optional); summary truncation helper. **`config.json`** + **`perf.py`** default **`qa_max_user_chars` 2048**; admin input max **16000**.
- **How verified:** `pytest Clinical-Note-Generator/server/tests` (66 passed, 1 skipped). Streaming QA tests still pass.
- **Follow-ups:** Manual **vision + text** streaming smoke on device; operator literature cache path.
- **Blockers:** None.

---

### 2026-04-07 — P9: ~12k completion cap, preprocessing budgets, orders/imaging tokens

- **Phase(s):** **P9** — **Done** (v1).
- **Summary:** Added **`server/core/token_limits.py`** — `MAX_COMPLETION_TOKENS_CAP` = 12288 (12×1024), `clamp_completion_tokens`, truncation helper. **`/api/generate_v8_stream`** clamps `max_tokens` to this cap; defaults from **`config.json`** (`default_note_max_tokens` 12288). Preprocessing section budgets raised to **4096 each** (12 288 total). **Order pipeline:** config keys `order_detect_max_tokens` (default 1536), `order_gen_max_tokens_default` / `order_gen_max_tokens_long` (1024 / 3072); clearer imaging vs procedure rules; procedure requisitions use imaging-style headers. **Admin** saves clamped values; **perf** default `qa_context_length` 12288. **Frontend:** chart field warning at **49 152** chars (~12k tokens heuristic), includes current encounter; copy explains server-side trim. **`admin.html`** max token inputs align to 12288. Tests: **`test_token_limits_p9.py`**.
- **How verified:** `pytest Clinical-Note-Generator/server/tests` (66 passed, 1 skipped).
- **Follow-ups:** **P8** literature + QA UX; **P10** modularize frontend.
- **Blockers:** None.

---

### 2026-04-07 — P7: per-encounter queue strip, unified offline queue, download deletes server copy

- **Phase(s):** **P7** — **Done** (v1).
- **Summary:** Implemented per-encounter queue surface in **`PCHost/web/index.html`**: queue strip at top of encounter, filtered by `app.activeEncounterId`. Added per-item **Retry / Download / Delete** actions; **download removes server row** after successful download (best effort). Unified offline/server-down handling by storing queued files in **IndexedDB** (`queueStorage`) with persisted local-only metadata (`cng_local_queue_meta_v1`) and merging local-only items with server `/api/queue` list. Added **Clear encounter queue** button (server: `DELETE /api/queue?encounter_id=<id>` + local-only cleanup). Hardened ASR: empty transcripts are **not** treated as success (frontend live + queued) and backend retains queued job/file (422 + status `needs_review`).
- **How verified:** `pytest Clinical-Note-Generator/server/tests` (61 passed, 1 skipped). Manual UI sanity.
- **Follow-ups:** **P8** literature button + QA UX.
- **Blockers:** None.

---

### 2026-04-07 — P6: Encounters slide-over UI (`encounters_ui.js`), busy gate, replace New Case

- **Phase(s):** **P6** — **Done** (v1).
- **Summary:** Added **`PCHost/web/encounters_ui.js`** — slide-over **Encounters** panel: list **`GET /api/encounters/`**, **New encounter** (POST + activate), **Open / Rename / Delete** (confirm), **Close encounter** (clear active thread via **`AuthWorkspace.closeCurrentEncounter`** → **`/api/workspace/clear`**). Desktop sidebar + mobile bottom nav + **Tools** sheet entry; replaced **New Case** / **Reset workspace** flows for signed-in users ( **`clearAll()`** opens panel). **`AuthWorkspace.isClinicalBusy()`** (recording, streaming, OCR flag, progress bars); **`processDocuments`** sets **`__cngOcrBusy`**. Service worker **`encounters_ui.js`** never-cache + cache bump **v21**.
- **How verified:** Manual UI; backend unchanged from P5 (pytest not re-run for this UI-only pass).
- **Follow-ups:** **P7** per-encounter queue strip + failure UX.
- **Blockers:** None.

---

### 2026-04-06 — P5: `UserEncounter`, workspace shell, `/api/encounters`, queue `encounter_id`

- **Phase(s):** **P5** — **Done** (v1).
- **Summary:** Added **`user_encounter`** table and **`UserEncounter`** model; legacy **`UserWorkspace`** payload migrated into first encounter **“Default”** with **`extras.activeEncounterId`** on workspace shell. **`server/core/encounter_workspace.py`**: **`ensure_encounters_for_user`**, **`compose_client_workspace_state`**, **`merge_incoming_workspace_state`**, **`clear_active_encounter_state`**. **`routes/workspace.py`** reads/writes active encounter. **`routes/encounters.py`**: list, create, get, patch label, activate, delete (JSON **`confirm: true`**); delete drops **queued_jobs** + files for that encounter. **`queued_jobs.encounter_id`** + SQLite migration in **`db.py`**. **`test_encounters_p5.py`**. **`MASTER_PLAN`** §P5 marked done.
- **How verified:** `pytest server/tests` (61 passed, 1 skipped).
- **Follow-ups:** **P6** encounter UI (replace New Case / busy gate).
- **Blockers:** None.

---

### 2026-04-06 — P3b: custom note types (create/delete), bulk revert, dynamic note-type menus

- **Phase(s):** **P3b** — **Done**.
- **Summary:** Extended **`UserPreferences.preferences_json`** with **`custom_note_types`** (`[{id, label, scope}]`). **`profile_service`**: **`create_custom_note_type`**, **`delete_custom_note_type`**, **`revert_all_builtin_templates`**, **`revert_note_templates_bulk`**, **`note_type_uses_other_builder`** (aligned with **`OTHER_NOTE_TYPES`**). Routes: **`POST /api/note-types/custom`**, **`DELETE /api/note-types/{id}`**, **`POST /api/note-types/revert-builtins`**, **`POST /api/note-types/revert-bulk`**. **`generate_v8_stream`** routes **`build_prompt_other`** vs **`build_prompt_v8`** using prefs + custom scope. **`PCHost/web/index.html`**: prompt modal add/delete custom type, reset all built-ins; **`noteType`** / **`noteTypeSelect`** / **`noteTypeMirror`** rebuilt from **`GET /api/note-types`**. Tests in **`test_profile_p3.py`**. **`MASTER_PLAN_DREAMCISION.md`** §P3b + phase table.
- **How verified:** `pytest server/tests` (56 passed, 1 skipped).
- **Follow-ups:** **P5** encounters.
- **Blockers:** None.

---

### 2026-04-05 — Pre-P5 handoff: P3/P4 doc sync, regression test, `chroma_data` gitignore

- **Phase(s):** Meta (documentation + repo hygiene); **P3/P4** testing notes closed in master plan.
- **Summary:** Documented **author-aware prompts** in **`MASTER_PLAN_DREAMCISION.md`** (phase table + §P4): **`{USER_DISPLAY_NAME}`**, **`{USER_EMAIL}`**, optional **`[Profile author]`** SYSTEM line. Marked **P3** multi-device + regression checklist items **done** (API contract + `test_merge_templates_untouched_user_matches_config_baseline` in **`test_profile_p3.py`**). Updated **`IMPLEMENTATION_LOG`** resume block; **`FRONTEND_INDEX`** / **`BACKEND_INDEX`** prompt-operator notes. Added **`RAG/chroma_data/`** to **`.gitignore`** for local Chroma dirs.
- **Files / areas:** `docs/planning-archive/MASTER_PLAN_DREAMCISION.md`, `docs/IMPLEMENTATION_LOG.md`, `.gitignore`, `FRONTEND_INDEX.md`, `BACKEND_INDEX.md`, `Clinical-Note-Generator/server/tests/test_profile_p3.py`.
- **How verified:** `pytest Clinical-Note-Generator/server/tests` (full suite).
- **Git:** Handoff commit on `main` (same change set as this log entry).
- **Follow-ups:** **P5** encounter data model & API.
- **Blockers:** None.

---

### 2026-04-05 — P4 complete: unified USER block, location in prompts, internal system template

- **Phase(s):** P4 — **Done** (v1).
- **Summary:** Refactored **`server/core/prompt/builder.py`**: **`_prompt_values`** adds **`{USER_LOCATION}`** (default text **Not specified** when unset); **`_apply_region_substitution`** replaces legacy **Nova Scotia** phrasing when a location is provided; **`custom_prompt`** / encounter addendum is merged via **`_compose_user_section`** into the **USER** block (removed separate **ADDITIONAL INSTRUCTIONS** / legacy **USER CUSTOM** blocks). **`generate_v8_stream`** accepts **`encounter_location`** (overrides profile **`default_location`**). **`GET /api/note_prompts`** no longer returns **`system`** / **`system_other`**. UI: **`#encounterLocation`** in the floating bar, **`extras.encounterLocation`** in workspace (`auth_workspace.js`); prompt modal copy distinguishes **account template** vs **encounter addendum**; removed unused **`defaultPromptSystem`** state.
- **Files / areas:** `Clinical-Note-Generator/server/core/prompt/builder.py`, `server/routes/notes.py`, `PCHost/web/index.html`, `PCHost/web/auth_workspace.js`, `server/tests/test_p4_prompt_builder.py`, `docs/planning-archive/MASTER_PLAN_DREAMCISION.md`.
- **How verified:** `pytest server/tests` (47 passed, 1 skipped).
- **Follow-ups:** **P5** encounter model for structured revert; operators can adopt **`{USER_LOCATION}`** in **`config.json`** system strings explicitly.
- **Blockers:** None.

---

### 2026-04-05 — P3 complete: profile, `UserPreferences`, note-type templates, generation + UI

- **Phase(s):** P3 — **Done** (v1 scope per master plan).
- **Summary:** Extended **`User`** with **`display_name`**, **`default_specialty`**, **`default_location`**, optional **`profile_updated_at`**. Added **`UserPreferences`** (`preferences_json`: `templates` / `templates_other`, `schema_version`) with **`profile_service`** (baseline from **`config.json`**, merge, patch, revert). Routes: **`GET/PUT /api/profile`**, **`GET/PUT /api/note-types`** (shallow patch of template maps), **`POST /api/note-types/{id}/revert`** (query `other=true` for “other” note types). **`GET /api/note_prompts`** returns **`templates`** (effective) + **`templates_baseline`** and requires **Bearer** auth. **`generate_v8_stream`** merges per-user templates and uses **`default_specialty`** when encounter speciality is empty. SQLite: **`ALTER TABLE user ADD COLUMN …`** on startup when needed. Frontend (**`PCHost/web/index.html`**): settings drawer profile fields; prompt modal account template save/revert + sync with **`loadProfileNoteTypes`**. Tests: **`server/tests/test_profile_p3.py`**, **`auth_utils.register_approve_login`** for shared auth; **`conftest`** keeps **`server.core.db.engine`** and **`core.db.engine`** aligned for routes that import **`core.db`**.
- **Files / areas:** `server/models/user.py`, `server/models/user_preferences.py`, `server/core/profile_service.py`, `server/core/db.py`, `server/schemas/profile.py`, `server/routes/profile.py`, `server/app.py`, `server/core/prompt/builder.py`, `server/routes/notes.py`, `PCHost/web/index.html`, `server/tests/test_profile_p3.py`, `server/tests/auth_utils.py`, `server/tests/conftest.py`, `server/tests/test_notes_modular.py`.
- **How verified:** `pytest server/tests` (42 passed, 1 skipped).
- **Follow-ups (deferred):** Custom note-type **POST/DELETE** (not in v1); **P4** unified prompt builder / reduce **`config.json`** duplication; email change + admin profile audit per master plan.
- **Blockers:** None.

---

### 2026-04-05 — P2 follow-up: `service_endpoints.json` (single operator file) + legacy launcher + ASR hardening

- **Phase(s):** P2 (closure / operator ergonomics); no change to P2 “done” status.
- **Summary:** Added repository-root **`service_endpoints.json`** as the **one** human-edited file for service URLs/host ports (`env` block → `apply_service_endpoints()` on FastAPI startup; **`pchost`** overrides merge into **`PCHost/server.js`**). Removed duplicate URL keys from **`Clinical-Note-Generator/config/config.json`** (RAG / order / OCR base URLs); RAG comment & order routing use **`LLM_*`** / file-backed env only. Moved **`start_fastapi_server.bat`** → **`Clinical-Note-Generator/legacy/`**; canonical batch is **`start_fastapi_server_external.bat`** (reads **`fastapi_port`** from JSON when `FASTAPI_PORT` unset). **ASR:** treat HTTP 200 + JSON **`error`**, empty transcription, and implicit **localhost:8095 → 8096** fallback; **`asr_engine`** returns **`normalize_to_wav`** / **`ffmpeg_bin`** for admin/debug.
- **Files / areas:** `service_endpoints.json`, `Clinical-Note-Generator/server/core/service_endpoints.py`, `server/app.py`, `server/core/llm_routing.py`, `server/routes/qa_chat.py`, `server/routes/asr.py`, `server/routes/admin.py`, `PCHost/server.js`, `Clinical-Note-Generator/config/config.json`, `start_fastapi_server_external.bat`, `Clinical-Note-Generator/legacy/start_fastapi_server.bat`, tests under `server/tests/`.
- **How verified:** `pytest` on `test_llm_routing.py`, `test_qa_chat_stream.py`, `test_asr_proxy.py` (12 passed).
- **Follow-ups:** Refresh **`MASTER_PLAN` P2** bullets (remove stale `config.json` references) — done in same doc pass as **P3** expansion. Next product phase after P3: **P4** prompt builder.
- **Blockers:** None.

---

### 2026-04-05 — P2 per-feature LLM routing (`LLM_*` env, `llm_routing.py`, startup log)

- **Summary:** Added `server/core/llm_routing.py` and wired `SimpleNoteGenerator` roles `note_gen` / `qa_text`, explicit URL pairs for RAG comment + order pipelines, OCR + vision QA clients. Startup logs list resolved endpoints (host:port only). Documented in `docs/ENV_VARIABLES.md`; canonical launcher comment in `start_fastapi_server_external.bat`. Tests: `server/tests/test_llm_routing.py`.
- **Files:** `server/core/llm_routing.py`, `server/services/note_generator_clean.py`, `server/routes/notes.py`, `server/routes/qa_chat.py`, `server/services/ocr_llm_client.py`, `server/services/vision_qa_client.py`, `server/app.py`, `server/routes/admin.py`, `docs/ENV_VARIABLES.md`, `start_fastapi_server_external.bat`, `docs/planning-archive/MASTER_PLAN_DREAMCISION.md`, tests.

---

### 2026-04-05 — Close P0/P1; SW NEVER_CACHE; remove NeMo/Nemotron artifacts; P14 operator notebook

- **Summary:** Marked **P0** and **P1** complete in [`MASTER_PLAN_DREAMCISION.md`](./planning-archive/MASTER_PLAN_DREAMCISION.md). Added `/admin.html` and `/static/admin.html` to `NEVER_CACHE` in `PCHost/web/service_worker.js` and bumped cache version (`v20`). Deleted root `NEMO_STREAMING_COMPATIBILITY.md`; removed Nemotron/NeMo streaming sections from `ARCHITECTURE_OPTIONS_MORNING_BRIEF.md`. Added **P14** — operator notebook & runbook (last), including deferred **admin password rotation** documentation. JWT + bootstrap admin accepted as P1 scope; cookie sessions and rate limits explicitly optional.
- **Files:** `PCHost/web/service_worker.js`, `ARCHITECTURE_OPTIONS_MORNING_BRIEF.md`, `docs/planning-archive/MASTER_PLAN_DREAMCISION.md`, `docs/IMPLEMENTATION_LOG.md`; removed `NEMO_STREAMING_COMPATIBILITY.md`.

---

### 2026-04-05 — P0 hygiene + P1 bootstrap admin (partial)

- **Phase(s):** P0 complete; P1 started.
- **Summary:** Removed legacy standalone `ocr.html` and admin nav link; marked NeMo doc historical; added optional **`ADMIN_BOOTSTRAP_EMAIL` / `ADMIN_BOOTSTRAP_PASSWORD`** startup creation of a single admin user (`ensure_bootstrap_admin`); admin UI button copy clarified (“Sign in (admin)”); documented env vars in `INSTALLATION_GUIDE.md`.
- **Files / areas:** Deleted `PCHost/web/ocr.html`; `PCHost/web/admin.html`; `Clinical-Note-Generator/server/app.py`, `server/core/bootstrap_admin.py`; `NEMO_STREAMING_COMPATIBILITY.md`; `Clinical-Note-Generator/docs/CNG_PROJECT_HANDOFF.md`; `INSTALLATION_GUIDE.md`.
- **How verified:** `python -c` import/run `ensure_bootstrap_admin` with no env (no-op); manual grep: no `ocr.html` in `PCHost` except docs referring to removal.
- **Git:** _(fill on commit)_.
- **Follow-ups:** P1 remainder: rate-limit admin login, optional HttpOnly session cookie for admin-only (if desired); remove redundant “paste token” prominence once team uses sign-in only. **P2** config/LLM routing or **P5** encounters per master plan order.
- **Blockers:** None.

### Template (copy for each entry)

```markdown
### YYYY-MM-DD — Short title

- **Phase(s):** P#
- **Summary:** What changed in one paragraph.
- **Files / areas:** paths or modules touched.
- **How verified:** tests run, manual checks, screenshots if needed.
- **Git:** commit hash or branch/PR (optional).
- **Follow-ups:** Next concrete steps; open questions.
- **Blockers:** None | description.
```

---

### 2026-04-05 — Log & plan scaffolding

- **Phase(s):** Meta (documentation only).
- **Summary:** Added this implementation log and linked it from the master plan so progress can be recorded incrementally.
- **Files / areas:** `docs/IMPLEMENTATION_LOG.md`, `docs/planning-archive/MASTER_PLAN_DREAMCISION.md` (cross-link).
- **How verified:** Files exist and render in repo.
- **Git:** _(fill on commit)_.
- **Follow-ups:** First real entry when P0 or another phase begins; keep phase table in sync.
- **Blockers:** None.

---

## Quick “resume here” block

*Last person to work on the project: copy the latest summary into this box when handing off.*

| Field | Value |
|-------|--------|
| **Date** | 2026-04-07 |
| **Active phase** | **P0–P7 + P3b done.** Next: **P8** — literature + QA UX. |
| **What was done last** | **P7:** per-encounter queue strip + unified offline queue + empty-ASR retained job/file. |
| **What to do next** | **P8** per master plan; optional **P1** admin login rate-limit. |
| **Branch / commit** | `main` — handoff commit titled *docs: pre-P5 handoff — author prompts…* |
| **Secrets / env** | JWT / bootstrap admin / DB URLs — never commit; operators edit **`service_endpoints.json`** for service ports (not secrets). |

---

## Optional: link to external tracker

If you use GitHub Issues / Projects / Jira, add one line here:

- **External board:** _(URL or “none”)_

---

*Append new entries above the “2026-04-05” sample as work progresses; keep newest at top.*
