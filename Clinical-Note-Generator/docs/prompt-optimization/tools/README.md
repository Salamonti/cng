# Prompt Optimization Tools (Archived + Reusable)

These scripts are preserved from successful consult optimization work and can be reused/adapted:

- `consult_optimizer.py`
  - Main evaluation/iteration harness used in consult tuning loops.

- `normalized_scoring.py`
  - Builds per-case achievable caps and computes normalized metrics.

## Notes
- Paths inside scripts may reference historical workspace locations; update constants before reuse.
- Keep sampler settings fixed when comparing prompt variants.
- Always run full-set confirmation + repeatability before promotion.
