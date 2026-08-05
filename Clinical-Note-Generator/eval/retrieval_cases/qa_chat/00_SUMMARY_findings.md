# QA Chat — Retrieval Eval Summary (10 synthetic doctor-style questions)

Method: 10 synthetic questions written to mirror how a doctor would actually type into QA chat — short, direct, informal phrasing — covering the same 10 specialty areas as the Evidence Based Comments eval (for cross-comparison of the two RAG consumers). Queried against the real running RAG service using qa_chat.py's actual construction: literal question text, top_k=8 (qa_chat_rag_top_k default), no date/specialty filter. No historical real QA questions exist to draw from (session state is in-memory only, never persisted — confirmed by code inspection), so these are necessarily synthetic; this was disclosed to the user before starting.

## Per-question verdicts
| # | Question | Verdict |
|---|---|---|
| q01 | Max dose of bisoprolol in heart failure | **Fail** — no result answers the specific dosing question asked |
| q02 | When to start home oxygen in COPD | Pass (topically), but 8/8 results are ONE duplicated chunk |
| q03 | GOLD criteria for severe COPD exacerbation | Pass (topically), 7/8 duplicated |
| q04 | Apixaban vs warfarin in AFib+CKD | Pass |
| q05 | Anticoagulation duration after unprovoked PE | **Fail** — wrong-specialty result (renal AKI guideline) surfaced |
| q06 | Sarcoidosis cardiac workup | Pass |
| q07 | KDIGO nephrotic syndrome referral criteria | Pass |
| q08 | Insulin pump settings for exercise in T1D | **Fail** — no exercise-specific guidance surfaced, ICU journal citation appears |
| q09 | MGUS vs smoldering myeloma monitoring | Partial pass |
| q10 | Malignant ascites management in ovarian cancer | Partial pass — treatment-line guideline surfaced, not ascites-specific |

**Score: 5 pass / 2 partial / 3 fail out of 10.**

## Per-question detail

**q01 — bisoprolol max dose in HF: FAIL.** None of the 8 results address bisoprolol dosing at all. Results returned are HFpEF spironolactone, diuretic-resistance, and vericiguat+sacubitril/valsartan papers — generally heart-failure-adjacent but none answer a specific, well-established dosing question (target 10mg daily per standard HF guidelines) that should be one of the most reliably-answerable queries in cardiology. This is a meaningful gap: specific pharmacologic dosing questions appear to be a weak spot.

**q02 — home oxygen in COPD: TOPICALLY CORRECT, SEVERELY DUPLICATED.** All 8 of 8 results are the identical "2020 GOLD Pocket Guide" chunk. Topically this is the right document (GOLD does define home-O2 criteria), but returning one chunk 8 times provides zero additional information beyond what one result would, and completely crowds out any other potentially useful source (e.g. a home-oxygen-specific guideline, LTOT trial data).

**q03 — GOLD severe exacerbation criteria: TOPICALLY CORRECT, SEVERELY DUPLICATED.** Same pattern — 7/8 results are "2020 GOLD Report" duplicated, 1/8 is the Pocket Guide variant. Right document family, near-zero result diversity.

**q04 — apixaban vs warfarin in AFib+CKD: PASS.** Surfaces the apixaban-dosing-appropriateness paper (relevant, same hit as EBC case 04) plus an edoxaban/AFib+CAD trial (reasonably adjacent — different DOAC, same clinical decision space). Best-performing anticoagulation-related query in this half.

**q05 — anticoagulation duration after unprovoked PE: FAIL.** No VTE-duration-specific guidance surfaced. Notably includes "KDIGO 2026 CLINICAL PRACTICE GUIDELINE FOR ACUTE KIDNEY" — a renal AKI guideline with no connection to a PE-anticoagulation-duration question, a clear specialty/topic mismatch. Also two results with genuinely broken titles: a table-legend fragment ("4F-PCC = four-factor prothrombin complex concentrate...") extracted as if it were a document title, and blank-title PubMed entries (see metadata findings below).

**q06 — sarcoidosis cardiac workup: PASS.** Surfaces the same cardiac-sarcoidosis EP/arrhythmia paper as EBC case 06, plus a second genuinely relevant paper ("Cardiac sarcoidosis: new insights beyond the granuloma using spatial proteomics") not seen in the EBC half. Best-performing query of this set along with q04/q07.

**q07 — KDIGO nephrotic referral criteria: PASS.** KDIGO 2024 guideline correctly surfaces (matches EBC case 01's correct hit), confirming this is a reliable, repeatable correct retrieval for nephrotic-syndrome-related queries phrased either as a note-derived focus or a direct question.

**q08 — insulin pump exercise settings in T1D: FAIL.** No exercise-specific pump-management guidance surfaced. One result's title is the single character "i" — the most severe metadata-extraction failure found in either half of this eval. Also surfaces "Intensive Care Med (2021) 47:1181-1247" — an ICU-medicine journal citation with no plausible connection to outpatient T1D exercise management, and a pediatric-obesity-in-T1D paper that doesn't address the actual question (exercise/pump settings) despite sharing "type 1 diabetes" as a keyword.

**q09 — MGUS vs smoldering myeloma monitoring: PARTIAL PASS.** KDIGO renal guideline (relevant — monoclonal-gammopathy-associated kidney disease is a real MGUS/SMM monitoring concern, consistent with EBC case 08's judgment) plus real myeloma-management papers, though skewed toward active-disease treatment (minimal residual disease assessment, daratumumab for relapsed/refractory disease) rather than precursor-condition monitoring specifically.

**q10 — malignant ascites management in ovarian cancer: PARTIAL PASS.** The NICE mirvetuximab guideline surfaces again (5/8 slots, heavily duplicated) — genuinely relevant to ovarian cancer management broadly, but this is a treatment-line guideline, not specifically an ascites-management guideline (paracentesis, diuretics, peritoneal catheter guidance would be the more precise answer to the literal question asked). Also includes a basic-science exosome/Wnt-pathway mechanism paper with no clinical management relevance, and a blank-title PubMed entry.

## Metadata defects found in this half (extending the Evidence Based Comments findings)
- **Blank titles**: q05 and q10 both surfaced PubMed results with a literal empty-string title (year still populated). Worse than the generic "VOLUME/ISSUE" titles — genuinely no information at all.
- **Single-character title**: q08's "i" is the single worst title-extraction failure across both halves of this eval.
- **Table-legend-as-title**: q05 surfaced a chunk whose "title" is actually an abbreviation-key/legend fragment from within a document, not the document's real title — suggests the title-extraction heuristic sometimes grabs the wrong text block from a PDF page.
- **Refinement to the EBC eval's "systemic finding 3"**: the earlier hypothesis (from the Evidence Based Comments half) was that one specific chunk, "VOLUME 105 | ISSUE 4S | APRIL 2024," was suspiciously over-retrieved across unrelated cardiology cases. This QA-chat half surfaces a close-but-distinct sibling — "VOLUME 105 | ISSUE 3S | MARCH 2024" — in q07 (a nephrology query). This changes the diagnosis: it is not one specific over-retrieved chunk, but an entire family of journal-supplement documents (same volume, consecutive issue numbers, one month apart) that all share the same title-extraction defect — the article title is being dropped and only the boilerplate "VOLUME | ISSUE | MONTH YEAR" header survives. This is very likely one specific journal's supplement series (the numbering pattern is consistent with an ADA Diabetes Care-style or ACC/AHA scientific-sessions-style supplement) failing ingestion uniformly, not a ranking/embedding bug on a single chunk. Worth checking the ingestion pipeline (guidelines_fetcher.py / the weekly corpus build) against whichever specific source produces this volume/issue numbering pattern.

## Combined recommendation (both halves)
The single highest-value fix remains chunk/document deduplication in the RAG API's own top-k results or in RAGHttpClient.query()'s normalization — q02's 8-of-8 duplicate result is the starkest example across all 20 queries run for this eval (both halves combined). The title-extraction defect on one specific journal-supplement source family is the second priority — it affects both consumers, recurs across at least 3 different volume/issue numbers, and produces titles ranging from merely unhelpful ("VOLUME 105 ISSUE 4S") to broken ("i", blank string). Specific-dosing-question weakness (q01) and occasional wrong-specialty mismatches (q05's renal guideline for a PE question, q08's ICU citation for an outpatient T1D question) are lower-frequency but worth tracking as the corpus grows.
