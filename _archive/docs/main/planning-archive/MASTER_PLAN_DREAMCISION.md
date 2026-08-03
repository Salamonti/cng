# DreamCision — Master implementation plan

> **Archived snapshot** (2026-04). Lives under `docs/planning-archive/`. For the next roadmap, see [`FUTURE_PLAN_BACKLOG.md`](./FUTURE_PLAN_BACKLOG.md). Ongoing engineering log: [`../IMPLEMENTATION_LOG.md`](../IMPLEMENTATION_LOG.md).

This document was the **single source of truth** for the migration from the Clinical Note Generator stack to **DreamCision**: multi-encounter workspaces, unified failure handling, profile-driven prompts, admin session model, QA improvements, rebranding, and follow-on work.

**Related folders for reference:** `D:\new-project` (spike / prior art), `D:\dream` (logo assets, star-free), `D:\asr-chunk-prototype` (optional Whisper overlap experiment — **last**).

**Progress & handoff:** Record completed work, verification, and “resume here” notes in [`../IMPLEMENTATION_LOG.md`](../IMPLEMENTATION_LOG.md) (newest entries at top). Update the **Phase status** table there whenever a phase moves forward.

---

## 1. Principles and non-negotiables

| Principle | Detail |
|-----------|--------|
| **ASR engine** | **Whisper only** (whisper.cpp / whisper-server). No alternate ASR stacks in this repo. |
| **ASR pipeline** | **No** mandatory post-processing layer for quality (user preference). Optional overlap-chunking experiment **only at end** of roadmap (§16). |
| **Encounters** | **ChatGPT-like threads**: persistent per user, same data from any browser; **not** scoped to QA (QA is **user-global**, §5). |
| **Deletion** | Encounters **permanently deleted** after explicit user confirmation (Nova Scotia College retention posture; user prefers delete over long retention). |
| **QA** | **User-specific**, **not** tied to encounters; **no** server requirement to retain full QA history (features may still use **session/ephemeral** context — §5). |
| **Admin** | **JWT login** (same `/auth/login` path as workspace) + **bootstrap admin** via env (`ADMIN_BOOTSTRAP_*`, operator-only), **not** committed to git. |
| **Rebrand** | **DreamCision**; logos from **`D:\dream`**; palette aligned with **`new-project`** where useful. |

---

## 2. Out of scope for “first ship” (explicit deferrals)

| Item | When |
|------|------|
| **Developer “RAG index” of the repo** (searchable architecture map) | **After** main product milestones (post–finish). |
| **Whisper overlap / 30s-style chunking** (pseudo-streaming quality experiment) | **Last** phase (§16); optional. |
| **Clinical document RAG** | Not redefined here; existing RAG endpoints unchanged unless explicitly listed. |

---

## 3. Phase overview (sequenced)

| Phase | Name | Summary |
|-------|------|---------|
| **P0** | Hygiene & dead ends | Remove legacy pages/links; safe cleanup. |
| **P1** | Admin session model | Replace token-paste UX; Python-bootstrap admin only. |
| **P2** | Config & LLM routing | Per-feature base URL/port (or env) for QA text, QA vision, note gen, RAG comment, orders, etc. |
| **P3** | Profile & preferences | User profile, email, specialty default, location; built-in note-type templates + revert; prompts. |
| **P3b** | Custom note types | **Done:** `custom_note_types` in prefs; **POST `/api/note-types/custom`**, **DELETE** custom id; **revert-builtins** + **revert-bulk**; generation routes custom **other** scope via `note_type_uses_other_builder`. |
| **P4** | Prompt builder refactor | **Done (v1):** internal system prompt; `{USER_LOCATION}` + encounter/profile location; `{USER_DISPLAY_NAME}` / `{USER_EMAIL}` + optional `[Profile author]` SYSTEM line; addendum merged in USER block; `/note_prompts` without system text. |
| **P5** | Encounter data model & API | **Done (v1):** `user_encounter` + `extras.activeEncounterId`; `/api/encounters` CRUD; workspace GET/PUT/Clear on encounter; `queued_jobs.encounter_id`; delete jobs on encounter delete. |
| **P6** | Encounter UI (ChatGPT-like) | **Done (v1):** `encounters_ui.js` slide-over; `/api/encounters` list/new/rename/activate/delete; **Close encounter** + busy gate; replaced New Case / reset flows. |
| **P7** | Queue & unified failure UX | **Done (v1):** per-encounter queue strip; Retry/Download/Delete; local-only (offline) queue + merge; empty-ASR not treated as success. |
| **P8** | Literature + QA UX | **Done (v1):** `literature_ui.js` + `/api/rag/recent_updates` panel; `qa.html` New topic / session_id; 16k message cap; higher QA/summary token defaults; flexible answer prompt. |
| **P9** | Tokens & parsing & orders | **Done (v1):** `core/token_limits.py` (cap 12×1024); note completion clamped; preprocessing 3×4096; order detect/gen config; QA context 12k; chart size UI warning. |
| **P10** | Modularize frontend | **Done (ship, 2026-04-08):** `css/workspace.css`; `js/workspace_app.js`, `settings_connection.js`, `qa_side_panel.js`, `mobile_tools.js`, **`settings_drawer.js`**, **`version_badge.js`**; slim `index.html`; `WORKSPACE_PAGE_TYPE` at top of `workspace_app.js`. **Deferred (optional P10b):** further split of `workspace_app.js` (`appState`, `noteGeneration`, …) — see `MODULARIZATION_PLAN.md`. |
| **P11** | DreamCision rebrand | **Done (ship, 2026-04-08):** DreamCision naming across main/QA/admin/legal; **`assets/dreamcision/`** PNGs (sync via `tools/sync_dream_logos.py` from **`D:\\dream`**); inline header wordmark + feather on main app; **`manifest.json`** + **`theme-color`**; **`privacy.html`** / **`licenses.html`**; PWA icons **192/512**. |
| **P12** | Whisper overlap experiment | Optional; prototype alignment only. |
| **P13** | Operator stack & LLM instances (admin) | **Done (v1 + P13b, 2026-04-09):** `service_endpoints.json` SOT; admin forms incl. **llama CLI fields** (`bind_host`, `-ctk`/`-ctv`, mmap/direct-io/ffa, `extra_llama_args`, etc.) + **models ▸ llama/whisper** browse preset; **no** legacy Model Management UI. |
| **P14** | Operator notebook (last) | **Done (v1, 2026-04-09):** [`OPERATOR_RUNBOOK_WINDOWS.md`](../OPERATOR_RUNBOOK_WINDOWS.md) — NSSM env template, checklist, ports, backups, bootstrap, rotation. |

**Dependency rule:** **P5 → P6 → P7** must stay in order. **P3**/**P4** can overlap **P5** design but **prompt builder** should **consume** profile APIs once **P3** exists. **P8** can start after **P6** shell exists (literature on main chrome).

---

## 4. Detailed phases

### P0 — Hygiene & dead ends — **Done** (2026-04-05)

**Goals:** Remove unused surfaces; reduce confusion; no behavior change to live OCR/ASR from main workspace.

**Tasks**

1. **Delete** `PCHost/web/ocr.html` if still present (standalone legacy page).
2. **Remove** all `ocr.html` links from `admin.html`, nav, CSS hacks; grep repo for `ocr.html` and fix docs (`Clinical-Note-Generator/docs/*`, handoff docs).
3. **Confirm** OCR **entry points from `index.html`** (camera, file, queue) **unchanged** — only remove **dead** links. *(Verified manually.)*
4. **Service worker:** `NEVER_CACHE` includes `index.html`, `qa.html`, **`admin.html`** (and `/static/` variants where used), auth JS, universal audio handler; cache version bumped when lists change.
5. ~~**Audit** Nemotron / NeMo references~~ — **removed** from repo (no Nemotron stack; NeMo compatibility doc deleted; architecture brief scrubbed).

**Exit criteria:** Clean grep; no user-facing link to removed `ocr.html`; PWA does not serve stale `admin.html`.

---

### P1 — Admin session model — **Done** (2026-04-05)

**Goals:** “Normal” admin login; **no** manual long-token paste as primary path; bootstrap admin from **env-only** secrets.

**Implemented (this stack)**

1. **Backend:** Same **JWT** `/auth/login` + `Authorization: Bearer` as the clinical app; admin routes require `is_admin`. *(Cookie-only sessions are **not** required for P1 closure.)*
2. **Seed:** `ADMIN_BOOTSTRAP_EMAIL` / `ADMIN_BOOTSTRAP_PASSWORD` → `ensure_bootstrap_admin()` on startup (`server/core/bootstrap_admin.py`).
3. **`admin.html`:** Email/password sign-in; token field removed; session token in **`sessionStorage.admin_workspace_token`** (optional legacy key in `admin_config` cleared over time).
4. **Security hardening** (rate limits, alternate session transports): **optional** follow-ups, not blocking P2.
5. **Operator runbook / admin password rotation:** deferred to **P14** (last), when you want written procedures.

**Exit criteria:** Admin usable without manual long-token paste; bootstrap credentials only via operator-controlled env; non-admin users cannot use admin APIs.

---

### P2 — Config & LLM routing (per feature) — **Done** (2026-04-05)

**Goals:** Operator control over **which upstream** (host/port or base URL) each LLM-using feature uses.

**Implemented**

1. **Inventory:** Note generation (`note_gen`), QA text (`qa_text`), OCR (`LLM_OCR_*` + legacy `OCR_URL_*`), QA vision (`LLM_QA_VISION_*` + legacy `VISION_QA_*` / `OCR_URL_PRIMARY`), RAG consult comment (`LLM_RAG_COMMENT_*`), order/imaging extraction (`LLM_ORDER_REQUEST_*`). **Defaults** for these (and `RAG_URL`, `FASTAPI_PORT`, etc.) live in **repository root** **`service_endpoints.json`** → `apply_service_endpoints()` in `server/core/service_endpoints.py` (runs at FastAPI import); explicit **process env** still overrides (`setdefault` semantics).
2. **Central module:** `Clinical-Note-Generator/server/core/llm_routing.py` — resolves primary/fallback per feature; **`NOTEGEN_URL_*`** remains the default chain when feature-specific `LLM_*` unset.
3. **Clients:** `SimpleNoteGenerator` takes `role` (`note_gen` | `qa_text`) or `explicit_urls=` for one-off endpoints; `get_simple_note_generator("qa_text")` for QA routes.
4. **Frontend proxy:** `PCHost/server.js` merges **`pchost`** from **`service_endpoints.json`** over `PCHost/config/server_config.json` for `backend_url`, `http_port`, `https_port`, `llama_gateway_url`.
5. **Docs:** `Clinical-Note-Generator/docs/ENV_VARIABLES.md` — table of all `LLM_*` / legacy env names; launcher points at **`service_endpoints.json`** + optional overrides.
6. **Startup:** `app.py` logs resolved **host:port** per feature (no paths/secrets).

**Exit criteria:** Met — operators route each surface from **`service_endpoints.json`** and/or env without code edits.

---

### P3 — Profile & preferences (user) — **Done** (implemented 2026-04-05)

**Goals (achieved for v1):** Per-user **profile** fields and **server-owned note-type template overrides** so prompts **follow the user** across browsers. **`User`** now includes **`display_name`**, **`default_specialty`**, **`default_location`** (and optional **`profile_updated_at`**). Per-user template maps live in **`UserPreferences.preferences_json`** (`templates`, `templates_other`, `schema_version`), seeded/merged from **`config.json`** baselines via **`server/core/profile_service.py`**. **`GET /api/auth/me`** remains auth identity; extended fields use **`GET/PUT /api/profile`**. **Workspace** extras may still hold encounter-scoped UI state; profile defaults are no longer workspace-only.

**Out of scope for P3 (defer)**

- **Encounter-scoped** overrides (specialty/location per thread) → **P5** data model + **P4** builder consumption.
- **Email verification** / magic-link flows → optional later; P3 only needs a **documented policy** (e.g. unique email, change-email with password confirm).
- **DreamCision rebrand** copy in Settings → can lean on **P11**; P3 is structure + APIs.

**Data model (recommended direction)**

1. **`User` table extensions (minimal columns)**  
   - `display_name` — nullable `str`, shown in UI and audit-friendly logs.  
   - `default_specialty` — nullable `str` (free text or controlled vocabulary later).  
   - `default_location` — nullable `str` (e.g. clinic / region line for prompts; aligns with P4 “location replaces static Nova Scotia”).  
   - Optional: `profile_updated_at` for cache invalidation.

2. **`user_preferences` JSON blob** (new table **or** reserved key inside `UserWorkspace.state_json` — **prefer new table** for clean migrations and smaller workspace payloads)  
   - `schema_version: int` (e.g. `1`).  
   - `note_types: list[NoteTypeDef]` where each item includes at least:  
     - `id` — stable slug (`progress`, `consult`, or UUID for custom).  
     - `label` — UI string (“Progress note”).  
     - `kind` — optional enum: `builtin` | `custom` (built-ins may be non-deletable).  
     - `user_prompt` — main editable instruction (maps to today’s per–note-type entries under `default_note_user_prompts` in config).  
     - Optional: `sort_order`, `enabled` (hide without delete).  
   - `revert_baseline` — optional stored **server snapshot** of built-in defaults at migration time, or **code-defined** defaults loaded from a single **`defaults_note_types.json`** (or migration seed) so “Revert” never depends on `config.json` on disk after deploy.

3. **Migration / seed**  
   - On first P3 deploy: for each existing user, initialize **`note_types`** from current **`config.json`** defaults (`default_note_user_prompts` + `default_note_user_prompts_other` if applicable) so behavior is unchanged until they edit.  
   - Document **ops**: after cutover, product **defaults** for *new* users come from seeded JSON in repo or migration, not from editing live `config.json` for user prompts.

**As built (storage shape):** `preferences_json` uses **`templates`** / **`templates_other`** dicts keyed by note-type id; **P3b** adds **`custom_note_types`** (`[{id, label, scope}]`) for user-defined types. **`GET /api/note-types`** projects rows as **`NoteTypeEntry`** (`kind`: **`builtin`** | **`custom`**). Revert baseline is loaded from **`config.json`** at runtime.

**APIs (as implemented)**

| Method | Path | Purpose |
|--------|------|--------|
| `GET` | `/api/profile` | Extended profile (auth identity stays on **`GET /api/auth/me`**). |
| `PUT` | `/api/profile` | Partial update `display_name`, `default_specialty`, `default_location` (**email change** deferred). |
| `GET` | `/api/note-types` | Built-in types with **`baseline_prompt`**, **`user_prompt`**, **`scope`** (`standard` / `other`). |
| `PUT` | `/api/note-types` | Shallow **patch**: merge `templates` / `templates_other` into preferences. |
| `POST` | `/api/note-types/{id}/revert` | Revert to config baseline; query **`other=true`** for other-scope keys. |
| `GET` | `/api/note_prompts` | Effective + baseline maps; **Bearer** required. |

**P3b APIs (custom types + bulk revert)**

| Method | Path | Purpose |
|--------|------|--------|
| `POST` | `/api/note-types/custom` | Create custom note type (`id` slug, `label`, `scope` **standard** \| **other**, optional `initial_prompt`). |
| `DELETE` | `/api/note-types/{id}` | Remove a **custom** type only (built-ins return 400). |
| `POST` | `/api/note-types/revert-builtins` | Clear user overrides for **all** built-in ids in both buckets; keeps custom types. |
| `POST` | `/api/note-types/revert-bulk` | Body `{ "standard": ["consult", ...], "other": ["referral", ...] }` — revert listed built-ins only. |

**Auth & policy**

- **Email:** Already **unique** on `User.email`; document whether users may change email in P3 or only via admin.  
- **Authorization:** All routes **`require_api_bearer`** + **`get_current_user`**; no cross-user reads.  
- **Admin:** Optional read-only **admin** view of profile (audit) — **defer** unless compliance requires it in P3.

**Frontend**

1. **`PCHost/web/index.html`:** Settings drawer — profile fields → **`PUT /api/profile`**.  
2. **Prompt settings modal:** per–note-type **account** template (server) with save / revert; workspace “extra” instructions unchanged; **`loadProfileNoteTypes`** after workspace/prompt bootstrap.  
3. **Generation:** **`generate_v8_stream`** merges DB preferences with config baseline; empty encounter speciality uses **`User.default_specialty`**.

**Wire-up to generation**

- Implemented in **`server/routes/notes.py`** + **`server/core/prompt/builder.py`** (`merged_user_prompts` / `merged_user_prompts_other`). Operator baseline remains **`config.json`** until **P4** consolidates the builder.

**Testing**

- [x] Profile **GET/PUT**; validation on profile fields (`test_profile_p3`).  
- [x] Note-type **patch** + **revert**; **`GET /api/note_prompts`** exposes **`templates`** vs **`templates_baseline`**.  
- [x] **Multi-device:** same server state for any authenticated client; **Bearer** + user-scoped routes covered by API tests; multi-browser smoke is operator QA, not a code gate.  
- [x] **Regression:** `test_merge_templates_untouched_user_matches_config_baseline` + **`test_note_prompts_includes_baseline`** assert empty preferences yield effective templates identical to **`config.json`** baselines.

**Exit criteria (v1):** **Met.** Per-user template overrides persist in **`UserPreferences`**; **`config.json`** remains the **operator baseline** for seed/revert and new keys until **P4**.

**Dependency note:** **P4** unifies prompt composition and may reduce dual sourcing; **P5** adds **per-encounter** overrides on top of profile defaults.

---

### P3b — Custom note types — **Done** (2026-04-06)

**Goals:** Users can **create** note types with a stable slug (`id`), **label**, and **scope** (**standard** → `build_prompt_v8` / `templates`; **other** → `build_prompt_other` / `templates_other`). **Delete** removes custom metadata and template keys. **Reset all built-in templates** and **bulk revert** reinstate operator baselines without dropping custom types. **`generate_v8_stream`** uses **`note_type_uses_other_builder`** so custom **other**-scoped types route like referral/summarize.

**Frontend:** **`index.html`** prompt modal — add custom type form; **Delete** when a custom type is selected; **Reset all built-in templates**; **`noteType`** / **`noteTypeSelect`** / **`noteTypeMirror`** options rebuilt from **`GET /api/note-types`**.

**Testing:** `test_profile_p3` — create/delete, revert-builtins, revert-bulk, **`note_type_uses_other_builder`**.

---

### P4 — Prompt builder refactor — **Done** (core v1, 2026-04-05)

**Goals (delivered in v1):** **System prompt** stays **off** the **`GET /api/note_prompts`** surface (internal composition only). **`{USER_LOCATION}`** in templates plus **legacy substitution** of hard-coded “Nova Scotia” when profile/encounter location is set. **`{USER_DISPLAY_NAME}`** (display name, else email, else “Not specified”) and **`{USER_EMAIL}`** for operator-authored **`config.json`** strings; when identity is known, **`[Profile author]`** may be appended to SYSTEM so the model knows the authoring clinician. **Encounter location** optional on generate (`encounter_location` or profile `default_location`). **Workspace “encounter addendum”** (`custom_prompt`) merged into the **single USER** block with the account note-type template — no separate **ADDITIONAL INSTRUCTIONS** section.

**Still for later / P5:** Richer per-encounter object (revert-to-profile for location/specialty in the data model); fully removing duplicate prose in **`config.json`** if operators still ship static regions without `{{USER_LOCATION}}`.

**Tasks (status)**

1. Builder composition centralized in **`server/core/prompt/builder.py`** (`_prompt_values`, `_compose_user_section`, `_apply_region_substitution`).
2. **`generate_v8_stream`** passes **`user_location`** from **`encounter_location`** payload or **`User.default_location`**.
3. UI: **`encounterLocation`** field + workspace **`extras.encounterLocation`**; prompt modal labels clarify **account template** vs **encounter addendum**.
4. Spot regression: optional; tests cover builder behavior.

**Testing**

- [x] **`server/tests/test_p4_prompt_builder.py`** — location placeholder, NS substitution, addendum inside USER, author placeholders + **`PROFILE_AUTHOR_MARKER`** when display name set.
- [x] Full **`pytest server/tests`** green.

**Exit criteria (v1):** **Met** — one USER narrative path for template + addendum; system strings not returned from **`/api/note_prompts`**.

---

### P5 — Encounter data model & API — **Done** (v1, 2026-04-06)

**Goals:** **Persistent encounters** per user; **versioning**; **active encounter id**; **hard delete** on user confirm; **backfill** single legacy workspace into one encounter.

**Implemented**

1. **Table** **`user_encounter`:** `id`, `user_id`, `label`, `state_json` (full workspace-shaped payload per thread), `version`, `created_at`, `updated_at` — model **`UserEncounter`**.
2. **Pointer:** `UserWorkspace.state_json.extras.activeEncounterId` (UUID string). Workspace row holds a **shell** after migration; clinical state lives on the active encounter. **`ensure_encounters_for_user`** creates workspace if missing, backfills first encounter **“Default”** from legacy workspace, sets pointer.
3. **APIs** (Bearer): **`GET/POST /api/encounters/`**, **`GET/PATCH /api/encounters/{id}`**, **`POST …/activate`**, **`DELETE …/{id}`** with JSON body **`{ "confirm": true }`**. On delete: remove **queued_jobs** for that encounter (files deleted); if last encounter removed, create a new empty **“Encounter”**; if deleted encounter was active, activate **most recently updated** remaining encounter.
4. **Workspace:** **`GET/PUT /api/workspace/`** and **`POST /api/workspace/clear`** read/write **active encounter** state (same merge rules as before); response **`version`** is workspace row version (optimistic lock).
5. **Queue:** **`queued_jobs.encounter_id`** (nullable, SQLite `ALTER` migration); new jobs get current active encounter id.

**Testing**

- [ ] Migration on copy of prod DB (staging) — operator checklist.
- [x] Optimistic concurrency: **409** on stale workspace version (`test_encounters_p5`).
- [x] Delete removes encounter and **queue jobs** for that encounter (`test_encounters_p5`).

**Exit criteria:** **Met** — clinical workspace state and new queue rows are associated with an encounter id in DB.

---

### P6 — Encounter UI (ChatGPT-like) — **Done** (v1, 2026-04-07)

**Goals:** One place to see **all encounters**; **rename**; **active** encounter; **replace** “New Case”, “Reset workspace”, “Clear all” with **encounter lifecycle**; **new user** starts with **one empty generic** encounter; **if all deleted**, recreate **one empty generic**; **login** opens **most recent** encounter; **cannot switch** encounter while busy (§7).

**Implemented**

1. **UI:** Slide-over panel from **`PCHost/web/encounters_ui.js`** — sidebar **Encounters**, mobile nav + **Tools** sheet entry; lists **`GET /api/encounters/`**, **New encounter** (`POST` + `activate`), row **Open / Rename / Delete** (confirm body), **Close encounter** (clears active thread via existing **`/api/workspace/clear`** path).
2. **Replaced** desktop “New Case” and mobile “New Case” with **Encounters**; signed-in **`clearAll()`** opens the panel instead of an instant wipe; **`AuthWorkspace.closeCurrentEncounter()`** (replaces reset copy); hidden auth **Reset Workspace** button label → **Close encounter**.
3. **Busy gate:** **`AuthWorkspace.isClinicalBusy()`** — recording, **`app.isStreaming`**, **`__cngOcrBusy`** (document OCR pipeline), visible **audio/document** progress bars.
4. **Mobile:** Full-width panel on narrow viewports; tools sheet row for Encounters.

**Testing**

- [ ] Two browsers: create encounter A, switch device B, see A — operator QA.
- [ ] Switch blocked during mocked long request — operator QA.
- [ ] Delete flow: confirm → gone, no orphan jobs (per policy) — covered by **P5** API tests; UI manual.

**Exit criteria:** **Met** — encounters are managed from the new control; legacy “New Case” / reset UX removed for signed-in users.

---

### P7 — Queue & unified failure UX — **Done** (v1, 2026-04-07)

**Goals:** **Per-encounter** queue surface; **Retry | Download | Delete**; **download** = **local device only**, then **remove server copy** (and local temp after success); **one system** for offline, server-down, processing-fail; **fix false ASR success** (§7.1).

**Tasks**

1. **False ASR success fix:** Do **not** clear queue / show success until **non-empty transcript** (or explicit “no speech” path that **does not** drop audio reference). Define **state machine**: pending → processing → done | failed | needs_review.
2. **UI:** Strip at **top of encounter** (or agreed placement) listing jobs with actions.
3. **Offline:** Single local queue (IndexedDB or existing) with same three actions; **download** clears local blob after success.
4. **Server:** Delete endpoint for job + file; idempotent retries.

**Testing**

- [x] Empty transcript → **not** treated as success; job retained for retry/download.
- [x] Download → file local → **server row removed** (best effort).
- [x] Airplane mode: local-only queue persists; retry when back.

**Exit criteria:** **Met** — no user loses audio without an explicit failed/retry path.

---

### P8 — Literature button + QA enhancements — **Done** (v1, 2026-04-07)

#### 8a — Literature (main app) — **implemented**

**Delivered:** Sidebar **Literature** (after Encounters) + mobile **Tools** sheet; **`literature_ui.js`** slide-over; **`GET /api/rag/recent_updates`** with 503/error UI; Refresh; sign-out closes panel.

**Testing:** API error path covered in UI copy; desktop/mobile entry points wired.

#### 8b — QA (`qa.html` + iframe) — **implemented**

**Delivered:** **New topic** (new `session_id`) vs **Clear chat**; **`qa_chat`** message cap **16k**; defaults **`qa_chat_max_tokens` 1400**, summary tokens + convo char caps; rolling context widened; prompt allows unstructured answers when sections don’t help; taller composer + safe-area padding; perf/admin **`qa_max_user_chars`** default **2048**.

**Critical regression suite (operator checklist before release):**

- [ ] **Streaming — text only**: smoke in browser.
- [ ] **Streaming — image + text**: smoke in browser.
- [ ] **Auth / token** unchanged for iframe (`qa.html`).

**Exit criteria (v1):** Code + automated tests green; **manual** streaming verification remains recommended each QA-facing release.

---

### P9 — Tokens, parsing, orders — **Done** (v1, 2026-04-07)

**Goals (achieved for v1):** **~12k** max completion tokens (12×1024); **section budgets** in preprocessing **4096×3** when enabled; **orders/imaging** higher detect/generation token budgets; **UI warning** when chart fields exceed ~12k-token heuristic.

**Implemented**

1. **`server/core/token_limits.py`** — `MAX_COMPLETION_TOKENS_CAP`, `clamp_completion_tokens`, `truncate_text_to_approx_token_budget_str`; note route uses clamp for **completion** `max_tokens`.
2. **`config.json`** — `default_note_max_tokens` 12288; `qa_context_length` 12288; `order_*_max_tokens` keys; preprocessing truncation **4096** per section.
3. **`core/order/pipeline.py`** — configurable detect/gen limits; imaging vs procedure prompt wording; **procedure** uses same structured requisition path as imaging.
4. **`PCHost/web/index.html`** — character warning at **~49k chars** per section, includes **current encounter**; message references server-side trim.
5. **`PCHost/web/admin.html`** — admin inputs capped to **12288** where relevant.
6. **Tests:** `server/tests/test_token_limits_p9.py`.

**Testing**

- [x] Large paste → UI warning before silent server-side truncation.
- [ ] Orders: operator sample cases — optional follow-up.

**Exit criteria:** **Met** — caps centralized; config-driven order limits; tests for token helpers.

---

### P10 — Modularize frontend — **Done (ship)**

**Goals:** Split **`PCHost/web/index.html`** into **CSS + JS modules** without changing behavior per slice.

**Delivered (2026-04-07 … 2026-04-08)**

1. **`css/workspace.css`** (+ tokens) — all main SPA chrome; **`index.html`** is markup + small boot scripts only.
2. **`js/workspace_app.js`** — core SPA (generation, queue, workspace integration); first line sets **`window.WORKSPACE_PAGE_TYPE = 'main'`**.
3. **`js/settings_connection.js`**, **`qa_side_panel.js`**, **`mobile_tools.js`** — connection badge, QA iframe, mobile tools.
4. **`js/settings_drawer.js`** — Profile **GET/PUT**, settings drawer, auth card placement, mobile top bar.
5. **`js/version_badge.js`** — **`#appVersionLabel`** from **`GET /api/version`**.
6. **`service_worker.js`** — **`NEVER_CACHE`** for hot CSS/JS; cache version bumped when those lists change.

**Deferred (optional — call it P10b or tech debt)**

- Split **`workspace_app.js`** into **`appState`**, **`noteGeneration`**, **`storage`**, **`audio`**, **`evidence`** modules — see **`MODULARIZATION_PLAN.md`** Phase 2. Not required to proceed to **P12+**.

**Exit criteria:** **Met** for shipping — thin shell, maintainable satellite modules; monolith remains one file until P10b.

---

### P11 — DreamCision rebrand — **Done (ship)**

**Goals:** **DreamCision** naming; **logos** from **`D:\dream`** (star-free PNGs synced into `PCHost/web/assets/dreamcision/`); coherent palette and legal surfaces.

**Implemented**

1. **`D:\\dream`** → **`assets/dreamcision/*.png`** (see **`tools/sync_dream_logos.py`**); wordmarks on **`qa.html`**, **`admin.html`**, **`privacy.html`**, **`licenses.html`**.
2. **Main app header (2026-04-08):** inline feather SVG + **DREAM / CISION** + **AI · SCRIBE · v1.0** in **`index.html`** + **`workspace.css`** (no external logo asset required).
3. **Version 1.0** in product copy; build line **`#appVersionLabel`** shows commit + UTC from **`GET /api/version`** (see **`version_badge.js`**).
4. **`privacy.html`** — PHIA (NS), PIPEDA, HIPAA overview, consent, user responsibility (not legal advice).
5. **`licenses.html`** — MIT/Apache/BSD acknowledgments + Node proxy note; links to OSI license texts.
6. **`manifest.json`**, **`theme-color`**, footer legal links on main app; QA footer with `target="_top"` for legal pages from iframe.
7. **Accessibility:** skip link to `#main-content` on main workspace.

**Testing**

- [x] **PWA:** **`manifest.json`** references **`icon-192.png`** and **`icon-512.png`** under **`assets/dreamcision/`**.
- [ ] Visual regression on target devices (operator smoke when convenient).

**Exit criteria:** **Met** — coherent brand on main, QA, admin, and legal pages.

---

### P12 — Whisper overlap chunking (optional, last)

**Goals:** Experiment with **internal-style** overlap (e.g. 30s windows, **25s committed + 5s overlap**) using **`D:\asr-chunk-prototype`** or new module — **optional** quality improvement for pseudo-streaming; **does not replace** full-file ASR for quality-critical path.

**Tasks**

1. Document algorithm; align with whisper.cpp capabilities.
2. Feature-flag in app if integrated.
3. Benchmark against full-file on same clips.

**Testing**

- [ ] No regression to default “full upload” path.

**Exit criteria:** Optional branch only; production default unchanged unless explicitly enabled.

---

### P13 — Operator stack & LLM instances (admin) — **Done (v1 + P13b, 2026-04-09)**

**Original stretch goal** was a **full** per-`llama-server` control plane (every binary flag + model pick from disk). **P13 v1** delivers a **tight, shippable** operator surface and defers the stretch items so the roadmap stays honest.

**Delivered (v1)**

1. **Single source of truth:** Repository root **`service_endpoints.json`** — `llama_instances`, `feature_routing`, `whisper_instances`, `services_urls`, `office_stack_processes`, `office_stack_order`, `windows_services`, `stack_cleanup_ports`, `ai_binary_defaults`; admin **GET/PUT** `/api/admin/service_endpoints` with **`sync_env_from_structure`** on save.
2. **Admin UI (`PCHost/web/admin.html`):** JSON editor + structured cards for LLM/Whisper instances (paths, sampler fields, `launch.arguments`); **Office stack** (Node / FastAPI / RAG) start/stop/start_all/stop_all; **AI** native `llama-server` / `whisper-server` start/stop; existing **service status** table + NSSM start/stop/restart where allowlisted.
3. **Backend:** `server/core/service_endpoints_sync.py`, `ai_process_launcher.py`, `office_stack_launcher.py`; **NSSM path:** `python -m server.core.office_stack_supervisor` (same argv logic as admin); **`startup/start-office-stack.ps1`** is a thin wrapper only.
4. **Safety gates (FastAPI env):** e.g. `ADMIN_PROCESS_CONTROL_ENABLED`, `ADMIN_SERVICE_CONTROL_ENABLED` — documented in code and batch/startup scripts.

**P13b (done, 2026-04-09)** — no legacy admin surfaces:

1. **Llama-server CLI in forms:** `bind_host`, cache types `-ctk`/`-ctv`, flash-attn / `--mmap` / `--direct-io`, `chat_template_kwargs`, `launch.working_dir`, `extra_llama_args` (JSON array); still use non-empty **`launch.arguments`** for full manual argv. Backend: `ai_process_launcher` (`bind_host`, `mmap` + **`--no-mmap`** when off).
2. **Models folder picker:** admin buttons **models ▸ llama** / **models ▸ whisper** call **`GET /api/admin/fs/browse?preset=llama_models|whisper_models`** (first matching `models/<llama|whisper>` under allowlisted roots).

**Removed from roadmap:** legacy hidden “Model Management / Model Parameters” blocks and config-only temperature batch **(replaced by `service_endpoints.json` + `config.json` editing as needed)**.

**Exit criteria (v1 + P13b):** Operators can **edit one JSON file**, **apply** expanded forms, **jump into models trees**, **start/stop** office and AI processes from admin (subject to gates), and **run the same stack** via **NSSM → Python supervisor** without maintaining a second command list in PowerShell.

---

### P14 — Operator notebook & runbook (**last** deliverable) — **Done (v1, 2026-04-09)**

**Goals:** One **operator-facing** runbook: production layout, NSSM, env, backups, bootstrap admin, and **admin password rotation** (operator-run procedure).

**Delivered in** [`OPERATOR_RUNBOOK_WINDOWS.md`](../OPERATOR_RUNBOOK_WINDOWS.md):

1. Paths, NSSM supervisor row, **example `AppEnvironmentExtra` / production env block** (JWT, bootstrap, `ADMIN_*`, `ADMIN_MUTATIONS_LOCALHOST_ONLY`).
2. FastAPI-only NSSM alternative; **three admin control mechanisms**; recommended prod vs dev defaults.
3. **Environment checklist**; **service layout & ports** (anchored to `service_endpoints.json`).
4. **Backups & recovery**; **failure / restart order**.
5. **Bootstrap admin** (`ADMIN_BOOTSTRAP_*`) and **password rotation** (change via app or DB + optional bootstrap clear).

**Also:** Linked from [`INSTALLATION_GUIDE.md`](../../INSTALLATION_GUIDE.md); companion [`ROADMAP_AUTHORITY.md`](./ROADMAP_AUTHORITY.md).

**Exit criteria:** **Met (v1)** — a single doc supports install, hardening, backup, and recovery without tribal knowledge.

---

## 5. Cross-cutting testing matrix

| Area | Automated (where feasible) | Manual |
|------|-----------------------------|--------|
| **Auth / session** | API tests for workspace, profile | Login across devices |
| **Encounters** | CRUD + concurrency | Two-browser sync |
| **Queue / ASR** | Job state machine unit tests | Airplane mode, retry, download |
| **QA** | Minimal API tests | **Both streaming paths** every release |
| **Admin** | Login integration | Session rotation |
| **Prompt builder** | Snapshot of assembled prompt in dev | Clinician spot-check |

---

## 6. Rollout strategy

1. **Feature flags** per major area (`ENCOUNTERS_ENABLED`, `NEW_QUEUE_UX`, etc.) until stable.
2. **Staging** with copy of SQLite/Postgres data for migration tests.
3. **Pilot** users internal only before full team.

---

## 7. Risk register (short)

| Risk | Mitigation |
|------|------------|
| **Migration data loss** | Backup DB before migrations; idempotent migrations |
| **QA regression** | Mandatory dual-path streaming checklist |
| **Busy gate** | Server-side job lock + client UI lock |
| **Token cost** | Summarization + caps in P8/P9 |

---

## 8. Ownership checklist (you vs implementer)

- **Operator:** Admin bootstrap secrets, FastAPI env files, `D:\dream` asset picks, staging DB backups.
- **Engineering:** Phases **P0–P11**, **P13 (v1 + P13b)**, and **P14 (v1)** are closed per §3–§4. **P12** remains optional.

---

## 9. Document history

| Date | Change |
|------|--------|
| 2026-04-05 | Initial consolidated plan from user requirements and design threads |
| 2026-04-05 | Added [`IMPLEMENTATION_LOG.md`](../IMPLEMENTATION_LOG.md) for ongoing progress tracking |
| 2026-04-05 | Added **P13** (per-llama admin) after P12 as deferred operator tooling |
| 2026-04-05 | **P0/P1 closed:** SW `NEVER_CACHE` for `admin.html`; removed NeMo/Nemotron doc + scrubbed references; **P14** = operator notebook last (incl. deferred admin password rotation docs) |
| 2026-04-05 | **P2 done:** per-feature `LLM_*` env + `server/core/llm_routing.py`; startup log of resolved LLM bases |
| 2026-04-05 | **P2 doc sync:** `service_endpoints.json` + PCHost `pchost` merge + `apply_service_endpoints()`; inventory bullets no longer cite `config.json` for RAG/order URLs |
| 2026-04-05 | **P3 expanded:** full baseline vs target model, draft REST table, migration/seed, testing, exit criteria; `IMPLEMENTATION_LOG` resume block set to **P3 next** |
| 2026-04-05 | **P3 done:** `User` profile columns, `UserPreferences` template maps, profile + note-types routes, generation merge, `index.html` UI, `test_profile_p3` + `auth_utils`; plan §P3 marked **Done** |
| 2026-04-05 | **P4 done:** builder USER merge, `USER_LOCATION` + NS substitution, `encounter_location` + profile location on generate, `/note_prompts` omits system text, `test_p4_prompt_builder`; plan §P4 marked **Done** |
| 2026-04-05 | **Pre-P5:** §P4 documents `USER_DISPLAY_NAME` / `USER_EMAIL` + `[Profile author]`; §P3 testing checkboxes closed (multi-device + merge regression test); `RAG/chroma_data/` gitignore + index doc sync |
| 2026-04-06 | **P3b done:** custom note types CRUD + revert-builtins/bulk; `note_type_uses_other_builder` in `notes.py`; prompt modal + dynamic note-type dropdowns |
| 2026-04-06 | **P5 done:** `UserEncounter`, workspace shell + `activeEncounterId`, `/api/encounters`, `queued_jobs.encounter_id`, `encounter_workspace.py`, tests `test_encounters_p5.py` |
| 2026-04-07 | **P6 done:** `PCHost/web/encounters_ui.js`, slide-over Encounters UI, `AuthWorkspace.isClinicalBusy` + `closeCurrentEncounter`, SW v21 + never-cache `encounters_ui.js` |
| 2026-04-07 | **P7 done:** per-encounter queue strip (top of encounter), Retry/Download/Delete actions, local-only offline queue + merge, empty-ASR retains job/file |
| 2026-04-09 | **P13 v1 done:** operator SOT `service_endpoints.json`, admin forms + process control + Python `office_stack_supervisor`; stretch (full CLI form, model picker) deferred to optional P13b; §4 P13 and §3 table updated |
| 2026-04-09 | **P14 started:** [`OPERATOR_RUNBOOK_WINDOWS.md`](../OPERATOR_RUNBOOK_WINDOWS.md); [`ROADMAP_AUTHORITY.md`](./ROADMAP_AUTHORITY.md); admin health + explainer + confirms; prod default `ADMIN_PROCESS_CONTROL_ENABLED=0`; optional `ADMIN_MUTATIONS_LOCALHOST_ONLY` |
| 2026-04-09 | **P14 done (v1):** runbook completed — NSSM `AppEnvironmentExtra` template, env checklist, ports/backups/recovery, bootstrap + admin rotation procedures; §3/§4 tables updated |
| 2026-04-09 | **P13b done:** llama CLI form fields + `fs/browse` preset `llama_models`/`whisper_models`; removed legacy Model Parameters admin UI; `ai_process_launcher` bind_host / mmap toggles |

---

*End of master plan.*
