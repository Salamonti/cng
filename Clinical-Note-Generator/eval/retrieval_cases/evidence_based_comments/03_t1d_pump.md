# Case: Type 1 Diabetes management, insulin pump, glycemic variability
case_id: f3834f3c7d2945cd... | note_type: consult | source: cases_2026-06-19.jsonl

## Clinical picture (query source)
28-year-old male, long-standing T1D, insulin pump + FreeStyle Libre 3 sensor, glycemic variability, A1C ~8.0%. top_k requested: 5.

## Results retrieved (deduplicated by title)
1. **[web, score ~0.56] "VOLUME 102 | ISSUE 5S | NOVEMBER 2022"** (guidelines) — 4 of 5 slots, unidentifiable title (likely ADA Standards of Care supplement based on volume/issue pattern, but not confirmable from metadata alone).
2. **[web, score 0.544] "Teplizumab for delaying the onset of stage 3 type 1 diabetes in people 8 years and over with stage 2 type 1 diabetes"** (guidelines, N/A year) — 1 of 5 slots.

## Relevance judgment
- "VOLUME 102 ISSUE 5S NOVEMBER 2022": **UNJUDGEABLE from metadata**, but if this is in fact ADA Standards of Care (plausible given the volume/issue numbering pattern matches Diabetes Care supplement conventions), it would be highly relevant. This is the same metadata-quality defect seen in case 01.
- Teplizumab/stage-2-T1D-prevention guideline: **IRRELEVANT to this patient.** This patient has established, longstanding T1D already on pump therapy — teplizumab is specifically for *delaying onset* in pre-symptomatic stage-2 patients who have not yet progressed to clinical (stage 3) diabetes. Surfacing a disease-prevention guideline for a patient who is years past that window is a genuine retrieval miss, and is exactly the kind of result that could mislead a doctor skimming Evidence Based Comments quickly.

## Case verdict: PARTIAL FAIL
The one clearly-identifiable, judgeable result is irrelevant to this patient's actual clinical stage. The dominant (4/5 slots) result is unidentifiable and cannot be credited as a pass without title/metadata fixes.

## Case-specific rubric criteria
1. Top-5 results must include a current diabetes-management guideline (ADA Standards of Care or equivalent) addressing glycemic control in established T1D. **[Currently: UNVERIFIABLE due to bad metadata]**
2. Top-5 results must NOT surface disease-prevention/pre-onset guidance (e.g. teplizumab stage-2 prevention) for a patient with established, longstanding disease — this is a stage-mismatch retrieval error. **[Currently: FAIL]**
3. All guideline-tier results must carry an identifiable title. **[Currently: FAIL]**
