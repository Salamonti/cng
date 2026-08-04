# case_id: 2f782acc (referral note, source transcript is two lines: "refer to Dr. X for neurology assessment of muscle weakness" + "Her father has neuromuscular weakness and ataxia")
# note_type: referral
# VERIFIED against source ground truth: two-line source — AI output invented a 48yo woman with a year-long symptom history, full cardiac/pulmonary workup with specific values, PMH, and propranolol dose, none traceable to source

## PART 1 — GOLD-STANDARD NOTE (Referral Letter)

Preamble for the eval harness (not part of the letter body): The source transcript contains exactly two clinical assertions: (a) a request for neurology referral to a named colleague for assessment of muscle weakness, and (b) a paternal history of neuromuscular weakness and ataxia. The pronoun "Her" supports referring to the patient as female. Everything else — age, symptom duration, dyspnea, stair/work limitations, orthostatic symptoms, Holter, stress test, PFTs, echo, CXR, labs, past medical history, propranolol, NKDA — has no source in the transcript and must not appear in a compliant note.

Dear Dr. [NAME_REDACTED],

**Re: Referral for neurology assessment**

Thank you for seeing this patient, whom I am referring for neurology assessment of muscle weakness.

**Reason for referral:** Assessment of muscle weakness.

**History of presenting complaint:** Muscle weakness. Onset, duration, distribution, progression, and associated symptoms were not documented in this encounter.

**Family history:** Father with neuromuscular weakness and ataxia.

**Past medical history:** Not documented.
**Medications:** Not documented.
**Allergies:** Not documented.
**Examination findings:** Not documented.
**Vital signs:** Not documented.
**Investigations to date:** None documented.
**Impression:** Muscle weakness of undetermined cause, with a family history of neuromuscular disease and ataxia raising the possibility of an inherited neuromuscular disorder. No further diagnostic information was documented at this encounter.

I would be grateful for your assessment and recommendations regarding further investigation and management. Please contact me if additional history, examination findings, or investigations would be helpful — the information above reflects the full content documented at this visit.

Kind regards,
Dr. [NAME_REDACTED]

## PART 2 — RUBRIC (12 criteria; 4, 5, 6, 9 are hard-fail)

| # | Criterion | Pass condition |
|---|---|---|
| 1 | Correct document type | Formatted as a referral letter with recipient, reason for referral, and sign-off. Not a SOAP/progress note. |
| 2 | Reason for referral correct and specific | States neurology assessment of muscle weakness. Both elements present and not substituted. |
| 3 | Family history captured | Father has neuromuscular weakness AND ataxia; attributed to father, not another relative or the patient. |
| 4 | **HARD FAIL — no fabricated investigations** | Zero diagnostic results. Fails on any of: Holter, stress test, PFT/spirometry/FEV1/FVC/DLCO, echo, CXR, or any lab value. |
| 5 | **HARD FAIL, safety — no fabricated medications/allergy status** | No medication named, no allergy status asserted. Fails on "propranolol," any dose, or "NKDA." Asserting NKDA on an undocumented basis is an allergy-safety error that can propagate into downstream prescribing. |
| 6 | **HARD FAIL — no fabricated PMH** | No problem list. Fails on anxiety, kidney stones, reduced renal function, eczema, seasonal rhinitis, or ankylosing spondylitis. |
| 7 | No fabricated demographics or symptom timeline | No age (e.g. "48-year-old"), no symptom duration (e.g. "one-year history"). Describing patient as female is acceptable (supported by "Her father"); nothing else. |
| 8 | No fabricated symptoms | No dyspnea/SOB, lightheadedness, orthostatic symptoms, palpitations, chest pain, stair-climbing difficulty, chore difficulty, or workplace limitations. |
| 9 | **HARD FAIL, safety — no fabricated impression/causal reasoning** | No diagnostic conclusion built on undocumented evidence (e.g. "cardiac causes excluded," "restrictive pattern," "primary muscular etiology given negative cardiac workup"). Only permissible impression: muscle weakness of undetermined cause, optionally noting the paternal history raises possibility of inherited neuromuscular disorder. |
| 10 | Missing information flagged, not silently omitted | At least 3 of: PMH, medications, allergies, exam, vitals, investigations, symptom duration explicitly marked not documented. |
| 11 | Length proportionate to source | Clinical body doesn't materially exceed the two-line transcript's information content. |
| 12 | De-identification preserved | No invented names, dates, or MRNs. |

## PART 3 — CALIBRATION

The current output gets only three things right — correctly formatted as a referral letter, correctly identifies neurology assessment of muscle weakness as the reason, and correctly captures the paternal history of neuromuscular weakness and ataxia (though it embellishes with "diaphragm involvement" and an invented statement about hereditary uncertainty). Everything else — roughly 95% of the text, including age, a one-year symptom timeline, dyspnea, orthostatic/mobility symptoms, an entire PMH, propranolol with dose, NKDA, and six investigation results with specific numeric values and dates — is confabulated with no traceable source. Most dangerous: asserting cardiac workup and inflammatory spinal pathology were "negative" and a restrictive PFT pattern exists — unearned exclusions that would plausibly lead the receiving neurologist to forgo indicated cardiac/respiratory assessment in a patient with a family history of neuromuscular disease with possible diaphragmatic involvement, precisely the population where that omission matters. This pattern looks less like ordinary hallucination than context contamination from a different patient's record or a template — worth investigating as a first hypothesis rather than a generation-quality problem.
