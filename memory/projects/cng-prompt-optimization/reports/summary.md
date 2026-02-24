# CNG CoT Single-Prompt Optimization Summary

## Best prompt
- `/home/solom/.openclaw/workspace/memory/projects/cng-prompt-optimization/prompts/prompt_v3_ndfix.txt`

## Best sampler
- temperature: 0.2
- top_p: 0.9
- max_tokens: 6000

## Best observed score (this session)
- 27.0 / 30 mean (sampled 15-case run)

## Ceiling assessment
- Target >99% is not realistic on current setup.
- Practical ceiling remains ~27.5-28.5 / 30 due:
  - manual-neutral items fixed at 2 points total in auto-scoring logic,
  - persistent medication formatting rubric sensitivity,
  - rare catastrophic formatting outliers.

See:
- `reports/score_table.md`
- `reports/changelog.md`
- `reports/failure_cluster_analysis.md`
- `reports/next_steps.md`
