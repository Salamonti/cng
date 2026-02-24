# Referral Prompt-Specific Rubric (Derived from Gold Standards v2)

Date (UTC): 2026-02-24
Scope: referral-eligible cases only
Source gold: `01-gold-standards-referral.csv`

## Raw score (30)

### A) Output form + tone contract (10)
1. No headings; natural letter flow (0-2)
2. Output-only contract (no preamble/trailing model text) (0-2)
3. Non-directive language (no ordering/submissive phrasing) (0-2)
4. Suggestive collegial wording (0-2)
5. Simple gratitude closing (0-2)

### B) Referral content quality (10)
6. Clear referral reason and ask/question (0-2)
7. Relevant concise history/context only (0-2)
8. Includes material PMH/meds/allergies/social only when needed (0-2)
9. Relevant investigations included without over-interpretation (0-2)
10. Management-to-date summarized without invented recommendations (0-2)

### C) Safety + grounding (10)
11. No hallucinated diagnoses/findings/urgency claims (0-2)
12. Certainty calibration and neutrality (0-2)
13. Chronology correctness (0-2)
14. Concision and readability (0-2)
15. Conflicts usage only when truly needed (0-2)

## Normalized method
For case i:
- raw_i: score /30
- gold_i: achievable cap mapped from referral gold dimensions
- norm_i = raw_i / gold_i

Run metric:
- normalized_mean_pct = 100 * mean(norm_i)

## Gold-to-rubric cap mapping
- `no_headings` caps A1
- `non_directive_tone` caps A3
- `suggestive_collegial` caps A4
- `gratitude_simple` caps A5
- `referral_question_specificity` caps B6

This rubric is generated after gold-standard construction (correct sequence).
