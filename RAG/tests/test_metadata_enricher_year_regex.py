"""P3-3 regression: extract_year()'s regexes used r'20[0-9]{3}' -- 5 digits
total -- which can never match a real 4-digit year ("2023" is 4 chars).
Every branch of the function was affected.
"""
from __future__ import annotations

from metadata_enricher import extract_year


def test_year_found_in_title():
    assert extract_year("2023 Clinical Practice Guideline", "") == "2023"


def test_year_found_via_published_pattern():
    assert extract_year("Untitled", "Published: 2022. Some content follows.") == "2022"


def test_year_found_via_copyright_pattern():
    assert extract_year("Untitled", "Copyright © 2021 Society of Medicine") == "2021"


def test_year_found_via_guideline_year_pattern():
    assert extract_year("Untitled", "The 2024 Guidelines recommend...") == "2024"


def test_year_found_as_standalone_token_in_text():
    assert extract_year("Untitled", "This document, 2020, covers general practice.") == "2020"
