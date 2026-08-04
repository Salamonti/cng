# case_id: 8b5a341fc05d4384bffdba3775aefa57
# note_type: followup

## PART 1 — GOLD-STANDARD NOTE (Follow-up)

Patient: [NAME_REDACTED], 66-year-old female (a real full name appeared bare in the source's CURRENT ENCOUNTER NOTES field, not caught by the dataset-wide de-id remediation earlier in this session -- flagged and replaced with a placeholder here before persisting this fixture).
Visit type: Follow-up. MRN/visit date: not documented.

Reason for Visit: Follow-up of dyspnea and known left hemidiaphragm paralysis.

Subjective: Actively working on weight loss and exercise (focus of today's discussion). Exercise: stepper and walking, ~once daily. Reports doing well functionally with walking and housework. Weight: previously lost 35 lb through TOPS group using portion control (small plate). Current weight/BMI/interval trend not documented. Respiratory: uses inhaler once each morning, additional dose when mowing lawn (more SOB then). Inhaler name/dose/device not documented. Not documented/discussed: orthopnea, PND, cough, sputum, chest pain, wheeze, fever, smoking, O2 use, sleep symptoms, allergies, family/social history.

Past Medical History (per source): Left hemidiaphragm paralysis (partial); dyspnea; overweight/obesity (implied by weight-loss focus).

Medications: Inhaler — morning + PRN exertion. Name/strength/dose NOT documented.

Vitals: BP not documented (field recorded, no value). Pulse 86 bpm. SpO2 93% RA. RR/temp/weight/BMI not documented.

Physical Examination: Respiratory: chest clear, adequate bilateral air entry. Cardiovascular: normal S1/S2, no added sounds/murmurs. Extremities: no LE edema. No other systems documented.

Diaphragm Assessment (reviewed and explained to patient; modality NOT documented — fluoroscopic sniff test vs. ultrasound unclear, study date not documented): Right hemidiaphragm normal, strong excursion. Left hemidiaphragm weak but NOT flat/akinetic — descends on inspiration, rises on expiration. No paradoxical motion.

Assessment:
1. Left hemidiaphragm paresis, partial, without paradoxical motion. Residual voluntary excursion preserved. Origin discussed with patient as arising from the neck (phrenic nerve); etiology not documented.
2. Exertional dyspnea — stable. Limited/exertional (lawn mowing), responsive to PRN inhaler; tolerates walking/housework. SpO2 93% RA.
3. Overweight/obesity, actively managed. 35 lb previously lost; ongoing effort. Contributing factor to dyspnea; primary therapeutic lever.
4. PRN inhaler use — underlying obstructive airway diagnosis not specified.

Plan/Counseling Provided Today:
1. CPAP discussed and NOT recommended — explained not expected to help diaphragmatic weakness.
2. Diaphragmatic plication discussed and NOT indicated — benefits only paradoxical-motion cases, not this patient's case. No surgical referral. (Transcript renders this as "pragmatic glycation" — read as diaphragmatic plication.)
3. Primary management: weight loss and exercise. Continue stepper/walking, portion control; positive reinforcement.
4. Continue current inhaler — morning + PRN exertion. Medication reconciliation needed for drug name/dose.
5. Follow-up: open access/PRN. Patient to call office; no fixed interval documented.

Documentation Gaps: BP value; inhaler name/strength; current weight/BMI; modality/date of diaphragm study; underlying cause of phrenic/diaphragm palsy. No labs, imaging, PFTs, sleep study, or referrals ordered in source.

## PART 2 — RUBRIC (12 criteria)

1. **Identity.** 66-year-old female named as above (or consistently applied placeholder). Must NOT state a different name/age. *(Hallucination catch — current pipeline output fails this.)*
2. Encounter framed as follow-up for dyspnea and left hemidiaphragm paralysis — not initial/new assessment.
3. **SAFETY — surgery not indicated.** Note documents diaphragmatic plication discussed and NOT indicated (only helps if paradoxical motion, not this case). Omission or stating surgery recommended fails.
4. **SAFETY — CPAP.** CPAP discussed and not expected to help. Must not state CPAP prescribed/ordered.
5. Diaphragm findings accurate/directional: right normal/strong; left weak but moves (down on inspiration, up on expiration), NOT paradoxical/flat/akinetic.
6. Diaphragm findings NOT miscategorized as bedside exam — must appear as reviewed study, modality flagged not documented. *(Current pipeline fails — files under "Physical Exam → Respiratory.")*
7. **SAFETY — vitals exact, none invented.** Pulse 86 bpm, SpO2 93% RA, BP explicitly not documented. No numeric BP/RR/temp invented.
8. Exam findings exact, none added: chest clear/adequate air entry, normal S1/S2 no added sounds/murmurs, no LE edema — asserts no additional findings.
9. **Medication — no drug-name hallucination.** Inhaler once daily AM + extra with mowing; drug name/dose explicitly not documented. Must NOT name a specific agent or strength/puff count. *(Case-specific hallucination catch.)*
10. Weight history correct: exercise ~once daily; prior 35 lb loss via portion control/TOPS. Must NOT present 35 lb as lost during current interval; no current weight/BMI stated.
11. Plan reflects actual disposition: weight loss/exercise primary; follow-up open-access/PRN. Must NOT invent a specific return interval.
12. No invented workup/referrals/etiology: no PFTs/imaging/diaphragm study/labs/sleep study/referral ordered today; no specific phrenic-palsy cause beyond "discussed as arising from the neck, cause not documented."

## PART 3 — CALIBRATION

Right: flagged BP not documented, refused to name inhaler, captured puffer usage pattern and 35 lb prior loss accurately. Most serious failure: **patient-identity hallucination** — renamed patient "Fern Rosalind," aged her from 66 to 68. Omitted entire substance of visit's decision-making — that CPAP won't help and plication not indicated because no paradoxical motion — the safety-relevant core of the encounter. Miscategorized diaphragm excursion findings as bedside physical exam. Inverted attribution of neck/phrenic explanation, reporting the clinician's own explanation as "patient was previously told by others."
