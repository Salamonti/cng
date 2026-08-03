# Phase 1 – `index.html` Modularization Plan

> **Archived** under `docs/planning-archive/`. Next frontend split ideas: [`FUTURE_PLAN_BACKLOG.md`](./FUTURE_PLAN_BACKLOG.md).

**Roadmap:** P10 is **closed for ship** (2026-04-08). Further splits of **`workspace_app.js`** are **optional P10b** (tech debt), not a gating phase.

## Phase 1 status — **complete** (2026-04-08)

Delivered:

| Item | Location |
| --- | --- |
| Styles externalized | `PCHost/web/css/workspace.css`, `dreamcision-tokens.css` |
| Main app logic | `PCHost/web/js/workspace_app.js` (core workspace + generation + queue UI) |
| Settings / connection badge | `PCHost/web/js/settings_connection.js` |
| QA side panel | `PCHost/web/js/qa_side_panel.js` |
| Mobile tools | `PCHost/web/js/mobile_tools.js` |
| Settings drawer, profile, auth card placement, mobile bar | `PCHost/web/js/settings_drawer.js` |
| Version label (`/api/version`) | `PCHost/web/js/version_badge.js` |
| Workspace page marker | `window.WORKSPACE_PAGE_TYPE = 'main'` set at top of `workspace_app.js` |
| Encounters panel boot | Initial “closed” state lives in `encounters_ui.js` |
| `index.html` | Markup + script tags only (no large inline app scripts) |

Phase 1 **does not** split `workspace_app.js` into `appState` / `noteGeneration` / etc. That work is **P10b / Phase 2** (below) and is **optional**.

---

## Baseline Constraints (historical)

- The main SPA must continue to expose globals such as `window.app`, `window.generateNote`, and `window.AuthWorkspace.queueSave()` until **P10b** introduces explicit namespaces (if ever).
- The Node proxy (`PCHost/server.js`) serves `PCHost/web` statically; new modules stay under that tree.

---

## Module Targets (optional P10b)

**Status (2026-04-09):** Shipped first P10b slices: `web/js/workspace_ui_state.js` (RAG/order UI state + `updateUiState`), `web/js/workspace_file_camera.js` (camera, file pick, drag/drop). Further splits (`appState`/`noteGeneration`/…) remain optional.

| New file | Responsibility | Source today |
| --- | --- | --- |
| `web/js/state/appState.js` | `window.app`, `updateUiState`, `applyUiStateFromWorkspace`, RAG/order UI state | `workspace_app.js` |
| `web/js/state/storage.js` | `saveToStorage`, `loadFromStorage`, `saveDraft`, IndexedDB queue | `workspace_app.js` |
| `web/js/services/noteGeneration.js` | `generateNote`, streaming, `finalizeNoteText`, consult/order kickoff | `workspace_app.js` |
| `web/js/services/audio.js` | Recording UI, ASR status, ties to `universal_audio_handler.js` | `workspace_app.js` |
| `web/js/services/ocrQueue.js` | OCR/ASR queue persistence | `workspace_app.js` |
| `web/js/ui/evidence.js` | RAG consult card, order/evidence buttons, polling | `workspace_app.js` |
| `web/js/bootstrap_main.js` or `type="module"` entry | Wire modules; attach minimal `window.*` shims | Replaces monolithic load order |

## P10b — Step-by-Step (outline)

1. Extract **app state** (`window.app` + helpers) into `appState.js`; load before other splits.
2. Extract **storage + queue** into `storage.js`; keep behavior identical for `AuthWorkspace` and autosave.
3. Extract **note generation** (largest block); use dependency injection or a small `CNG` namespace.
4. Extract **audio** and **evidence** modules; run regression on ASR + RAG consult flows.
5. Add **ESM bootstrap** (or IIFE bundle) and trim `workspace_app.js` to re-exports/shims only, then remove shims when callers are migrated.
6. Run the **regression checklist** (below) on desktop + mobile.

---

## Testing / Regression Checklist

Use after any extraction:

- **Auth:** login/logout; `workspace-auth-changed`; auth card in drawer vs main.
- **Workspace:** edits persist via `/api/workspace`; sync pill; encounters switch/new.
- **Note generation:** each `noteType`; streaming; consult/order buttons; `copyNote` / clipboard sanitizer.
- **Audio/ASR:** record start/stop; queue jobs if offline path.
- **OCR queue:** queue survives reload where applicable.
- **Other pages:** `qa.html`, `admin.html` still resolve shared scripts; service worker picks up `NEVER_CACHE` entries for changed JS.

---

## Historical: original Step-by-Step Extraction (Phase 1)

The numbered steps 1–7 in the original plan described the migration path; steps 1–2 (CSS + large JS file) and satellite modules are done; steps 3–7 map to **P10b** above.
