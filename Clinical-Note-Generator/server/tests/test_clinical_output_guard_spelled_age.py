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


def test_fabricated_age_is_still_rejected():
    """The safety property must survive the fix."""
    result = validate_clinical_note(_source("forty-five"), _NOTE_TEMPLATE.format(age=92))
    assert not result.accepted
    assert any("unsupported patient age" in reason for reason in result.reasons)


def test_spelled_number_tokens_expands_both_forms():
    assert "45" in _spelled_number_tokens("a forty-five year old")
    assert "72" in _spelled_number_tokens("seventy two years")
    assert "19" in _spelled_number_tokens("nineteen")
    assert _spelled_number_tokens("no numbers here") == set()
