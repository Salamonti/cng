# Case: Recurrent pulmonary embolism
case_id: 14aa748608334c9f... | note_type: consult | source: cases_2026-06-17.jsonl

## Clinical picture (query source)
61-year-old woman with history of extensive bilateral PE (April 2024) and a more recent recurrent event. top_k requested: 5.

## Results retrieved (deduplicated by title)
1. **[web, score 0.536] "VOLUME 105 | ISSUE 4S | APRIL 2024"** (guidelines) — 4 of 5 slots, unidentifiable, same recurring chunk as case 04.
2. **[pubmed, score 0.540] "Long-Term Outcomes of Antithrombotic Treatment in a Cohort of 864 Patients With Fontan Circulation"** (PMC, 2026) — 1 slot.

## Relevance judgment
- Fontan-circulation antithrombotic outcomes paper: **LOW RELEVANCE / LIKELY MISMATCH.** Fontan circulation is a specific pediatric-congenital single-ventricle physiology population; this is a 61-year-old adult with recurrent PE and no stated congenital heart history. The overlap is only "antithrombotic treatment outcomes," which is too broad a similarity to justify surfacing a study population this narrow and unrelated to the actual patient.
- "VOLUME 105 ISSUE 4S APRIL 2024": **UNJUDGEABLE, and this is the third case in this set where this exact chunk dominates the result set** (also cases 04 and 07) — see cross-case finding.

## Case verdict: FAIL
Neither judgeable result is genuinely on-target for recurrent adult PE (e.g. no CHEST/ACCP VTE guideline, no extended-anticoagulation-duration guidance, no thrombophilia workup guidance surfaced — all of which are the actual clinical questions a recurrent-PE consult raises).

## Case-specific rubric criteria
1. Top-5 results must include VTE/PE-specific guidance (e.g. CHEST antithrombotic guideline, ISTH, or equivalent) addressing recurrent PE / extended anticoagulation duration. **[Currently: FAIL — not surfaced]**
2. Results should not surface case series from clinically distinct populations (e.g. pediatric congenital heart disease) as if generally applicable based on superficial keyword overlap alone. **[Currently: FAIL]**
3. "VOLUME 105 ISSUE 4S APRIL 2024" recurrence across cases — see cross-case finding. **[Currently: FLAGGED]**
