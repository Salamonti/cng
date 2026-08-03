"""P3-2 regression: deid/v1.py's regex layer must handle conversational,
lowercase ASR-transcript text and non-US date formats -- the app's actual
input source is spoken dictation, not typed/capitalized EHR text.
"""
from __future__ import annotations

from server.core.deid.v1 import deidentify_text


def test_lowercase_doctor_name_is_redacted():
    # name_doctor was the one name_* pattern missing re.IGNORECASE -- every
    # sibling pattern already handled lowercase ASR output correctly.
    result = deidentify_text("dr smith ordered a chest x-ray")
    assert "smith" not in result["text"].lower()
    assert "[NAME_REDACTED]" in result["text"]


def test_capitalized_doctor_name_still_redacted():
    result = deidentify_text("Dr. Smith ordered a chest x-ray")
    assert "Smith" not in result["text"]
    assert "[NAME_REDACTED]" in result["text"]


def test_day_first_month_name_date_is_redacted():
    # Standard Canadian/British date order -- day before the month name.
    result = deidentify_text("Follow-up scheduled for 25 January 2026.")
    assert "25 January 2026" not in result["text"]
    assert "[DATE_REDACTED]" in result["text"]


def test_ordinal_suffix_date_is_redacted():
    # Spoken "the twenty-fifth" transcribes as "25th" routinely.
    result = deidentify_text("Seen again on the 25th January 2026.")
    assert "25th January 2026" not in result["text"]
    assert "[DATE_REDACTED]" in result["text"]


def test_month_first_ordinal_date_is_redacted():
    result = deidentify_text("Started January 1st, 2026.")
    assert "January 1st, 2026" not in result["text"]
    assert "[DATE_REDACTED]" in result["text"]


def test_iso_and_numeric_dates_still_work():
    # Confirm the pre-existing formats weren't broken by the new alternation.
    result = deidentify_text("Labs drawn 2026-01-15 and follow-up 01/20/2026.")
    assert "2026-01-15" not in result["text"]
    assert "01/20/2026" not in result["text"]
