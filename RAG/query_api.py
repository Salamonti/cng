# C:\RAG\query_api.py
#!/usr/bin/env python3
"""
query_api.py

FastAPI service that exposes a /query endpoint for the RAG index.

Features
- Loads Chroma persistent collection from settings.yaml via store.get_client/get_collection.
- Uses the existing Embedder and hybrid scoring (dense cosine + BM25) from bm25_index.
- Supports optional filters: specialty (exact), date_from/date_to (post-filter), and keyword includes.
- Returns top-k chunks with text, metadata, and hybrid score.

Run (VSCode Task)
  uvicorn query_api:app --reload --port 8000

"""
from __future__ import annotations

import json
from collections import OrderedDict, defaultdict
from contextlib import nullcontext
from dataclasses import asdict
from functools import lru_cache
from threading import Lock
from typing import Any, Dict, List, Optional, Tuple, cast
import copy

import numpy as np
import uvicorn
import yaml
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import ORJSONResponse
from pydantic import BaseModel, Field

from store import get_client, get_collection
from embedder import Embedder
from bm25_index import BM25Helper

QUERY_CACHE_MAX = 128
_QUERY_CACHE: "OrderedDict[Tuple[str, int, str, str, str, Tuple[str, ...], int], List[Dict[str, Any]]]" = OrderedDict()
_QUERY_CACHE_LOCK = Lock()
from metrics import RequestMetrics, get_current_metrics
from utils_meta import gather_quality_counters, dedupe_and_normalize_hits

# -----------------------------
# Keyword utilities & summarizer
# -----------------------------

STOPWORDS = {
    "the","a","an","and","or","but","if","in","on","at","by","for","to","of","with","as","is","are","was","were","be","been",
    "this","that","these","those","it","its","from","into","about","over","after","before","than","then","also","we","our","you",
    "their","there","here","such","may","can","could","should","would","will","not","no","yes","do","does","did","have","has","had",
}

def _tokens(s: str) -> List[str]:
    import re
    return re.findall(r"[A-Za-z0-9%]+", s.lower())

def extract_keywords(text: str, extra: Optional[List[str]] = None, min_len: int = 3, max_terms: int = 16) -> List[str]:
    kws: List[str] = []
    seen = set()
    if extra:
        for k in extra:
            kk = k.lower().strip()
            if kk and kk not in seen and kk not in STOPWORDS and len(kk) >= min_len:
                seen.add(kk)
                kws.append(kk)
    for tok in _tokens(text):
        if tok in STOPWORDS or len(tok) < min_len:
            continue
        if tok not in seen:
            seen.add(tok)
            kws.append(tok)
        if len(kws) >= max_terms:
            break
    return kws

def summarize_chunk(text: str, query_kws: List[str], target_words: int = 160) -> str:
    # Lightweight extractive summary by sentence ranking with keyword hits
    import re
    sents = re.split(r"(?<=[.!?])\s+", text.strip())
    if len(sents) <= 2:
        return text
    qset = set(query_kws)
    scored: List[Tuple[float, str]] = []
    for s in sents:
        toks = set(_tokens(s))
        overlap = len(qset & toks)
        length_pen = 0.2 if len(s) > 400 else 0.0
        score = overlap - length_pen
        scored.append((score, s))
    scored.sort(key=lambda x: x[0], reverse=True)
    out: List[str] = []
    wc = 0
    for _, s in scored:
        w = len(_tokens(s))
        out.append(s)
        wc += w
        if wc >= target_words:
            break
    if not out:
        out = sents[:3]
    summary = " ".join(out)
    # Trim if we overshot
    words = summary.split()
    if len(words) > target_words:
        summary = " ".join(words[:target_words]) + "..."
    return summary


def _parse_date_any(s: str) -> Tuple[int, int, int]:
    s = (s or "").strip()
    # try common forms quickly
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d", "%Y-%m", "%Y"):
        try:
            import datetime as dt
            if fmt == "%Y":
                y = int(s)
                return (y, 1, 1)
            d = dt.datetime.strptime(s, fmt)
            return (d.year, d.month, d.day)
        except Exception:
            continue
    # fallback: digits only first 8
    import re
    digs = re.sub(r"[^0-9]", "", s)
    if len(digs) >= 8:
        try:
            y = int(digs[:4])
            m = int(digs[4:6])
            d = int(digs[6:8])
            return (y, m, d)
        except Exception:
            pass
    return (0, 0, 0)


class QueryRequest(BaseModel):
    query: str = Field(..., description="Natural language query")
    top_k: int = Field(6, ge=1, le=50)
    specialty: Optional[str] = Field(None, description="Filter by metadata.specialty (exact match)")
    date_from: Optional[str] = Field(None, description="ISO date lower bound; post-filter (e.g., 2022-01-01)")
    date_to: Optional[str] = Field(None, description="ISO date upper bound; post-filter")
    include_keywords: Optional[List[str]] = Field(None, description="Require these keywords in text (any)")


class Hit(BaseModel):
    id: Optional[str] = None
    text: str
    metadata: Dict[str, Any]
    score: float
    summary: Optional[str] = None


class QueryResponse(BaseModel):
    results: List[Hit]
    used_filters: Dict[str, Any]
    context: str
    references: List[Dict[str, Any]]
    refs: List[Dict[str, Any]]
    meta: Dict[str, Any]


def _query_cache_key(req: "QueryRequest", corpus_version: int) -> Tuple[str, int, str, str, str, Tuple[str, ...], int]:
    return (
        req.query.strip(),
        req.top_k,
        req.specialty or "",
        req.date_from or "",
        req.date_to or "",
        tuple(req.include_keywords or ()),
        corpus_version,
    )


def _get_cached_hits(key: Tuple[str, int, str, str, str, Tuple[str, ...], int]) -> Optional[List[Dict[str, Any]]]:
    with _QUERY_CACHE_LOCK:
        hits = _QUERY_CACHE.get(key)
        if hits is not None:
            _QUERY_CACHE.move_to_end(key)
            return copy.deepcopy(hits)
    return None


def _store_cached_hits(key: Tuple[str, int, str, str, str, Tuple[str, ...], int], hits: List[Dict[str, Any]]) -> None:
    with _QUERY_CACHE_LOCK:
        _QUERY_CACHE[key] = copy.deepcopy(hits)
        _QUERY_CACHE.move_to_end(key)
        while len(_QUERY_CACHE) > QUERY_CACHE_MAX:
            _QUERY_CACHE.popitem(last=False)


@lru_cache(maxsize=1)
def _load_settings() -> Dict[str, Any]:
    cfg = yaml.safe_load(open("settings.yaml", "r"))
    return cfg


@lru_cache(maxsize=1)
def _get_embedder() -> Embedder:
    cfg = _load_settings()
    return Embedder(cfg["embedding_model"])


@lru_cache(maxsize=1)
def _get_collection():
    cfg = _load_settings()
    client = get_client(cfg["persist_directory"])  # chroma path
    return get_collection(client, name=cfg.get("collection_name", "medical_rag"))


def _where_for(request: QueryRequest) -> Dict[str, Any]:
    where: Dict[str, Any] = {}
    if request.specialty:
        # exact match on metadata.specialty if present
        where["specialty"] = request.specialty
    return where


def _text_date(meta: Dict[str, Any]) -> str:
    # prefer 'timestamp', then 'year', then 'date'
    return str(meta.get("timestamp") or meta.get("year") or meta.get("date") or "")


def _date_ok(meta: Dict[str, Any], dfrom: Optional[str], dto: Optional[str]) -> bool:
    if not dfrom and not dto:
        return True
    y, m, d = _parse_date_any(_text_date(meta))
    if (y, m, d) == (0, 0, 0):
        return False
    if dfrom:
        y0, m0, d0 = _parse_date_any(dfrom)
        if (y, m, d) < (y0, m0, d0):
            return False
    if dto:
        y1, m1, d1 = _parse_date_any(dto)
        if (y, m, d) > (y1, m1, d1):
            return False
    return True


def _keywords_ok(text: str, kws: Optional[List[str]]) -> bool:
    if not kws:
        return True
    t = text.lower()
    return any(k.lower() in t for k in kws)


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    na = np.linalg.norm(a)
    nb = np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def hybrid_search_filtered(
    req: QueryRequest,
    metrics: Optional[RequestMetrics] = None,
    cfg: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    col = _get_collection()
    emb = _get_embedder()
    metrics = metrics or get_current_metrics()
    cfg = cfg or _load_settings()

    use_rrf = bool(cfg.get('use_rrf', False))
    rrf_k = int(cfg.get('rrf_k', 60))

    def _measure(name: str):
        return metrics.measure(name) if metrics else nullcontext()

    query_kws = extract_keywords(req.query, extra=req.include_keywords or [])
    qkw_set = set(query_kws)

    with _measure("embed_query"):
        qvec = emb.encode([req.query])[0]
    where = _where_for(req)

    dense_n = max(req.top_k * 3, 10)

    with _measure("vector_search"):
        res = col.query(
            query_embeddings=[qvec.tolist()],
            n_results=dense_n,
            where=where if where else None,  # type: ignore
            include=["documents", "metadatas", "distances"],
        )

    def _first(x):
        if isinstance(x, list) and len(x) > 0:
            return x[0]
        return x

    docs_raw = res.get("documents", [[]])
    metas_raw = res.get("metadatas", [[]])
    ids_raw = res.get("ids", [[]])
    dists_raw = res.get("distances", [[]])

    docs: List[str] = cast(List[str], _first(docs_raw) or [])
    metas: List[Dict[str, Any]] = cast(List[Dict[str, Any]], _first(metas_raw) or [])
    ids: List[str] = list(_first(ids_raw) or [])

    dists: List[float] = []
    try:
        d0 = _first(dists_raw)
        if isinstance(d0, np.ndarray):
            dists = [float(x) for x in d0.tolist()]
        elif isinstance(d0, (list, tuple)):
            tmp_seq: List[Any] = list(d0)
            if tmp_seq and isinstance(tmp_seq[0], (list, tuple, np.ndarray)):
                tmp_seq = list(tmp_seq[0])
            dists = [float(x) for x in tmp_seq]
    except Exception:
        dists = []

    with _measure("bm25_search"):
        bm25 = BM25Helper(docs) if docs else None
        bm25_scores = bm25.scores(req.query) if bm25 else [0.0] * len(docs)

    HYBRID_LAMBDA = 0.10
    RERANK_KEYWORD_LAMBDA = 0.15
    out: List[Dict[str, Any]] = []
    with _measure("hybrid_merge"):
        for idx, (text, meta) in enumerate(zip(docs, metas)):
            sim = 0.0
            if idx < len(dists):
                sim = 1.0 - float(dists[idx])
            bm_raw = bm25_scores[idx] if idx < len(bm25_scores) else 0.0
            bm_norm = bm25.normalize(bm_raw) if bm25 else 0.0
            hybrid = sim * (1 - HYBRID_LAMBDA) + bm_norm * HYBRID_LAMBDA
            if qkw_set:
                dtoks = set(_tokens(text))
                overlap = len(qkw_set & dtoks) / max(1, len(qkw_set))
            else:
                overlap = 0.0
            final_score = hybrid * (1 - RERANK_KEYWORD_LAMBDA) + overlap * RERANK_KEYWORD_LAMBDA
            out.append({
                "text": text,
                "metadata": meta,
                "score": final_score,
                "id": ids[idx] if idx < len(ids) else None,
                "_sim": sim,
                "_bm25": bm_norm,
            })

    out = [h for h in out if _date_ok(h.get("metadata", {}), req.date_from, req.date_to)]
    out = [h for h in out if _keywords_ok(h.get("text", ""), req.include_keywords)]

    if use_rrf and out:
        vec_rank = {item.get("id"): rank for rank, item in enumerate(sorted(out, key=lambda x: x.get("_sim", 0.0), reverse=True))}
        bm_rank = {item.get("id"): rank for rank, item in enumerate(sorted(out, key=lambda x: x.get("_bm25", 0.0), reverse=True))}
        for item in out:
            rid = item.get("id")
            r_vec = vec_rank.get(rid, len(out))
            r_bm = bm_rank.get(rid, len(out))
            item["_rrf_score"] = 1.0 / (rrf_k + r_vec + 1) + 1.0 / (rrf_k + r_bm + 1)
        out.sort(key=lambda item: (-item.get("_rrf_score", item["score"]), -item["score"], item.get("id") or ""))
    else:
        out.sort(key=lambda r: (-r["score"], r.get("id") or ""))

    top = out[: req.top_k]
    enriched: List[Dict[str, Any]] = []
    SUMMARIZE_THRESHOLD = 500
    for h in top:
        txt = h.get("text", "")
        wc = int(h.get("metadata", {}).get("word_count") or 0)
        if wc == 0:
            wc = len(_tokens(txt))
        if wc > SUMMARIZE_THRESHOLD:
            h["summary"] = summarize_chunk(txt, query_kws=query_kws, target_words=160)
        else:
            h["summary"] = txt
        enriched.append(h)

    for item in enriched:
        item.pop("_sim", None)
        item.pop("_bm25", None)
        item.pop("_rrf_score", None)

    return enriched


def _summarize_hits(hits: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Produce lightweight aggregates for logging and UI hints."""
    summary: Dict[str, Any] = {
        "count": len(hits or []),
        "score": {},
        "years": {},
        "specialties": {},
    }
    if not hits:
        return summary

    scores: List[float] = []
    year_values: List[int] = []
    specialty_scores: Dict[str, List[float]] = defaultdict(list)

    for h in hits:
        score_raw = h.get("score", 0.0)
        try:
            score = float(score_raw)
        except Exception:
            score = 0.0
        scores.append(score)

        md = h.get("metadata") or {}
        spec = str(md.get("specialty") or "").strip()
        if spec:
            specialty_scores[spec].append(score)

        year, _, _ = _parse_date_any(_text_date(md))
        if year:
            year_values.append(year)

    if scores:
        sorted_scores = sorted(scores)
        mid = len(sorted_scores) // 2
        if len(sorted_scores) % 2 == 1:
            median = sorted_scores[mid]
        else:
            median = (sorted_scores[mid - 1] + sorted_scores[mid]) / 2.0
        summary["score"] = {
            "mean": round(sum(scores) / len(scores), 4),
            "median": round(median, 4),
            "max": round(max(scores), 4),
            "min": round(min(scores), 4),
        }

    if year_values:
        summary["years"] = {
            "earliest": min(year_values),
            "latest": max(year_values),
        }

    if specialty_scores:
        summary["specialties"] = {
            spec: {
                "count": len(values),
                "mean_score": round(sum(values) / len(values), 4),
            }
            for spec, values in specialty_scores.items()
        }

    return summary


def _package(hits: List[Dict[str, Any]], query: str, used_filters: Dict[str, Any]) -> Tuple[str, List[Dict[str, Any]], Dict[str, Any]]:
    """Build LLM-ready context block and references list for downstream UI and models."""
    # Context block: numbered summaries
    lines: List[str] = []
    refs: List[Dict[str, Any]] = []
    seen_sources: List[str] = []
    for idx, h in enumerate(hits, start=1):
        m = h.get("metadata", {}) or {}
        title = m.get("title") or m.get("guideline_type") or m.get("lab_test") or ""
        src = m.get("source") or m.get("society") or ""
        year = m.get("timestamp") or m.get("year") or m.get("date") or ""
        link = m.get("link") or ""
        journal = m.get("journal") or ""
        society = m.get("society") or ""
        doc_id = m.get("doc_id") or ""
        # context uses the summary for brevity
        summary = h.get("summary") or h.get("text") or ""
        lines.append(f"[{idx}] {summary}")
        # references for UI
        ref = {
            "index": idx,
            "source": src,
            "title": title,
            "journal": journal,
            "society": society,
            "year": year,
            "link": link,
            "doc_id": doc_id,
            "score": round(float(h.get("score", 0.0)), 4),
        }
        refs.append(ref)
        if src and src not in seen_sources:
            seen_sources.append(src)

    import datetime as _dt
    context_block = "\n\n".join(lines)
    meta = {
        "generated_at": _dt.datetime.now().isoformat(),
        "query": query,
        "count": len(hits),
        "sources": seen_sources,
        "filters": used_filters,
        "aggregates": _summarize_hits(hits),
    }
    return context_block, refs, meta


app = FastAPI(title="RAG Query API", version="1.0.0", default_response_class=ORJSONResponse)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> Dict[str, Any]:
    try:
        col = _get_collection()
        _ = col.count()  # type: ignore
        return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "detail": str(e)}


@app.post("/query", response_model=QueryResponse)
def query(req: QueryRequest) -> QueryResponse:
    cfg = _load_settings()
    corpus_version = int(cfg.get("corpus_version", 0))
    cache_key = _query_cache_key(req, corpus_version)

    metrics = RequestMetrics(query=req.query, top_k=req.top_k)
    with metrics.activate():
        cached_hits = _get_cached_hits(cache_key)
        if cached_hits is not None:
            norm_hits = cached_hits
            metrics.record_counter("cache_hit", 1)
        else:
            metrics.record_counter("cache_hit", 0)
            hits = hybrid_search_filtered(req, metrics=metrics, cfg=cfg)

            norm_hits: List[Dict[str, Any]] = []
            for h in hits or []:
                text_value = h.get("text") if isinstance(h, dict) else ""
                md = h.get("metadata") if isinstance(h, dict) else {}
                score = h.get("score") if isinstance(h, dict) else 0.0
                text_value = str(text_value or "")
                md = md or {}
                try:
                    score = float(score or 0.0)
                except Exception:
                    score = 0.0
                norm_hits.append(
                    {
                        "id": (h.get("id") if isinstance(h, dict) else None),
                        "text": text_value,
                        "metadata": md,
                        "score": score,
                        "summary": (h.get("summary") if isinstance(h, dict) else None),
                    }
                )

            norm_hits = dedupe_and_normalize_hits(norm_hits)
            _store_cached_hits(cache_key, norm_hits)

        used = {
            "specialty": req.specialty,
            "date_from": req.date_from,
            "date_to": req.date_to,
            "top_k": req.top_k,
            "keywords": req.include_keywords or extract_keywords(req.query),
        }

        with metrics.measure("build_prompt"):
            context, refs, meta = _package(norm_hits, req.query, used)

        quality = gather_quality_counters(norm_hits, query=req.query, context_text=context)
        for key, value in quality.items():
            metrics.record_counter(key, value)
        metrics.set_measurement("ttfb_llm", 0.0)

    total_elapsed = metrics.finish()
    metrics.log()

    timings_ms = {name: round(value * 1000, 3) for name, value in metrics.measurements.items()}
    meta.setdefault("metrics", {})
    meta["metrics"]["timings_ms"] = timings_ms
    meta["metrics"]["total_ms"] = round(total_elapsed * 1000, 3)
    meta["metrics"]["quality"] = quality

    return QueryResponse(
        results=[Hit(**h) for h in norm_hits],
        used_filters=used,
        context=context,
        references=refs,
        refs=refs,
        meta=meta,
    )



if __name__ == "__main__":
    uvicorn.run(
        "query_api:app",
        host="0.0.0.0",
        port=8007,
        workers=1,
        loop="asyncio",
        http="h11",
        timeout_keep_alive=30,
    )
