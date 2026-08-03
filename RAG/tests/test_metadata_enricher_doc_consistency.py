"""P3-3 regression: year/DOI/GRADE were inferred independently per chunk --
a document split into many chunks could get a different (each individually
plausible) answer per chunk for what is actually a document-level property,
since a mid-document chunk's own local text might mention an unrelated
year, citation DOI, or evidence grade phrase. Confirmed in production as
nonsense year values on some chunks of a document whose title correctly
resolved the real year on others.
"""
from __future__ import annotations

from metadata_enricher import enrich_chunk


def test_without_doc_caches_different_chunk_text_can_disagree():
    # Baseline: this is the OLD (still-supported, doc_caches=None) behavior
    # -- demonstrates why the inconsistency was possible at all.
    meta = {"doc_id": "guideline-123", "title": "Untitled guideline"}
    chunk_a = enrich_chunk("As shown in the 2019 pilot study, outcomes improved.", dict(meta))
    chunk_b = enrich_chunk("Published: 2023. This guideline supersedes prior versions.", dict(meta))
    assert chunk_a.get("year") == "2019"
    assert chunk_b.get("year") == "2023"


def test_with_doc_caches_all_chunks_of_same_document_agree():
    meta = {"doc_id": "guideline-123", "title": "Untitled guideline"}
    doc_caches = {}

    chunk_a = enrich_chunk("Published: 2023. This guideline supersedes prior versions.", dict(meta), doc_caches=doc_caches)
    # A later chunk of the SAME document, whose own local text would (on
    # its own) resolve to a different year.
    chunk_b = enrich_chunk("As shown in the 2019 pilot study, outcomes improved.", dict(meta), doc_caches=doc_caches)

    assert chunk_a.get("year") == "2023"
    assert chunk_b.get("year") == "2023"  # inherits chunk A's resolved year, not its own 2019 match


def test_doc_caches_are_scoped_per_doc_id_not_global():
    doc_caches = {}
    doc1 = enrich_chunk("Published: 2020.", {"doc_id": "doc-a", "title": "A"}, doc_caches=doc_caches)
    doc2 = enrich_chunk("Published: 2024.", {"doc_id": "doc-b", "title": "B"}, doc_caches=doc_caches)
    assert doc1.get("year") == "2020"
    assert doc2.get("year") == "2024"


def test_doi_and_grade_also_stay_consistent_across_chunks():
    meta = {"doc_id": "guideline-456", "title": "Untitled"}
    doc_caches = {}

    chunk_a = enrich_chunk(
        "This guideline (doi: 10.1234/abc.5678) makes a strong recommendation, high quality evidence.",
        dict(meta),
        doc_caches=doc_caches,
    )
    # A chunk with no DOI/grade signal of its own must inherit chunk A's.
    chunk_b = enrich_chunk("Unrelated body text with no citation markers at all.", dict(meta), doc_caches=doc_caches)

    assert chunk_a.get("doi")
    assert chunk_b.get("doi") == chunk_a.get("doi")
    if chunk_a.get("grade"):
        assert chunk_b.get("grade") == chunk_a.get("grade")
