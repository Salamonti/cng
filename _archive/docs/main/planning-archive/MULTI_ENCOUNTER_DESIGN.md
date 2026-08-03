# Multi-Encounter Architecture Design

## Current Single-Encounter Flow (Limitations)
- **Frontend persistence:** `index.html` writes every text box into a single localStorage entry `clinicalNoteData` (`PCHost/web/index.html:5022-5055`). Global state (`window.app`) and RAG/order status live under `app.uiState` (`index.html:5205-5244`, `index.html:3992-4042`). There is no concept of encounter IDs; the UI assumes one active patient.
- **Workspace sync:** `AuthWorkspace.collectWorkspaceState()` serializes the current encounter into `state.extras` (`PCHost/web/auth_workspace.js:692-722`). Extras contain `transcription`, `currentEncounter`, `oldVisits`, `mixedOther`, `userSpeciality`, `generatedNote`, `customPrompts`, `appSettings`, and `ui`.
- **Backend storage:** `/api/workspace` stores exactly one row per user in `user_workspace.state_json` (`Clinical-Note-Generator/server/routes/workspace.py:11-118`). The `extras` blob mirrors the frontend fields; optimistic concurrency is enforced with a `version` integer.
- **Queue + ASR/OCR:** The queue table only records `user_id`, not patient context (`server/models/queued_job.py:9-27`). The ASR proxy (`server/routes/asr.py:203-283`) accepts one audio file per request and writes results straight into the current encounter textarea. OCR uploads append `--- filename ---` blocks into whichever field was active (`PCHost/web/index.html:3028-3071`).
- **Note generation:** `/api/generate_v8_stream` consumes the 3 fields without any encounter identifier (`server/routes/notes.py:1448-1668`). Consult/order pollers also assume a single `window.lastGenerationId` (`index.html:3262-3335`).
- **User preferences:** Specialty, prompt overrides, and UI state are buried inside `workspace.extras` and wiped when the workspace is cleared. No normalized `user_profile` table exists; `User` only stores email/hash (`server/models/user.py:1-11`).

These constraints make it impossible to juggle multiple patients, persist per-encounter metadata, or reuse preferences across encounters.

## Backend Design
1. **Schema additions**
   - `user_encounter` table (SQLModel) with fields: `id` (UUID PK), `user_id` FK, `label` (patient name/MRN), `state_json` (same structure as current workspace extras but scoped to one encounter), `draft_version`, `last_opened`, and optional immutable metadata (e.g., MRN hash).\
     *Migration:* seed one encounter per existing workspace row by copying `workspace.state_json.extras` into the new table and storing the generated ID back into `workspace.state_json.active_encounter_id`.
   - `user_preferences` table keyed by `user_id` with JSON columns for `custom_prompts`, `app_settings`, and any profile fields (e.g., specialty, default note type). This lets us clear encounter data without erasing prompts.

2. **API surface**
   - `/api/encounters` `GET`/`POST`: list encounters (id, label, last_updated); create a new one from a template.\
   - `/api/encounters/{id}` `GET`/`PUT`/`DELETE`: load/save a single encounter `state_json`, enforce optimistic versions, delete data safely (soft delete to avoid orphaned queue files).\
   - `/api/workspace` shrinks to global metadata: `active_encounter_id`, per-user preferences, sync cursors. Existing fields under `state.extras` become legacy read-only for migration.
   - Update `/api/generate_v8_stream` and consult/order polling endpoints to accept an optional `encounter_id` in the request body/header so background jobs can update the correct encounter record once they finish.
   - Extend queue APIs so `QueuedJob` stores `encounter_id` and optional `target_field` metadata. The OCR/ASR retry worker can then merge results back into the right encounter even if the user switches tabs.

3. **Concurrency + limits**
   - Enforce per-user encounter quotas (e.g., max 8 open). Add indexes on `(user_id, updated_at)` for the list endpoint.\
   - Keep optimistic concurrency per encounter: each `user_encounter` row gets its own `version` counter, so two tabs editing different patients do not fight over the same `/api/workspace` version.

## Frontend Design
1. **State management**
   - `appState` (see modularization plan) should track `encounters: Map<id, EncounterState>` and a pointer to `activeEncounterId`. Each `EncounterState` wraps the 3 text fields, generated note, UI state (RAG/order metadata), and timestamps.
   - Replace reliance on `localStorage['clinicalNoteData']` with a small cache keyed by `encounterId`. Example structure:
     ```json
     {
       "activeEncounterId": "enc-123",
       "encounters": {
         "enc-123": { "fields": {...}, "ui": {...} },
         "enc-456": { ... }
       }
     }
     ```
     Keep a migration path that loads the legacy key once and seeds the first encounter.

2. **UI/UX**
   - Add an encounter rail/tab list (left sidebar or above the chart card) with actions: New, Duplicate, Rename, Close. Selecting a tab swaps the inputs by loading the encounter from memory (and lazy-loading from `/api/encounters/{id}` if not cached).
   - When the user clicks “New Case” the app creates a new encounter record (POST `/api/encounters`) and clears local form fields without dropping preferences.

3. **Workspace + sync**
   - `AuthWorkspace` becomes a thin orchestrator: it loads user preferences and the encounter index via `/api/encounters`, caches them locally, and only PUTs the active encounter when fields change.\
   - Auto-save logic (`auth_workspace.js:725-750`) should debounce per-encounter saves and include the `encounter_id` in the payload.\
   - The sync pill can show encounter-specific status (e.g., “Syncing case A/B”), so `queueSave()` needs the active ID to render human-readable labels.

4. **Audio/OCR integration**
   - `transcribeAudio()` should attach the active `encounter_id` (and target field) when queueing or sending files so the backend can rehydrate the correct record.\
   - `appendChunkTranscript()` must operate on `app.encounters[active].transcriptionDisplay` to avoid overwriting text from other patients.\
   - When OCR jobs finish, merge the extracted blocks into the encounter that originated the upload rather than whatever is currently active. Persist this mapping (encounter ID + target field) inside the queue payload.

5. **Note generation & downstream artifacts**
   - `generateNoteOnline()` should post `{ encounter_id, transcription_text, ... }` and cache `lastGenerationId` per encounter (e.g., `app.encounters[id].ui.lastGenerationId`).\
   - Consult comment and order modals (`index.html:1985-2140`) must read/write from `app.encounters[id].ui` so toggling between patients shows the correct evidence.\
   - Clearing one encounter should only wipe that tab; global “Clear All” can iterate over encounters and delete them server-side.

6. **User preferences**
   - Move specialty + custom prompts out of encounter extras. On load, fetch `/api/preferences` and populate `window.app.settings` + `app.customPrompts`. Encounter saves no longer need to include these blobs, drastically reducing payload size.
   - Provide UI affordances (e.g., “Apply preference to all encounters”) by writing to the new profile endpoint rather than copying strings across every row.

## Risks & Mitigations
- **Payload/backward compatibility:** Older mobile builds may still send workspace blobs. Keep `/api/workspace` backward-compatible during rollout by accepting both schemas and migrating on the fly.
- **Queue coordination:** Existing queue entries will lack `encounter_id`. Add fallback logic that defaults to the active encounter until all clients are updated.
- **Conflict resolution:** Users could open multiple tabs for the same encounter. Include `version` and `updated_at` in `/api/encounters/{id}` responses and surface conflicts in the UI similar to the current workspace 409 flow.
- **Testing surface:** Multi-encounter touches note generation, OCR/ASR, workspace sync, and consult polling. Regression suites should add encounter-switch steps (enter data in encounter A, switch to B, return to A, ensure fields remain intact).

## Next Steps
1. Land the modularization split so `index.html` logic is composed of importable modules.
2. Implement backend migrations for `user_encounter` and `user_preferences`, seed data, and expose the new APIs.
3. Update `AuthWorkspace` + storage modules to consume the new endpoints while keeping compatibility shims for the legacy single-encounter blob.
4. Ship the new encounter rail and per-encounter storage, guarded by a feature flag so we can roll out to internal testers before enabling for all users.
