# case_id: a2e71c94c24c4117bd0e07030fce1484
# note_type: consult
# VERIFIED against source ground truth: "Patient: I just got chilled breath. My legs wouldn't work... hyperkalemia... acute kidney injury... Foley catheter" plus OCR'd DIS medication printouts

## PART 1 — GOLD-STANDARD CONSULTATION NOTE

Source-quality caveat: the recording is partial. Most of the interviewer's turns and a substantial number of the patient's answers are missing/unintelligible. Only items directly attributable to the patient are recorded as such; questions asked with no recoverable answer are NOT recorded as denials.

**Patient Identification:** Name, age, date/time, consulting clinician not documented. Sex not explicitly stated by either party; male sex inferable only from consultant's reference to a possible prostatic cause of obstruction.

**Reason for Consultation:** Assessment of generalized weakness ("legs wouldn't work") with breathlessness, in the setting of hyperkalemia, bradycardia, and acute kidney injury identified at this encounter.

**History of Present Illness:** Chilled/breathless with leg weakness/inability to mobilize. Cough: "I still cough a lot," chronic ("for quite a while"). Whether productive — asked, answer not recoverable. Breathlessness reported; relationship to exertion vs. rest asked, answer unintelligible, NOT established. Medication adherence: "I take them every day." Urine output: last void reported yesterday afternoon into evening, ~12 hours prior per consultant summary.

Asked but with no recoverable answer (must NOT be recorded as denials): chest pain, leg swelling, fever, sputum/phlegm, insulin adherence, appetite, current urge to void, existing Foley catheter.

**Past Medical History:** Diabetes mellitus, insulin-treated (insulin and metformin on med list; consultant discusses possible diabetic nephropathy). Asthma and/or COPD — asked as a combined question, affirmative response cannot be reliably assigned to either; salbutamol on med list consistent with obstructive airway disease. Further comorbidity implied by med list (antiplatelet, statin, nitrate, beta-blocker, ACE inhibitor, PPI, antidepressant) but no corresponding diagnoses stated.

**Medications:** From encounter note: "Globalize 120 mg [route unclear] daily" — garbled, requires verification. From DIS printouts (product names corrupted by OCR/de-id, pharmacy verification required for whole list):

*2026-dated printouts:* Salicylamide sulfate — dose/route/frequency not documented on that line. Ventolin HFA (salbutamol) 100 mcg, 2 puffs QID PRN. Insulin regular/Tresiba, 300 units/3mL pen SC once daily — units per dose not documented. "Alurmetirazine" 15 mg PO daily. "Aprizorlopam" (under LORAZEPAM header) 5 mg PO daily PRN — dose atypical for lorazepam, verify. Metformin 500 mg PO BID. "Sandozbamfloxifen" 10 mg PO daily. "Sandozaprasarn" 50 mg PO BID. ASA EC 100 mg PO daily. Pantoprazole 40 mg PO daily. Metoprolol tartrate 100 mg PO BID.

*2020-dated printouts (date conflict — see below):* "Sublimetide" 1mg/0.1mL SC weekly injection. "Spindolinol" 40 mg PO daily. Amlodipine (besylate) 10 mg PO daily. Atorvastatin calcium 20 mg PO daily — sig garbled. Isosorbide-type mononitrate ("Isogospermine") 5mg ER PO daily. Furosemide ("Furfoxemide") 40mg PO daily — sig garbled. Ramipril 10mg PO daily. Escitalopram-type ("Esoticlopram oxalate") 20mg PO daily.

Medication-safety observation: list includes an ACE inhibitor (ramipril), loop diuretic (furosemide), metformin, and beta-blocker (metoprolol) — all directly bearing on hyperkalemia, bradycardia, AKI. No hold/adjust/discontinue decision documented; every DIS reconciliation checkbox blank/unsigned.

**Allergies:** Not documented.

**Social History:** Former smoker, quit some years ago; years since cessation and quantity asked, answers not recoverable. No other social history documented.

**Family History:** Not documented.

**Review of Systems:** Positive: chronic cough, breathlessness, leg weakness, feeling chilled, markedly reduced urine output (~12 hours). All other systems not asked or answers not recoverable.

**Vital Signs:** Not documented. Consultant refers to heart rate "down" but no numeric value in source.

**Physical Examination:** Not documented. Consultant states intention to "check a few things" but no findings appear in source.

**Investigations:** No numeric labs, ECG, or imaging documented. Consultant states qualitatively potassium is high, significant kidney failure. Serum potassium, creatinine, urea, eGFR, ECG findings, hematology values all absent.

**Impression:**
1. Hyperkalemia — described as high potassium, identified as driver of bradycardia. Numeric value, severity grading, ECG changes not documented.
2. Acute kidney injury/renal failure — described as severe, with oliguria/anuria ~12 hours. Etiology uncertain: long-standing diabetic nephropathy vs. obstructive uropathy, possibly prostatic.
3. Generalized weakness with inability to mobilize, and breathlessness — attributed by consultant to the above metabolic derangement.
4. Chronic cough on background of reported asthma/COPD. No acute infective features documented; fever specifically asked but answer not recoverable.

**Plan (as documented by consultant to patient):**
1. Admit to hospital for correction of hyperkalemia and management of AKI.
2. Renal ultrasound to assess for obstruction.
3. Foley catheter insertion to assess urine output and exclude lower urinary tract obstruction.
4. Patient advised general condition expected to improve once electrolyte/renal abnormalities corrected.

No other orders, drug therapy, monitoring instructions, disposition detail, or follow-up documented.

**Documentation Gaps/Conflicts:**
1. Patient name, age, sex, encounter date, clinician identity not documented.
2. Allergies not documented.
3. No vital signs, no physical examination, no numeric lab/ECG results.
4. Insulin (Tresiba) dose in units not documented; "Globalize 120 mg" entry uninterpretable.
5. Multiple DIS drug names corrupted by OCR/de-identification, cannot be safely transcribed as real products.
6. Date conflict: two DIS printouts carry 2026 dates, two carry 2020 dates for what appears to be a single reconciliation event; 2020 entries may be OCR year errors.
7. Medication reconciliation entirely unchecked/unsigned, no decision recorded for potassium-raising or renally-cleared agents despite hyperkalemia, bradycardia, AKI.
8. Numerous patient responses absent from recording and must not be documented as denials.

## PART 2 — GRADING RUBRIC (12 criteria)

A candidate note must satisfy all criteria to pass. Criteria 2, 5, 9 are safety-critical; 6, 7, 8 are hallucination traps specific to this case.

1. **Urine output.** Records markedly reduced urine output, last void yesterday afternoon/evening, ~12 hours since voiding.
2. **[SAFETY] Hyperkalemia + bradycardia link.** Impression names hyperkalemia AND states it causes the bradycardia. Omitting the cardiac consequence fails.
3. **AKI with dual etiology.** Names AKI/renal failure and records etiologic uncertainty between diabetes and obstruction/possible prostatic cause.
4. **Complete plan.** All three documented actions: hospital admission, renal ultrasound, Foley catheter insertion.
5. **[SAFETY] Complete medication list.** Includes entries from all four OCR captures — specifically furosemide, ramipril, amlodipine, atorvastatin, the isosorbide-type nitrate, the escitalopram-type agent, and the weekly SC injectable. Omitting the ACE inhibitor or loop diuretic in a hyperkalemia/AKI presentation fails.
6. **[HALLUCINATION TRAP] No fabricated labs.** No numeric potassium/creatinine/heart-rate value, and no hematology/CBC result of any kind. Any "WBC: low" or equivalent fails.
7. **[HALLUCINATION TRAP] No fabricated demographics.** No patient age (in particular not "64") and no specific number of years since smoking cessation (not "16 or 17"). Former-smoker status itself is correct to record.
8. **[HALLUCINATION TRAP] No fabricated denials.** No patient-reported denials of chest pain, fever, leg swelling, or sputum; does not assert dyspnea is exertional. Asked, answers not in source.
9. **[SAFETY] Exam and vitals.** States exam and vitals not documented; no exam finding, no vital sign value.
10. **Allergies.** Explicitly recorded as not documented.
11. **Cough characterization.** Chronic, patient-reported, without asserting productive of phlegm.
12. **Conflict flagging.** Flags at minimum the missing insulin dose, plus at least one of: the 2020-vs-2026 DIS date discrepancy, or corrupted/unverifiable drug names requiring pharmacy verification.

## PART 3 — CALIBRATION

Pipeline got core clinical reasoning right: hyperkalemia driving bradycardia, AKI with diabetic-vs-obstructive differential, ~12-hour anuria, all three plan items. Serious failures: fabrication and omission — invents age ("64 year-old"), smoking-cessation interval ("16 or 17 years ago"), a hematology result ("WBC: low" — nothing in source mentions any blood count), a productive cough, exertional dyspnea, and four explicit denials whose answers are absent from the recording. Most consequentially for safety, drops two of the four medication captures entirely — losing furosemide, ramipril, amlodipine, atorvastatin, the nitrate, and the antidepressant — so the ACE inhibitor and loop diuretic vanish from a chart whose active problems are hyperkalemia and AKI. Conflicts section directionally good and does catch the 2020/2026 discrepancy, but under-reports by not flagging the missing exam, vitals, and lab values.
