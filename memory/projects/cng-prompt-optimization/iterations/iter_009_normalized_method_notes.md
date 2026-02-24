# iter_009 - normalized method notes

- Added deterministic gold-standard heuristic caps per case.
- Added normalized run metric computation against per-case achievable max.
- Reconstructed sampled run source indices using optimizer sampling behavior (seed=42, sampleN from run_id).
- Produced:
  - reports/case_gold_standards.csv
  - reports/normalized_run_metrics.json
  - reports/normalized_score_table.md
