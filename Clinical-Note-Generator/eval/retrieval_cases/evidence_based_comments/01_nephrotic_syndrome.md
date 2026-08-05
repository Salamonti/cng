# Case: Nephrotic syndrome secondary to proteinuric kidney disease
case_id: a66b681a0e094d0bb9c7e246df485474 | note_type: consult | source: cases_2026-06-18.jsonl

## Clinical picture (query source)
Consult note. Impression: nephrotic syndrome (proteinuria 5.55g/24h, edema, hypertension, hyperlipidemia), etiology undetermined (ddx membranous nephropathy, FSGS, minimal change, lupus, amyloidosis). Plan: empagliflozin started for proteinuria reduction, workup for anti-PLA2R antibodies, viral screen, amyloidosis/lupus screen, possible biopsy pending, BP 183/128 on presentation.

Focus query built from: impression, plan, HPI, investigations, PMH, medications, physical_exam (production default "sections" strategy). top_k requested: 5.

## Results retrieved (raw top-5, deduplicated by title)
1. **[web, score 0.636] "VOLUME 108 | ISSUE 4S | OCTOBER 2025"** (guidelines, no further metadata) — retrieved 4 of 5 slots (near-identical chunk repeated).
2. **[web, score 0.605] "KDIGO 2024 CLINICAL PRACTICE GUIDELINE"** (guidelines) — retrieved once.

## Relevance judgment
- KDIGO 2024 guideline: **RELEVANT.** KDIGO publishes the actual glomerular-disease/nephrotic-syndrome guideline this case needs (membranous nephropathy, FSGS, MCD workup and management). Correct hit.
- "VOLUME 108 | ISSUE 4S | OCTOBER 2025": **UNJUDGEABLE — title carries no identifying information.** Cannot determine relevance without opening the source link; the metadata itself is a defect. Occupies 4 of 5 result slots via duplication, meaning only 2 *distinct* documents were actually surfaced in a top-5 query, not 5.

## Case verdict: PARTIAL PASS
One genuinely on-target guideline (KDIGO) surfaced, but 80% of the result slots were consumed by duplicate/unidentifiable chunks of a single document, crowding out what could have been additional relevant hits (e.g. FSGS-specific or amyloidosis-specific guidance, given the differential explicitly includes both).

## Case-specific rubric criteria
1. Top-5 results must include at least one nephrology society guideline (KDIGO or equivalent) addressing glomerular disease/nephrotic syndrome workup. **[Currently: PASS]**
2. Top-5 results must not consist of >2 duplicate chunks of the same source document. **[Currently: FAIL — 4/5 slots are the same chunk]**
3. All guideline-tier results must carry an identifiable title (society + topic), not a bare volume/issue string. **[Currently: FAIL for "VOLUME 108 ISSUE 4S"]**
