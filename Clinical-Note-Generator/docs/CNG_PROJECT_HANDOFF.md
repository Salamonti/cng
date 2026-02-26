# CNG Project Handoff & Continuity Plan

Last updated: 2026-02-25 (UTC)
Owner: Islam
Maintainer context: Albert (OpenClaw on VPS)

## 1) What this project is

CNG (Clinical Note Generator) is a clinical documentation workflow with:
- frontend UI (served from `PCHost/web/index.html`),
- backend app/server logic under `Clinical-Note-Generator/server`,
- supporting RAG and prompt optimization assets,
- local workstation runtime + VPS orchestration.

Primary goals:
- reliable note generation,
- strict formatting compliance,
- fast clinician workflow with minimal friction.

---

## 2) Where to find key information

## Core runtime/app
- App code root: `cng/Clinical-Note-Generator/`
- App docs: `cng/Clinical-Note-Generator/docs/`
- Prompt optimization docs: `cng/Clinical-Note-Generator/docs/prompt-optimization/`

## Web UI currently edited most
- Live UI file: `cng/PCHost/web/index.html`
- Workspace/auth UI helper: `cng/PCHost/web/auth_workspace.js`
- Proxy/server: `cng/PCHost/server.js`

## Prompt assets (optimized set)
- Optimization workspace: `memory/projects/cng-prompt-optimization/`
- Prompts folder: `memory/projects/cng-prompt-optimization/prompts/`
- Final prompt set folder: `memory/projects/cng-prompt-optimization/prompts/final/`
  - `final_consult_prompt.txt` (current final consult prompt)
  - `final_followup_prompt.txt` (stub)
  - `final_referral_prompt.txt` (stub)

## Infra/search/model notes
- Runtime notes: `TOOLS.md`
- Daily logs: `memory/2026-02-25.md`
- Curated long-term memory: `MEMORY.md`
- SearX runbook: `memory/projects/searxng-openclaw-setup.md`
- LLM routing policy: `memory/projects/llm-routing-rules.md`

---

## 3) What we completed in this session

## A) Search + tooling reliability
- Replaced dependence on Brave API with secured SearXNG proxy route:
  - `https://ieissa.com:3443/searxng/search`
- Added reusable VPS helper scripts:
  - `scripts/searx_search.py`
  - `scripts/searx_search.sh` (+ `--json` mode)
- Wired OpenClaw gateway env loading via:
  - `/home/solom/.openclaw/secrets.env`
  - systemd drop-in `~/.config/systemd/user/openclaw-gateway.service.d/searx.conf`

## B) Model stack + policy
- Active hierarchy established:
  1. Whisper/OCR endpoints for specialized extraction
  2. Ministral14 local default
  3. Codex 5.3
  4. GPT-5.2
  5. Claude Sonnet/Opus for premium/high-stakes
- Added fail-fast model routing policy and user preferences.

## C) Node pairing / remote execution
- Workstation paired to VPS OpenClaw over Tailscale.
- Persistent node host installed as Windows Scheduled Task.
- Safe exec approvals baseline configured (direct binaries allowed; shell-wrapper still restricted unless expanded).

## D) CNG UI fixes shipped
- Markdown rendering improved in consult comment output:
  - headings, lists, tables, hr handling.
- Generate behavior unified across all 3 generate buttons.
- Recording-state behavior improved and tied across recording controls.
- OCR progress bar repositioned for visibility.
- Clear/reset focus behavior adjusted.

## E) Prompt organization + rules
- Final prompt naming structure created:
  - `final_consult_prompt.txt`
  - `final_followup_prompt.txt`
  - `final_referral_prompt.txt`
- Global numeric/unit formatting rule moved into SYSTEM prompt for final/promotion-ready prompts.

---

## 4) Current known caveats / specifics

1. **Two deployment contexts must not be confused**
- Git-managed mirror path (workstation): `C:\project-root`
- Live runtime symlink targets include `C:\PCHost`, `C:\Clinical-Note-Generator`.
- In this setup, updating from `C:\project-root` can affect live runtime through symlinks.

2. **Node command approvals**
- Some shell-style commands may still be denied due to allowlist policy.
- Direct binary invocation works and is preferred for automation safety.

3. **Interactive testing boundary**
- Full end-user UI behavior ultimately must be verified in workstation live app context.
- VPS side can do code-level and remote smoke checks, but workstation live UX remains source of truth.

4. **Prompt source-of-truth transition**
- Final prompt assets currently live under `memory/projects/cng-prompt-optimization/prompts/final/`.
- If app runtime consumes prompts from another path, sync step must be explicit.

5. **Search endpoint security model**
- Shared public app port remains public.
- SearX route protected via API key + app-layer restrictions.
- Avoid blanket port blocks that would break app/OpenWebUI access.

---

## 5) What is left to do

## Priority 1 (stability)
- Continue phased `index.html` hardening:
  - Phase 3: production cleanup (debug log gating, alert->toast consistency)
  - Phase 4: JS modularization into focused files (`markdown`, `audio`, `generate`, `workspace`)
- Keep fail-fast cycle:
  - single focused change,
  - single verification,
  - stop/escalate quickly if repeated failure.

## Priority 2 (prompt pipeline)
- Optimize and finalize `final_followup_prompt.txt`.
- Optimize and finalize `final_referral_prompt.txt`.
- Ensure app runtime points to `final/...` files as canonical prompt set.

## Priority 3 (execution ergonomics)
- Expand node approvals only as needed for specific tasks.
- Keep default restrictive stance; broaden minimally.

---

## 6) Detailed implementation plan (next sessions)

## Phase A — CNG frontend stabilization (safe, incremental)
1. Freeze current known-good state and baseline test checklist.
2. Phase 3 cleanup (no behavior change target):
   - gate noisy logs,
   - unify non-blocking user messaging,
   - remove dead/duplicative handlers.
3. End-to-end smoke tests (workstation live) on key flows:
   - generate normal,
   - generate while recording,
   - clear/reset focus,
   - markdown rendering.
4. If pass, commit + push.

## Phase B — Modularization (controlled refactor)
1. Extract markdown renderer to separate script.
2. Extract recording/transcription logic.
3. Extract generate orchestration logic.
4. Extract workspace/auth interactions.
5. Regression test after each extraction step.
6. Commit per module (small reversible diffs).

## Phase C — Prompt finalization
1. Treat `final_consult_prompt.txt` as current production candidate.
2. Build eval sets for follow-up and referral note types.
3. Optimize follow-up/referral prompts with same scoring rubric discipline.
4. Promote best versions into `prompts/final/`.

## Phase D — Deployment discipline
1. Push to GitHub first.
2. Validate in non-live session.
3. Only then apply/update workstation live runtime.

---

## 7) Operational rules (must keep)

- Always specify execution context in commands: VPS vs workstation + path.
- Verify/test first when feasible before asking user to implement.
- Fail-fast troubleshooting; avoid repetitive loops.
- Preserve public access for app/OpenWebUI while securing sensitive routes only.

---

## 8) Quick restart checklist for next session

1. Read this file first.
2. Confirm node connectivity (`WORKSTATION` connected).
3. Confirm current deployed `index.html` commit in GitHub and workstation.
4. Pick next phase item (A3 cleanup or modularization step 1).
5. Run one change + one test + report.



## 9) Phase execution status (latest)

- Phase 3 cleanup: DONE
  - debug logs gated behind `debugLog` and `window.DEBUG_MODE`
  - `alert()` usage removed from audio debug flow (toast-based)
- Phase 4 modularization: CORE DONE
  - markdown renderer extracted to `PCHost/web/markdown_renderer.js`
  - generate/focus orchestration extracted to `PCHost/web/generate_ui_flow.js`
  - audio UI helpers extracted to `PCHost/web/audio_ui_utils.js`
- Deployment state: GitHub only, workstation live not updated yet (intentional).
