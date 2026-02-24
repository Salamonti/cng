# Followup Prompt-Specific Rubric (Draft v1)

Date (UTC): 2026-02-24
Status: Drafted for baseline run

## Raw score (30)

### Domain A — Structure and contract (10)
1. Required section titles/order for followup (0-2)
2. Output-only contract (no preamble/trailing chatter) (0-2)
3. Current-vs-prior chronology clarity (0-2)
4. No forbidden characters / plain text compliance (0-2)
5. Conflicts section usage correctness (0-2)

### Domain B — Clinical content alignment (10)
6. Subjective emphasizes interval follow-up since prior visit (0-2)
7. Physical Exam limited to current encounter + fallback handling (0-2)
8. Investigations include materially relevant data/trends only (0-2)
9. Assessment is problem-based and non-generic (0-2)
10. Plan is specific, encounter-grounded, and non-generic (0-2)

### Domain C — Safety and grounding (10)
11. No hallucinated diagnoses/plans/investigations (0-2)
12. Certainty calibration (no unjustified escalation) (0-2)
13. Medication handling correctness (changes only when documented) (0-2)
14. Cross-section contamination avoided (content in correct section) (0-2)
15. Concision with fidelity (no fluff, no clinically relevant omission) (0-2)

## Normalized scoring
- Per-case achievable cap to account for sparse transcripts or absent prior-visit detail.
- Core expected cap rules (initial):
  - Cap interval-detail items when prior timeline not available in source.
  - Cap specificity items when source plan is genuinely generic.
  - Cap investigation-trend scoring when no serial data exists.

## Weak-item priority
- B9 Assessment non-generic
- B10 Plan non-generic
- B6 Interval follow-up framing
