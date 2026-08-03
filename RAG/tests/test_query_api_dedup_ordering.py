"""P3-3 regression: hybrid_search_filtered() used to truncate to top_k
BEFORE applying the per-document dedup cap -- if the top-scoring candidates
happened to cluster on a couple of documents, dedup then had nothing left
to backfill from, so a query came back with FEWER than top_k results even
though the larger candidate pool (dense_n ~= top_k * 4) had other
genuinely relevant documents sitting just past the truncation cut.

hybrid_search_filtered() itself needs a live Chroma collection + embedder
to invoke directly, so this exercises the exact same reusable function
(dedupe_and_normalize_hits) and the exact same ordering the fix applies --
dedupe the full candidate pool, THEN truncate -- against the old ordering,
to prove the new order is what actually preserves result count/diversity.
"""
from __future__ import annotations

from utils_meta import dedupe_and_normalize_hits


def _candidate_pool():
    # 8 candidates, sorted by score descending (as hybrid_search_filtered
    # produces): the top 4 are all from doc "A", the rest spread across
    # docs B, C, D, E. A realistic case: one very-well-matched document
    # contributes several strong chunks, but other documents are still
    # genuinely relevant.
    pool = []
    for i in range(4):
        pool.append({"id": f"a{i}", "text": f"chunk a{i}", "metadata": {"doc_id": "A"}, "score": 0.9 - i * 0.01})
    for i, doc in enumerate(["B", "C", "D", "E"]):
        pool.append({"id": f"{doc.lower()}0", "text": f"chunk {doc}", "metadata": {"doc_id": doc}, "score": 0.7 - i * 0.02})
    return pool


def test_dedup_before_truncate_preserves_result_count():
    pool = _candidate_pool()
    top_k = 4
    max_per_doc = 2

    # NEW (fixed) order: dedupe the full pool first, THEN truncate.
    deduped_then_truncated = dedupe_and_normalize_hits(pool, max_per_doc=max_per_doc)[:top_k]
    assert len(deduped_then_truncated) == top_k
    doc_ids = [h["metadata"]["doc_id"] for h in deduped_then_truncated]
    # Genuinely diverse -- not all 4 slots eaten by document A.
    assert doc_ids.count("A") <= max_per_doc
    assert len(set(doc_ids)) > 1


def test_truncate_before_dedup_was_the_old_bug():
    pool = _candidate_pool()
    top_k = 4
    max_per_doc = 2

    # OLD (buggy) order: truncate to top_k first, THEN dedupe.
    truncated_then_deduped = dedupe_and_normalize_hits(pool[:top_k], max_per_doc=max_per_doc)
    # Demonstrates the bug this fix addresses: the top 4 candidates were
    # all document A, so deduping AFTER truncation drops 2 of them with
    # nothing left in the (already-truncated) list to backfill from --
    # final result count shrinks below top_k even though 4 other relevant
    # documents were sitting in the larger pool.
    assert len(truncated_then_deduped) < top_k
