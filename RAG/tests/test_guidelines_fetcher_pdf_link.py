"""P3-3 regression: find_pdf_link_in_html() must recognize PDF links that
don't literally end in ".pdf". WHO's actual repository (iris.who.int, a
DSpace/bitstream system) serves PDFs via opaque handle/bitstream URLs with
no .pdf extension in the path at all -- confirmed in production every WHO
entry in raw_docs/guidelines_who.jsonl had text="" because the old
endswith(".pdf")-only check never found a candidate to even try.
"""
from __future__ import annotations

from guidelines_fetcher import find_pdf_link_in_html


def test_finds_literal_pdf_extension_link():
    html = '<html><body><a href="/reports/guideline.pdf">Download</a></body></html>'
    assert find_pdf_link_in_html(html, "https://example.org/page") == "https://example.org/reports/guideline.pdf"


def test_finds_iris_who_int_bitstream_link_without_pdf_extension():
    html = (
        '<html><body>'
        '<a href="https://iris.who.int/bitstream/handle/10665/376642/9789240084751-eng">'
        'View publication</a>'
        '</body></html>'
    )
    link = find_pdf_link_in_html(html, "https://www.who.int/publications/i/item/9789240084751")
    assert link == "https://iris.who.int/bitstream/handle/10665/376642/9789240084751-eng"


def test_finds_relative_bitstream_link():
    html = '<html><body><a href="/server/api/core/bitstreams/abc-123/content">PDF</a></body></html>'
    link = find_pdf_link_in_html(html, "https://iris.who.int/handle/10665/376642")
    assert link == "https://iris.who.int/server/api/core/bitstreams/abc-123/content"


def test_returns_none_when_no_pdf_candidate_present():
    html = '<html><body><a href="/about-us">About</a><a href="/contact">Contact</a></body></html>'
    assert find_pdf_link_in_html(html, "https://example.org/") is None
