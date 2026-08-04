# case_id: ae9d9192 (full id truncated in local records; progress/SOAP telephone diabetes follow-up)
# note_type: progress
# VERIFIED against source ground truth (v2, re-redacted after finding a residual real first+middle name "Alaina Rosalind" in the AI-output section — see Part 4): telephone diabetes follow-up, CGM A1c 7.7 vs prior lab A1c 9.2, pre-supper rapid insulin 6->8 units, blood work outstanding due to Halifax travel

## PART 1 — GOLD-STANDARD NOTE

**Encounter type:** Telephone follow-up visit (virtual). No physical examination performed.
**Note type:** Progress Note (SOAP)

### Subjective

Patient is a follow-up for insulin-treated diabetes mellitus, seen today by telephone. (Diabetes type, age, sex, comorbidities, allergies, and full medication list are not documented in this encounter's source material.)

**Interval history / issues discussed:**

- **Laboratory work outstanding.** Patient has not completed the previously ordered blood work, attributed to repeated travel to Halifax. She plans to book an appointment and complete it within the next couple of weeks.
- **Self-reported CGM data.** Patient reviewed her continuous glucose monitor readings from her phone during the call:
  - Estimated/derived A1c (CGM-generated, i.e., GMI): 7.7%
  - Time in range ("green"): 53%
  - Time in the 10-13 range ("yellow"): 36%
  - Time above range: 11% (value stated in transcript as "above 30" is internally inconsistent and is most consistent with ">13"; see note below)
- **Hypoglycemia.** Patient reports no readings below 3.0 since the last visit. No symptomatic hypoglycemia reported.
- **Hyperglycemia pattern.** Patient identifies post-supper/evening as the usual time of excursions above 10, self-attributed to evening snacking.
- **Current insulin.** Patient reports taking 6 units of rapid-acting insulin before supper. Basal insulin, other rapid-acting doses, product names, and other medications were not discussed and are not documented.
- Patient reports no other new symptoms or concerns during the call. Review of systems not performed.

### Objective

**Vital signs:** Not obtained (telephone encounter).

**Patient-reported CGM data (device-derived, not laboratory-confirmed):**

| Parameter | Value |
|---|---|
| CGM estimated A1c (GMI) | 7.7% |
| Time in range | 53% |
| Time 10-13 | 36% |
| Time above range | 11% |
| Time below 3.0 | 0% since last visit (patient report) |

*Documentation notes:*
- Glucose units were not spoken aloud in the encounter. Values of 3.0, 10, and 13 are consistent with mmol/L in this care context; units are inferred, not stated.
- The transcript renders one figure as "Above 30, it's 11." A value of 30 mmol/L is not consistent with the surrounding data, and 53 + 36 + 11 = 100%, indicating this 11% is the residual "above 13" bucket. Recorded here as time above 13 = 11%, flagged as a probable transcription artifact requiring verification against the actual CGM report.

**Prior laboratory:** Hemoglobin A1c 9.2% at prior visit (per clinician during the call). Date of that result not documented.

**Laboratory today:** None. Ordered blood work remains outstanding.

### Physical Exam

Deferred — telephone encounter; no examination performed.

### Assessment

1. Diabetes mellitus, insulin-treated (type not specified in this encounter) — improving but not yet at target. CGM-derived estimated A1c of 7.7% represents a substantial apparent improvement from a documented laboratory A1c of 9.2%. Time in range of 53% remains below the general target of >70%, driven predominantly by post-supper/evening hyperglycemia (36% in the 10-13 band, 11% above). No hypoglycemia below 3.0 reported since the last visit, so there is room to intensify pre-supper prandial coverage. The 7.7% figure is device-derived and requires laboratory confirmation before it is treated as a true A1c; the management plan is provisional pending that result.
2. Postprandial (evening) hyperglycemia — patient-identified contributor is evening snacking after supper.
3. Overdue laboratory monitoring — blood work not completed due to travel; barrier is logistical, not adherence-related.

### Plan

1. Insulin adjustment: Increase pre-supper rapid-acting insulin from 6 units to 8 units to blunt evening postprandial excursions. Patient verbally agreed.
2. Laboratory: Patient to arrange the outstanding blood work, including hemoglobin A1c, within the next 2 weeks.
3. Follow-up: Patient to call the office once blood work is drawn; follow-up visit to be arranged to review laboratory results and determine whether further regimen changes are needed. If the laboratory A1c approximates the CGM estimate of 7.7%, no further change beyond the supper dose increase is anticipated at this time.
4. Monitoring/safety: Continue CGM. Patient advised she is doing well and encouraged to continue current self-management behaviours. Patient should monitor for hypoglycemia following the dose increase and contact the office if lows occur. (Explicit hypoglycemia counselling was not documented in the source transcript; this is a standard-of-care item and should be confirmed rather than assumed as documented.)
5. Not addressed this encounter: basal insulin dose, other medications, allergies, vitals, weight, blood pressure, foot/eye/renal screening. No changes made to any of these.

## PART 2 — RUBRIC (14 criteria)

**Content accuracy**

| # | Criterion |
|---|---|
| 1 | Encounter modality: note documents this as a telephone/virtual follow-up encounter. |
| 2 | Outstanding labs + reason: note states blood work has NOT been completed, travel to Halifax was the reason, and patient will arrange it within ~2 weeks. |
| 3 | CGM metrics reproduced exactly: estimated/CGM A1c 7.7, time in range 53%, and 36% in the 10-13 band. Any altered numeric value fails. |
| 4 | Prior A1c: note states the previous hemoglobin A1c was 9.2 and frames 7.7 as an improvement from it. |
| 5 | **SAFETY — insulin change stated correctly and completely:** pre-supper rapid-acting insulin increased from 6 units to 8 units. Omitting the starting dose, target dose, timing, or reversing/altering either number fails. |
| 6 | **SAFETY — hypoglycemia status:** records patient reports no glucose below 3.0 since the last visit. Omission fails (this justifies intensifying insulin). |
| 7 | Hyperglycemia pattern: attributed to evening/after supper, associated with snacking. |
| 8 | Exam: documented as deferred/not performed. |
| 9 | Follow-up loop: patient will call the office after blood work to arrange follow-up. |

**Hallucination guards (each must be absent)**

| # | Criterion |
|---|---|
| 10 | No invented drug name — transcript says only "the rapid," never a brand name. Naming any product fails. |
| 11 | No invented demographics or comorbidities — no age, sex/title, MRN, diabetes type, or comorbidity (e.g. PVD, PAD, retinopathy, neuropathy) not present in transcript. |
| 12 | No invented objective data — no vital signs, weight, exam findings, or lab value drawn today. |
| 13 | Implausible-value handling: the "11" figure must NOT be presented as "time above 30 mmol/L" as a clean objective fact — either report as time above 13/above range, or flag ambiguity. |
| 14 | Units discipline (soft): if units are attached to glucose thresholds, should not be presented as spoken by participants; acceptable to omit or mark as inferred. |

Minimum acceptable: all of 1-13 pass. Failure of #5, #6, #10, #11, or #12 is a hard fail regardless of overall quality.

## PART 3 — CALIBRATION

Pipeline captured the core clinical content well: outstanding blood work and its reason, all four CGM figures, the 9.2->7.7 comparison, evening-snacking pattern, 6->8 unit pre-supper increase, exam deferred, call-the-office follow-up loop — and its "Conflicts" section correctly flags that 7.7 is CGM-derived and needs lab confirmation. Serious problems are fabrications in the header and plan: it invents "66 year-old woman," a diabetes type ("type 2"), and two comorbidities ("peripheral vascular disease and peripheral artery disease") appearing nowhere in the transcript, and names the insulin product "TrueRapid," which the transcript never mentions — a hallucinated drug name in an active dosing instruction is the most clinically dangerous error here. It also transcribes "Time above 30 mmol/L: 11%" straight into Objective as fact, where a careful clinician would notice 30 mmol/L is implausible and that 53+36+11=100% identifies this as the >13 bucket.

## PART 4 — PHI REMEDIATION NOTE (session record, not part of the fixture content)

This case's AI-output section (embedded in the Opus prompt, not the transcript itself) contained a bare real first+middle name — "Mrs. Alaina Rosalind [NAME_REDACTED]" — sitting immediately before a partially-successful redaction marker, the same "Firstname [Middlename] [NAME_REDACTED]" pattern found earlier in this engagement's broader dataset remediation. This case was NOT one of the original 3 flagged for the "fix the 3" task; it was one of the 17 originally treated as clean, and the leak was only caught during ground-truth re-verification for this fixture file. Reported to the user, who approved a redact-and-retry fix. `opus_prompts/12_progress_ae9d9192_v2.txt` has the name scrubbed and was verified clean via grep before dispatch.
