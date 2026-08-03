"""P3-3 regression: extract_grade() used to return the first PATTERN in
GRADE_PATTERNS to match anywhere in the chunk -- priority by list order,
not by where the match actually sits in the text. A chunk spanning two
distinct recommendations could get whichever pattern ranks higher in the
list, regardless of which recommendation it actually describes.
"""
from __future__ import annotations

from chunking_pipeline import extract_grade


def test_earlier_in_text_wins_over_higher_list_priority():
    # "grade c" / "very low" (a LOWER-priority pattern, appears LATER in
    # GRADE_PATTERNS) sits textually FIRST; "high-quality evidence" (a
    # HIGHER-priority pattern, appears EARLIER in GRADE_PATTERNS) sits
    # textually LATER, describing an unrelated point elsewhere in the chunk.
    text = (
        "The evidence quality here is grade C, very low certainty. "
        "However, elsewhere in the literature there is high-quality evidence "
        "for a completely different intervention."
    )
    strength, quality = extract_grade(text)
    assert (strength, quality) == ("conditional", "low")


def test_single_clear_recommendation_still_matches():
    # "recommend against" (earliest match, strong/moderate) correctly wins
    # over "high-quality evidence" later in the same sentence -- both are
    # about the same recommendation here, but proximity picks the phrase
    # closer to the actual recommendation verb.
    text = "We strongly recommend against this intervention based on high-quality evidence."
    strength, quality = extract_grade(text)
    assert strength == "strong"
    assert quality == "moderate"


def test_no_grade_language_returns_none():
    strength, quality = extract_grade("This chunk describes patient demographics only.")
    assert (strength, quality) == (None, None)
