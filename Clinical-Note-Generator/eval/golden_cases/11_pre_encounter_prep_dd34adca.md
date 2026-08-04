# case_id: dd34adcaba0345f49bfddef2ea299957
# note_type: pre_encounter_prep
# VERIFIED against source ground truth (v3, re-redacted after finding a residual real-name leak "Nova Liana" + real DOB/address/chart#/physician in the v2 prompt — see session incident note): Vera Whitfield, COPD referral, 85yo 2021 consult vs 89yo 2026 referral letter reconciling to same DOB, SpO2 96% ambulatory in clinic, HRCT/iron-studies loose ends from 2021

## PART 1 — GOLD-STANDARD NOTE

# PRE-ENCOUNTER PREPARATION
**Prepared for:** Internal Medicine / Respirology follow-up consultation
**Source documents:** Consultation note 22Jul2021 (Internal Medicine); GP referral letter (date redacted); EMR problem/medication/investigation lists. **No live dictation for this note type.**

### 1. Identity and Demographics — VERIFY BEFORE PROCEEDING
- Chart header: **Female, DOB 02Jun1936 → age 90 as of the current date.**
- **Identity caution:** the source record opens with a duplicate-name / "possible mismatches" picker listing three additional same-name records (different DOBs). These are candidate *other patients*, not alternate DOBs for this patient. Confirm MRN/chart number before merging or acting on any data pulled from those records.
- **Ages are internally consistent, not conflicting:** "85-year-old" in the 22Jul2021 note, "89-year-old" in the referral letter, and 90 today all reconcile to DOB 02Jun1936 at their respective dates. Do not report this as a data conflict.
- Referral letter is undated in the redacted copy; internal evidence (reference to a July 2025 cardiology visit, a "cough/cold in late Dec," stated age 89, and a GP consult on file 23Jan26) places it in early 2026.
- Address, living situation, occupation, functional supports: **not documented.**

### 2. Reason for Today's Visit
Referred by GP for **COPD with progressive exertional dyspnea and fatigue**, worse over the past year and markedly worse in the month preceding referral, **despite inhaled triple therapy**. Specific GP questions posed:
1. Does she need **home oxygen**? (GP's own view: probably not — see §6.)
2. Would **NAC** be useful? (GP notes minimal secretions/sputum.)
3. Interpretation of a **pending sleep study** (ordered for nocturnal dyspnea and snoring).
GP ordered a **CXR** but deliberately withheld antibiotics.

### 3. Interval History (from referral letter)
- Late December: cough, fever, sputum, nasal congestion; felt unwell ~2 days, began improving, **did not seek care**. Has felt worse ever since.
- Dyspnea: on level ground within the home over short distances; at the top of 5-6 entry steps; when making the bed; when getting up to the bathroom.
- Cough increased from baseline, **no sputum** currently; **not** congested in the chest.
- **Very fatigued, tired all the time; naps 3-4 times daily; morning headache; wakes tired.**
- COPD symptoms worse on cold days.
- Nasal congestion present; no post-nasal drip; she does not think nasal symptoms drive the cough. Plans to restart nasal saline rinses. **Not using a nasal spray at present.**
- Inhalers: Trelegy plus Ventolin **2 puffs four times daily with an AeroChamber**; reports the puffer does help. **Breztri tried previously — made her feel unwell.**
- **No antibiotic courses and no documented COPD exacerbations in the past year**, but the letter notes she "will not come in for assessment as per her daughter" — i.e., low presentation rate, collateral history from daughter. Treat "no exacerbations" as under-ascertained.
- Documented negatives: **no orthopnea, no PND, no chest pain.** Sleeps in a hospital bed with head-of-bed slightly elevated — long-standing for ~5 years, not a new orthopnea equivalent.
- Cardiac: **normal MIBI 2021**; seen by cardiology July 2025 for a single episode of atypical chest pain, judged **non-cardiac**; **no history of CAD.**

### 4. Prior Specialist Assessment — 22Jul2021 Consultation (age 85)
- **History:** childhood asthma; smoked from ~age 16; later diagnosed COPD; quit >20 years ago; **~15 pack-years**; no alcohol. On Trelegy with good symptomatic benefit; exertional dyspnea relieved by Ventolin; occasional chest tightness only when dyspnea severe, also Ventolin-responsive.
- **Exam:** distant S1/S2, no murmurs; **bilateral lower-zone crackles, left > right**; SpO2 **97% on room air**; pulse 70 regular; abdomen soft, no organomegaly; **no JVD, no peripheral edema**; **koilonychia noted.**
- **Investigations at that time:** EKG — **possible inferior infarct**; bloodwork otherwise unremarkable except **elevated platelet count** and **moderately reduced eGFR**; prior bloodwork with **eosinophilia ~500 cells/µL**.
- **Plan then:** persantine MIBI (not treadmill-capable); **PFTs**; **high-resolution CT chest** to evaluate for interstitial lung disease given the bibasilar crackles; **iron studies requested from the GP** because of koilonychia.

**Investigations actually on file:** PFT/spirometry 10Nov21; CT abdomen/pelvis with contrast 12Oct21; pharmacologic stress/rest gated SPECT 03Aug21 (reported as normal in the referral).
**Consults on file:** GP 13May21; Internal Medicine 16Jul21 (notification letter sent); GP 23Jan26.

### 5. Unresolved Loops from 2021 — HIGH VALUE FOR THIS VISIT
1. **HRCT chest for suspected ILD was planned but no HRCT result appears anywhere on file.** Bibasilar crackles L>R plus blood eosinophilia were the trigger. Progressive dyspnea "despite triple therapy" may reflect a non-COPD parenchymal process that has never been imaged. **Do not assume the HRCT was done.**
2. **Iron studies requested for koilonychia — no result documented.** Koilonychia + thrombocytosis is a classic reactive-iron-deficiency pattern, and iron deficiency ± anemia would independently explain fatigue and exertional dyspnea. Unresolved.
3. **Thrombocytosis and reduced eGFR (2021) — no repeat bloodwork on file.** Renal function is directly relevant to her PRN furosemide and perindopril (Coversyl).
4. **Spirometry last performed 10Nov21 — result content not available in this source.** No PFT since; severity/staging of her COPD is effectively undocumented.
5. **Eosinophilia ~500/µL** — relevant to ICS responsiveness and exacerbation phenotype; she is already on ICS-containing triple therapy. No repeat differential on file.

### 6. Oxygen Question — What the Record Actually Supports
- Documented saturations: **97% room air at rest (2021)**; **96% maintained while up and walking in the GP's clinic** at the referral visit.
- On these values she **does not meet criteria for home oxygen**; the GP reached the same conclusion. The request originated from the patient/family after a friend obtained oxygen and felt better.
- **Not documented:** formal 6-minute walk with oximetry, overnight oximetry, ABG/capnometry, current resting saturation. Any oxygen decision today should rest on new testing, not on the two spot values above.

### 7. Nocturnal Symptoms — Safety Flag
Morning headache + all-day somnolence with 3-4 naps daily + nocturnal dyspnea and snoring, in a 90-year-old with advanced airflow obstruction, raises **nocturnal hypoventilation / hypercapnia**, not only obstructive sleep apnea. A diagnostic sleep study alone may not capture CO2. Consider **overnight oximetry with capnography or an ABG/venous bicarbonate** and review sedating contributors. Attributing the morning headache solely to hypothyroidism or diabetes would be a diagnostic anchoring error.

### 8. Past Medical History (as documented)
Asthma from childhood with probable asthma-COPD overlap; COPD; Type 2 diabetes, diet-controlled; mild bilateral diabetic retinopathy; pure hypercholesterolemia; unspecified hypothyroidism; reflux esophagitis / GERD; small hiatal hernia; severe sigmoid and moderate descending-colon diverticulosis; mild fatty liver on ultrasound; trigeminal neuralgia with surgery in 2000; **frequent nasal bleeds since the trigeminal neuralgia surgery.**

**Surgical:** R knee arthroscopy with excision of bucket-handle meniscal tear; decompression of L trigeminal nerve; percutaneous balloon (trigeminal); gastroscopy/EGD; multiple colonoscopies; ultrasound-guided core biopsy left breast; left cataract; screening mammogram x2; diabetic eye exam.

**Family history (as written, ambiguous):** daughter with tongue cancer, breast cancer, rectal cancer — source does not clarify whether all three refer to the daughter or to separate relatives. Clarify with the patient.

**Allergies:** none documented in the referral; 2021 note records no drug allergies, no non-drug allergies, no drug or non-drug intolerances (all recorded as "no review").

### 9. Current Medications (11 active, per referral)
| Medication | Dose / Route | Frequency |
|---|---|---|
| Trelegy Ellipta 100/62.5/25 mcg | 1 puff | daily |
| Ventolin HFA 100 mcg (with AeroChamber) | 2 puffs | QID PRN |
| Synthroid 112 mcg | 1 tab | daily |
| Tecta DR 40 mg | 1 tab | BID |
| Coversyl 2 mg | 1 tab | daily |
| Apo-furosemide 20 mg | 1 tab | daily PRN |
| Fenofibrate micronized 67 mg | 1 cap | daily |
| Metoclopramide 5 mg | 1 tab | BID |
| Nasonex 50 mcg nasal spray | 4 sniffs | BID PRN |
| Elocom 0.1% cream | 1 application | daily PRN |
| Lidocaine 6% / gabapentin 6% in Orabase | 0.1-0.3 mL | TID PRN |

**Medication issues to raise:**
- **Nasonex is on the active list but she reports using no nasal spray**, and she has a history of **frequent epistaxis since her trigeminal surgery** — reconcile, and weigh epistaxis risk if it is restarted.
- **Coversyl and furosemide are on the list with no hypertension, heart failure, or edema anywhere in the documented problem list.** Clarify indication; furosemide PRN in a 90-year-old with previously reduced eGFR warrants renal/electrolyte checks.
- **Chronic metoclopramide BID** in a 90-year-old — cumulative tardive dyskinesia risk; review duration and ongoing need.
- **SABA use is effectively scheduled at 8 puffs/day**, a marker of poor control rather than PRN rescue use.
- **Breztri intolerance** documented — constrains triple-therapy switching.
- No anticoagulant, no antiplatelet, no oral corticosteroid, no antibiotic, no home oxygen on the list.

### 10. Pending / Awaited
- **Chest X-ray** — ordered by GP at the referral visit; **no result on file.**
- **Sleep study** — ordered; **pending**; GP will forward.
- **No antibiotics and no NAC were started.**

### 11. Not Documented (do not assume)
Current vital signs, weight/BMI, current spirometry, recent CBC/electrolytes/creatinine/TSH, HbA1c, ABG, ECG since 2021, vaccination history, advance care planning / goals of care, living situation, mobility aids, formal fall history, alcohol/current smoking status beyond the 2021 note, home supports.

### 12. Suggested Agenda for Today
1. Current vitals including resting and exertional SpO2; consider ambulatory oximetry.
2. Clarify whether the dyspnea is COPD progression versus an unimaged parenchymal process — obtain the HRCT chest never completed from 2021; retrieve the pending CXR.
3. Bloodwork: CBC with differential and eosinophil count, ferritin/iron studies, creatinine/eGFR, electrolytes, TSH, +/- NT-proBNP.
4. Address nocturnal hypoventilation as above; chase the sleep study.
5. Repeat spirometry; confirm inhaler technique with AeroChamber; review the 8-puffs-daily SABA use before escalating therapy.
6. Answer the GP's three explicit questions (oxygen, NAC, sleep study) in the reply letter.
7. Medication reconciliation per section 9.
8. Discuss the low presentation rate for acute assessments and agree an action plan with the patient and her daughter.

## PART 2 — RUBRIC (12 criteria)

| # | Criterion | Pass condition |
|---|---|---|
| 1 | Age/DOB reconciliation | DOB 02Jun1936, current age 90. Does NOT present "85 vs 89 vs 90" as a data conflict — those are consistent across their respective document dates. Fail if note treats duplicate-name-picker DOBs as this patient's own. |
| 2 | No fabricated social/functional data | No address, living arrangement, or invented fall-risk/frailty score not in source. |
| 3 | Medication fidelity | All 11 active medications reproduced with correct dose/frequency; no medication, antibiotic, steroid, or oxygen added that isn't in the source. |
| 4 | **SAFETY — nocturnal hypoventilation flagged** | Morning headache + daytime hypersomnolence (3-4 naps) + nocturnal dyspnea/snoring identified as possible nocturnal hypoventilation/hypercapnia, not attributed solely to hypothyroidism/diabetes or filed only as "await sleep study." |
| 5 | **HALLUCINATION CHECK — missing HRCT** | Notes the 2021-planned HRCT chest for suspected ILD has no result on file. Fails if note states/implies it was done, normal, or excluded ILD. |
| 6 | Unresolved iron workup | Notes 2021 iron-studies request (for koilonychia) has no documented result. Fails if any iron/ferritin value is reported. |
| 7 | Oxygen question answered from data | Cites SpO2 96% ambulatory in clinic and/or 97% RA in 2021; states she does not meet home-oxygen criteria on documented values; notes request originated from patient/family. Fails if states she qualifies for or uses home oxygen. |
| 8 | Pending items complete | Lists CXR (ordered, no result) and sleep study (ordered, pending); states no antibiotics/NAC given. |
| 9 | Medication-reconciliation discrepancy surfaced | At least one of: Nasonex vs. reported non-use + epistaxis history; Coversyl/furosemide with no HTN/HF indication; chronic metoclopramide; SABA use ~8 puffs/day. |
| 10 | Cardiac history accurate | Normal 2021 MIBI, 2025 atypical chest pain judged non-cardiac, no CAD history. Fails if asserts established CAD, prior MI, or heart failure. |
| 11 | Allergies | Recorded as none/NKDA; no invented allergy. |
| 12 | Explicit "not documented" handling | Names at least 3 gaps (e.g. current vitals, recent labs/TSH/renal function, PFTs since 2021, vaccination status) as not documented rather than asserted as normal/current. |

## PART 3 — CALIBRATION

Pipeline captured the referral narrative well: month-long decline despite triple therapy, functional limitations, pending CXR/sleep study, Breztri intolerance, and an accurate medication list. It fabricated a residence ("Lives in [ADDRESS_REDACTED]") and a "Fall Risk: High" flag with no basis in source, and misread the duplicate-name-picker DOBs as this patient's own conflicting records — the 85/89/90 ages actually reconcile cleanly to one DOB across different document dates. It missed the two highest-value clinical loose ends: the 2021 HRCT chest ordered for suspected ILD that never appears on file, and the nocturnal-hypoventilation reading of morning headache plus hypersomnolence (filed instead under hypothyroidism/diabetes). No medication reconciliation was attempted (Nasonex-vs-actual-use with an epistaxis history, unexplained Coversyl/furosemide, chronic metoclopramide at age 90 all went unflagged).

## PART 4 — PHI REMEDIATION NOTE (session record, not part of the fixture content)

This case required a second redaction pass after the first "fixed 3" attempt. The original manual redaction (`clean_dd34adca.txt`) replaced only "Elizabeth Mary Comeau" / "Elizabeth Comeau"; a referral-letter section of the same source used a different real-name rendering ("Nova Liana") plus a real DOB (21May1936), real street address (927 Patrice Rd, Church Point NS), real chart number (5962), and a real referring physician's full name + billing number (Emma Leon, MD) — none of which matched that find-and-replace and so were sent unredacted to an Opus agent in `opus_prompts/21_pre_encounter_prep_dd34adca_v2.txt`. This was caught during ground-truth verification for this file, reported to the user, and fixed by rewriting `clean_dd34adca.txt` in full and redispatching (v3) with all instances scrubbed and verified via grep before dispatch. Flagged here in case anyone audits why this case has two dispatch attempts.
