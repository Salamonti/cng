# Next-Step Recommendations

1. Run full 109-case evaluation for `prompt_v3_ndfix` with checkpointing every 10 cases.
2. Add automatic retry for transient HTTP 400/timeout failures to reduce catastrophic outliers.
3. Restore stricter investigations date instruction from v2 while keeping v3 no-blank guardrail.
4. Add post-generation sanitizer for section heading/order normalization before scoring.
5. Consider two-pass strategy only for failing cases (cheap repair pass) to lift tail performance.
6. Recalibrate rubric item 4.2 for legitimate long medication names; current threshold still penalizes clinically valid lines.
