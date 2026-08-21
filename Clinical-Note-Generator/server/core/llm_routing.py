# server/core/llm_routing.py
"""Per-feature LLM upstream resolution (P2).

Defaults come from repository root service_endpoints.json (applied at app startup).
Explicit environment variables still override. See docs/ENV_VARIABLES.md for variable names.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

DEFAULT_TEXT_PRIMARY = "http://127.0.0.1:8004"
DEFAULT_OCR_PRIMARY = "http://127.0.0.1:8004"
DEFAULT_VISION_FALLBACK_PRIMARY = "http://127.0.0.1:8004"
DEFAULT_ASR_REFINE = DEFAULT_TEXT_PRIMARY


def _strip_url(val: Optional[str]) -> Optional[str]:
    if not val or not str(val).strip():
        return None
    return str(val).strip().rstrip("/")


def _first_non_empty(*candidates: Optional[str]) -> Optional[str]:
    for c in candidates:
        s = _strip_url(c)
        if s:
            return s
    return None


def resolve_llm_urls(feature: str) -> Tuple[Optional[str], Optional[str]]:
    """Return (primary, fallback) base URLs for a first-class routing feature.

    Features:
      - note_gen — clinical note streaming / completion
      - qa_text — QA chat (non-vision)
    """
    if feature == "note_gen":
        p = _first_non_empty(
            os.environ.get("LLM_NOTE_GEN_URL"),
            os.environ.get("NOTEGEN_URL_PRIMARY"),
            DEFAULT_TEXT_PRIMARY,
        )
        f = _first_non_empty(
            os.environ.get("LLM_NOTE_GEN_URL_FALLBACK"),
            os.environ.get("NOTEGEN_URL_FALLBACK"),
        )
        return p, f

    if feature == "qa_text":
        p = _first_non_empty(
            os.environ.get("LLM_QA_TEXT_URL"),
            os.environ.get("NOTEGEN_URL_PRIMARY"),
            DEFAULT_TEXT_PRIMARY,
        )
        f = _first_non_empty(
            os.environ.get("LLM_QA_TEXT_URL_FALLBACK"),
            os.environ.get("NOTEGEN_URL_FALLBACK"),
        )
        return p, f

    if feature == "asr_refine":
        p = _first_non_empty(
            os.environ.get("LLM_ASR_REFINE_URL"),
            os.environ.get("NOTEGEN_URL_PRIMARY"),
            DEFAULT_ASR_REFINE,
        )
        # P2-1: this used to hardcode None regardless of any env var --
        # asr_refine had no fallback support at all, not just an unset one.
        f = _first_non_empty(os.environ.get("LLM_ASR_REFINE_URL_FALLBACK"))
        return p, f

    raise ValueError(f"Unknown LLM routing feature: {feature}")


def resolve_ocr_llm_urls() -> Tuple[Optional[str], Optional[str]]:
    """OCR image pipeline (document camera / uploads)."""
    p = _first_non_empty(
        os.environ.get("LLM_OCR_URL"),
        os.environ.get("OCR_URL_PRIMARY"),
        DEFAULT_OCR_PRIMARY,
    )
    f = _first_non_empty(
        os.environ.get("LLM_OCR_URL_FALLBACK"),
        os.environ.get("OCR_URL_FALLBACK"),
    )
    return p, f


def resolve_qa_vision_urls() -> Tuple[Optional[str], Optional[str]]:
    """Vision QA (image + question). Legacy VISION_QA_* still supported."""
    p = _first_non_empty(
        os.environ.get("LLM_QA_VISION_URL"),
        os.environ.get("VISION_QA_URL"),
        os.environ.get("OCR_URL_PRIMARY"),
        DEFAULT_VISION_FALLBACK_PRIMARY,
    )
    f = _first_non_empty(
        os.environ.get("LLM_QA_VISION_URL_FALLBACK"),
        os.environ.get("VISION_QA_URL_FALLBACK"),
    )
    return p, f


def resolve_rag_comment_urls(cfg: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    """RAG consult comment LLM. URL from LLM_RAG_COMMENT_URL (see service_endpoints.json)."""
    p = _first_non_empty(
        os.environ.get("LLM_RAG_COMMENT_URL"),
    )
    if p:
        f = _strip_url(os.environ.get("LLM_RAG_COMMENT_URL_FALLBACK"))
        return p, f
    return resolve_llm_urls("note_gen")


def resolve_order_request_urls(cfg: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    """Order / imaging request extraction LLM (LLM_ORDER_REQUEST_URL; see service_endpoints.json)."""
    p = _first_non_empty(
        os.environ.get("LLM_ORDER_REQUEST_URL"),
    )
    if p:
        f = _strip_url(os.environ.get("LLM_ORDER_REQUEST_URL_FALLBACK"))
        return p, f
    return resolve_llm_urls("note_gen")


def safe_endpoint_label(url: Optional[str]) -> str:
    """Host:port (or scheme) for logs — no path/query (avoids leaking tokens)."""
    if not url:
        return "(unset)"
    try:
        u = urlparse(url)
        if u.hostname:
            port = f":{u.port}" if u.port else ""
            scheme = (u.scheme or "http") + "://"
            return f"{scheme}{u.hostname}{port}"
        return url[:64]
    except Exception:
        return "(invalid)"


def summarize_llm_routing_for_log(cfg: Optional[Dict[str, Any]] = None) -> List[str]:
    """Lines for startup logging (no secrets)."""
    cfg = cfg if isinstance(cfg, dict) else {}
    lines: List[str] = []

    def add(label: str, p: Optional[str], f: Optional[str]) -> None:
        fb = safe_endpoint_label(f) if f else "—"
        lines.append(f"  {label}: primary={safe_endpoint_label(p)}  fallback={fb}")

    np, nf = resolve_llm_urls("note_gen")
    add("note_gen", np, nf)

    qp, qf = resolve_llm_urls("qa_text")
    add("qa_text", qp, qf)

    op, ofb = resolve_ocr_llm_urls()
    add("ocr", op, ofb)

    vp, vf = resolve_qa_vision_urls()
    add("qa_vision", vp, vf)

    rp, rf = resolve_rag_comment_urls(cfg)
    add("rag_comment", rp, rf)

    op2, of2 = resolve_order_request_urls(cfg)
    add("order_request", op2, of2)

    arp, arf = resolve_llm_urls("asr_refine")
    add("asr_refine", arp, arf)

    return lines
