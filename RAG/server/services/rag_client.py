# C:\RAG\server\services\rag_client.py
import os
import sys
import re
from pathlib import Path
from typing import List, Tuple, Dict


def _rag_root() -> Path:
    """Resolve RAG project root in a cross-platform way.
    Order:
      1) RAG_ROOT env var if set
      2) Two levels up from this file (repo root)
      3) Windows fallback C:\\RAG if it exists
    """
    env = os.environ.get("RAG_ROOT")
    if env:
        return Path(env)
    # Try repo root relative to this file
    here = Path(__file__).resolve()
    repo_root = here.parents[2] if len(here.parents) >= 3 else here.parent
    if repo_root.exists():
        return repo_root
    # Fallback to Windows default if present
    win_default = Path(r"C:\\RAG")
    return win_default


def _ensure_rag_import():
    root = _rag_root()
    if root.exists():
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
    else:
        raise RuntimeError(f"RAG root not found: {root}. Set RAG_ROOT env if different.")


def retrieve_context(query: str, top_k: int = 16, include_keywords: List[str] | None = None, date_from: str | None = None) -> Tuple[str, List[Dict]]:
    _ensure_rag_import()
    from query_api import QueryRequest, hybrid_search_filtered, _package  # type: ignore
    req = QueryRequest(query=query, top_k=top_k, include_keywords=include_keywords or [], date_from=date_from)
    hits = hybrid_search_filtered(req)
    ctx, refs, meta = _package(hits, req.query, {"top_k": req.top_k})
    return ctx, refs


def clean_context(s: str) -> str:
    try:
        import ftfy  # type: ignore
        s = ftfy.fix_text(s)
    except Exception:
        pass
    for k, v in {"\u2022": "- ", "\u2013": "-", "\u2014": "-", "\u00B7": ". "}.items():
        s = s.replace(k, v)
    drop = [
        r"^QUALITY OF EVIDENCE\b",
        r"^BENEFITS VERSUS HARMS\b",
        r"^PATIENT VALUES AND\b",
        r"Guidelines at-a-Glance",
        r"American Academy of Sleep Medicine",
        r"^\s*P:\s*\d{3}-\d{3}-\d{4}\b",
        r"A patient.?s guide",
        r"^B[>\=hHbB].*",
        r"^H[>\=bB].*",
    ]
    out = []
    for ln in s.splitlines():
        if any(re.search(p, ln, flags=re.I) for p in drop):
            continue
        ln = re.sub(r"\[\s*(STRONG|CONDITIONAL|WEAK|MODERATE|LOW|VERY\s*LOW)\s*\]", "", ln, flags=re.I)
        out.append(ln)
    s = "\n".join(out)
    s = re.sub(r"([A-Za-z])-\s*\n\s*([A-Za-z])", r"\1\2", s)
    s = re.sub(r"(?<!\n)\n(?!\n)", " ", s)
    s = re.sub(r"\[\s*\d+(?:\s*[-–]\s*\d+)?(?:\s*,\s*\d+(?:\s*[-–]\s*\d+)?)*\s*\]", "", s)
    s = re.sub(r"\s{3,}", "  ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def get_context(query: str) -> Tuple[str, List[Dict], str]:
    """Convenience: retrieve + clean; returns (ctx_raw, refs, ctx_clean)."""
    ctx, refs = retrieve_context(
        query=query,
        top_k=16,
        include_keywords=["PAP", "CPAP", "adherence", "mask", "follow-up", "therapy", "APAP", "CPAP titration"],
        date_from="2018-01-01",
    )
    return ctx, refs, clean_context(ctx)
