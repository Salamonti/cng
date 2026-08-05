# Case: Sarcoidosis, stable pulmonary disease, cardiac sarcoidosis rule-out
case_id: ff1b282119a1400f... | note_type: followup | source: cases_2026-06-16.jsonl

## Clinical picture (query source)
Followup: sarcoidosis stable pulmonary disease, elevated ACE level (58 U/L), plan to rule out cardiac sarcoidosis via repeat echocardiogram, currently asymptomatic cardiac-wise. top_k requested: 5.

## Results retrieved (deduplicated by title)
1. **[pubmed, score ~0.63] "Clinical and Imaging Abnormalities Associated With Inducible Ventricular Arrhythmias During Electrophysiologic Study in Patients With Cardiac Sarcoidosis and Mildly Impaired Left Ventricular Function"** (PMC, 2026) — 2 of 5 slots.
2. **[web, score 0.529] "2026"** (guidelines) — 3 of 5 slots, title is literally just a year with no further identifying content.

## Relevance judgment
- Cardiac sarcoidosis EP/ventricular arrhythmia paper: **RELEVANT.** This case's own plan is explicitly "rule out cardiac sarcoidosis" — a paper on cardiac sarcoidosis risk stratification (imaging + EP findings predicting ventricular arrhythmia) is directly on-topic for exactly that clinical question.
- "2026" guideline: **UNJUDGEABLE — this is the single worst metadata example in the eval set.** A bare four-digit year provides zero information about source, society, or topic.

## Case verdict: PARTIAL PASS
The genuinely relevant hit is strong and directly on-target. But 3 of 5 slots are consumed by a document identified only by the string "2026," which is a severe metadata/display defect independent of whether the underlying content is actually relevant.

## Case-specific rubric criteria
1. Top-5 results must include cardiac-sarcoidosis-specific guidance given the note's explicit cardiac rule-out plan. **[Currently: PASS]**
2. No result should display a title consisting only of a bare year with no other identifying text. **[Currently: FAIL — worst offender in the set]**
