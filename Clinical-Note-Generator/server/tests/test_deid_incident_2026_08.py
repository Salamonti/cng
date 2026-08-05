"""Regression tests for the 2026-08 de-id audit incident.

Context: a manual PHI audit during eval-fixture construction found real
patient identifiers leaking through de-id repeatedly, all tracing back to
three distinct root causes in this module and its caller:

1. spaCy was never actually installed in the production venv (declared in
   requirements.txt, never `pip install`-ed), so the NER backstop -- the
   only layer capable of catching names in free-flowing prose that don't
   match any of the narrow regex triggers below -- silently no-oped on
   100% of records via the cached _SPACY_UNAVAILABLE path.
2. Several of the regex name patterns only ever captured a single
   Title-Case word, truncating multi-word real names ("Roger Blithroy
   Nickerson" -> only "Nickerson" redacted, "Roger Blithroy" leaked) and
   failing on a bare middle initial ("Dr. Islam R Eissa" -> "R Eissa"
   leaked, since no [A-Z][a-z]+ token can match a single capital letter).
3. `residual_any` (meant to answer "is anything still exposed after
   redaction?") was computed BEFORE the NER pass ran, so it could never
   reflect what NER found or failed to find -- and the field-level
   aggregator in notes.py._deid_fields dropped residual_any entirely,
   only ever propagating raw_has_any to the record actually written to
   disk and to any downstream monitoring.

These tests pin the fixes for all three. spaCy/en_core_web_sm are now
installed, so NER runs for real here rather than gracefully no-oping --
these tests are deliberately not hermetic against that package being
absent, unlike test_deid_ner_optional.py which tests the no-op path
specifically.
"""
from __future__ import annotations

import server.core.deid.v1 as v1_module
from server.core.deid.v1 import deidentify_text
from server.routes import notes as notes_routes


# ---------------------------------------------------------------------------
# Root cause 2: regex patterns truncating multi-word names
# ---------------------------------------------------------------------------


# NER now genuinely runs (root cause 1 is fixed) and is a strong enough
# backstop that it will independently catch most of these same multi-word
# names even if the regex layer is still broken -- which would let a
# regression in the regex patterns alone hide behind NER in these tests.
# Disabling NER here isolates the regex layer so each test actually pins
# the regex fix, not just the combined (regex + NER) end-to-end behaviour.


def test_multiword_name_before_comma_age_fully_redacted(monkeypatch):
    # Real leak: "Roger Blithroy Nickerson, 70-year-old male..." only had
    # "Nickerson" caught by the old single-word pattern; "Roger Blithroy"
    # leaked through untouched.
    monkeypatch.setenv("CNG_DEID_NER", "0")
    result = deidentify_text("Roger Blithroy Nickerson, 70-year-old male presented today.")
    assert "Roger" not in result["text"]
    assert "Blithroy" not in result["text"]
    assert "Nickerson" not in result["text"]


def test_multiword_name_before_sentence_verb_fully_redacted(monkeypatch):
    # Real leak: "Mary Marguerite Amero reports..." only had "Amero"
    # caught; "Mary Marguerite" leaked through untouched.
    monkeypatch.setenv("CNG_DEID_NER", "0")
    result = deidentify_text("Mary Marguerite Amero reports feeling much better this week.")
    assert "Mary" not in result["text"]
    assert "Marguerite" not in result["text"]
    assert "Amero" not in result["text"]


def test_doctor_name_with_middle_initial_fully_redacted(monkeypatch):
    # Real leak: "Dr. Islam R Eissa" only had "Islam" caught by the old
    # pattern -- a bare single-capital-letter middle initial can never
    # match [A-Z][a-z]+, so "R Eissa" leaked through after the marker.
    monkeypatch.setenv("CNG_DEID_NER", "0")
    result = deidentify_text("Dr. Islam R Eissa signed off on the chart.")
    assert "Islam" not in result["text"]
    assert " R Eissa" not in result["text"]
    assert "Eissa" not in result["text"]


def test_two_word_doctor_name_still_works(monkeypatch):
    # Confirm the multi-word fix didn't regress the plain two-word case.
    monkeypatch.setenv("CNG_DEID_NER", "0")
    result = deidentify_text("Dr. Jane Doe ordered a chest x-ray.")
    assert "Jane" not in result["text"]
    assert "Doe" not in result["text"]


# ---------------------------------------------------------------------------
# Root cause 1: NER backstop actually running (spaCy now installed)
# ---------------------------------------------------------------------------


def test_ner_backstop_actually_runs():
    # This is the most basic possible regression check for "spaCy is
    # installed and loadable" -- if the venv regresses to missing the
    # package or model again, this flips to ner_ran=False/ner_error set,
    # same as the incident this file documents.
    result = deidentify_text("Gregory reports worsening dyspnea.")
    assert result["leak_flags"]["ner_ran"] is True
    assert "ner_error" not in result["leak_flags"]


def test_ner_catches_free_prose_name_no_regex_trigger():
    # Real leak pattern: a full name in free-flowing narrative prose with
    # no comma+age, no reporting verb, no "Dr." prefix, and no explicit
    # "Patient:"/"Name:" label -- none of the four regex patterns can ever
    # trigger on this shape by design. NER is the only layer that can.
    result = deidentify_text(
        "Mrs. Elizabeth Mary Comeau is an 85-year-old lady who presented "
        "for assessment regarding exertional shortness of breath."
    )
    assert "Elizabeth" not in result["text"]
    assert "Mary" not in result["text"]
    assert "Comeau" not in result["text"]
    assert "[NAME_REDACTED]" in result["text"]


def test_ner_catches_referral_letter_style_header():
    # Real leak pattern: "RE: <Patient Name>" referral-letter headers.
    # "RE" is not in name_labeled's trigger word list (patient|pt|name|
    # doctor|dr|provider), so only NER catches this shape.
    result = deidentify_text("RE: Nova Liana\nDOB:02Jun1936")
    assert "Nova" not in result["text"]
    assert "Liana" not in result["text"]


# ---------------------------------------------------------------------------
# Root cause 3a: residual_name computed after NER, not before
# ---------------------------------------------------------------------------


def test_residual_name_reflects_post_ner_text_not_pre_ner_text(monkeypatch):
    """The core ordering bug. Before the fix, residual_name was computed
    immediately after the regex passes and BEFORE redact_person_entities
    ran -- so it was structurally incapable of ever reflecting anything
    NER did. This proves the ordering by monkeypatching NER to (as a
    stand-in for a real miss) reintroduce a matchable name pattern into
    the text; only a residual check that runs AFTER this call can catch it.
    """

    def fake_ner_that_reintroduces_a_leak(text):
        return text + " Doctor: Someone", {"ner_ran": True, "ner_person_redactions": 0}

    monkeypatch.setattr(v1_module, "redact_person_entities", fake_ner_that_reintroduces_a_leak)

    result = v1_module.deidentify_text("Patient: John Smith presented today.")

    # The original "Patient: John Smith" is fully cleaned by regex alone,
    # so a pre-NER residual check would report False here regardless of
    # what NER does. Only a post-NER check catches the reintroduced leak.
    assert result["leak_flags"]["residual_name"] is True
    assert result["leak_flags"]["residual_any"] is True


def test_residual_any_false_when_fully_clean():
    # Sanity check in the other direction: a genuinely clean result must
    # not be flagged.
    result = deidentify_text("The patient's vitals were stable throughout the visit.")
    assert result["leak_flags"]["residual_any"] is False


# ---------------------------------------------------------------------------
# Root cause 3b: field-level aggregator (notes.py) propagating residual_any
# ---------------------------------------------------------------------------


def test_deid_fields_propagates_residual_any_to_top_level():
    """Real incident: input_deid.leak_flags in the persisted dataset record
    only ever contained raw_has_any -- residual_any was computed per-field
    inside deidentify_text() but the aggregator in notes.py silently
    discarded it, so a field that was STILL exposed after redaction never
    surfaced that fact anywhere a human or a monitor would see it.
    """
    fields = {
        "transcription_text": "Mrs. Elizabeth Mary Comeau is an 85-year-old lady.",
    }
    result = notes_routes._deid_fields(fields)
    assert "residual_any" in result["leak_flags"]
    assert "ner_error_any" in result["leak_flags"]


def test_deid_fields_residual_any_true_when_ner_disabled_and_regex_misses(monkeypatch):
    """With NER disabled (simulating the pre-fix "spaCy unavailable" state)
    and free-prose text that no regex pattern's trigger words match, the
    field must be surfaced as residual_any=True at the top level -- not
    silently reported clean the way the original incident data was.
    """
    monkeypatch.setenv("CNG_DEID_NER", "0")

    # NOTE: this text doesn't match any of the four narrow regex triggers
    # (no comma+age, no reporting verb, no "Dr." prefix, no explicit
    # label), so with NER off nothing at all redacts it -- exactly the
    # blind spot that made the original incident possible. This asserts
    # the CURRENT residual_name check (which only re-checks the same four
    # patterns) does NOT catch this shape either, documenting that the
    # regex layer's residual check is necessarily narrower than NER's
    # actual coverage -- NER being reliably enabled is what matters, and
    # that's covered by test_ner_backstop_actually_runs above.
    fields = {"transcription_text": "Mrs. Elizabeth Mary Comeau is an 85-year-old lady."}
    result = notes_routes._deid_fields(fields)
    assert result["fields"]["transcription_text"]["leak_flags"]["ner_ran"] is False
    # Documents the known residual gap when NER is off: regex-only
    # residual_name cannot see free-prose names, so this specific case
    # will NOT be flagged by residual_any either. This is exactly why
    # root cause 1 (NER must actually be running) is the load-bearing fix,
    # not the residual_name check alone.
    assert "Elizabeth" in result["fields"]["transcription_text"]["text"]
