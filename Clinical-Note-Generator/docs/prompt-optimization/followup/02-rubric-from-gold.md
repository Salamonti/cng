# Followup Prompt-Specific Rubric (Derived from Gold Standards v1)

Date (UTC): 2026-02-24
Source gold: `01-gold-standards-followup.csv`

## Raw score (30)

### A) Contract + structure (10)
1. Required section titles/order for followup output (0-2)
2. Output-only contract (no preamble/trailing chatter) (0-2)
3. Current-vs-prior chronology distinction (0-2)
4. Plain-text/character compliance (0-2)
5. Conflicts section conditional use correctness (0-2)

### B) Followup clinical quality (10)
6. Interval follow-up framing anchored to prior visits when available (0-2)
7. Physical Exam current-encounter handling with required fallbacks (0-2)
8. Investigation summary/trends only when supported by data (0-2)
9. Assessment specificity (non-generic, problem-focused) (0-2)
10. Plan specificity (non-generic, encounter-grounded actions) (0-2)

### C) Grounding + safety (10)
11. No hallucinated facts/diagnoses/plans (0-2)
12. Certainty calibration (no over-assertion) (0-2)
13. Medication handling correctness (changes only if documented) (0-2)
14. Cross-section contamination avoided (0-2)
15. Concision with no key omission (0-2)

## Normalized method
For case i:
- raw_i: rubric score out of 30
- gold_i: achievable cap mapped from gold dimensions in `01-gold-standards-followup.csv`
- norm_i = raw_i / gold_i

Run metric:
- normalized_mean_pct = 100 * mean(norm_i)

## Gold-to-rubric cap mapping
- `interval_followup` caps B6.
- `non_generic_assessment` caps B9.
- `non_generic_plan` caps B10.
- `investigation_trend` caps B8.
- `chronology_split` caps A3.

This rubric is now valid ordering-wise (gold first -> rubric second).
