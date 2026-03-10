# C:\RAG\utils_meta.py
import re
# utils_meta.py
from collections import defaultdict
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Set, Tuple, Optional

def _to_primitive(v: Any) -> Any:
    # allow only str, int, float, bool
    if isinstance(v, (str, int, float, bool)):
        return v
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if v is None:
        return ""  # or "null"
    # lists/sets/tuples → semicolon-joined string (or str(v) if you prefer)
    if isinstance(v, (list, set, tuple)):
        return "; ".join(map(str, v))
    # dicts → flatten then re-run below
    if isinstance(v, dict):
        # handled by flatten
        return v
    # everything else → string
    return str(v)

def flatten_meta(d: Dict[str, Any], prefix: str = "") -> Dict[str, Any]:
    flat: Dict[str, Any] = {}
    for k, v in d.items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            flat.update(flatten_meta(v, key))
        else:
            flat[key] = _to_primitive(v)
    return flat

def sanitize_metas(metas: list[Dict[str, Any]]) -> list[Dict[str, Any]]:
    # 1) flatten + primitive-cast
    cleaned = [flatten_meta(m) for m in metas]

    # 2) enforce a consistent key set across rows
    all_keys = set()
    for m in cleaned:
        all_keys.update(m.keys())
    normalized = []
    for m in cleaned:
        mm = {k: _to_primitive(m.get(k, "")) for k in all_keys}
        normalized.append(mm)
    return normalized


def _extract_year(*values: Any) -> str:
    for val in values:
        if not val:
            continue
        match = re.search(r"(19|20)\d{2}", str(val))
        if match:
            return match.group(0)
    return ""


def normalize_metadata_fields(meta: Dict[str, Any]) -> Dict[str, Any]:
    """Ensure common metadata fields are present and normalized."""
    out = dict(meta or {})
    doc_id = str(out.get("doc_id") or out.get("document_id") or out.get("id") or "").strip()
    if doc_id:
        out["doc_id"] = doc_id

    publisher = out.get("publisher") or out.get("source") or out.get("society")
    if publisher:
        out["publisher"] = str(publisher)
    if "source" not in out and publisher:
        out["source"] = str(publisher)

    if doc_id:
        out.setdefault("group_id", doc_id)

    out["guideline_year"] = _extract_year(
        out.get("guideline_year"),
        out.get("year"),
        out.get("last_updated"),
        out.get("date"),
    )

    doc_type = out.get("doc_type") or out.get("type") or "document"
    out["doc_type"] = str(doc_type).strip().lower()

    specialty = out.get("specialty") or ""
    out["specialty"] = str(specialty).strip()

    geography = out.get("geography") or ""
    out["geography"] = str(geography).strip()

    version = out.get("version") or out.get("last_updated") or ""
    out["version"] = str(version).strip()

    doi = out.get("doi") or ""
    out["doi"] = str(doi).strip()

    nid = out.get("nid") or out.get("nid_id") or ""
    out["nid"] = str(nid).strip()

    return out


# ---------------------------------------------------------------------------
# Quality metric helpers
# ---------------------------------------------------------------------------
_STOPWORDS: Set[str] = {
    "the", "a", "an", "and", "or", "but", "if", "in", "on", "at", "by", "for", "to", "of",
    "with", "as", "is", "are", "was", "were", "be", "been", "this", "that", "these",
    "those", "it", "its", "from", "into", "about", "over", "after", "before", "than",
    "then", "also", "we", "our", "you", "their", "there", "here", "such", "may", "can",
    "could", "should", "would", "will", "not", "no", "yes", "do", "does", "did", "have",
    "has", "had",
}


def _tokenize(text: str) -> List[str]:
    import re
    return [
        tok for tok in re.findall(r"[A-Za-z0-9%]+", (text or "").lower())
        if tok and tok not in _STOPWORDS
    ]


def _doc_id_from_meta(meta: Dict[str, Any]) -> str:
    for key in ("doc_id", "document_id", "id", "source_id", "guideline_id"):
        val = meta.get(key)
        if val:
            return str(val)
    return ""


def gather_quality_counters(
    hits: Iterable[Dict[str, Any]],
    query: str = "",
    context_text: str = "",
) -> Dict[str, Any]:
    hits_list = list(hits or [])
    retrieved_k = len(hits_list)

    doc_ids: Set[str] = set()
    sources: Set[str] = set()
    query_terms = set(_tokenize(query))

    scores: List[float] = []
    specialty_scores: Dict[str, List[float]] = defaultdict(list)
    years: List[int] = []

    coverage_hits = 0
    for h in hits_list:
        meta = h.get("metadata", {}) if isinstance(h, dict) else {}
        doc_id = _doc_id_from_meta(meta)
        if doc_id:
            doc_ids.add(doc_id)
        src = meta.get("source") or meta.get("publisher") or meta.get("society")
        if src:
            sources.add(str(src))

        try:
            score = float(h.get("score", 0.0))
        except Exception:
            score = 0.0
        scores.append(score)

        spec = str(meta.get("specialty") or "").strip()
        if spec:
            specialty_scores[spec].append(score)

        year_str = _extract_year(
            meta.get("guideline_year"),
            meta.get("year"),
            meta.get("timestamp"),
            meta.get("date"),
        )
        if year_str:
            try:
                years.append(int(year_str))
            except Exception:
                pass

        snippet = h.get("summary") or h.get("text") or ""
        if query_terms and set(_tokenize(snippet)) & query_terms:
            coverage_hits += 1

    overlap_tokens = len((context_text or "").split())

    score_mean = round(sum(scores) / len(scores), 4) if scores else 0.0
    score_max = round(max(scores), 4) if scores else 0.0
    score_min = round(min(scores), 4) if scores else 0.0

    specialty_mean_scores = {
        spec: round(sum(vals) / len(vals), 4) for spec, vals in specialty_scores.items() if vals
    }

    year_span = {
        "earliest": min(years),
        "latest": max(years),
    } if years else {}

    return {
        "retrieved_k": retrieved_k,
        "unique_docs": len(doc_ids) if doc_ids else retrieved_k,
        "sources_diversity": len(sources) if sources else retrieved_k,
        "coverage_hits": coverage_hits,
        "overlap_tokens": overlap_tokens,
        "score_mean": score_mean,
        "score_max": score_max,
        "score_min": score_min,
        "specialty_mean_scores": specialty_mean_scores,
        "year_span": year_span,
    }


def normalize_whitespace(text: str) -> str:
    if not text:
        return ""
    return " ".join(str(text).split())


def dedupe_and_normalize_hits(
    hits: Iterable[Dict[str, Any]],
    *,
    max_per_doc: int = 2,
) -> List[Dict[str, Any]]:
    """Deduplicate/normalize while allowing up to max_per_doc chunks per document."""
    cleaned: List[Dict[str, Any]] = []
    seen_ids: Dict[str, int] = {}
    for h in hits or []:
        if not isinstance(h, dict):
            continue
        meta = h.get("metadata") or {}
        doc_id = _doc_id_from_meta(meta)
        text = h.get("text")
        if text is not None:
            h["text"] = normalize_whitespace(text)
        summary = h.get("summary")
        if summary is not None:
            h["summary"] = normalize_whitespace(summary)
        if doc_id:
            used = seen_ids.get(doc_id, 0)
            if used >= max(1, int(max_per_doc)):
                continue
            seen_ids[doc_id] = used + 1
        cleaned.append(h)
    return cleaned
