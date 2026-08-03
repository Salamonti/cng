"""Note output cleanup: thinking strip, conflicts strip, age placeholders."""

import sys
from pathlib import Path

_SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(_SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(_SERVER_ROOT))

from server.routes import notes as notes_routes


def test_strip_model_reasoning_from_final_output():
    raw = "<think>hidden chain</think>\n\n## Patient Identification\n\nMr. A, 45 year-old man."
    out = notes_routes.clean_model_output_final(raw)
    assert "hidden chain" not in out
    assert "Patient Identification" in out


def test_strip_age_omitted_from_patient_identification():
    raw = (
        "## Patient Identification\n\n"
        "Mr. John Smith, [age omitted] year-old man, presented today for follow-up.\n\n"
        "## Subjective\n\nStable."
    )
    out = notes_routes.clean_model_output_final(raw)
    assert "[age omitted]" not in out.lower()
    assert "Mr. John Smith" in out


def test_strip_conflicts_section():
    text = "## Plan\n\n1. CT chest\n\n## Conflicts\n\n1. Age unclear."
    body = notes_routes.strip_conflicts_section(text)
    assert "Conflicts" not in body
    assert "CT chest" in body


def test_trim_leading_content_before_patient_identification():
    raw = "Let me draft the note.\n\n## Patient Identification\n\nMrs. B presented today."
    out = notes_routes.clean_model_output_final(raw)
    assert out.startswith("## Patient Identification") or out.startswith("Patient Identification")


def test_prune_per_system_not_documented_from_physical_exam():
    raw = (
        "## Physical Exam\n\n"
        "General appearance: Not documented.\n"
        "Cardiovascular: ESM over A2.\n"
        "Respiratory: Lungs clear on deep inspiration.\n\n"
        "## Assessment\n\nStable."
    )
    out = notes_routes.clean_model_output_final(raw)
    assert "General appearance" not in out
    assert "Cardiovascular: ESM over A2" in out
    assert "Respiratory: Lungs clear" in out


def test_keep_whole_section_physical_exam_deferred():
    raw = "## Physical Exam\n\nPhysical exam: Deferred.\n\n## Plan\n\nFollow up."
    out = notes_routes.clean_model_output_final(raw)
    assert "Physical exam: Deferred" in out


def test_remove_whole_section_physical_exam_not_documented_line():
    raw = "## Physical Exam\n\nPhysical exam: Not documented.\n\n## Conflicts\n\n1. PE incomplete."
    out = notes_routes.clean_model_output_final(raw)
    assert "Physical exam: Not documented" not in out
    assert "PE incomplete" in out
