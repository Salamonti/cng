import os


def test_deid_ner_optional_does_not_crash_when_spacy_missing(monkeypatch):
    """NER layer should be optional; missing spaCy must not break de-ID."""

    monkeypatch.setenv("CNG_DEID_NER", "1")

    from server.core.deid.v1 import deidentify_text

    out = deidentify_text("Gregory reports worsening dyspnea.")

    assert "text" in out
    assert "redaction_counts" in out
    assert "leak_flags" in out
    # Either spaCy ran or it gracefully no-oped.
    assert "ner_ran" in out["leak_flags"]
    assert "name_ner" in out["redaction_counts"]
