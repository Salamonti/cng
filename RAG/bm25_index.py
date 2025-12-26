# C:\RAG\bm25_index.py
# bm25_index.py
import json
import os
import re
from typing import List, Tuple

from rank_bm25 import BM25Okapi

# --- simple tokenization ---
def _tokens(s: str) -> List[str]:
    return re.findall(r"[A-Za-z0-9]+", s.lower())

# --- a tiny helper around BM25Okapi ---
class BM25Helper:
    def __init__(self, docs: List[str]):
        self.docs = docs
        self.tokens = [_tokens(d) for d in docs]
        self.bm25 = BM25Okapi(self.tokens)
        self._max = 1.0  # avoid div by zero

    def scores(self, query: str) -> List[float]:
        q = _tokens(query)
        arr = self.bm25.get_scores(q).tolist()
        m = max(arr) if arr else 0.0
        if m > self._max:
            self._max = m
        return arr

    def normalize(self, raw: float) -> float:
        return (raw / self._max) if self._max else 0.0

# --- global cache to avoid rebuilds & circular imports ---
_cache = {
    "ids": None,       # type: List[str] | None
    "docs": None,      # type: List[str] | None
    "bm25": None,      # type: BM25Helper | None
    "count": 0,
}


def _persist_path() -> str:
    base_dir = os.environ.get("BM25_PERSIST_DIR", "./chroma_store")
    os.makedirs(base_dir, exist_ok=True)
    return os.path.join(base_dir, "bm25_index.json")


def _load_persisted() -> bool:
    path = _persist_path()
    if not os.path.exists(path):
        return False
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        ids = data.get("ids") or []
        docs = data.get("docs") or []
        max_score = data.get("max_score", 1.0)
        helper = BM25Helper(docs)
        helper._max = max_score
        _cache["ids"] = ids
        _cache["docs"] = docs
        _cache["bm25"] = helper
        _cache["count"] = len(ids)
        return True
    except Exception:
        return False


def _persist(ids: List[str], docs: List[str], helper: BM25Helper) -> None:
    path = _persist_path()
    try:
        data = {
            "ids": ids,
            "docs": docs,
            "max_score": helper._max,
        }
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
    except Exception:
        pass

def warm_bm25(col) -> Tuple[BM25Helper, List[str]]:
    """Load ALL ids+docs from chroma (ok for small corpora) and build BM25."""
    res = col.get()  # if your corpus grows, persist this at ingest time instead
    ids = res.get("ids", [])
    docs = res.get("documents", [])
    helper = BM25Helper(docs)
    _cache["ids"] = ids
    _cache["docs"] = docs
    _cache["bm25"] = helper
    _cache["count"] = len(ids)
    _persist(ids, docs, helper)
    return helper, ids

def get_bm25(col) -> Tuple[BM25Helper, List[str]]:
    """Return a ready BM25 + aligned id list; rebuild if cache is empty or size changed."""
    if _cache["bm25"] is None or not _cache["ids"]:
        if _load_persisted():
            return _cache["bm25"], _cache["ids"]
        return warm_bm25(col)
    # sanity: if collection size changed (e.g., re-ingest), rebuild
    current = col.count() if hasattr(col, "count") else None
    if current is not None and current != _cache["count"]:
        return warm_bm25(col)
    return _cache["bm25"], _cache["ids"]
