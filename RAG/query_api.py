# query_api.py
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

from collections import OrderedDict, defaultdict
from contextlib import nullcontext
from functools import lru_cache
from threading import Lock
from typing import Any, Dict, List, Optional, Tuple, cast
import copy
import os
import re

import numpy as np
import uvicorn
import yaml
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import ORJSONResponse
from pydantic import BaseModel, Field

from store import get_client, get_collection
from embedder import Embedder
from bm25_index import BM25Helper, get_bm25
from web_search import search_medical
from crawl_extractor import extract_text

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
    tier: Optional[str] = None


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


def _get_tier(source: str, cfg: Dict[str, Any]) -> str:
    """Determine the tier of a source based on configuration."""
    tier_sources = cfg.get("tier_sources", {})
    source_lower = source.lower()
    
    for tier, sources in tier_sources.items():
        for s in sources:
            if s.lower() in source_lower:
                return tier
    return "web"  # Default to lowest tier


def _apply_tier_boost(score: float, source: str, cfg: Dict[str, Any]) -> float:
    """Apply tier-based boost to score."""
    tier = _get_tier(source, cfg)
    tier_boosts = cfg.get("tier_boosts", {})
    boost = tier_boosts.get(tier, 0.0)
    return score + boost


def _meets_evidence_threshold(text: str, cfg: Dict[str, Any]) -> bool:
    """Check if text meets minimum evidence threshold."""
    min_chars = cfg.get("evidence_min_chars", 0)
    if min_chars <= 0:
        return True
    return len(text) >= min_chars


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

    dense_cfg = int(cfg.get("dense_top_k", 16))
    dense_n = max(req.top_k * 4, dense_cfg, 12)

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

        # Global BM25 provides stronger lexical grounding than candidate-only BM25.
        bm25_global = None
        bm25_global_scores: List[float] = []
        bm25_id_pos: Dict[str, int] = {}
        try:
            bm25_global, global_ids = get_bm25(col)
            bm25_global_scores = bm25_global.scores(req.query)
            bm25_id_pos = {str(i): pos for pos, i in enumerate(global_ids or [])}
        except Exception:
            bm25_global = None

    HYBRID_LAMBDA = float(cfg.get("hybrid_lambda", 0.10))
    RERANK_KEYWORD_LAMBDA = float(cfg.get("keyword_overlap_lambda", 0.15))
    out: List[Dict[str, Any]] = []
    with _measure("hybrid_merge"):
        for idx, (text, meta) in enumerate(zip(docs, metas)):
            sim = 0.0
            if idx < len(dists):
                sim = 1.0 - float(dists[idx])
            bm_raw = bm25_scores[idx] if idx < len(bm25_scores) else 0.0
            bm_norm = bm25.normalize(bm_raw) if bm25 else 0.0
            if bm25_global and idx < len(ids):
                gid = ids[idx]
                gpos = bm25_id_pos.get(str(gid))
                if gpos is not None and gpos < len(bm25_global_scores):
                    gbm_raw = bm25_global_scores[gpos]
                    bm_norm = max(bm_norm, bm25_global.normalize(gbm_raw))
            hybrid = sim * (1 - HYBRID_LAMBDA) + bm_norm * HYBRID_LAMBDA
            if qkw_set:
                dtoks = set(_tokens(text))
                overlap = len(qkw_set & dtoks) / max(1, len(qkw_set))
            else:
                overlap = 0.0
            final_score = hybrid * (1 - RERANK_KEYWORD_LAMBDA) + overlap * RERANK_KEYWORD_LAMBDA

            # Temporal decay: older guidelines score lower
            # decay = max(0, 1 - (current_year - publication_year) * 0.05)
            # 2026: x1.0, 2023: x0.85, 2020: x0.70, 2015: x0.50
            import datetime as _dt
            current_year = _dt.datetime.now().year
            year_val, _, _ = _parse_date_any(_text_date(meta or {}))
            if year_val >= 2000:
                temporal_decay = max(0.0, 1.0 - (current_year - year_val) * 0.05)
            else:
                temporal_decay = 1.0  # No decay if year unknown

            src = str((meta or {}).get("source") or (meta or {}).get("society") or "").lower()
            authority_boost = 0.0
            for kw in ("guideline", "thoracic", "chest", "nice", "idsa", "acc", "aha", "who", "asco", "ats"):
                if kw in src:
                    authority_boost = 0.02
                    break

            final_score = final_score * temporal_decay + authority_boost
            
            # Apply tier-based boost
            src = str((meta or {}).get("source") or (meta or {}).get("society") or "").lower()
            tier = _get_tier(src, cfg)
            final_score = _apply_tier_boost(final_score, src, cfg)
            
            out.append({
                "text": text,
                "metadata": meta,
                "score": final_score,
                "id": ids[idx] if idx < len(ids) else None,
                "_sim": sim,
                "_bm25": bm_norm,
                "tier": tier,
            })

    out = [h for h in out if _date_ok(h.get("metadata", {}), req.date_from, req.date_to)]
    out = [h for h in out if _keywords_ok(h.get("text", ""), req.include_keywords)]
    
    # Apply evidence threshold - filter out chunks that don't meet minimum character count
    out = [h for h in out if _meets_evidence_threshold(h.get("text", ""), cfg)]

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

    # P3-3: minimum relevance floor, applied before top_k truncation so a
    # query with few genuinely relevant chunks doesn't get backfilled with
    # near-zero-score noise just to hit a target count. Deliberately
    # conservative (0.1) since final_score already blends similarity, BM25,
    # temporal decay, and small tier/authority boosts -- this is a floor
    # against near-irrelevant matches, not a quality gate that needs
    # careful per-corpus tuning.
    min_relevance = float(cfg.get("min_relevance_score", 0.1))
    if min_relevance > 0:
        out = [h for h in out if h.get("score", 0.0) >= min_relevance]

    # P3-3: this dedup used to run in query() AFTER truncating to top_k --
    # dense_n above retrieves ~4x top_k candidates specifically so there's
    # room to dedupe from, but capping per-document chunks after the
    # truncation instead of before it meant a query whose top_k results
    # happened to cluster on a couple of documents would come back with
    # FEWER than top_k results overall, silently dropping other genuinely
    # relevant documents that were sitting just past the truncation cut.
    # Applying it here, before truncation, lets dedup make room and get
    # backfilled from the larger candidate pool.
    per_doc = int(cfg.get("max_chunks_per_doc", 2))
    out = dedupe_and_normalize_hits(out, max_per_doc=per_doc)

    top = out[: req.top_k]
    enriched: List[Dict[str, Any]] = []
    SUMMARIZE_THRESHOLD = int(cfg.get("summarize_threshold_words", 700))
    SUMMARY_TARGET_WORDS = int(cfg.get("summary_target_words", 220))
    for h in top:
        txt = h.get("text", "")
        wc = int(h.get("metadata", {}).get("word_count") or 0)
        if wc == 0:
            wc = len(_tokens(txt))
        if wc > SUMMARIZE_THRESHOLD:
            h["summary"] = summarize_chunk(txt, query_kws=query_kws, target_words=SUMMARY_TARGET_WORDS)
        else:
            h["summary"] = txt
        enriched.append(h)

    for item in enriched:
        item.pop("_sim", None)
        item.pop("_bm25", None)
        item.pop("_rrf_score", None)
        # Keep tier — needed by qa_chat.py for UI tier badges

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


# ---------------------------------------------------------------------------
# Step 4 (H-5/H-6): shared-key auth + PHI egress gate
# The RAG API is reached server-side by FastAPI (RAGHttpClient). It is NOT
# called directly by the browser, so we require a shared X-API-Key on the
# business endpoints. The key lives in secrets.env (RAG_API_KEY); the backend
# (RAGHttpClient) injects it so it never reaches the browser.
# ---------------------------------------------------------------------------
_RAG_API_KEY = os.environ.get("RAG_API_KEY", "")


async def require_api_key(x_api_key: str = Header(default="", alias="X-API-Key")) -> None:
    if not _RAG_API_KEY:
        raise HTTPException(status_code=503, detail="RAG API key not configured on server")
    if not x_api_key or x_api_key != _RAG_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


# Strong PHI patterns for the web-query egress gate. These are the explicit,
# machine-checkable identifiers we refuse to send to external search engines.
_PHI_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHI_DOB = re.compile(r"\b(19|20)\d{2}[-/](0[1-9]|1[0-2])[-/](0[1-9]|[12]\d|3[01])\b")
_PHI_SIN = re.compile(r"\b\d{3}-\d{3}-\d{3}\b")
_PHI_POSTAL = re.compile(r"\b[A-Za-z]\d[A-Za-z][ -]?\d[A-Za-z]\d\b")
_PHI_ID_KW = re.compile(r"\b(PHN|MCP|health card|healthcare number|medical record number|patient id)\b", re.IGNORECASE)


def _phi_in_query(query: str) -> Optional[str]:
    """Return the name of the first PHI indicator found in a query, else None."""
    q = query or ""
    for name, pat in (
        ("email", _PHI_EMAIL),
        ("date-of-birth", _PHI_DOB),
        ("SIN/SSN", _PHI_SIN),
        ("postal-code", _PHI_POSTAL),
        ("patient-identifier-keyword", _PHI_ID_KW),
    ):
        if pat.search(q):
            return name
    return None


app = FastAPI(title="RAG Query API", version="1.0.0", default_response_class=ORJSONResponse)
# Strict CORS: RAG is called server-side; this only covers accidental direct
# browser access. Explicit origins only, credentials off (no wildcard + cookies).
_RAG_ALLOWED_ORIGINS = [
    o.strip()
    for o in os.environ.get(
        "RAG_ALLOWED_ORIGINS",
        "http://127.0.0.1:3000,http://localhost:3000,http://192.168.0.108:3000,"
        "https://notes.ieissa.com,https://dreamcision.com,https://www.dreamcision.com",
    ).split(",")
    if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_RAG_ALLOWED_ORIGINS,
    allow_credentials=False,
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


@app.post("/query", response_model=QueryResponse, dependencies=[Depends(require_api_key)])
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
                        "tier": (h.get("tier") if isinstance(h, dict) else None),
                    }
                )

            # P3-3: the actual per-document cap now happens inside
            # hybrid_search_filtered(), before its top_k truncation (see the
            # comment there) -- hits here already satisfies max_per_doc, so
            # this call is a no-op for dedup specifically. Kept for its
            # other job, normalize_whitespace() on text/summary.
            per_doc = int(cfg.get("max_chunks_per_doc", 2))
            norm_hits = dedupe_and_normalize_hits(norm_hits, max_per_doc=per_doc)
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


class WebQueryRequest(BaseModel):
    query: str = Field(..., description="Natural language query")
    top_k: int = Field(6, ge=1, le=50, description="Number of results from each source")
    web_search: bool = Field(False, description="Include web search results (opt-in; off by default to avoid PHI egress)")
    crawl_depth: int = Field(8, ge=0, le=20, description="Number of web pages to crawl for full text")
    include_local: bool = Field(True, description="Include local RAG index results")


def _classify_web_source(url: str, title: str, cfg: Dict[str, Any]) -> str:
    """Classify a web source into a tier based on URL and title patterns."""
    url_lower = (url or "").lower()
    title_lower = (title or "").lower()
    
    # Guideline sources (highest tier)
    guideline_patterns = [
        "theacc.com", "acc.org", "aha.org", "heart.org",  # ACC/AHA
        "nice.org.uk",  # NICE
        "who.int",  # WHO
        "guidelinecentral.com", "mdguidelines.com",
        "upToDate.com", "merckmanuals.com",
        "mayoclinic.org", "clevelandclinic.org",
        "thoracic.org", "ersnet.org", "chestnet.org",
        "acs.org", "asco.org", "nccn.org",
    ]
    for pat in guideline_patterns:
        if pat in url_lower:
            return "guideline"
    
    # Check if title contains guideline keywords
    guideline_title_keywords = ["guideline", "clinical practice guideline", "consensus statement"]
    for kw in guideline_title_keywords:
        if kw in title_lower:
            return "guideline"
    
    # PubMed/PMC sources
    pubmed_patterns = ["pubmed.ncbi.nlm.nih.gov", "ncbi.nlm.nih.gov/pmc", "pubmed"]
    for pat in pubmed_patterns:
        if pat in url_lower:
            return "pubmed"
    
    # Trial sources
    trial_patterns = ["clinicaltrials.gov", "trial"]
    for pat in trial_patterns:
        if pat in url_lower or pat in title_lower:
            return "trial"
    
    return "web"


def _score_web_result(url: str, title: str, snippet: str, cfg: Dict[str, Any]) -> float:
    """Score a web result based on authority, recency, and relevance."""
    score = 0.4  # Base score
    
    # Authority boost based on tier
    tier = _classify_web_source(url, title, cfg)
    tier_boosts = cfg.get("tier_boosts", {})
    score += tier_boosts.get(tier, 0.0)
    
    # Recency boost - look for year in title
    import re
    year_match = re.search(r'\b(202[5-9]|203\d)\b', title)
    if year_match:
        score += 0.05  # Recent guideline boost
    
    # PubMed base boost
    if "pubmed" in url.lower():
        score += 0.05
    
    return min(score, 0.95)  # Cap at 0.95


@app.post("/web_query", response_model=QueryResponse, dependencies=[Depends(require_api_key)])
def web_query(req: WebQueryRequest) -> QueryResponse:
    """
    Hybrid query that combines local RAG index with web search + full-text extraction.
    
    1. Searches local RAG index
    2. Searches web using SearXNG
    3. Extracts full text from top web results using Crawl4AI
    4. Combines and ranks all results with proper tier classification
    """
    # PHI egress gate: never forward patient-identifying text to external services.
    phi_hit = _phi_in_query(req.query)
    if phi_hit:
        raise HTTPException(
            status_code=400,
            detail=f"Query blocked: contains {phi_hit} (patient-identifying data) and "
                   "cannot be sent to external search services.",
        )
    cfg = _load_settings()
    
    # Get local RAG results
    local_hits = []
    if req.include_local:
        local_req = QueryRequest(
            query=req.query,
            top_k=req.top_k,
            specialty=None,
            date_from=None,
            date_to=None,
            include_keywords=None,
        )
        local_hits = hybrid_search_filtered(local_req, cfg=cfg)
    
    # Get web search results - fetch more to increase chance of finding recent guidelines
    web_hits = []
    if req.web_search:
        # Fetch more results to increase coverage of recent guidelines
        search_results = search_medical(req.query, top_k=max(req.crawl_depth, 10))
        
        # Separate PubMed URLs from others — PubMed has CAPTCHA, use EFetch instead
        import re
        pubmed_id_pattern = re.compile(r'pubmed\.ncbi\.nlm\.nih\.gov/(\d+)')
        pubmed_urls = []
        other_urls = []
        
        for result in search_results:
            url = result.get("url", "")
            pmid_match = pubmed_id_pattern.search(url)
            if pmid_match:
                pubmed_urls.append({
                    "pmid": pmid_match.group(1),
                    "title": result.get("title", ""),
                    "url": url,
                })
            else:
                other_urls.append(result)
        
        # Extract full text from non-PubMed URLs using Crawl4AI
        crawled_texts = []
        for result in other_urls[:req.crawl_depth]:
            url = result.get("url", "")
            markdown = extract_text(url)
            if markdown:
                crawled_texts.append({
                    "url": url,
                    "title": result.get("title", ""),
                    "markdown": markdown,
                })
        
        # For PubMed URLs, fetch abstracts via EFetch API (no CAPTCHA)
        if pubmed_urls:
            try:
                import requests as req_lib
                # Batch fetch up to 20 PMIDs
                for i in range(0, len(pubmed_urls), 20):
                    batch = pubmed_urls[i:i+20]
                    ids = ",".join([pm["pmid"] for pm in batch])
                    efetch_url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=pubmed&id={ids}&retmode=xml"
                    resp = req_lib.get(efetch_url, timeout=15)
                    if resp.status_code == 200:
                        import xml.etree.ElementTree as ET
                        try:
                            root = ET.fromstring(resp.text)
                            for article in root.findall('.//PubmedArticle'):
                                title_el = article.find('.//ArticleTitle')
                                abstract_el = article.find('.//AbstractText')
                                pmid_el = article.find('.//PMID')
                                
                                title = title_el.text if title_el is not None else ""
                                abstract = abstract_el.text if abstract_el is not None else ""
                                pmid = pmid_el.text if pmid_el is not None else ""
                                
                                # Find matching original result
                                matching = next((pm for pm in batch if pm["pmid"] == pmid), None)
                                url = matching["url"] if matching else f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
                                
                                crawled_texts.append({
                                    "url": url,
                                    "title": title,
                                    "markdown": abstract,
                                })
                        except ET.ParseError:
                            pass
            except Exception:
                pass
        
        # Convert crawled results to hit format with proper tier classification
        for item in crawled_texts:
            url = item["url"]
            title = item["title"]
            tier = _classify_web_source(url, title, cfg)
            score = _score_web_result(url, title, item["markdown"][:200], cfg)
            
            web_hits.append({
                "text": item["markdown"],
                "metadata": {
                    "source": tier,  # Use classified tier as source
                    "title": item["title"],
                    "link": item["url"],
                    "engine": "crawl4ai" if "pubmed" not in url.lower() else "efetch",
                },
                "score": score,
                "tier": tier,  # Assign proper tier
                "summary": item["markdown"][:500] + "...",
            })
    
    # Combine and sort by score
    all_hits = local_hits + web_hits
    all_hits.sort(key=lambda x: -x.get("score", 0.0))
    
    # Take top_k results
    final_hits = all_hits[:req.top_k]
    
    # Build response
    used = {
        "query": req.query,
        "top_k": req.top_k,
        "web_search": req.web_search,
        "crawl_depth": req.crawl_depth,
        "include_local": req.include_local,
        "local_count": len(local_hits),
        "web_count": len(web_hits),
    }
    
    context, refs, meta = _package(final_hits, req.query, used)
    
    return QueryResponse(
        results=[Hit(**h) for h in final_hits],
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
        workers=2,
        loop="asyncio",
        http="h11",
        timeout_keep_alive=30,
    )
