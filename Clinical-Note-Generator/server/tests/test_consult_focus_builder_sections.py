"""Regression test: section extraction must stop at the NEXT heading, not
just a repeat of its own heading's aliases.

Before this fix, extract_section_by_heading()'s "where does this section
end" boundary regex was built only from the current section's own aliases.
A section whose heading never recurs later in the note (e.g. Impression,
which appears exactly once) had no way to detect its own end, so it
swallowed every section after it -- Plan, and anything past Plan -- instead
of stopping cleanly at the next section's heading.
"""
from server.services.consult_focus_builder import (
    extract_clinical_data_sections,
    extract_section_by_heading,
)

NOTE = """Patient ID
Jane Doe, 45F.

History of Present Illness
Patient reports chest pain for 3 days.

Past Medical History
Hypertension, T2DM.

Medications
Metformin 500mg BID.

Allergies
NKDA.

Family History
Mother has CAD.

Social History
Non-smoker.

Physical Examination
Alert, no distress.

Investigations
2026-07-10: Troponin negative.

Impression
Likely musculoskeletal chest pain, low cardiac risk.

Plan
1. I will order an ECG.
2. I will start ibuprofen.
3. I will follow up in 2 weeks.
"""


def test_impression_stops_at_plan_heading():
    body = extract_section_by_heading(NOTE, "Impression", aliases=["Assessment", "Diagnosis", "Diagnoses", "Impressions"])
    assert "musculoskeletal chest pain" in body
    assert "Plan" not in body
    assert "ibuprofen" not in body


def test_plan_runs_to_end_of_note_when_nothing_follows():
    body = extract_section_by_heading(NOTE, "Plan", aliases=["Management", "Recommendations", "Plan of Care", "Treatment Plan"])
    assert "order an ECG" in body
    assert "follow up in 2 weeks" in body


def test_extract_clinical_data_sections_splits_all_sections_cleanly():
    sections = extract_clinical_data_sections(NOTE)
    assert sections["impression"] == "Likely musculoskeletal chest pain, low cardiac risk."
    assert "ibuprofen" not in sections["impression"]
    assert "order an ECG" in sections["plan"]
    assert "Troponin negative" in sections["investigations"]
    assert "Troponin" not in sections["impression"]


def test_investigations_stops_at_impression_heading():
    body = extract_section_by_heading(NOTE, "Investigations", aliases=["Labs", "Laboratory", "Imaging", "Studies", "Pathology"])
    assert "Troponin negative" in body
    assert "musculoskeletal" not in body
