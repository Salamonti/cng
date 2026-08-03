"""P2-5 regression: get_bm25() must not pay for a full synchronous rebuild
on the first live query after a corpus update when a fresh, matching
rebuild has already been persisted to disk by the weekly pipeline's
separate rebuild_bm25.py process.
"""
import pytest

import bm25_index


class _FakeCol:
    def __init__(self, ids, docs):
        self._ids = ids
        self._docs = docs

    def count(self):
        return len(self._ids)

    def get(self, limit=None, offset=0, include=None):
        ids = self._ids[offset:offset + limit] if limit else self._ids[offset:]
        docs = self._docs[offset:offset + limit] if limit else self._docs[offset:]
        return {"ids": ids, "documents": docs}


@pytest.fixture(autouse=True)
def _isolated_cache_and_persist_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("BM25_PERSIST_DIR", str(tmp_path))
    for key in ("ids", "docs", "bm25"):
        bm25_index._cache[key] = None
    bm25_index._cache["count"] = 0
    yield


def test_get_bm25_uses_fresh_persisted_file_instead_of_rebuilding(monkeypatch):
    col_v1 = _FakeCol(["a", "b"], ["doc a", "doc b"])
    bm25_index.warm_bm25(col_v1)  # seeds in-memory cache and persists to disk

    # Simulate the weekly pipeline's rebuild_bm25.py -- a *separate* process --
    # having already rebuilt and persisted a fresh index matching the new
    # collection size. The live process's in-memory _cache is still unaware.
    ids_v2, docs_v2 = ["a", "b", "c"], ["doc a", "doc b", "doc c"]
    bm25_index._persist(ids_v2, docs_v2, bm25_index.BM25Helper(docs_v2))
    assert bm25_index._cache["count"] == 2  # still stale in-memory

    rebuild_calls = []
    monkeypatch.setattr(
        bm25_index,
        "warm_bm25",
        lambda col: rebuild_calls.append(col) or pytest.fail("should not rebuild"),
    )

    col_v2 = _FakeCol(ids_v2, docs_v2)
    helper, ids = bm25_index.get_bm25(col_v2)

    assert ids == ids_v2
    assert not rebuild_calls


def test_get_bm25_falls_back_to_full_rebuild_when_persisted_file_is_also_stale():
    col_v1 = _FakeCol(["a", "b"], ["doc a", "doc b"])
    bm25_index.warm_bm25(col_v1)

    # No separate rebuild happened -- the persisted file on disk is still v1,
    # so the live collection (v2) genuinely has no fresh index available yet.
    col_v2 = _FakeCol(["a", "b", "c"], ["doc a", "doc b", "doc c"])
    helper, ids = bm25_index.get_bm25(col_v2)

    assert ids == ["a", "b", "c"]
    assert bm25_index._cache["count"] == 3
