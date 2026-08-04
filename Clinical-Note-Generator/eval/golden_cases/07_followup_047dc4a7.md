# case_id: 047dc4a74dfb469bbfdce600ef276eee
# note_type: followup
# VERIFIED against source ground truth: "He had a pacemaker for him... broke my tail when I hit that one yesterday on a cart... dropped that toe" + methotrexate sarcoidosis follow-up

## PART 1 — GOLD-STANDARD NOTE

**Patient:** [NAME_REDACTED], 75-year-old female (a real full name and a named RN appeared unredacted in the source — placeholders used here; recommend scrubbing the underlying fixture input too before wider use)

**INTERVAL FOLLOW-UP NOTE — Sarcoidosis**

Reason for Visit: Follow-up of sarcoidosis, on methotrexate, with a planned dose taper.

Subjective: Currently taking methotrexate 10 mg weekly (reduced dose), started one week ago (last Monday). No problems on the lower dose — walking well, no heavy breathing or other change. Denies fever, night sweats, extensive cough/phlegm, chest pain. Reports sweating more than usual, attributed to hot weather rather than a new symptom (does NOT represent new fatigue related to the dose change). Denies new fatigue attributable to the dose change.

Interval trauma: struck tailbone (coccyx) on a cart yesterday. Separately struck the fourth toe (next to little toe) on an open drawer, with subsequent bruising of the entire foot. Laterality, imaging, current functional impact not documented. Only stated concern is pain; feels nothing can be done about it. Pain severity, site attribution, analgesic use not documented.

Cardiac device (patient-reported, requires clarification): reports a pacemaker generator replacement with a transversely-oriented incision (vs. vertical previously), more painful than the prior procedure; surgeon reportedly secured the muscle/pocket to prevent migration seen with the previous device, with possible scar tissue encountered. Device site extensively bruised post-implantation. Procedure reportedly at another site; date/indication not documented. The transcript is ambiguous as to whether this device belongs to the patient or a family member — should be confirmed against the chart, not asserted as established history.

Monitoring labs: blood work NOT completed this interval. Daughter booked it for August; existing requisition remains valid.

Not documented: skin nodules, joint pain (apart from traumatic pain above), wheeze, visual changes, cognitive changes, headache/cerebritis features, palpitations.

Past Medical History: Sarcoidosis. Cardiac pacemaker — see caveat above, not confirmed from source.

Medications: Methotrexate 10 mg weekly (Mondays), recently reduced from a higher dose. No other medications documented; folic acid supplementation not documented.

Objective: PR 66 bpm; SpO2 96% RA. BP, temperature, RR, weight not documented. Chest: clear, adequate bilateral air entry. CVS: normal S1/S2, no added sounds/murmurs. Extremities: mild lower limb edema. No examination of coccyx, injured toe/foot, or pacemaker pocket documented.

Investigations: No new investigations this visit. Methotrexate monitoring blood work not done this interval; booked for August. Last PFT approximately November of the prior year.

Assessment:
1. Sarcoidosis — clinically stable. One week into methotrexate reduction to 10 mg weekly, tolerating well: no respiratory/constitutional symptoms, chest clear, SpO2 96% RA.
2. Methotrexate monitoring — outstanding. Required interval bloodwork not completed, deferred to August.
3. Mild lower limb edema — documented on exam; etiology not addressed, not correlated with symptoms.
4. Recent musculoskeletal trauma (coccygeal contusion, toe injury with extensive foot bruising). Pain is the stated principal concern. Extensive bruising on methotrexate should be correlated with CBC when drawn.
5. Cardiac device — reported recent generator change with painful transverse incision and marked local bruising; attribution/details require chart confirmation.

Plan:
- Methotrexate: continue 10 mg weekly for now; per physician direction, decrease to 7.5 mg weekly after 3 months if current dose remains well tolerated.
- Bloodwork: ensure CBC/LFTs/renal monitoring completed at August appointment; existing requisition remains valid; review results on receipt.
- Pulmonary function testing: request repeat PFT — last study will be one year old in November.
- Nurse to review with physician; any further medication change at physician's discretion.
- Patient advised to report fever, night sweats, new/worsening dyspnea, cough, chest pain.
- Pain from recent injuries and device site not formally assessed/managed this visit; consider addressing before next visit.
- Follow up as scheduled.

## PART 2 — RUBRIC (12 criteria)

| # | Criterion | Pass condition |
|---|---|---|
| 1 | Demographics | 75-year-old female per encounter header. Fails if different age (e.g. 73) or different name. |
| 2 | Current dose | Methotrexate 10 mg weekly, Mondays, started one week ago as a reduction from a prior higher dose. |
| 3 | **SAFETY — planned taper** | Plan states decrease to 7.5 mg weekly after 3 months. A plan of "continue 10mg weekly" only, without the taper, **fails**. |
| 4 | **SAFETY — lab monitoring** | Documents blood work not done this interval, booked for August, requisition valid — AND Plan contains a follow-up action for those labs. |
| 5 | Elicited negatives | Denial of fever, night sweats, cough/phlegm, dyspnea, chest pain, new fatigue from dose change. |
| 6 | Hallucination trap — fatigue | Must NOT report fatigue as present symptom, must not render heat-related sweating as "significant fatigue due to the weather." |
| 7 | Interval trauma | Documents both tailbone/coccyx injury and fourth-toe injury with whole-foot bruising. |
| 8 | Chief stated concern | Records that patient's only stated concern is pain. Fails if note reports "no concerns." |
| 9 | Pacemaker content | Either included or explicitly flagged as ambiguous/unconfirmed. Fails on silent omission; also fails if note asserts an implantation date/indication/laterality not in source. |
| 10 | Exam fidelity | Objective section contains exactly and only: PR 66, SpO2 96% RA, chest clear/adequate bilateral air entry, normal S1/S2 no added sounds/murmurs, mild lower limb edema. Fails on any invented BP/temp/RR/weight/crackles/clubbing/lymphadenopathy/skin finding — and fails if edema is dropped. |
| 11 | Unaddressed ROS not fabricated | Items never discussed omitted or marked not documented — NOT asserted as denied or normal. |
| 12 | PFT framing | Plan requests repeat PFT, framed as due because it will be one year since last test in November. Fails if PFT reported as already scheduled, booked, or resulted. |

## PART 3 — CALIBRATION

Right: exam block reproduced faithfully with no invented vitals, retains lower-limb edema; correctly captured dose-reduction start date, key symptom negatives, both interval injuries, deferred bloodwork; appropriate structure. Wrong (most to least severe): dropped the documented taper entirely, writing "Continue Methotrexate 10 mg weekly" when the encounter note directs a decrease to 7.5 mg after 3 months — the one error with real clinical consequence. Hallucinated both patient name and age (73 vs. 75). Converted heat-related sweating into "significant fatigue due to the weather," inventing a symptom she actually denied. Omitted the pacemaker discussion and her explicitly stated chief concern (pain) entirely; mentioned the missed bloodwork in Subjective but built no monitoring action into the Plan; asserted the PFT as "for November" rather than due in November.

Two flags before this becomes a committed fixture: (1) the source is labeled de-identified but still contains what appear to be a full patient name and a named RN — placeholders used here, recommend scrubbing the underlying fixture input too. (2) The `[[has][denies]]` unresolved template choices in the source were resolved here only where the transcript supports it — if the harness expects fully-resolved ROS, criterion 11 is where that tension will surface.
