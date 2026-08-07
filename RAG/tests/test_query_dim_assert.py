"""STEP 9a tests: embedding dimension drift protection.

Regression: RAG had no query-time assertion that the embedder's output
dimension matches the stored corpus dimension. A changed EMBEDDER_DIM /
matryoshka truncation / swapped embedder would either fail with an obscure
Chroma error deep in col.query() or silently search a wrong-dim collection.
"""
import numpy as np
import pytest


class _FakeCol:
    """Minimal Chroma-collection stand-in exposing get() with embeddings."""

    def __init__(self, stored_dim):
        self._stored = stored_dim

    def get(self, limit=1, include=None, **kwargs):
        if self._stored is None:
            return {"embeddings": []}
        return {"embeddings": [np.zeros(self._stored, dtype=np.float32)]}


def _qvec(dim):
    return np.zeros(dim, dtype=np.float32)


def test_dim_assert_ok_when_matching():
    from query_api import _assert_query_dim

    col = _FakeCol(stored_dim=1024)
    # matching dims -> no raise
    _assert_query_dim(col, _qvec(1024), "m/harrier")


def test_dim_assert_raises_on_mismatch():
    from query_api import _assert_query_dim

    col = _FakeCol(stored_dim=1024)
    with pytest.raises(RuntimeError, match="1024"):
        _assert_query_dim(col, _qvec(768), "m/harrier")


def test_dim_assert_allows_empty_collection():
    from query_api import _assert_query_dim

    col = _FakeCol(stored_dim=None)
    # empty collection -> allowed through (first-time index)
    _assert_query_dim(col, _qvec(1024), "m/harrier")


def test_collection_embedding_dim_parses_vector():
    from query_api import _collection_embedding_dim

    assert _collection_embedding_dim(_FakeCol(stored_dim=512)) == 512
    assert _collection_embedding_dim(_FakeCol(stored_dim=None)) is None
