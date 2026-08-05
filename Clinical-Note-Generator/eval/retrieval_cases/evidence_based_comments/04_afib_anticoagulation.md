# Case: Atrial fibrillation anticoagulation management
case_id: e767671ecba84285... | note_type: consult | source: cases_2026-06-18.jsonl

## Clinical picture (query source)
Consult discussing long-term necessity of anticoagulation ("blood thinner") in atrial fibrillation. top_k requested: 5.

## Results retrieved (deduplicated by title)
1. **[web, score 0.592] "2017 Edition"** (guidelines) — unidentifiable, 1 slot.
2. **[pubmed, score 0.575] "Clinical Patterns and Appropriateness of Apixaban Dosing in Patients With Atrial Fibrillation"** (PMC, 2026) — 1 slot.
3. **[web, score ~0.56] "VOLUME 105 | ISSUE 4S | APRIL 2024"** (guidelines) — 3 of 5 slots, unidentifiable.

## Relevance judgment
- Apixaban dosing appropriateness in AFib: **RELEVANT.** Directly on-target — anticoagulant dosing appropriateness is exactly the clinical question the note's Plan raises.
- "2017 Edition" and "VOLUME 105 ISSUE 4S APRIL 2024": **UNJUDGEABLE from metadata.** The latter title recurs verbatim in cases 04, 05, and 07 of this eval set (AFib, recurrent PE, CHF+AFib) despite those being three distinct clinical pictures — worth flagging as a possible over-retrieved/generically-high-scoring chunk rather than three independently-earned relevant hits. See cross-case finding in the summary file.

## Case verdict: PARTIAL PASS
One clearly relevant, directly on-target paper. The remaining 4 slots are unidentifiable, and the recurring "VOLUME 105 ISSUE 4S" chunk across multiple unrelated cases raises a retrieval-quality question independent of this case alone.

## Case-specific rubric criteria
1. Top-5 results must include at least one source directly addressing anticoagulant choice/dosing in AFib (e.g. apixaban/DOAC guidance). **[Currently: PASS]**
2. All guideline-tier results must carry an identifiable title. **[Currently: FAIL]**
3. "VOLUME 105 ISSUE 4S APRIL 2024" should not appear as a top result across clinically unrelated queries without independent justification — see cross-case finding. **[Currently: FLAGGED]**
