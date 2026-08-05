# Case: Monoclonal gammopathy of undetermined significance (MGUS)
case_id: ccc10db55a3e43c0... | note_type: consult | source: cases_2026-06-17.jsonl

## Clinical picture (query source)
Confirmed monoclonal protein (IgG kappa 2.1) in the gamma region, MGUS workup/monitoring. top_k requested: 5.

## Results retrieved (deduplicated by title)
1. **[web, score ~0.55] "KDIGO Clinical Practice Guideline on the Evaluation and..."** (guidelines, title truncated) — 3 of 5 slots.
2. **[pubmed, score 0.600] "Progressive Macroglossia and Dysphagia as the Initial Manifestation of Systemic Amyloid Light-Chain (AL) Amyloidosis Associated With Multiple Myeloma: A Case Report and Literature Review"** (PMC, 2026) — 1 slot.
3. **[pubmed, score 0.582] "Sciatica as an atypical initial presentation of multiple myeloma: a case report"** (PMC, 2026) — 1 slot.

## Relevance judgment
- KDIGO guideline (title truncated, likely on monoclonal-gammopathy-related kidney disease evaluation): **PLAUSIBLY RELEVANT** — KDIGO does publish guidance on monoclonal-gammopathy-associated renal disease, which is directly pertinent to MGUS monitoring. Truncated title is a lesser version of the same metadata problem (better than a bare volume/year, but still incomplete).
- AL amyloidosis case report: **RELEVANT.** MGUS progressing to AL amyloidosis is a recognized, clinically important transformation risk — exactly the kind of "what to watch for" evidence a doctor managing MGUS should see.
- Multiple myeloma sciatica case report: **TANGENTIAL.** MGUS-to-myeloma progression is relevant in principle, but this specific case report (an atypical *presenting* symptom of myeloma) is a narrow curiosity rather than actionable monitoring guidance — lower value than the amyloidosis hit.

## Case verdict: PASS
Best-performing case in the set — all three distinct documents are at least plausibly relevant, with two strongly so, reflecting genuine understanding of MGUS's key clinical concern (progression risk to myeloma/amyloidosis).

## Case-specific rubric criteria
1. Top-5 results must include guidance on monoclonal-gammopathy-associated organ involvement (renal and/or amyloidosis) as the central MGUS monitoring concern. **[Currently: PASS]**
2. Guideline titles should not be truncated mid-word in a way that obscures the specific topic. **[Currently: PARTIAL FAIL]**
