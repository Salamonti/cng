# Evidence Based Comments — Retrieval Eval Summary (10 real cases)

Method: 10 real cases pulled from production dataset (\`output_deid.note\`, already de-identified by the pipeline's own de-id pass). For each, the query was built using the ACTUAL production function \`build_consult_focus(note, strategy="sections")\` and queried against the real running RAG service (127.0.0.1:8007) with the same top-k capping logic \`pipeline.py\` uses (base 16, capped to 5 when focus >= 150 words). No synthetic data — this is exactly what a doctor clicking "Evidence Based Comments" on each of these real notes would have received.

## Per-case verdicts
| # | Case | Verdict |
|---|---|---|
| 01 | Nephrotic syndrome | Partial pass |
| 02 | Severe COPD | Pass |
| 03 | T1D pump management | Partial fail |
| 04 | AFib anticoagulation | Partial pass |
| 05 | Recurrent PE | Fail |
| 06 | Sarcoidosis, cardiac rule-out | Partial pass |
| 07 | CHF + AFib | Fail |
| 08 | MGUS | Pass |
| 09 | Acute severe anemia | Fail |
| 10 | Ovarian cancer, ascites | Pass |

**Score: 4 pass / 3 partial / 3 fail out of 10.** Retrieval clearly *can* work well (cases 02, 08, 10 all surfaced strong, specific, correctly-matched guidance), but is inconsistent, and three systemic defects account for most of the failures — none of which are case-specific, all three recur across multiple unrelated conditions:

## Systemic finding 1 — Severe duplicate-chunk retrieval (affects 8 of 10 cases)
In 8 of 10 cases, the top-5 result set contained the same source document repeated 2-4 times (different chunks of the same PDF, presumably), meaning the *effective* number of distinct documents surfaced per query was often only 2, not 5. This wastes the majority of the result budget and is very likely suppressing genuinely relevant material that would otherwise rank in the top 5. **This is the single highest-value fix available** — likely needs deduplication by source document (not just by chunk) either at the RAG API layer or in \`RAGHttpClient.query()\`'s result normalization.

## Systemic finding 2 — Guideline-tier results frequently carry unidentifiable titles
Multiple results across cases 01, 03, 04, 05, 06, 07, and 09 had titles like "VOLUME 108 | ISSUE 4S | OCTOBER 2025", "2017 Edition", or literally just "2026" — no society name, no topic. This makes it impossible to judge relevance (for this eval, and presumably for the doctor reading Evidence Based Comments in the actual product) without opening the source link. Worth checking whether this is a chunking/metadata-extraction gap specific to certain PDF sources (the pattern "VOLUME N | ISSUE NS | MONTH YEAR" suggests a specific journal-supplement source, likely Diabetes Care or a similar ADA-style publication, that isn't having its actual article title captured during ingestion).

## Systemic finding 3 — One specific chunk ("VOLUME 105 | ISSUE 4S | APRIL 2024") dominated 3 unrelated cases
This exact chunk was a top result in case 04 (AFib anticoagulation), case 05 (recurrent PE), and case 07 (CHF+AFib) — three cardiology-adjacent but clinically distinct queries. This *could* be a legitimately broad, relevant cardiology guideline supplement (plausible — e.g. an ACC/AHA scientific sessions issue covering multiple topics), in which case it's a correct hit each time. But it could also indicate an embedding/ranking bug where this particular chunk scores artificially high regardless of query specifics. **Cannot be resolved without fixing finding 2 first** (need the actual title to know what this document is).

## Systemic finding 4 — Stale/inactive content surfaced without flagging (case 09)
"Inactive ACP Guidelines" was surfaced as a top-5 result with no indication to the reader that it's explicitly superseded. This is a harder defect than low relevance — it's evidence being served that the source itself says should not be relied upon. Worth an explicit corpus filter to exclude or down-rank anything whose own metadata/title indicates inactive/retired/superseded status.

## Overall rubric for regression-testing future retrieval changes against this case set
1. **[Duplication]** No single source document should occupy more than 2 of 5 result slots in a query. Regression if a future run shows worse duplication than this baseline on the same 10 queries.
2. **[Metadata]** Guideline-tier results must carry a real, non-truncated title (society/publication + topic), not a bare volume/issue/year string. Track % of results with unidentifiable titles across the set as a metric — baseline here is roughly 40-50% of all result slots.
3. **[Staleness]** No result whose own title/metadata indicates "inactive," "retired," "superseded," or "withdrawn" should appear in top-k without an explicit staleness flag.
4. **[Population match]** Case reports/papers describing a materially different patient population (pediatric vs. adult, different primary diagnosis, unrelated triggering drug/condition) should not rank above genuinely on-topic guideline content based on superficial keyword overlap alone (cases 03, 05, 07, 09 all show this failure mode).
5. **[Per-case pass rate]** This baseline run: 4/10 clean pass. Track this number over time as the corpus/ranking evolves — regression if it drops below 4/10 on this same fixed case set with the same fixed queries.

## Caveats
- Query construction used no \`specialty\` hint (production passes the doctor's profile specialty; this eval didn't have a specific doctor profile to attach, so results may differ slightly from what a real doctor with a set specialty would see).
- Web-search supplementation (SearX, run in parallel in production) was NOT included in this eval — only the RAG service's own guideline/PubMed/PMC/ClinicalTrials corpus was queried. Evidence Based Comments in production also blends in live web results, which this eval doesn't capture.
- Relevance judgments above are my own clinical-domain assessment of the retrieved *metadata* (title/source/tier), not the full retrieved text chunk in every case — where the title was identifiable I judged based on it; where duplicated/unidentifiable, I flagged rather than guessed.
