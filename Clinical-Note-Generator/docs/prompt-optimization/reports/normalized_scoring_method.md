# Normalized Scoring Method (Raw + Gold-Normalized)

## Why
Raw auto-score is out of 30, but some rubric items are effectively capped by source completeness or rubric artifacts (especially 4.2 medication line-length). A normalized score against case-specific achievable max gives fairer cross-case and cross-run comparisons.

## Definitions
For each case `i`:
- `raw_i` = evaluator score (0..30)
- `gold_i` = achievable max under data/rubric constraints (<=30)
- `norm_i = raw_i / gold_i`

For a run with `N` cases:
- `raw_mean_30 = sum(raw_i)/N`
- `normalized_mean_pct = 100 * sum(norm_i)/N`
- `gold_mean_30 = sum(gold_i)/N`

## Gold-standard derivation used
Implemented in `scripts_normalized.py` using deterministic heuristics:
- 2.2 cap when CURRENT_ENCOUNTER text is too sparse for evidence-grounded >=80-word HPI
- 3.1 cap when encounter text is too sparse for reliable 60+ word narrative HPI
- 3.4 cap for very sparse full-case input where safe non-hallucinatory note likely <200 words
- 4.2 cap for medication content likely to exceed 8-core-word rubric threshold despite clinically correct formatting
- 4.3 cap only if no ISO dates exist (not triggered in this dataset)

## Dataset-level findings
From `reports/case_gold_standards.csv` (109 cases):
- Mean achievable max: **28.61 / 30**
- Min/Max achievable: **25 / 30** to **30 / 30**
- Main blocker frequency: **4.2 medication format cap** (101/109 cases)

## Run metrics (normalized)
See `reports/normalized_run_metrics.json` and `reports/normalized_score_table.md`.
Best observed among sampled runs:
- `iter_003_v3_sample15`: raw 27.0/30, normalized **94.54%**
- `iter_007_v5_hpifix_sample15`: raw 26.93/30, normalized **94.24%**

## Integration recommendation
1. Keep current raw 30-point reporting for continuity.
2. Add normalized reporting as the primary optimization KPI.
3. Promotion gate:
   - `normalized_mean_pct >= 95.0`
   - and each major weak item pass-rate >=94% (2.2, 2.3, 2.4, 4.2).
