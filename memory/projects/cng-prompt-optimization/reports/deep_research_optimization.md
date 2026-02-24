# Deep Research and Optimization Plan for CNG CoT Prompt (Ministral 14B / llama.cpp-style)

## 1) Reliability best practices for medium models

### A. Constrained formatting that actually sticks
- Prefer **small, finite hard rules** repeated once near output step, not many scattered reminders.
- Convert vague requirements to measurable constraints (e.g., `HPI exactly 2 paragraphs, 120-170 words`).
- Enforce **pre-output self-checklist** (section presence, non-empty required fields, forbidden chars, plan prefix rules).
- Output only final artifact when possible (hide intermediate reasoning in production) to reduce stage leakage.

### B. Anti-drift techniques
- Use **stage-isolation contract**: extraction stages collect facts only; writing stage cannot introduce unseen facts.
- Explicitly separate patient-reported facts (HPI) from clinician decisions (Plan) to avoid plan-language leakage into HPI.
- Use fail-safe replacements for missing fields: exact token `not documented`.
- Keep one canonical section order and exact heading strings to maximize regex scorer stability.

### C. Few-shot strategy for 14B-class models
- Use **micro-few-shots** (1-2 compact positive examples + 1 negative counterexample) for weakest items only.
- High-value few-shot targets: not-documented handling, concise medication line formatting, impression prose style.
- Avoid long generic exemplars; they increase latency and cause style copying drift.

### D. Stage isolation design pattern
- Stage 1-2: normalize and extract facts.
- Stage 3: validate completeness and apply missing-data defaults.
- Stage 4: write final note from validated structure only.
This works better than long free-form CoT in medium models where token pressure degrades late-section compliance.

### E. Sampler tuning for consistency
For structured notes, prioritize determinism:
- Temperature: **0.10-0.20**
- Top-p: **0.80-0.92**
- Max tokens: cap aggressively to avoid rambling and truncation tail risk
- Keep same seed when benchmarking ablations
Observed in this project: lower temp / lower top-p variants improved latency and reduced variance without harming means materially.

---

## 2) Case-specific gold-standard maxima
Generated file: `reports/case_gold_standards.csv`
- 109 cases analyzed
- Mean achievable max: 28.61/30
- Major blocker: rubric item 4.2 (medication line-length threshold artifact)

Method implemented in `scripts_normalized.py` with deterministic heuristics tied to source completeness.

---

## 3) New normalized scoring
Defined and implemented in `scripts_normalized.py`.
Outputs:
- `reports/normalized_run_metrics.json`
- `reports/normalized_score_table.md`

Best current sampled performance: **94.54% normalized** (iter_003_v3_sample15).
Target remains >=95% normalized.

---

## 4) Weakest-item optimization strategy (to >=94% item pass, >=95% normalized)

### Priority 1: 2.4 Not-documented handling
Intervention:
- Hard pre-output checklist with explicit no-blank rule
- Exact fallback token for required nullable sections
Expected gain: +0.4 to +0.8 raw points depending failure frequency.

### Priority 2: 2.2 + 3.1 HPI reliability
Intervention:
- Stage split: `hpi_facts` then constrained synthesis
- Require coverage fields (onset/progression/severity/associated symptoms/functional impact)
- Ban plan verbs in HPI explicitly
Expected gain: +0.2 to +0.5.

### Priority 3: 2.3 Impression structure
Intervention:
- Force 2-4 sentence prose template: diagnosis summary + uncertainty + evidence + immediate clinical framing
Expected gain: +0.1 to +0.3.

### Priority 4: 4.2 Medication format
Intervention:
- Prompt-side concise line guardrails
- Rubric-side calibration for legitimate long drug names (parallel policy fix)
Expected gain: +0.2 to +0.4 (mostly rubric calibration).

---

## 5) Proposed prompt vNext
Created: `prompts/prompt_v7_reliability.txt`

Design intent:
- Silent staged processing
- Explicit pre-output validation gate
- Strong nullable-section defaults
- Tight HPI and Plan structural contracts
- Reduced verbosity vs v2/v6 to lower latency and drift

---

## 6) Ablation plan (required before production gate)

Run full 109-case or stratified 60-case evaluation with fixed seed.

A0 baseline: v6 current prompt
A1 + pre-output checklist only
A2 + HPI coverage contract
A3 + not-documented hard fallback
A4 + concise medication-line contract
A5 full v7 (all interventions)

Track per run:
- raw mean /30
- normalized mean %
- weak-item pass rate (2.2/2.3/2.4/4.2)
- latency mean/p90

Promotion criteria:
- normalized >=95.0%
- each weak item >=94% pass
- no regression in 3.3, 4.4, 4.5, 1.8

---

## 7) Implementation updates completed
- Added `scripts_normalized.py` (gold standards + normalized metrics)
- Generated required outputs:
  - `reports/case_gold_standards.csv`
  - `reports/normalized_scoring_method.md`
  - `reports/normalized_run_metrics.json`
  - `reports/normalized_score_table.md`
- Added prompt proposal:
  - `prompts/prompt_v7_reliability.txt`
