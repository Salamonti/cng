"""Regression test: a spoken age transcribed as words must not reject the note.

The age grounding check compares digits in the note ("45-year-old") against
number tokens in the source. Whisper renders a spoken age either as digits or
as words depending on context, so before this fix a transcript containing
"forty-five year old" had no "45" token and the guard rejected an entirely
correct note — the same class of false-reject that took note generation down
on 2026-08-01. Expanding the source side with word->digit equivalents keeps
the check's real purpose (catching a fabricated age) intact.
"""
from server.core.clinical_output_guard import (
    _spelled_number_tokens,
    validate_clinical_note,
)

_NOTE_TEMPLATE = (
    "Patient ID\n"
    "A {age}-year-old man.\n\n"
    "History of Present Illness\n"
    "Reports chest pain today.\n"
)


def _source(age_text):
    return (
        "PATIENT DATA:\n"
        f"Patient is a {age_text} year old man with chest pain, "
        "seen today for evaluation.\n"
    )


def test_hyphenated_spelled_age_is_accepted():
    result = validate_clinical_note(_source("forty-five"), _NOTE_TEMPLATE.format(age=45))
    assert result.accepted, result.reasons


def test_space_separated_spelled_age_is_accepted():
    result = validate_clinical_note(_source("seventy two"), _NOTE_TEMPLATE.format(age=72))
    assert result.accepted, result.reasons


def test_single_word_age_is_accepted():
    result = validate_clinical_note(_source("nineteen"), _NOTE_TEMPLATE.format(age=19))
    assert result.accepted, result.reasons


def test_digit_age_still_accepted():
    result = validate_clinical_note(_source("45"), _NOTE_TEMPLATE.format(age=45))
    assert result.accepted, result.reasons


def test_ocr_run_together_age_is_accepted():
    """Faxed charts lose the space: "71year old". \\b could not see the digits."""
    source = "PATIENT DATA:\nShe is a 71year old female with leukocytosis.\n"
    result = validate_clinical_note(source, _NOTE_TEMPLATE.format(age=71))
    assert result.accepted, result.reasons


def test_age_mismatch_is_non_fatal():
    """An age the source does not support is LOGGED, never a rejection.

    Deliberate contract change (2026-08-06) after the guard blocked a second
    production note. Every "is this fact in the source?" heuristic runs on
    messy OCR/ASR text and is too unreliable to withhold a whole encounter's
    note over; clinicians review and sign every note. Fatal is reserved for
    model FAILURE modes that are evident from the output alone -- those are
    covered by test_true_failure_modes_still_fatal below.
    """
    result = validate_clinical_note(_source("forty-five"), _NOTE_TEMPLATE.format(age=92))
    assert result.accepted, "age grounding must not block a note"


def test_true_failure_modes_still_fatal():
    """Demoting grounding checks must not soften real degeneration checks."""
    source = _source("forty-five")
    for label, draft in (
        ("empty", ""),
        ("reasoning leak", "<think>planning</think>\nA note."),
        ("meta commentary", "I will now write the note. Here is the note."),
        ("placeholder", "Patient is a XX-year-old [NAME] with issues."),
        ("ngram runaway", "the patient reports ongoing severe pain today " * 12),
    ):
        result = validate_clinical_note(source, draft)
        assert not result.accepted, f"{label} should still be fatal"


def test_spelled_number_tokens_expands_both_forms():
    assert "45" in _spelled_number_tokens("a forty-five year old")
    assert "72" in _spelled_number_tokens("seventy two years")
    assert "19" in _spelled_number_tokens("nineteen")
    assert _spelled_number_tokens("no numbers here") == set()
