# Referral Prompt-Specific Rubric (Draft v1)

Date (UTC): 2026-02-24
Status: Drafted for baseline run

## Raw score (30)

### Domain A — Form and contract (10)
1. No headings; natural single-letter format flow (0-2)
2. Output-only contract (no preamble/trailing model text) (0-2)
3. Professional tone without directive/ordering language (0-2)
4. Collegial suggestion wording (0-2)
5. Simple gratitude/thanks closing style (0-2)

### Domain B — Referral content quality (10)
6. Clear reason for referral and consult question (0-2)
7. Relevant HPI summary only (0-2)
8. Relevant PMH/meds/allergies/social factors only when material (0-2)
9. Relevant investigations included without over-interpretation (0-2)
10. Management-to-date summarized without adding recommendations (0-2)

### Domain C — Safety and grounding (10)
11. No hallucinated facts/diagnoses/urgency claims (0-2)
12. Certainty calibration and neutral language (0-2)
13. Chronology correctness (0-2)
14. Concision and readability (0-2)
15. Conflicts section usage correctness when needed (0-2)

## Normalized scoring
- Per-case achievable cap for sparse sources where complete referral details are unavailable.
- Core expected cap rules (initial):
  - Cap reason-specificity when referral question absent in source.
  - Cap investigation-detail completeness when only partial diagnostics are present.
  - Cap management summary detail when source has no explicit treatment timeline.

## Weak-item priority
- A1 No headings contract
- A3 Non-directive language
- A4 Suggestion style
- A5 Simple gratitude
