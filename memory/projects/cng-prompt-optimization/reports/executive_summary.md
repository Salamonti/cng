# Executive Summary - CNG Prompt Optimization

## What was completed
1. Built case-specific achievable-max framework and generated `reports/case_gold_standards.csv` for all 109 cases.
2. Implemented normalized scoring pipeline in `scripts_normalized.py`.
3. Produced normalized run metrics and table (`reports/normalized_run_metrics.json`, `reports/normalized_score_table.md`).
4. Wrote deep reliability research + implementation plan (`reports/deep_research_optimization.md`).
5. Proposed prompt vNext (`prompts/prompt_v7_reliability.txt`) focused on deterministic formatting and anti-drift.

## Key findings
- Mean achievable ceiling is **28.61/30** (not uniformly 30).
- Best existing sampled run reaches **94.54% normalized** (near target, but below 95%).
- Main bottlenecks: **2.4 not-documented handling**, **2.2 HPI completeness**, **2.3 impression structure**, and rubric artifact in **4.2 medication format**.

## Exact next actions
1. Run ablation matrix A0-A5 (defined in deep report) with fixed seed on at least 60 cases (prefer 109).
2. Use normalized scoring as primary KPI; keep raw /30 secondary.
3. Promote only if:
   - normalized mean >=95.0%
   - item pass >=94% for 2.2, 2.3, 2.4, 4.2
   - no regression in already-stable items (1.8, 3.3, 4.4, 4.5).
4. Parallel workstream: calibrate rubric 4.2 for clinically valid long medication names.
5. If A5 passes, standardize on v7 prompt and retire older variants.
