# CNG Prompt Optimization Handbook (Consult / Followup / Referral)

Date: 2026-02-24  
Scope: Practical playbook based on completed consult optimization and subsequent followup/referral trials.

## 1) Core Method That Worked

Use this sequence strictly:

1. **Select prompt scope clearly** (which prompt key is in play).
2. **Understand pipeline usage in code** (where system/user prompts are injected).
3. **Build gold standards first** (full-case set; prompt-specific achievable caps).
4. **Derive rubric from gold** (not the other way around).
5. **Run baseline** on full target set.
6. **Iterate on weakest items** using sample subsets for speed.
7. **Confirm on full set** before making decisions.
8. **Run repeatability pass** (independent repeat).
9. **Promote only with stability**, not single-run peaks.

---

## 2) Prompt Keys and Exclusion Discipline

- Keep optimization scoped to the selected prompt key.
- Do not silently expand scope.
- For this project, defaults were:
  - Exclude by default unless explicitly selected: main system prompt, consult prompt, custom prompt.

---

## 3) Gold Standard Design Rules

### Why gold standards matter
Raw score alone can mislead when source data is sparse. Use per-case achievable caps.

### Gold standards should encode:
- **Source sparsity ceilings** (don’t reward hallucination).
- **Format traps** (e.g., line-length style checks that can over-penalize valid text).
- **Prompt-type constraints** (followup continuity, referral no-headings/tone).

### Practical rule
If data is missing, cap achievable score for that dimension instead of forcing fabricated detail.

---

## 4) Rubric Construction Rules

Build rubric *from* gold.

- Separate **true failure** from **rubric artifact**.
- Include both:
  - **Raw score** for continuity.
  - **Normalized score** for fair cross-case comparison.
- Track weak dimensions explicitly as optimization targets.

---

## 5) Iteration Strategy That Actually Helps

### Use two lanes
1. **Fast lane (sample iterations)**
   - 10–30 cases max
   - Focus on weak items only
2. **Decision lane (full confirmation)**
   - Full eligible set
   - Repeat run required

### Stop conditions
- Target reached (e.g., >=95% normalized), OR
- No improvement for 3 consecutive iterations, OR
- Significant worsening (strategy shift required).

---

## 6) What We Learned by Note Type

## Consult
- Strongest success case.
- Full-set normalized target exceeded with repeatability.
- Weak-item targeting + normalized scoring produced stable promotion decision.

## Followup
- Biggest recurring losses:
  - interval continuity mention misses,
  - generic assessment/plan leakage.
- Helpful trick:
  - final self-check to remove generic lines unless source-grounded.
- Best accepted result stayed below 95 but materially improved.

## Referral
- Biggest baseline failure was **heading leakage** and **directive language**.
- Strong constraints helped:
  - no headings,
  - collegial/suggestive tone,
  - explicit reason preservation gate.
- Still needed tradeoff handling between tone compliance and reason fidelity.

---

## 7) Model-Specific Notes (ministral14 @ :8081)

- Alias: `ministral14`
- Vision-capable (text + image)
- Effective for iterative evaluation loops, but can show instability on narrowly constrained style/tone tasks.
- Use explicit self-check contracts for format/tone adherence.

---

## 8) System Prompt vs User Prompt

- Default approach: optimize user prompt first with fixed system prompt.
- If gains plateau, run a **controlled system-prompt mini-sweep** (2–3 variants) while holding user prompt constant.
- Keep changes minimal and measurable.

---

## 9) Sampling Policy (Critical)

Never evaluate on irrelevant cases.

- Referral optimization: include only referral-intent cases.
- Followup optimization: prefer cases with meaningful prior-visit signal.

This avoids false regressions and misleading score noise.

---

## 10) Runtime / Ops Discipline

- Treat long runs as first-class jobs.
- Post explicit milestone updates.
- Verify completion with both:
  - process completion, and
  - llama `/slots` idle check (all slots `is_processing=false`).

---

## 11) Artifact Checklist (Per Prompt)

For each optimized prompt directory:

1. `00-scope-and-requirements.md`
2. `01-gold-standards-*.csv/.md`
3. `02-rubric-from-gold.md`
4. `03-baseline-*`
5. `04+ iteration reports`
6. full confirmation report
7. repeatability report
8. `final-*-prompt.txt`
9. final summary (technical + executive + recommendation)

---

## 12) Practical Tricks That Worked

- Add **final internal self-check gates** before output.
- Penalize generic text **unless source-grounded**.
- Make interval mention conditional on prior-visit evidence.
- Keep heading strings/order exact only when downstream parser requires it.
- Prefer concrete lexical constraints over vague style words.
- Use stable sampler defaults during comparison (temp/top_p/max_tokens fixed).

---

## 13) Useful Saved Code

Reusable scripts are archived in:

- `docs/prompt-optimization/tools/consult_optimizer.py`
- `docs/prompt-optimization/tools/normalized_scoring.py`

These preserve the consult-era optimization/scoring logic and can be adapted for future prompt types.

---

## 14) Final Rule

**Promotion decisions are based on full-set + repeatability, not sample wins.**
