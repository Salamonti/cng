# Referral Prompt Optimization Scope and Requirements

Date (UTC): 2026-02-24
Status: In progress

## Selected prompt
- Config key: `default_note_user_prompts_other.referral`

## Pipeline usage (code mapping)
- Prompt body loaded from config and rendered in `build_prompt_other(...)` for non-standard note types.
- Code path:
  - `server/routes/notes.py` -> `build_prompt_other(...)`
- Runtime composition for `referral`:
  - system prompt: `default_note_system_prompt_other`
  - user prompt: `default_note_user_prompts_other.referral`
  - merged patient data block under `PATIENT DATA`
  - optional custom instructions appended

## Optimization target
- Full-set normalized score >= 95%
- Stable repeatability on independent rerun

## User-confirmed quality emphasis
1. Final referral letter must be natural language (no headings).
2. Avoid submissive/order-like language.
3. Use suggestion-oriented, collegial wording.
4. Keep simple gratitude/thanks tone.

## Known pitfalls to enforce
- Rubric false negatives separated from true failures
- Sparse-source achievable caps
- Sample-overfit prevention via full-set confirmation
- Output-only contracts (no preamble/trailing noise)
- Length-vs-fidelity balance to avoid hallucination drift
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
