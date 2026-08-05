# Case: Ovarian cancer with recurrent ascites
case_id: 7eed06b829d74a1e... | note_type: consult | source: cases_2026-06-18.jsonl

## Clinical picture (query source)
Known history of ovarian cancer, currently experiencing recurrent significant abdominal ascites. top_k requested: 5.

## Results retrieved (deduplicated by title)
1. **[pubmed, score 0.556] "Low-grade appendiceal mucinous neoplasms combined with incidental gallbladder cancer: a case report"** (PMC, 2026) — 1 slot.
2. **[guideline, score 0.656] "Mirvetuximab soravtansine for treating folate receptor-alpha-positive platinum-resistant epithelial ovarian, fallopian tube or primary peritoneal cancer"** (NICE) — 3 of 5 slots.
3. **[pubmed, score 0.551] "Opioid-Induced Nausea and Vomiting in Patients With Cancer: A Narrative Review"** (PMC, 2026) — 1 slot.

## Relevance judgment
- NICE mirvetuximab guideline: **RELEVANT.** Directly on-target — this is current, named, real guidance for recurrent/platinum-resistant epithelial ovarian cancer, exactly the population this patient is in. Best-identified single result in the entire eval set (full, specific, real guideline title).
- Appendiceal neoplasm/gallbladder cancer case report: **IRRELEVANT.** Different primary cancer entirely; no clear connection to this patient's ovarian cancer/ascites presentation beyond both being abdominal malignancies.
- Opioid-induced N/V review: **TANGENTIAL, but plausibly useful.** Not specific to ascites/ovarian cancer, but general cancer-symptom-management content could be marginally relevant if this patient is on opioids for pain — not confirmed from the focus text alone.

## Case verdict: PASS
Strongest, most identifiable single hit in the whole eval set (NICE mirvetuximab), correctly matched to a specific, current, named guideline for the patient's exact cancer type and treatment-resistance status. Demonstrates the corpus/retrieval CAN work very well when it works.

## Case-specific rubric criteria
1. Top-5 results must include current, named guidance specific to the patient's cancer type and treatment history (platinum-resistant recurrent ovarian cancer). **[Currently: PASS — strongest hit in the set]**
2. Results should not surface case reports of an entirely different primary malignancy based on superficial "abdominal cancer" overlap. **[Currently: FAIL for the appendiceal/gallbladder result]**
