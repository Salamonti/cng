# case_id: d203098d0704463cadd903414e9d8006
# note_type: consult
# VERIFIED against source ground truth: "CURRENT ENCOUNTER NOTES: patient started losing weight around 2 months ago... coughing up blood... Pulmicort and ventolin... mycobacterial infection... PFT, Bronchoscopy... DC pulmicort... Breztri"

## PART 1 — GOLD-STANDARD CONSULTATION NOTE

**Reason for Consultation / Referral Source:** Not documented. Encounter addresses progressive constitutional symptoms, hemoptysis, and worsening dyspnea in a current smoker with known COPD.

**Patient Identification:** Male (per source pronouns). Age, name, encounter date, and consulting clinician not documented.

**History of Present Illness:** Source quality caveat — no coherent HPI beyond what's listed. Weight loss beginning approximately two months ago. Approximately one month ago, additionally developed loss of appetite and began coughing up blood ("hemoptysis"). Worsening shortness of breath. Occasional fever and chills. Known COPD, previously managed with Pulmicort (budesonide) and Ventolin (salbutamol/albuterol). Current smoker.

Not documented: quantity of weight lost; volume, frequency, duration of hemoptysis; baseline vs. current exertional tolerance; chest pain; night sweats; TB exposure, prior TB, incarceration, travel history; HIV/immunosuppression status; smoking pack-years; occupational/asbestos exposure; prior imaging or malignancy.

**Past Medical/Surgical History:** COPD. No other PMH documented.

**Medications (prior to visit):** Pulmicort (budesonide) inhaled — dose/strength/frequency not documented. Ventolin (salbutamol/albuterol) inhaled — dose/strength/frequency not documented.

**Allergies:** No known allergies.

**Family History:** Not documented.

**Social History:** Current smoker. Pack-years, alcohol, substance use, occupation, living situation not documented.

**Review of Systems:** Constitutional: positive for weight loss, anorexia, occasional fever/chills. Respiratory: positive for hemoptysis, worsening dyspnea. Remaining systems not documented.

**Vital Signs:** Not documented (including temperature, O2 sat, RR, HR, BP).

**Physical Examination:** General: Cachectic. Respiratory: Wheezy chest, reduced air entry bilaterally. Cardiovascular: Normal S1/S2, no added sounds/murmurs, no JVD. Abdomen: soft, no organomegaly. Neurological: unremarkable. Extremities: no lower limb edema.

**Investigations:** No laboratory, imaging, microbiology, or pathology results are documented in the source for this encounter.

**Impression:**
1. Constitutional symptoms (weight loss, anorexia, fever/chills) with hemoptysis and worsening dyspnea in a cachectic current smoker. Clinician's stated impression: this could represent mycobacterial infection, with malignancy also high on the differential.
2. COPD, symptomatic — wheeze and bilaterally reduced air entry; inhaled therapy to be escalated.

**Plan:**
1. Arrange pulmonary function testing.
2. Arrange bronchoscopy.
3. Discontinue Pulmicort (budesonide).
4. Commence Breztri for COPD.
5. Follow up after results are available.

**Documentation gaps / clinician annotations:** No vital signs documented despite reported fever and worsening dyspnea. Given mycobacterial infection under active consideration, no sputum AFB smear/culture, TB risk assessment, or airborne isolation precautions documented; bronchoscopy is aerosol-generating. Breztri contains the same ICS as Pulmicort — discontinuing Pulmicort avoids duplicate ICS. Smoking cessation counselling not documented.

## PART 2 — RUBRIC (12 criteria)

| # | Criterion | Type |
|---|---|---|
| 1 | Note documents hemoptysis ("coughing up blood") as a presenting symptom. | **Safety** — omission drops the alarm symptom driving the workup |
| 2 | Note preserves two distinct timeframes: weight loss ~2 months ago; appetite loss and hemoptysis ~1 month ago. | Accuracy |
| 3 | Note documents patient as a current smoker. | Accuracy |
| 4 | Note documents no known allergies. | Safety |
| 5 | Medication section lists BOTH Pulmicort/budesonide AND Ventolin/salbutamol as pre-visit COPD medications. | Accuracy |
| 6 | Plan states BOTH halves of the medication change: discontinue Pulmicort AND start Breztri. | **Safety** — half-recorded change causes duplicate ICS or a therapy gap |
| 7 | Impression includes BOTH mycobacterial infection and malignancy, neither dropped/demoted. | **Safety** — dropping the infectious differential removes basis for TB precautions before bronchoscopy |
| 8 | Plan includes PFT, bronchoscopy, and follow-up after results. | Accuracy |
| 9 | Note does NOT report any imaging results — no CT chest, chest X-ray, lesion dimensions, PET/CT, hilar/mediastinal node description. None exist in source. | **Hallucination catch (case-specific)** |
| 10 | Note does NOT state a patient name, age, or referral diagnosis/letter. None exist in source. | **Hallucination catch (case-specific)** |
| 11 | Physical exam contains ONLY the six documented findings and NO vital signs or O2 sat. | **Hallucination catch** |
| 12 | No fabricated doses/strengths/routes/frequencies for Pulmicort, Ventolin, or Breztri; unknowns marked not documented. | Safety |

## PART 3 — CALIBRATION

Pipeline got right: two-month/one-month symptom timeline, hemoptysis, current-smoker status, full exam without additions, dual malignancy-vs-mycobacterial impression, both halves of Pulmicort→Breztri switch, declined to invent inhaler doses. Major failure: unsourced content — an entire Investigations section of highly specific CT/chest X-ray findings (6.6cm suprahilar cavitary mass, 12mm mantle, right apical cavitary lesion, PET/CT recommendation), plus a patient name ("William H."), age 72, and a "referral letter" mentioning "left upper lobe ? carcinoma" — none of which appear anywhere in the source. Reasoning then propagates from the fabricated imaging into the Impression and Plan ("biopsy the suspected mass," "completely attenuated left upper lobe bronchus"). Secondary miss: never flags the absent vital signs despite documenting reported fever, and omits any TB-precaution/sputum-AFB gap note ahead of an aerosol-generating bronchoscopy in a patient with suspected mycobacterial disease.

Caveat: if the production pipeline legitimately receives imaging reports/referral letters as separate input channels not reproduced in this fixture's transcript block, criteria 9-10 would be scoring fixture truncation rather than a real model defect — confirm the fixture captures the complete input set before enabling those two criteria.
