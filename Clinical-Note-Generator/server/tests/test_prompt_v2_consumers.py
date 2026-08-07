"""Regression test: downstream consumers must handle prompt-policy-v2 notes.

Prompt policy v2 varies the plan heading per note type and tells the model to
OMIT sections it cannot support. Two consumers still hard-coded v1's fixed
headings and failed silently rather than loudly:

  * _extract_plan_section() matched only "Plan"/"Assessment & Plan"/"A/P", so
    for admission/transfer/consult notes it returned "" and the order pipeline
    short-circuited to {"status": "done", "items": []} -- indistinguishable
    from a genuinely order-free encounter. Orders & Referrals appeared to work
    and produced nothing.
  * parse_note_sections() required a leading '#', but production notes emit
    plain-text headings, so every section came back empty and patient
    materials reported "no information found" for fully documented notes.

These tests pin BOTH consumers against the heading vocabulary v2 actually
emits, so the next prompt change fails a test instead of a clinic day.
"""
from server.routes.notes import _extract_plan_section
from server.services.patient_materials_sections import parse_note_sections


# (label, plan-style heading) drawn from policy_v2 STANDARD_/OTHER_NOTE_PROMPTS
V2_PLAN_HEADINGS = [
    ("progress", "Plan"),
    ("followup", "Plan"),
    ("admission", "Admission Plan"),
    ("transfer", "Receiving-Team Plan"),
    ("consult", "Recommendations"),
    ("discharge", "Disposition"),
    ("procedure", "Follow-up"),
    ("v1_legacy", "Assessment & Plan"),
    ("v1_short", "A/P"),
    ("management", "Management"),
]


def _note_with(heading):
    return (
        "Presenting Concern\n"
        "Chest pain for three days.\n\n"
        "Assessment\n"
        "Likely musculoskeletal.\n\n"
        f"{heading}\n"
        "1. I will order an ECG.\n"
        "2. I will start ibuprofen.\n"
    )


class TestPlanExtractionAcrossNoteTypes:
    def test_every_v2_plan_heading_is_found(self):
        failures = []
        for label, heading in V2_PLAN_HEADINGS:
            extracted = _extract_plan_section(_note_with(heading))
            if "order an ECG" not in extracted:
                failures.append(f"{label} ({heading!r}) -> {extracted[:40]!r}")
        assert not failures, "plan extraction failed for: " + "; ".join(failures)

    def test_markdown_headings_still_work(self):
        note = "## Assessment\nX.\n\n## Plan\n1. I will order an ECG.\n"
        assert "order an ECG" in _extract_plan_section(note)

    def test_no_plan_heading_returns_empty_for_caller_fallback(self):
        """Callers fall back to the full note; this must stay a clean empty."""
        assert _extract_plan_section("Impression\nNo plan heading here.\n") == ""


class TestPatientMaterialSectionsAcrossNoteTypes:
    def test_plain_text_headings_parse(self):
        """Production notes have no '#'. Requiring one emptied every section."""
        sections = parse_note_sections(
            "History of Present Illness\nChest pain.\n\n"
            "Medications\nMetformin 500mg BID.\n\n"
            "Impression\nMusculoskeletal pain.\n\n"
            "Plan\n1. I will order an ECG.\n"
        )
        assert "Metformin" in sections.get("medications", "")
        assert "Musculoskeletal" in sections.get("assessments", "")
        assert "ECG" in sections.get("plan", "")

    def test_v2_note_type_headings_parse(self):
        sections = parse_note_sections(
            "Presenting Concern\nChest pain.\n\n"
            "Medications and Allergies\nRamipril 5mg.\n\n"
            "Assessment\nStable angina.\n\n"
            "Admission Plan\n1. Admit to medicine.\n"
        )
        assert "Ramipril" in sections.get("medications", "")
        assert "angina" in sections.get("assessments", "")
        assert "Admit" in sections.get("plan", "")

    def test_markdown_headings_still_parse(self):
        sections = parse_note_sections("## Medications\nAspirin 81mg.\n\n## Plan\n1. Continue.\n")
        assert "Aspirin" in sections.get("medications", "")

    def test_bold_markdown_headings_parse(self):
        """Production v8 notes emit **Bold:** section headings; unwrap before matching."""
        sections = parse_note_sections(
            "**History of Present Illness:**\n6 weeks dry cough and fatigue.\n\n"
            "**Physical Examination:**\nMild expiratory wheeze.\n\n"
            "**Assessment:**\nAsthma.\n\n"
            "**Plan:**\n1. Fluticasone inhaler.\n"
        )
        assert "Asthma" in sections.get("assessments", ""), sections
        assert "Fluticasone" in sections.get("plan", ""), sections
        assert "cough" in sections.get("hpi", ""), sections

    def test_bold_join_markdown_headings_parse(self):
        """Combined '## **Assessment**' and '**Assessment/Impression**' forms."""
        sections = parse_note_sections(
            "## **Assessment/Impression**\nCOPD exacerbation.\n\n"
            "**Medications and Allergies**\nAmoxicillin.\n"
        )
        assert "COPD" in sections.get("assessments", "")
        assert "Amoxicillin" in sections.get("medications", "")

    def test_content_lines_are_not_mistaken_for_headings(self):
        """The original '#'-required guard existed to stop this; keep it true."""
        sections = parse_note_sections(
            "Medications\nMetformin 500mg BID.\nWeight: 85kg\nBP: 130/80\n"
        )
        assert "weight" not in sections
        assert "Weight: 85kg" in sections.get("medications", "")
