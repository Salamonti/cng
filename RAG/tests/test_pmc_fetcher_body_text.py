"""P3-3 regression: _extract_pmc_body_text() must not truncate paragraph
text at the first inline markup tag. PMC's JATS XML wraps dosages, units,
and citation markers in inline elements (<bold>, <italic>, <xref>, <sup>)
constantly -- p.text alone only captures text before the FIRST such tag.
"""
from __future__ import annotations

from pmc_fetcher import _extract_pmc_body_text


def test_paragraph_text_after_inline_markup_is_not_dropped():
    xml = (
        '<article><body><p>'
        'The patient received <bold>500 mg</bold> of amoxicillin twice daily for 7 days.'
        '</p></body></article>'
    )
    text = _extract_pmc_body_text(xml)
    assert "twice daily for 7 days" in text
    assert "500 mg" in text


def test_multiple_inline_tags_and_citation_markers_preserved():
    xml = (
        '<article><body>'
        '<p>Baseline <italic>HbA1c</italic> was <sup>a</sup>7.2% before treatment'
        '<xref ref-type="bibr" rid="R1">1</xref> and improved after 12 weeks.</p>'
        '</body></article>'
    )
    text = _extract_pmc_body_text(xml)
    assert "improved after 12 weeks" in text
    assert "7.2%" in text


def test_multiple_paragraphs_all_included():
    xml = (
        '<article><body>'
        '<p>First <bold>paragraph</bold> continues here.</p>'
        '<p>Second <italic>paragraph</italic> continues too.</p>'
        '</body></article>'
    )
    text = _extract_pmc_body_text(xml)
    assert "continues here" in text
    assert "continues too" in text


def test_falls_back_to_abstract_when_no_body_paragraphs():
    xml = (
        '<article><front><abstract>'
        '<p>This study examined <bold>outcomes</bold> in diabetic patients.</p>'
        '</abstract></front></article>'
    )
    text = _extract_pmc_body_text(xml)
    assert "outcomes" in text
    assert "diabetic patients" in text
