# Failure Cluster Analysis (sampled optimization runs)

Primary clusters across valid runs:

1. **HPI completeness leakage (2.2)**
- Frequent: under 80 words or plan-language contamination.
- Observed in all variants; worst in concise guardrail prompt.
- Likely cause: insufficient symptom density in sparse transcripts and stage-to-stage leakage.

2. **Medication formatting (4.2)**
- Frequent: overlong lines or empty meds section.
- Persistent across variants and consistent with rubric sensitivity to long medication names.
- This remains the largest non-manual blocker after HPI.

3. **Investigations ISO-date compliance (4.3)**
- Regressed in v3/v3b due simplified stage wording.
- Missing explicit date fallback behavior caused some outputs without YYYY-MM-DD groups.

4. **Occasional catastrophic format collapse**
- Rare but high-impact outliers (single cases scoring very low) where full-note heading/order constraints were not obeyed.
- These outliers inflate variance and block >95% consistency.

## Practical conclusion
- Best practical configuration in this session is `prompt_v3_ndfix` with temp 0.2, top_p 0.9.
- Realistic achievable auto-score ceiling on this model remains approximately **27.5-28.5 / 30** under current rubric behavior.
- >99% aggregate (29.7/30+) is not realistic on Ministral 14B with current prompt-only strategy.
