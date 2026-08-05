# Case: Congestive heart failure and atrial fibrillation
case_id: 2536b702a4e64baf... | note_type: consult | source: cases_2026-06-18.jsonl

## Clinical picture (query source)
Heart failure stable on current medication regimen, review of recent testing, atrial fibrillation co-management. top_k requested: 5.

## Results retrieved (deduplicated by title)
1. **[web, score ~0.55] "VOLUME 105 | ISSUE 4S | APRIL 2024"** (guidelines) — 4 of 5 slots, unidentifiable, same chunk as cases 04 and 05.
2. **[pubmed, score 0.547] "Eculizumab-Induced Acute Heart Failure Decompensation in a Patient with Clinically Suspected Complement-Mediated Thrombotic Microangiopathy: A Case Report"** (pubmed, 2026) — 1 slot.

## Relevance judgment
- Eculizumab-induced HF decompensation / TMA case report: **IRRELEVANT.** This is a single case report about a rare drug-induced complication (eculizumab, used for complement-mediated diseases like PNH/aHUS) causing heart failure decompensation. Nothing in this case's note suggests eculizumab use, TMA, or PNH/aHUS — this looks like a keyword-similarity false positive on "heart failure decompensation" divorced from the actual clinical context.
- "VOLUME 105 ISSUE 4S APRIL 2024": **this is now the THIRD case (04, 05, 07) where this exact unidentifiable chunk dominates results**, across three different primary conditions (AFib anticoagulation, recurrent PE, CHF+AFib) — see cross-case finding below.

## Case verdict: FAIL
Neither judgeable result is genuinely relevant. No ACC/AHA/ESC heart failure or atrial fibrillation management guideline was clearly identified in the result set (though "VOLUME 105 ISSUE 4S" may plausibly be one — this is exactly the problem the metadata defect creates: a potentially-correct hit cannot be credited because it's unidentifiable).

## Case-specific rubric criteria
1. Top-5 results must include a heart-failure and/or atrial-fibrillation management guideline (ACC/AHA/ESC or equivalent) identifiable by name. **[Currently: UNVERIFIABLE due to bad metadata — likely present but uncreditable]**
2. Results should not surface rare drug-induced case reports based on superficial "heart failure decompensation" keyword overlap when the triggering drug/condition isn't present in the patient's record. **[Currently: FAIL]**
3. "VOLUME 105 ISSUE 4S APRIL 2024" recurrence across cases 04/05/07 — see cross-case finding, this is the single most important systemic issue this eval set surfaced.
