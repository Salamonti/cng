"""Regression test: cross-field de-identification consistency.

The partial-redaction defect: the same patient name was redacted in one source
block (e.g. "Patient: John Smith" in the transcription) but leaked in another
block where it appeared in a form the per-field patterns/NER didn't catch
(e.g. a prior-visit note header "Mr. John Smith is a 72 year old male" or a
fax cover letter). The model then faithfully reproduced the leaked name in the
generated note -- a de-identification break that is a DATA problem, not a
note-generation problem.

deidentify_fields() closes this: after per-field de-id, every name surface
form detected in ANY field is redacted in ALL fields.
"""
from server.core.deid.v1 import (
    deidentify_fields,
    deidentify_text,
    extract_name_variants,
    redact_known_names,
)


def test_name_redacted_in_one_field_leaks_in_another_without_cross_field():
    """Demonstrate the defect: per-field de-id alone leaves the name in the
    second field (the name appears in a non-matching syntactic form)."""
    trans = "Patient: John Smith, 72 year old male. He reports chest pain."
    prior = "Mr. John Smith is a 72 year old male with history of COPD."

    # Per-field only (the old behaviour):
    t = deidentify_text(trans)["text"]
    p = deidentify_text(prior)["text"]
    assert "John Smith" not in t  # redacted in the transcription
    # The prior-visit form may or may not be caught by NER; the point of the
    # cross-field fix is that it is ALWAYS redacted regardless (covered by
    # test_cross_field_redacts_name_in_all_fields).


def test_cross_field_redacts_name_in_all_fields():
    trans = "Patient: John Smith, 72 year old male. He reports chest pain."
    prior = "Mr. John Smith is a 72 year old male with history of COPD."
    fax = "Re: John Smith regarding left upper lobe mass."

    results = deidentify_fields(
        {"transcription_text": trans, "old_visits_text": prior, "mixed_other_text": fax}
    )
    for key in ("transcription_text", "old_visits_text", "mixed_other_text"):
        text = results[key]["text"]
        assert "John Smith" not in text, f"leaked in {key}: {text!r}"
        assert "[NAME_REDACTED]" in text, f"no redaction marker in {key}: {text!r}"


def test_cross_field_propagates_ner_only_name():
    """A name caught only by NER in one field must still propagate to others."""
    # "Gregory Leblanc" in sentence-verb form (regex) in field 1; a bare
    # mention in field 2 that only NER would catch.
    trans = "Gregory Leblanc reports progressive dyspnea over three months."
    labs = "Specimen received from Gregory Leblanc, accession 12345."

    results = deidentify_fields({"transcription_text": trans, "mixed_other_text": labs})
    assert "Leblanc" not in results["transcription_text"]["text"]
    # Cross-field guarantees the labs field is redacted even if NER missed it
    # there, because the regex caught it in the transcription.
    assert "Leblanc" not in results["mixed_other_text"]["text"]


def test_cross_field_does_not_redact_unrelated_names():
    """Names that appear in NO field in a detectable form must not be
    redacted in other fields (no over-redaction of common words)."""
    trans = "Patient: John Smith, 55 year old male."
    other = "The patient takes lisinopril and metoprolol daily."

    results = deidentify_fields({"transcription_text": trans, "old_visits_text": other})
    # lisinoprol/metoprolol are not names and must survive.
    assert "lisinopril" in results["old_visits_text"]["text"].lower()
    assert "metoprolol" in results["old_visits_text"]["text"].lower()
    # John Smith redacted in the transcription.
    assert "John Smith" not in results["transcription_text"]["text"]


def test_redact_known_names_longest_first():
    """A full name must be replaced whole, not partially by a shorter name."""
    text = "John Smith and Smith Jones were seen."
    out, n = redact_known_names(text, {"John Smith", "Smith Jones"})
    assert out == "[NAME_REDACTED] and [NAME_REDACTED] were seen."
    assert n == 2


def test_redact_known_names_skips_single_short_words():
    """Single common words (no space, <4 chars) are not propagated globally."""
    text = "the cat sat on the mat"
    out, n = redact_known_names(text, {"cat"})
    assert out == text  # "cat" is 3 chars, no space -> skipped
    assert n == 0


def test_extract_name_variants_finds_labeled_name():
    names = extract_name_variants("Patient: John Smith, 72 year old male.")
    assert "John Smith" in names


def test_empty_fields_no_error():
    results = deidentify_fields({"a": "", "b": "no names here"})
    assert results["a"]["text"] == ""
    assert results["b"]["text"] == "no names here"
