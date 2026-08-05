# Case: Severe COPD with persistent hypoxia
case_id: aa2bd4474ecc4d11... | note_type: consult | source: cases_2026-06-15.jsonl

## Clinical picture (query source)
Consult note. Impression: very severe obstructive lung disease (FEV1 18% predicted), persistent hypoxia. Focus query built from impression, plan, HPI, investigations, PMH, medications, physical_exam. top_k requested: 5.

## Results retrieved (deduplicated by title)
1. **[guideline, score 0.683] "2020 GOLD Report — Download for personal use"** (GOLD) — retrieved 3 of 5 slots.
2. **[pubmed, score ~0.65] "A Real-World Evaluation of Therapeutic Inertia in Patients with Severe Uncontrolled COPD"** (PMC, 2026) — retrieved 2 of 5 slots.

## Relevance judgment
- GOLD 2020 Report: **RELEVANT.** GOLD is the primary international COPD management guideline; directly on-target for a very-severe-COPD consult. Note the corpus copy is the 2020 edition — worth flagging that GOLD updates annually and a 2020 copy may be several editions stale relative to current recommendations (specifically staging/pharmacotherapy escalation criteria have changed since).
- Therapeutic inertia in severe COPD paper: **RELEVANT.** Directly on-topic — the case's own plan revolves around whether current therapy is being escalated appropriately, which is exactly what this paper addresses.

## Case verdict: PASS
Both distinct documents surfaced are genuinely on-target. Main defect is duplication (5 slots, only 2 distinct documents) rather than relevance — real signal here, but half of it is wasted slot-space, and there's a not-yet-verified corpus-staleness concern for the GOLD edition.

## Case-specific rubric criteria
1. Top-5 results must include the GOLD COPD guideline or equivalent international standard. **[Currently: PASS]**
2. Top-5 results must not consist of >2 duplicate chunks of the same source document. **[Currently: FAIL — 3/5 + 2/5 = only 2 distinct docs across 5 slots]**
3. Guideline edition/publication year should be current within ~2 years of encounter date, or the pipeline should flag staleness if not. **[Currently: unverified — 2020 GOLD edition vs. 2026 encounter date is a 6-year gap worth checking]**
