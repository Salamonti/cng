"""Regression test: de-identification markers must NOT be treated as model
placeholder demographics.

The de-id pipeline (server/core/deid/v1.py) replaces names/dates/MRNs/phones/
emails with bracketed markers: [NAME_REDACTED], [DATE_REDACTED], [MRN_REDACTED],
[PHONE_REDACTED], [EMAIL_REDACTED]. The model faithfully reproduces these
markers in the note (the patient's name is genuinely redacted upstream).

Before the fix, _PLACEHOLDER_RE matched [NAME_REDACTED] (its [name...] branch,
where [^\\]]* swallowed "_REDACTED") and flagged it as FATAL "placeholder
demographic content" -- rejecting clinically-sound notes that simply carried
the de-id markers. Two of the 20 golden cases (12, 18) were fully blocked this
way. The (?!_REDACTED) lookahead excludes the pipeline markers while still
catching genuine model-generated stubs like [name], [age], [DOB], [patient name].
"""
from server.core.clinical_output_guard import validate_clinical_note

PROMPT = (
    "SYSTEM:\nWrite a consult note.\n\n"
    "USER:\nPATIENT DATA:\n"
    "Patient: [NAME_REDACTED], 43-year-old male\n"
    "Transcription: patient reports bilateral lower extremity edema\n\nASSISTANT:"
)


def test_deid_markers_are_not_placeholder_rejections():
    """A note carrying the de-id pipeline markers must be accepted."""
    output = (
        "**Patient:** [NAME_REDACTED], 43-year-old male\n"
        "**Date:** [DATE_REDACTED]\n\n"
        "**Reason for Consultation:** Evaluation of new-onset edema.\n\n"
        "**History of Present Illness:** Patient reports bilateral lower "
        "extremity edema over two weeks.\n\n"
        "**Medications:**\n*   Furosemide 40 mg PO daily\n\n"
        "**Plan:**\n1.  Order basic metabolic panel.\n2.  Follow up in 2 weeks."
    )
    result = validate_clinical_note(PROMPT, output)
    assert result.accepted, f"de-id markers wrongly rejected: {result.reasons}"


def test_all_deid_marker_types_pass():
    """Every de-id marker type must be tolerated."""
    output = (
        "Patient: [NAME_REDACTED]. MRN: [MRN_REDACTED]. "
        "Phone: [PHONE_REDACTED]. Email: [EMAIL_REDACTED]. "
        "DOB: [DATE_REDACTED]. Reason: follow-up visit."
    )
    result = validate_clinical_note(PROMPT, output)
    assert result.accepted, f"de-id markers wrongly rejected: {result.reasons}"


def test_model_generated_stubs_still_rejected():
    """Genuine model placeholder stubs must STILL be caught."""
    for stub in [
        "Patient: [name], [age]-year-old. Reason: [patient condition].",
        "Patient: John, DOB: [DOB]. Reason: cough.",
        "Patient: [patient name], 50yo. Reason: pain.",
        "Age: [age]. Sex: [sex]. Reason: dizziness.",
    ]:
        result = validate_clinical_note(PROMPT, stub)
        assert not result.accepted, f"model stub not rejected: {stub!r}"
        assert "placeholder demographic content detected" in result.reasons


def test_name_redacted_with_suffix_still_passes():
    """[NAME_REDACTED] with trailing context (e.g. a surname fragment the
    de-id left) must not be mistaken for a model stub."""
    output = "Zane [NAME_REDACTED], 43-year-old male. Reason: edema."
    result = validate_clinical_note(PROMPT, output)
    assert result.accepted, f"de-id marker with context wrongly rejected: {result.reasons}"
