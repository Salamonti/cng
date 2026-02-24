# Followup Prompt Optimization Scope and Requirements

Date (UTC): 2026-02-24
Status: In progress

## Selected prompt
- Config key: `default_note_user_prompts.followup`

## Pipeline usage (code mapping)
- Prompt body loaded from config and rendered in `build_prompt(...)`.
- Code path:
  - `server/routes/notes.py` -> `build_prompt(...)`
  - Note type normalization maps `follow-up`, `follow up`, `follow_up` -> `followup`
- Runtime composition for standard note types:
  - system prompt: `default_note_system_prompt`
  - user prompt: `default_note_user_prompts.followup`
  - patient data block appended under `PATIENT DATA`
  - optional user custom instructions appended as extra layer

## Optimization target
- Full-set normalized score >= 95%
- Stable repeatability on independent rerun

## User-confirmed quality emphasis
1. Follow-up should behave like a focused mini-consult anchored to prior visits.
2. Avoid generic/non-specific impression items.
3. Avoid generic/non-specific plan items.
4. Preserve source-grounded output-only behavior.

## Known pitfalls to enforce
- Rubric false negatives separated from true failures
- Sparse-source achievable caps
- Sample-overfit prevention via full-set confirmation
- Output-only contracts (no preamble/trailing noise)
- Section-order brittleness checks where required
- Latency/stability tracked with score

## Planned artifacts
- `01-gold-standards.*`
- `02-rubric.*`
- `03-baseline.*`
- `04-iterations.*`
- `05-full-confirmation.*`
- `06-repeatability.*`
- `final-technical-report.md`
- `executive-summary.md`
- `reproducibility-report.md`
- `promotion-recommendation.md`
