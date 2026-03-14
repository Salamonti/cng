# retriever.py
# retriever.py
from collections import OrderedDict
from typing import List, Dict, Any, Optional, Tuple
from contextlib import nullcontext
from concurrent.futures import ThreadPoolExecutor
import copy
import numpy as np
from bm25_index import get_bm25
from metrics import RequestMetrics, get_current_metrics

MIN_SIM = 0.15
HYBRID_LAMBDA = 0.20  # 10% lexical, 90% semantic
_RESULT_CACHE: "OrderedDict[Tuple[str, int, int], List[Dict[str, Any]]]" = OrderedDict()
_CACHE_MAX = 128

def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    na = np.linalg.norm(a)
    nb = np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))

def _measure(name: str, metrics: Optional[RequestMetrics]):
    if metrics:
        return metrics.measure(name)
    return nullcontext()


def _cache_key(query: str, k: int, corpus_version: int) -> Tuple[str, int, int]:
    return (query.strip(), k, corpus_version)


def _get_cached(key: Tuple[str, int, int], metrics: Optional[RequestMetrics]) -> Optional[List[Dict[str, Any]]]:
    hits = _RESULT_CACHE.get(key)
    if hits is not None:
        _RESULT_CACHE.move_to_end(key)
        if metrics:
            metrics.record_counter("cache_hit", 1)
        return copy.deepcopy(hits)
    return None


def _store_cache(key: Tuple[str, int, int], hits: List[Dict[str, Any]]) -> None:
    _RESULT_CACHE[key] = copy.deepcopy(hits)
    _RESULT_CACHE.move_to_end(key)
    while len(_RESULT_CACHE) > _CACHE_MAX:
        _RESULT_CACHE.popitem(last=False)


def search(
    col,
    embedder,
    query: str,
    k: int = 5,
    metrics: Optional[RequestMetrics] = None,
    corpus_version: int = 0,
    use_cache: bool = True,
) -> List[Dict[str, Any]]:
    """
    Return top-k results with hybrid score = 0.9 * cosine + 0.1 * normalized BM25.
    Falls back cleanly if anything is missing.
    """
    metrics = metrics or get_current_metrics()
    cache_key = _cache_key(query, k, corpus_version) if use_cache else None
    if use_cache and cache_key:
        cached = _get_cached(cache_key, metrics)
        if cached is not None:
            return cached
        if metrics:
            metrics.record_counter("cache_hit", 0)

    # dense query vec
    with _measure("embed_query", metrics):
        qvec = embedder.encode([query])[0]

    # lexical (BM25) over the whole collection (cached)
    bm25, all_ids = get_bm25(col)
    def _bm25_task():
        if metrics:
            with metrics.measure("bm25_search"):
                return bm25.scores(query)
        return bm25.scores(query)

    def _vector_task():
        if metrics:
            with metrics.measure("vector_search"):
                return col.query(
                    query_embeddings=[qvec.tolist()],
                    n_results=k,
                    include=["documents", "metadatas", "distances"]
                )
        return col.query(
            query_embeddings=[qvec.tolist()],
            n_results=k,
            include=["documents", "metadatas", "distances"]
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        future_bm25 = pool.submit(_bm25_task)
        future_vec = pool.submit(_vector_task)
        bm25_scores = future_bm25.result()
        res = future_vec.result()
    docs  = res.get("documents", [[]])[0]
    metas = res.get("metadatas", [[]])[0]
    ids   = res.get("ids", [[]])[0]
    dists_raw = res.get("distances", [[]])
    distances: List[float] = []
    try:
        d0 = dists_raw[0] if isinstance(dists_raw, (list, tuple)) else dists_raw
        if isinstance(d0, np.ndarray):
            distances = [float(x) for x in d0.tolist()]
        elif isinstance(d0, (list, tuple)):
            distances = [float(x) for x in d0]
    except Exception:
        distances = []


    # quick id -> position map for bm25 lookup
    id_to_pos = {doc_id: i for i, doc_id in enumerate(all_ids)}

    with _measure("hybrid_merge", metrics):
        out: List[Dict[str, Any]] = []
        for idx, (d, m, doc_id) in enumerate(zip(docs, metas, ids)):
            sim = 0.0
            if idx < len(distances):
                sim = 1.0 - distances[idx]
            # lexical piece (normalized)
            pos = id_to_pos.get(doc_id)
            bm25_raw = bm25_scores[pos] if pos is not None else 0.0
            bm25_norm = bm25.normalize(bm25_raw)

            hybrid = sim * (1 - HYBRID_LAMBDA) + bm25_norm * HYBRID_LAMBDA
            if hybrid >= MIN_SIM:
                out.append({"text": d, "metadata": m, "score": hybrid, "id": doc_id})

        out.sort(key=lambda r: (-r["score"], r.get("id") or ""))

    result = out[:k]
    if use_cache and cache_key:
        _store_cache(cache_key, result)
        return copy.deepcopy(result)
    return result
