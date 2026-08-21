"""Regression test: an absence placeholder ("not documented", "none performed",
"no data available") must only strip a LINE when it IS the line's content, not
when it is a qualifier on a grounded fact like a medication.

Root cause (2026-08-17): _ABSENCE_PLACEHOLDER_RE matches the bare phrase
"not documented". DeepSeek (and any model that follows the prompt's "mark it
not documented" rule) appends "(dose not documented)" to every medication whose
dose was not stated. The guard saw "not documented" in the line but not in the
source and deleted the WHOLE medication line -- dropping the grounded med name
(amlodipine, labetalol, clonazepam, ...) and leaving only meds with a stated
dose. Direct model calls kept all meds; the post-processor stripped them.
"""
from server.core.clinical_output_guard import sanitize_clinical_note

PROMPT = (
    "SYSTEM:\nWrite a consult note.\n\n"
    "USER:\nPATIENT DATA:\n"
    "Patient reports hypertension treated with Amlodipine, Labetalol, and "
    "Clonazepam for anxiety, Furosemide for fluid, and Salbutamol. "
    "Doses are not recorded in the transcript. Also on an inhaled "
    "controller. Allergy: none known.\n\nASSISTANT:"
)


def test_dose_not_documented_qualifier_keeps_med_line():
    """A med line whose dose is "not documented" must be KEPT (the med name is
    grounded); only the bare placeholder should be dropped."""
    output = (
        "**Medications:**\n"
        "- Amlodipine (dose not documented)\n"
        "- Labetalol (dose not documented)\n"
        "- Clonazepam (dose not documented, for anxiety)\n"
        "- Furosemide (dose not documented)\n"
        "- Salbutamol 2 puffs once daily\n"
        "- Symbicort (one puff once daily)\n"
        "**Allergies:**\n"
        "- Not documented\n"
    )
    result = sanitize_clinical_note(PROMPT, output)
    for med in ["Amlodipine", "Labetalol", "Clonazepam", "Furosemide"]:
        assert med in result, f"grounded med line was wrongly stripped: {med}"
    # Meds with real stated doses must also remain.
    assert "Salbutamol 2 puffs" in result
    assert "Symbicort" in result
    # The bare absence placeholder (its own line) must still be removed.
    assert "Not documented" not in result
    # The legitimate qualifier text itself is preserved (it is not fabrication).
    assert "dose not documented" in result


def test_bare_absence_placeholder_line_still_stripped():
    """Whole-line, content-free absence placeholders are still removed."""
    output = (
        "**Plan:**\n- None performed today\n- No data available\n- Some real plan item\n"
    )
    result = sanitize_clinical_note(PROMPT, output)
    assert "None performed today" not in result
    assert "No data available" not in result
    assert "Some real plan item" in result
