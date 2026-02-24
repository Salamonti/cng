# Prompt Optimization Changelog

## iter_001_v2_sample25
- Prompt: `prompt_v2_baseline.txt`
- Sampler: temp 0.2, top_p 0.9, max_tokens 6000
- Status: invalid baseline run (older parser version before heading-colon handling fix). Not used for model comparison.

## iter_002_v2_sample15
- Prompt: `prompt_v2_baseline.txt`
- Sampler: temp 0.2, top_p 0.9, max_tokens 6000
- Mean score: **26.8 / 30**
- Key misses: HPI completeness (2.2), medication line length/empties (4.2)

## iter_003_v3_sample15
- Prompt delta from v2:
  - moved not-documented handling to hard constraints
  - explicit "no blank required sections" rule
  - explicit final output starts at Patient ID
  - tighter HPI/Impression wording
- Sampler: temp 0.2, top_p 0.9, max_tokens 6000
- Mean score: **27.0 / 30** (+0.2 vs iter_002)
- Tradeoff: slightly worse investigations date formatting (4.3) in some notes

## iter_004_v3b_sample15
- Prompt delta from v3:
  - shorter guardrail-first prompt
  - reduced verbosity
- Sampler: temp 0.15, top_p 0.85, max_tokens 5000
- Mean score: **26.6 / 30** (-0.4 vs iter_003)
- Better speed but more format/order failures

## iter_005_v3_full109
- Attempted full 109-case run for v3
- Runtime exceeded practical window in this session; partial artifacts were written and run was stopped.
- Next action: resume full run with periodic checkpointing and progress logging.
