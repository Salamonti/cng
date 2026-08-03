# Architecture Options — Morning Brief (2026-03-19 Delivery)

> Audit focus areas: split `index.html`, evaluate React migration, support multiple concurrent patients/workspaces, tighten user-specific prompts/profile defaults, and deliver prioritized recommendations. **ASR:** Whisper-only (full-file / queue path); no alternate streaming ASR stacks in this repo.

## 1. Splitting `PCHost/web/index.html`
**Current state.** A single 6,322-line file mixes inline CSS, HTML, and JS (`index.html#L1-L6322`). Global state (`window.app` at `index.html#L5205`) and UI handlers (e.g., `generateNoteOnline()` around `#L3180`) sit alongside layout markup and toasts. The CSS block spans the first 400 lines; all logic lives in `<script>` at the bottom.

**Findings:**
- Shared helpers already exist as external files (`generate_ui_flow.js`, `auth_workspace.js`, `universal_audio_handler.js`), so splitting the remaining logic is mainly mechanical.
- Queue/reset logic is isolated between `#L4200-L4350`, and storage helpers live around `#L5022-L5054`, giving clear seams.

**Options:**
1. **Modularize in-place (recommended).**
   - Extract the `<style>` block into `web/styles.css` (import via `<link>`), move JS into ES modules (`app_state.js`, `note_generation.js`, `queue.js`), and break the HTML into `<template>` tags or partial files loaded at runtime.
   - Pros: works with current static hosting and avoids build tooling. Low regression risk because DOM structure stays the same.
   - Effort: 2–3 engineering days to separate CSS/JS + adjust bundling, plus another 1–2 days for partial templates if desired.
2. **Adopt a light bundler (Vite/Rollup) but keep vanilla JS.**
   - Enables tree-shaking and module imports without polluting global scope, but requires updating `server.js` static path and adding a build step.
   - Effort: ~1 week including dev/prod parity and CI updates.
3. **Full SPA rewrite (see React section).**
   - Highest flexibility but longest path; better paired with a React migration decision instead of an isolated split.

**Recommended path:** Start with Option 1 this week—move CSS/JS out of `index.html` and wrap each card (Auth, Patient Inputs, Transcription, Note Output, Evidence, Queue) into template fragments. This yields immediate readability and unlocks incremental feature work (multi-patient tabs) without waiting on a full framework migration.

## 2. React Migration Assessment
**Pros of moving to React/Vite:**
- Component boundaries and state management (hooks/context) suit complex UI states (queues, streaming, multiple patients) better than manual DOM updates.
- Easier testing (Jest/Vitest) and reuse between pages (`qa.html`, `admin.html`, `index.html`).
- Better long-term maintainability once modules are split.

**Cons / Risks:**
- Requires a build step, asset pipeline, and potentially SSR if SEO is needed.
- All existing DOM IDs/classes referenced from backend workspace serializer (`auth_workspace.js#L692`) would have to be retained or rewritten.
- Regression risk in audio capture + streaming, since React wrappers around `MediaRecorder` require careful effect management.

**Migration options:**
1. **React islands inside current pages.** Mount React components into existing DOM containers (e.g., the Evidence modal) while keeping the rest vanilla. Low risk but limited benefit.
2. **Single React SPA served by Node.** Use Vite + React Router, move workspace/auth/audo logic into components, and expose the same REST calls.
3. **Component library only.** Build reusable UI primitives in React (cards, lists) but export as web components consumed by vanilla JS.

**Recommendation:** Defer a full React rewrite until after Option 1 (file split) lands and multi-patient requirements are agreed upon. React becomes more compelling once we introduce patient tabs/dashboards that are awkward in vanilla JS. If approved, plan for Option 2 with a feature-flagged rollout (e.g., `/beta`) over 4–6 weeks.

## 3. Multiple Active Patients/Workspaces
**Constraints:**
- Frontend persists exactly one encounter via `localStorage['clinicalNoteData']` (`index.html#L5022-L5034`).
- Workspace sync also stores a single encounter snapshot inside `workspace.state_json.extras` (`auth_workspace.js#L692-L720`).
- Backend workspace table (`server/models/workspace.py#L11` + `routes/workspace.py#L53-L114`) allows only one JSON blob per user.
- API payloads (`/api/generate_v8_stream`, `/api/generation/{id}/*`) lack a `patient_id` or `workspace_id` parameter; queue jobs are user-scoped only (`routes/queue.py#L34-L125`).

**Options:**
1. **Extend workspace extras to hold multiple encounters.** Store `extras.encounters` as an array keyed by `patient_id`, each containing the three note fields + metadata. Add a `currentEncounterId` pointer. Light backend changes but pushes more work to the client, and PUT payload size might exceed the 2 MB limit.
2. **Add normalized tables (`patient_workspace`, `encounter_state`).** Create SQLModel tables referencing `user_id`, allowing multiple active encounters with `id`, `label`, `last_updated`, and state JSON. Expose `/api/workspaces` (list/create/delete) alongside `/api/workspace/{id}` for sync. Requires migration scripts but scales and enforces quotas per encounter.
3. **Browser-only multi-tabs, server stays single-state.** Keep just one server workspace but let the UI maintain multiple drafts locally and “swap” the active one into the server before generating. Fragile for multi-device usage.

**Recommended architecture:** Option 2. Introduce `UserEncounter` table (UUID PK, user FK, `state_json`, `metadata`, `version`). Add endpoints: list encounters, create (seed from baseline), select (marks active), delete/clear. Frontend displays a patient/workspace rail (tabs) and loads only the active encounter into `window.app`. Use optimistic concurrency per encounter so simultaneous edits remain safe. Estimated effort: backend 3–4 days (models, routes, migration), frontend 4–5 days (UI, storage, sync), plus QA/regression.

## 4. User-Specific Prompts Override UX
**Current behavior:**
- Custom prompts are stored only within workspace extras (`index.html#L5639-L5699`), tied to the single encounter snapshot.
- There is no API dedicated to prompt overrides; deleting the workspace resets prompts.

**Proposal:**
1. Create a `user_preferences` table (or extend `UserWorkspace` with a separate `preferences_json`) storing note-type overrides, last reset date, and optional titles.
2. Expose `/api/preferences/prompts` (GET/PUT/DELETE) so the UI can save overrides independently from encounter state and offer a “Revert to system defaults” button without clearing PHI fields.
3. In the frontend, move the prompt settings drawer into its own module (after Option 1 split). Persist overrides directly via the new API; when a user selects a note type, show three indicators: system default (read from `config.json` via `/api/note_prompts`), user override (if any), and workspace-scope override (e.g., per patient) to support contextual tweaks.
4. Implement “one-click revert” by calling DELETE on the API and clearing local caches; optionally keep a local undo stack for the current session.

**Effort:** ~2 backend days (model + endpoints) and 2 frontend days (new drawer view/state).

## 5. User Profile Defaults (specialty, metadata)
**Current behavior:**
- Specialty text box sits on the command bar (`index.html#L1725-L1745`) and is saved with encounter extras (same as field data). Clearing the workspace wipes it.
- `User` model (`server/models/user.py#L12-L19`) only stores email, hashed password, admin flags.

**Proposal:**
1. Create `user_profiles` table (1:1 with `User`) storing `specialty`, `timezone`, `default_note_type`, and UI preferences. Seed from existing workspace extras when first saving.
2. Add `/api/profile` GET/PUT endpoints so the command bar can pull defaults immediately after login and persist updates without touching PHI data.
3. On the frontend, show a subtle “Use default” chip next to specialty/note type fields; clicking it reads from profile defaults rather than workspace extras. Provide “Reset to system default” and “Clear” actions.
4. Preserve backward compatibility by falling back to workspace extras if no profile exists yet.

**Effort:** Backend 2 days (model, endpoints, migration), frontend 1–2 days (UI, integration).

## 6. Prioritized Recommendations
1. **Split `index.html` (Option 1)** to unblock every other initiative. Extract CSS/JS, wrap cards as modules, and wire lint/tests for the new files.
2. **Design multi-encounter storage (Option 2)** with normalized tables + API endpoints. Update workspace UI into tabs once the file split lands.
3. **Add user preferences/profile tables** so prompts and specialty defaults survive workspace clears, enabling consistent UX.
4. **Reassess React migration** after the above work lands; if multi-encounter UI becomes unwieldy in vanilla JS, green-light a React SPA with a staged rollout.

Deliverables for follow-up:
- Technical design docs for the encounter schema + profile APIs.
- Milestone schedule for UI modularization → React decision gate.
