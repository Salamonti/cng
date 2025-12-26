#!/usr/bin/env python3
"""
prompt_builder.py

Flexible prompt templates for the RAG pipeline with clear separation of
retrieved context and user query. Works for both general Q&A and consult-note
commentary (do not rewrite notes; add a short evidence-focused comment on
Impression/Plan).

Usage (library)
  from prompt_builder import build_prompt, make_messages

  # Using packaged output from the /query API
  context = query_response["context"]         # numbered summaries
  references = query_response["references"]   # list of refs

  text, meta = build_prompt(
      mode="qa",
      query="NSTEMI dual antiplatelet therapy",
      context=context,
      references=references,
      max_context_words=1200,
  )

  # For chat backends, create messages
  messages = make_messages(mode="qa", query="...", context=context, references=references)

Modes
  - "qa"            : General Q&A (short, cited)
  - "consult_comment": Add-on comment for a clinical note (Impression/Plan only)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Tuple


def _trim_words(s: str, max_words: int) -> str:
    if max_words <= 0:
        return s
    words = s.split()
    if len(words) <= max_words:
        return s
    return " ".join(words[:max_words]) + "..."


def _format_references(refs: List[Dict[str, Any]]) -> str:
    lines: List[str] = []
    for r in refs[:10]:  # cap for compactness
        idx = r.get("index") or "-"
        title = r.get("title") or ""
        src = r.get("source") or r.get("society") or ""
        year = r.get("year") or ""
        link = r.get("link") or ""
        parts = [f"[{idx}]", src, title]
        if year:
            parts.append(str(year))
        if link:
            parts.append(str(link))
        lines.append(" | ".join([p for p in parts if p]))
    return "\n".join(lines)


def _system_prompt(mode: str) -> str:
    if mode == "consult_comment":
        return (
            "You are a clinical assistant who writes brief, evidence-backed comments.\n"
            "Task: Provide a structured addendum for Impression/Plan sections only.\n"
            "Rules:\n"
            "- Use ONLY the retrieved context below for clinical claims.\n"
            "- Output plain-text paragraphs (no bullets or numbering).\n"
            "- Cover exactly three paragraphs in this order: Differential considerations; Impression/Plan alignment; Key management guidance.\n"
            "- Do NOT include inline citations or bracketed numbers.\n"
            "- Do NOT rewrite the note; do NOT restate the full history.\n"
        )
    # default QA
    return (
        "You are a concise clinical assistant.\n"
        "Answer strictly from the retrieved context below.\n"
        "Rules:\n"
        "- Keep the answer focused (5–8 sentences).\n"
        "- Do NOT include inline citations or bracketed numbers.\n"
        "- If no relevant context, say you cannot find support.\n"
    )


def _user_template(mode: str) -> str:
    if mode == "consult_comment":
        return (
            "Retrieved Context (summaries):\n{context}\n\n"
            "User Query:\n{query}\n\n"
            "Write a concise, evidence-backed comment to accompany the Impression/Plan. Output exactly three paragraphs:\n"
            "1) Differential considerations: offer 2-3 alternative diagnoses with rationale grounded in the context; if none are supported, say so.\n"
            "2) Impression/Plan alignment: state whether the existing plan aligns with evidence, flagging missing tests, contraindications, or unsupported steps.\n"
            "3) Key management guidance: highlight actionable next steps that respect the evidence and patient constraints.\n"
            "Use only the provided context; do not add inline citations.\n"
            "References:\n{references}\n"
        )
    return (
        "Retrieved Context (summaries):\n{context}\n\n"
        "Question:\n{query}\n\n"
        "Give a concise evidence-backed answer.\n"
        "Do not include inline citations; references are appended for the reader.\n"
        "References:\n{references}\n"
    )


def build_prompt(
    mode: str,
    query: str,
    context: str,
    references: List[Dict[str, Any]] | None = None,
    max_context_words: int = 1600,
) -> Tuple[str, Dict[str, Any]]:
    """Return (prompt_text, meta) for a single-prompt LLM.

    - mode: "qa" or "consult_comment"
    - context: numbered summaries; keep separate from query
    - references: list used to render a Reference section
    - max_context_words: optional cap to avoid overlong prompts
    """
    context_c = _trim_words(context or "", max_context_words)
    refs_block = _format_references(references or [])
    sys_block = _system_prompt(mode)
    user_block = _user_template(mode).format(context=context_c, query=query, references=refs_block)
    prompt = sys_block + "\n\n" + user_block
    meta = {"mode": mode, "context_words": len(context_c.split()), "has_references": bool(references)}
    return prompt, meta


def make_messages(
    mode: str,
    query: str,
    context: str,
    references: List[Dict[str, Any]] | None = None,
    max_context_words: int = 1600,
) -> List[Dict[str, str]]:
    """Return chat messages for chat-style models (system + user)."""
    context_c = _trim_words(context or "", max_context_words)
    refs_block = _format_references(references or [])
    system = _system_prompt(mode)
    user = _user_template(mode).format(context=context_c, query=query, references=refs_block)
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
