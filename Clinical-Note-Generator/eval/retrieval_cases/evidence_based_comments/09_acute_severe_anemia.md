# Case: Acute symptomatic severe anemia with elevated troponin
case_id: 0e86968a0dc24a47... | note_type: consult | source: cases_2026-06-14.jsonl

## Clinical picture (query source)
Significant anemia (Hb 71 g/L), elevated troponin, presenting with shortness of breath/chest pain as the primary driver. top_k requested: 5.

## Results retrieved (all 5 distinct — no duplication in this case)
1. **[pubmed, score 0.599] "Compound Heterozygous Sickle Cell-Beta Thalassemia Presenting As Chronic Hemolytic Anemia With Microcytosis and Prominent Left Ventricular Trabeculation: A Case Report"** (PMC, 2026)
2. **[pubmed, score 0.525] "Beyond Hemoglobin Levels: Integrating Anemia Phenotyping Into Prognostic Models for Patients With Stroke Following Intravenous Thrombolysis"** (PMC, 2026)
3. **[web, score 0.505] "SUPPLEMENT 3 • VOL 9 • 2009"** (guidelines) — unidentifiable.
4. **[pubmed, score 0.563] "From Septic Shock to Hemorrhagic Shock: A Rare Presentation of Ischemic Rectal Ulcer in a Critically Ill Patient"** (PMC, 2026)
5. **[web, score 0.502] "Inactive ACP Guidelines"** (guidelines, year N/A)

## Relevance judgment
- Sickle cell-thalassemia case report: **TANGENTIAL.** A different, genetically distinct chronic anemia etiology; this patient's case doesn't establish a hemoglobinopathy. Low value.
- Stroke/anemia-phenotyping prognostic model paper: **TANGENTIAL.** About anemia as a prognostic factor in stroke patients specifically, not about acute severe anemia management or the cardiac strain (troponin elevation) this patient presents with.
- "SUPPLEMENT 3 VOL 9 2009": **UNJUDGEABLE**, and notably a 2009 date if genuine would make it 17 years old relative to a 2026 encounter — worth flagging for staleness regardless of topic.
- Septic-to-hemorrhagic shock rectal ulcer case report: **IRRELEVANT.** No shock, sepsis, or GI bleeding source is mentioned in this case.
- **"Inactive ACP Guidelines": FLAG — explicitly labeled inactive/superseded, and it was still surfaced as a top-5 result.** This is the clearest single example in the eval set of the corpus surfacing material it should be excluding or at minimum down-weighting.

## Case verdict: FAIL
Worst-performing case in the set. None of the five results is genuinely on-target for acute symptomatic severe anemia with cardiac strain (missing: transfusion threshold guidance, e.g. AABB/international patient blood management guidelines, or troponin-elevation-in-anemia workup guidance). One result is explicitly stale/inactive and should not have been surfaced at all.

## Case-specific rubric criteria
1. Top-5 results must include transfusion-threshold / patient-blood-management guidance (e.g. AABB) relevant to symptomatic severe anemia. **[Currently: FAIL — not surfaced]**
2. Results explicitly labeled "Inactive" in their own title/metadata must not be surfaced, or must be visibly flagged as superseded if surfaced. **[Currently: FAIL — hard defect, not a judgment call]**
3. Results should not be dominated by case reports of unrelated conditions (hemoglobinopathy, stroke, sepsis) that share only the word "anemia" with the actual clinical picture. **[Currently: FAIL]**
