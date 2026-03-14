# composer.py
"""
composer.py — richer consult comment using multiple RAG hits
Drop-in replacement for your consult-comment builder. This keeps things
simple: no inline citations in the body; references are listed at the end.

Usage at the call site (where you previously had
`return OPINION_TEMPLATE.format_map(fields)`):

    return compose_consult_comment(query, hits)

"""
from typing import List, Dict, Any
from contextlib import nullcontext
from metrics import get_current_metrics

# If other parts of your code import OPINION_TEMPLATE, that import can remain;
# it won't be used for the consult comment anymore.
# from prompts import OPINION_TEMPLATE  # optional, not needed here


def format_references(snippets: List[Dict[str, Any]]) -> str:
    out: List[str] = []
    for i, s in enumerate(snippets, start=1):
        md = (s.get("metadata") if isinstance(s, dict) else {}) or {}
        src = md.get("source", "Unknown source")
        title = md.get("title") or md.get("guideline_type") or md.get("lab_test") or "Document"
        section = md.get("section") or md.get("journal") or ""
        out.append(f"[{i}] {src} — {title}" + (f" — {section}" if section else ""))
    return "\n".join(out)


def _best_lines(text: str, limit_words: int = 220) -> str:
    """Return the first 2–3 sentences trimmed to ~limit_words."""
    import re
    sents = re.split(r"(?<=[.!?])\s+", (text or "").strip())
    take = " ".join(sents[:3]).strip()
    words = take.split()
    return " ".join(words[:limit_words]) + ("…" if len(words) > limit_words else "")


def build_cited_opinion(query: str, hits: List[Dict[str, Any]]) -> str:
    metrics = get_current_metrics()
    meter = metrics.measure("build_prompt") if metrics else nullcontext()
    with meter:
        if not hits:
            return (
                "INDEPENDENT OPINION (RAG)\n"
                "Status: RAG not available or no relevant sources retrieved for this query.\n"
                "Action: Cannot provide an evidence-backed opinion.\n"
            )

        # Use top 3-5 hits to construct a structured, compact comment
        top = hits[:5]

        # Optional short extracts for potential future UI use (not printed inline)
        _extracts = [_best_lines(h.get("summary") or h.get("text", ""), 220) for h in top if h.get("text")]

        refs = format_references(top)

        body: List[str] = [
            "INDEPENDENT OPINION (RAG)",
            f"Summary: {query.strip()}",
            "Suggested Impression: See references.",
            "Suggested Management:",
            "1) Diagnosis/Criteria: summarize consistent diagnostic cues across sources.",
            "2) Key Findings to Act On: abnormal labs/imaging, risk flags from sources.",
            "3) Treatment (first-line): drug, dose, route, frequency; note renal/hepatic adjustments when stated.",
            "4) Alternatives/Escalation: when first-line fails or contraindicated.",
            "5) Monitoring & Safety: parameters, intervals, stopping rules.",
            "6) Follow-up: timeframe and what to reassess.",
            "",
            "References:",
            refs,
        ]

    if metrics:
        metrics.record_counter("coverage_hits", len(top))

    return "\n".join(body)


def compose_consult_comment(query: str, hits: List[Dict[str, Any]]) -> str:
    """Small wrapper used at the call-site in notes flow.
    Replace `return OPINION_TEMPLATE.format_map(fields)` with:
        return compose_consult_comment(query, hits)
    """
    return build_cited_opinion(query, hits)
