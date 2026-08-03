# ASR pipeline resilience plan

This document captures **known failure modes**, **target behavior**, and a **phased plan** to harden the Record → persist → transcribe → queue path without redesigning the whole pipeline. It complements [`ASR_PIPELINE_MAP.md`](./ASR_PIPELINE_MAP.md).

**Master sequencing** (sync + ASR + streaming) lives in **[`docs/GRAND_PLAN.md`](../../docs/GRAND_PLAN.md)**. Use the **Master rollout order** section below for operator execution; this file remains the detail for failure modes **A–E**.

**Scope:** FastAPI queue/transcribe endpoints + PCHost workspace (`workspace_app.js`, `universal_audio_handler.js`). Auth model specifics live with `auth_workspace.js` / token issuance — this plan states requirements; implementation choices belong with whoever owns auth.

---

## Goals

1. **Single source of truth** for “does this encounter still have server-side full audio?” — avoid long-lived **client-only** ids contradicting the DB/filesystem after TTL prune, admin deletes, merge replacements, or restores.
2. **No silent data loss on stop** when the network or auth is degraded at the moment of **finalize upload / transcribe / queue**: blobs remain **recoverable** for the **same encounter** until encounter delete or retention TTL (policy exceptions excluded).
3. **Valid access token** through the **critical window**: from intentional stop through completion of **persist + live transcribe attempt + fallback queue** (or explicit local durable fallback).
4. **Preserve existing product rules:** encounter-scoped queue processing, `asr_recording` merge semantics, Re-transcribe download path, duplicate suppression — fixes should **reconcile state**, not bypass guards.
5. **Maximum sensitivity (clinical):** VAD **off** on all clinical ASR paths; tune toward capturing quiet/distant speech even at the cost of extra noise — see **ASR sensitivity profile** in [`docs/GRAND_PLAN.md`](../../docs/GRAND_PLAN.md) (G7).
6. **Whole-audio re-transcribe must persist:** Re-transcribe on merged encounter audio replaces and saves transcript to workspace, not preview-only — see **Full-audio re-transcribe** in the grand plan (G7).
7. **Workspace sync must not stomp local chart/note text:** See **G5a Sync-P2.5** in [`docs/GRAND_PLAN.md`](../../docs/GRAND_PLAN.md) — 409 merge must not overwrite fresher server ASR when user only edited chart fields (**AO-F3**; server guard in `merge_incoming_workspace_state`).

---

## Failure modes and target fixes

### A. Stale `asr_recording` job id in client storage

**What happens:** `localStorage` keys such as `asr_recording_jobs_<eid>` hold canonical queue job ids after upload success. Server-side rows/files may later disappear (TTL cascade, admin action, DB restore, successful merge replacing prior job id). The UI may still assume “server has audio.”

**Target behavior:** Client cache is a **cache**. Any authoritative answer comes from the **server** (or a negative server response), then the cache is updated.

**Planned work:**

| Step | Action |
|------|--------|
| A1 | **Inventory** every reader/writer of encounter recording metadata (upload success, merge response, encounter switch, logout, Re-transcribe download). Document in a short appendix or inline checklist when touching ASR again. |
| A2 | **Reconcile on encounter load:** When workspace/encounter payload is loaded (existing refresh paths), include **canonical recording pointers** — e.g. current `asr_recording` job id(s) or a boolean `has_encounter_recording` plus download-ready flag — and **overwrite or clear** client keys to match. Prefer **one** backend shape to avoid drift between list encounters vs workspace GET. |
| A3 | **Reconcile on 404:** If Re-transcribe download or job-by-id fetch returns **404**, clear stale ids for **that encounter**, refresh UI, optionally toast once (“recording refreshed — try again”). Idempotent: repeated 404 should not spam. |
| A4 | **Optional heartbeat:** Lightweight GET “recording status for encounter” used sparingly (e.g. after long idle tab) — lower priority than A2/A3. |

**Acceptance criteria:** After server deletes recording without client knowing, the next user action or encounter refresh **corrects** UI; Re-transcribe does not repeatedly hit dead ids without recovery.

---

### B. Access token expiry mid-session (especially during Record → Stop pipeline)

**What happens:** Short-lived JWTs expire while the user is dictating or before uploads finish. Server calls return **401**; naive clients neither refresh nor fall back durably.

**Target behavior:** Prefer **keeping a valid access token** through the pipeline via **refresh** (not infinite-lived access tokens). “Busy pipeline” should trigger **proactive refresh** before expiry while recording/stopping/transcribing/uploading, bounded by policy.

**Planned work:**

| Step | Action |
|------|--------|
| B1 | **Confirm auth capabilities:** Refresh tokens **exist** (`POST /api/auth/refresh`, httpOnly cookie, `RefreshToken` model). Document access-token TTL (default 600 min from config) and **remaining gaps**: raw `fetch` call sites and pipeline-aware scheduling (B2–B4), not absence of refresh. |
| B2 | **Pipeline-aware refresh:** While `recording \|\| stopping \|\| transcribing \|\| pending upload after stop` (exact flags depend on `audioPipelinePhase` / handler state), schedule refresh **before** `exp` (e.g. at half remaining lifetime or fixed cadence), with **retry/backoff** on failure. |
| B3 | **Stop-path ordering:** Ensure refresh completes **before** `POST /queue` (asr_recording / transcribe) and `POST /transcribe_diarized` when stop fires if token is near expiry (policy decision: block stop briefly vs queue behind refresh — document choice). |
| B4 | **Security bounds:** Cap maximum extension (e.g. max recording duration aligns with org policy); rotation/reuse rules for refresh tokens unchanged — **do not** mint infinitely-long access tokens for convenience alone. |

**Acceptance criteria:** In synthetic tests, expiry mid-recording does not prevent successful persist + transcribe when refresh infrastructure is healthy.

---

### C. Auth rejected on stop (401/403) — durability vs “connected” UI

**What happens:** `queueRequest` and persist paths require `Authorization`. HTTP **401** is often **not** classified like network failure, so **IndexedDB local queue** may never run; user expectation “blob still queued locally” may fail.

**Target behavior:** If server **rejects auth** for encounter-scoped upload/transcribe after stop, still achieve **durable local artifact** for that encounter where technically possible: **RecordingRecovery / IndexedDB queue / explicit download** — with clear UX (“sign in to sync”) without implying sync succeeded.

**Planned work:**

| Step | Action |
|------|--------|
| C1 | **Audit stop sequence:** Trace `storeLatest` → `transcribeAudio` → `queueRequest` catch branches when response is **401/403** vs network error vs timeout. |
| C2 | **Policy:** Define whether **401** should trigger **same local-queue path** as offline (encounter_id preserved in metadata) when blob is still in memory — security review if PHI is written to IndexedDB without auth (often acceptable if device already trusted and encrypted-at-rest expectations match product policy). |
| C3 | **RecordingRecovery alignment:** Ensure finalize paths already independent of token where designed; close gaps if any step assumes auth too early. |
| C4 | **UX:** Single predictable toast hierarchy: auth failure vs queued locally vs download fallback. |

**Acceptance criteria:** With intentional revoked/expired token at stop, user retains **recoverable local path** or explicit **download**, never silent discard of final blob without acknowledgment.

---

### D. IndexedDB / quota / private mode

**What happens:** Local storage fails; download fallback may or may not succeed.

**Target behavior:** Already partially mitigated; plan adds **explicit testing matrix** and optional **second fallback** (copy to clipboard not viable for audio — stick to download + recovery UX).

**Planned work:** QA matrix (Safari private, Firefox strict, quota simulation); document limits in user-facing help only if product wants.

---

### E. Operational monitoring (production readiness)

**Planned work:** Alerts or dashboards on ASR upstream failures, queue `/process` failure rate, incident store volume (`asr_diagnostics`), disk usage on queue_files — reference [`ENV_VARIABLES.md`](./ENV_VARIABLES.md) for toggles.

---

## Implementation phases (this document ↔ grand plan)

**Status uses the GRAND_PLAN legend: CODE (in repo) / DEPLOYED (running) / VERIFIED (smoke + sign-off). See [GRAND_PLAN Status & execution truth](../../docs/GRAND_PLAN.md#status--execution-truth-read-first). As of 2026-06-12, ASR-P0/P1 are CODE-complete but the FastAPI restart and smoke pass have NOT been run.**

| ASR phase | Grand milestone | Focus | Deliverables | Status (2026-06-12) |
|-----------|-----------------|-------|----------------|---------------------|
| **ASR-P0** | **G1′** | Truth & reconciliation | A2, A3; clear stale keys on 404 | CODE ✅ · DEPLOYED ⚠️ (FastAPI restart pending) · VERIFIED ❌ |
| **ASR-P1** | **G4** (requires **G3** authFetch) | Auth during pipeline + stop durability | B2–B4; C1–C4 | CODE ✅ · DEPLOYED ✅ (web) · VERIFIED ❌ |
| **ASR-P2** | **G8** (partial) | Monitoring & QA | E; IndexedDB matrix D | CODE ◻ partial · VERIFIED ❌ |

Streaming diarization (**Stream-P0/P1**, grand **G6/G7**) extends this plan after **G5** (proxy WS/SSE). Whisper batch remains default until Stream-P1 parity sign-off.

Dependencies: **ASR-P0** reduces phantom UI state; **ASR-P1** addresses token and auth rejection together — **do not enable G2/G4 401→RecordingRecovery until A3 (404 reconcile) ships**.

---

## Master rollout order

Single operator sequence for the full program. Phase codes match [`docs/GRAND_PLAN.md`](../../docs/GRAND_PLAN.md).

```
G0   Foundations (flags OFF, telemetry baseline)
 │
 ├── G1   Sync-P0: unload save + API base          ┐
 └── G1′  ASR-P0: A2/A3 recording truth           ┘ parallel
      │
      ▼
G2   Sync-P1: SQLite WAL + awaited saves (+ 401 shell; gate on A3)
      ▼
G3   Sync-P2: authFetch + cold refresh  ◄── LINCHPIN — freeze other client fetch edits
      ▼
 ├── G4   ASR-P1: B2–B4 + C1–C4
 └── G5   Sync-P3: version endpoint, proxy timeouts/WS, honest health
      ▼
G6   Stream-P0 scaffold (STREAMING_ASR_ENABLED=0)
      ▼
G7   Stream-P1 Nemotron + Sortformer + Gemma (parity gate before default)
      ▼
G8   Sync-P4 + ASR-P2 monitoring, SW, docs
```

### Grand milestone → ASR steps

| Execute order | Milestone | ASR / sync deliverables | Service restart? |
|---------------|-----------|-------------------------|------------------|
| 1 | G0 | Flags; baseline metrics | No |
| 2a | G1 | Sync unload save, `auth_api_base` fix | No (web only) |
| 2b | G1′ | **A1–A3** (A1 inventory can be doc-only Friday) | No |
| 3 | G2 | WAL; awaited saves; C shell behind flag | **FastAPI** ~30s |
| 4 | G3 | authFetch; cold `/api/auth/refresh` | No |
| 5 | G4 | **B2–B4**, **C1–C4** | No |
| 5a | **G5a** | **Sync-P2.5 anti-overwrite** — block pull/409 stomp; AO-F3 protects server ASR on 409 merge | No (web only) |
| 6 | G5 | Version GET + recording meta; proxy; health | **PCHost** ~30s |
| 7+ | G6–G8 | Streaming scaffold → live → cleanup | After hours |

### Do not run in parallel

- **G3** with any other milestone that edits client `fetch` paths.
- **G2 WAL** with **G6/G7** worker bring-up.
- **Stream-P0+** before **G5** proxy streaming timeouts.
- **G1** workspace contract changes bundled with **G5** version endpoint in one release.

### Per-milestone operator steps

1. Confirm deploy tree matches NSSM cwd ([`docs/DEPLOYMENT_PATHS.md`](../../docs/DEPLOYMENT_PATHS.md)).
2. Backup `Clinical-Note-Generator/data/user_data.sqlite` (and `-wal`/`-shm` after G2).
3. Deploy with **new flags OFF**; smoke current behavior.
4. Enable **one** milestone flag; soak (see grand plan test matrix **T1–T18**).
5. Bump `PCHost/web/service_worker.js` `CACHE_NAME` when web assets change.
6. Rollback: flag OFF first; revert code only if schema/API contract changed.

### ASR-focused smoke tests (minimum per milestone)

| After | Run |
|-------|-----|
| G1′ | **T3**, **T4** — stale job id; 404 Re-transcribe |
| G2 | **T5**, **T6** (partial if C not complete) |
| G4 | **T6**, **T9** — 401 at stop; expiry mid-recording |
| G5a | **T19–T22b** — offline edit not stomped; 409 local wins; ASR not wiped on chart-only conflict (AO-F3) |
| G5 | **T11** — long `transcribe_diarized` through proxy |
| G7 | **T16**, **T17** — fallback; parity vs batch |

---

## Friday afternoon execution (Phase 1 — Windows)

**When:** Phase 1 Friday **17:00** → Saturday **~02:00**. Full timing: [`docs/GRAND_PLAN.md`](../../docs/GRAND_PLAN.md#phase-1--g0g5a-g5-on-windows-fri-1700--sat-0200).

**Scope:** **G0–G5** on **current Windows host**. Phase 2 (Sat–Sun) is **userver migration** — not G6–G8.

**Reality as of 2026-06-12:** code is merged + pytest-green (CODE ✅). This window — restarts, smoke, sign-off — has **NOT been run**. FastAPI has not been restarted, so `GET /api/workspace/version` is still **404** on the live server (client falls back to `/api/workspace/`, so the app works). The one blocker is an elevated `Restart-Service OfficeStack -Force`.

### Pre-flight (30 min, before 17:00)

- [ ] G0–G5 code merged; pytest smoke pass (**already true 2026-06-12**)
- [ ] Repo path == NSSM `AppDirectory`
- [ ] Backup `user_data.sqlite`
- [ ] `CACHE_NAME` ready to bump
- [ ] Flag state per [GRAND_PLAN flag policy](../../docs/GRAND_PLAN.md#feature-flag-policy-authoritative--supersedes-any-all-flags-off-wording-below): safety flags **ON**, `STREAMING_ASR_ENABLED=0`

### Phase 1 checklist (G0 → G5)

- [ ] **G0** telemetry baseline
- [ ] **G1 + G1′** → T1–T4 (confirm `SYNC_BEACON_FIX`, `ASR_RECORDING_RECONCILE` **ON** per flag policy; flip `=0` only to roll back)
- [ ] **G2** WAL → **FastAPI restart** → T5 (live DB already WAL — confirm)
- [ ] **G3** authFetch → T7–T8 → **T8b** (Generate request URL is `/api/generate_v8_stream`, not bare — Regression Guard R1)
- [ ] **G4** ASR-P1 → T6, T9
- [ ] **G5** proxy + version → **FastAPI + PCHost restart** → confirm `GET /api/workspace/version` returns **401** not 404 → T10–T11
- [ ] **G5a** anti-stomp → T19–T22b
- [ ] Log commit hash + flag state in `IMPLEMENTATION_LOG.md` by Sat 02:00 (marks VERIFIED)

### Phase 2 starts Saturday (userver)

- [ ] Migrate PCHost, FastAPI, RAG, SQLite, systemd, TLS, DNS
- [ ] Build **whisper.cpp** on userver; pool **`8095`, `8096`** (production); deploy **vLLM** for Gemma at **8081** / **8037** — **not `llama-server`** in production (see GRAND_PLAN Phase 2 AI stack policy)
- [ ] Smoke: login, sync, Record→Stop→**local** transcribe, note generate

**Phase 3 (Mon–Fri):** standalone streaming module on userver — see grand plan **Phase 3**.  
**Phase 4 (next Fri–Sun):** plug module into Dreamcision + G8 + production sign-off.

---


## Non-goals (this plan)

- Replacing whisper pooling or ffmpeg normalization (already covered elsewhere).
- Changing **encounter-only queue processing** semantics — intentional product boundary.
- Guaranteeing cross-encounter automatic transcription without user action (manual upload remains user workflow).

---

## Review cadence

| When | Action |
|------|--------|
| After **G1′** (ASR-P0) | Mark A2/A3 status in this doc |
| After **G3** | Mark B1 complete (refresh wired globally) |
| After **G4** (ASR-P1) | Mark P1 complete |
| Before **G7** default flip | Clinical parity + labeling sign-off |

---

## Related references

- [`docs/GRAND_PLAN.md`](../../docs/GRAND_PLAN.md) — master rollout order, G0–G8, streaming track  
- [`ASR_PIPELINE_MAP.md`](./ASR_PIPELINE_MAP.md) — component map  
- [`ENV_VARIABLES.md`](./ENV_VARIABLES.md) — ASR / timeout env vars  
- Server: `server/routes/queue.py` (`asr_recording` merge), `server/routes/asr.py`, `server/core/encounter_workspace.py` (TTL prune)  
- Client: `PCHost/web/js/workspace_app.js`, `PCHost/web/universal_audio_handler.js`
