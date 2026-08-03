# server/routes/notes.py
import asyncio
from datetime import datetime, timezone
import logging
import time
import re
import json
import os
import uuid
from typing import Dict, Optional, Any, List, Tuple
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse, JSONResponse
from sqlmodel import Session

from server.services.note_generator_clean import get_simple_note_generator, SimpleNoteGenerator, ExternalServiceError
from server.core.clinical_output_guard import ClinicalOutputRejected
from server.core.phi_audit import log_phi_access
from server.core.llm_routing import resolve_order_request_urls, resolve_rag_comment_urls
from server.services.rag_http_client import RAGHttpClient
from server.metrics import metrics as global_metrics
from server.core.db import get_session
from server.core.dependencies import get_current_user
from server.core.security import decode_access_token
from server.core.profile_service import (
    ensure_user_preferences,
    merge_templates_for_generation,
    baseline_templates_from_config,
    note_type_uses_other_builder,
)
from server.core.deid.v1 import deidentify_text
from server.core.logging.dataset_logger import log_case_record
from server.core.http_actor import extract_request_actor
from server.core.stores.generation_store import (
    _generation_cache,
    _generation_meta,
    _consult_comment_store,
    _order_request_store,
    cache_lock as _cache_lock,
)
from server.core.prompt.builder import (
    build_prompt_v8 as _build_prompt_v8_impl,
    build_prompt_other as _build_prompt_other_impl,
    build_note_prompt_legacy as _build_note_prompt_legacy_impl,
    _fill_template as _fill_template_impl,
    _cfg_text as _cfg_text_impl,
)
from server.core.streaming.helpers import _stream_response, _stream_response_v8, _stream_qa_response  # noqa: F401 — used dynamically
from server.core.consult.pipeline import _generate_consult_comment as _generate_consult_comment_impl
from server.core.order.pipeline import _generate_order_requests as _generate_order_requests_impl
from server.core.token_limits import (
    clamp_completion_tokens,
    DEFAULT_COMPLETION_TOKENS,
    truncate_text_to_approx_token_budget_str,
)
from server.models.user import User
from server.models.user_encounter import UserEncounter


router = APIRouter()
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# In-memory cache for generation feedback + metadata
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------
CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "config.json"

OTHER_NOTE_TYPES = {"referral", "summarize", "custom", "procedure", "pre_encounter_prep"}


def load_config() -> Dict:
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


# Always use llama-server for consistent performance
print("Using llama-server for note generation (efficient, single persistent process)")
note_gen = get_simple_note_generator("note_gen")

END_MARKER = "__STREAM_END__"
NOTE_STOP_SEQUENCES = [
    "Thank you for involving me in this patient's care.",
    "Thank you for accepting this referral.",
    "Sincerely,",
]
NOTE_END_TOKEN = "END_OF_NOTE"
NOTE_STOP_TOKENS = [NOTE_END_TOKEN, f"\n{NOTE_END_TOKEN}"]
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x09\x0b-\x1f\x7f]")
_FORMAT_SYMBOLS_RE = re.compile(r"[#*=_+\-]{3,}")
NUMERIC_UNIT_STYLE_INSTRUCTION = (
    "FINAL OUTPUT STYLE: Use numerals with compact clinical units in the final note "
    "(e.g., 5 mg, 100 mcg, 10 mL, 2 units). "
    "Do not spell out dose numbers/units when a compact form is appropriate. "
    "For medication lines, prefer: Medication Dose Unit Route Frequency when available."
)


def _service_error_detail(err: ExternalServiceError) -> Dict[str, Any]:
    return {
        "service": err.service,
        "primary": err.primary_url,
        "fallback": err.fallback_url,
        "errors": err.errors,
    }


def _has_minimum_signal(text: str, *, min_alnum: int) -> bool:
    """Return True if text still contains enough alphanumeric characters to be useful."""
    if not text:
        return False
    return sum(1 for ch in text if ch.isalnum()) >= min_alnum


def _sanitize_chart_text(text: str) -> str:
    """Reduce sequences that can accidentally trip model stop logic."""
    if not text:
        return ""
    cleaned = _CONTROL_CHARS_RE.sub(" ", text)
    cleaned = _FORMAT_SYMBOLS_RE.sub(" ", cleaned)
    cleaned = re.sub(r"\s{3,}", "  ", cleaned)
    cleaned = cleaned.strip()
    return cleaned if _has_minimum_signal(cleaned, min_alnum=10) else text.strip()


def _sanitize_transcription_text(text: str) -> str:
    if not text:
        return ""
    cleaned = _CONTROL_CHARS_RE.sub(" ", text)
    cleaned = re.sub(r"\s{3,}", "  ", cleaned)
    cleaned = cleaned.strip()
    return cleaned if _has_minimum_signal(cleaned, min_alnum=6) else text.strip()


def _strip_note_end_marker(text: str) -> str:
    if not text:
        return ""
    idx = text.find(NOTE_END_TOKEN)
    if idx == -1:
        return text
    return text[:idx].rstrip()


def _logs_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "logs"


def _missed_q_path() -> Path:
    d = _logs_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d / "rag_missed_questions.jsonl"


def _append_missed_question(record: Dict[str, Any]) -> None:
    try:
        p = _missed_q_path()
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _split_prompt(prompt: str) -> Dict[str, str]:
    text = prompt or ""
    system = ""
    user = text

    if "SYSTEM:\n" in text:
        after = text.split("SYSTEM:\n", 1)[1]
        if "\n\nUSER:\n" in after:
            system, user = after.split("\n\nUSER:\n", 1)
        else:
            system = after
            user = ""
    elif "USER:\n" in text:
        user = text.split("USER:\n", 1)[1]

    if "\n\nASSISTANT:" in user:
        user = user.split("\n\nASSISTANT:", 1)[0]

    return {"system": system.strip(), "user": user.strip()}


def _deid_fields(fields: Dict[str, str]) -> Dict[str, Any]:
    out_fields: Dict[str, Any] = {}
    totals: Dict[str, int] = {}
    leak_any = False
    for key, raw_val in fields.items():
        result = deidentify_text(raw_val or "")
        out_fields[key] = result
        counts = result.get("redaction_counts", {}) or {}
        for cname, cval in counts.items():
            totals[cname] = int(totals.get(cname, 0)) + int(cval or 0)
        leak_flags = result.get("leak_flags", {}) or {}
        leak_any = leak_any or bool(leak_flags.get("raw_has_any"))
    return {
        "fields": out_fields,
        "redaction_counts_total": totals,
        "leak_flags": {"raw_has_any": leak_any},
    }


def _model_meta() -> Dict[str, str]:
    endpoint = "/v1/chat/completions" if bool(getattr(note_gen, "use_chat_api", False)) else "/completion"
    return {
        "chat_model_name": str(getattr(note_gen, "chat_model_name", "") or ""),
        "model_path": str(getattr(note_gen, "model_path", "") or ""),
        "endpoint_used": endpoint,
    }


def _log_case_completion(
    *,
    case_id: str,
    created_at: str,
    duration_s: float,
    note_type: str,
    pipeline: str,
    prompt: str,
    input_fields: Dict[str, str],
    output_text: str,
    prompt_tokens: int,
    completion_tokens: int,
    actor: Dict[str, Optional[str]],
) -> None:
    prompt_parts = _split_prompt(prompt)
    prompt_deid = {
        "system": deidentify_text(prompt_parts.get("system", "")).get("text", ""),
        "user": deidentify_text(prompt_parts.get("user", "")).get("text", ""),
    }
    output_deid = deidentify_text(output_text or "")
    case_record = {
        "case_id": case_id,
        "created_at": created_at,
        "duration_s": round(float(duration_s), 3),
        "note_type": note_type,
        "pipeline": pipeline,
        "user_id": actor.get("user_id"),
        "user_email": actor.get("user_email"),
        "model": _model_meta(),
        "prompt": prompt_deid,
        "input_deid": _deid_fields(input_fields),
        "output_deid": {
            "note": output_deid.get("text", ""),
            "redaction_counts": output_deid.get("redaction_counts", {}),
            "leak_flags": output_deid.get("leak_flags", {}),
        },
        "tokens": {
            "prompt_tokens": int(prompt_tokens),
            "completion_tokens": int(completion_tokens),
            "method": "approx_word_count",
        },
        "feedback_snapshot": None,
    }
    log_case_record(case_record)


def truncate_to_context_length_tokens(text: str, max_tokens: int) -> str:
    """Approximate token truncation with visible marker (P9: see core.token_limits)."""
    return truncate_text_to_approx_token_budget_str(text, max_tokens)


def _meta_year(meta: Dict[str, Any]) -> int:
    """Extract a four-digit year from common metadata fields."""
    try:
        y = str(meta.get("timestamp") or meta.get("year") or meta.get("date") or "").strip()
        if len(y) >= 4 and y[:4].isdigit():
            return int(y[:4])
    except Exception:
        pass
    return 0


def _first_nonempty(meta: Dict[str, Any], keys: Tuple[str, ...]) -> str:
    for key in keys:
        val = meta.get(key)
        if val:
            text = str(val).strip()
            if text:
                return text
    return ""


def _normalize_reference_items(
    raw_refs: List[Dict[str, Any]],
    *,
    cap: Optional[int] = None,
    sort_key: Optional[Any] = None,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Flatten RAG results into UI-friendly reference entries plus evidence chunks."""
    entries: List[Dict[str, Any]] = []
    for item in raw_refs or []:
        if not isinstance(item, dict):
            continue
        md = dict(item.get("metadata") or {})
        # Prefer the shorter summary field; fall back to raw text if missing.
        text = str(item.get("summary") or item.get("text") or "")
        if len(text) > 280:
            cutoff = text[:280]
            last_space = cutoff.rfind(" ")
            if last_space > 220:
                cutoff = cutoff[:last_space]
            text = cutoff.strip() + " ..."
        title = _first_nonempty(
            md,
            (
                "title",
                "guideline_type",
                "lab_test",
                "paper_title",
                "section",
                "doc_id",
                "document_id",
                "id",
            ),
        )
        source = _first_nonempty(md, ("source", "society", "publisher"))
        section = _first_nonempty(md, ("section", "heading", "chapter"))
        link = _first_nonempty(md, ("link", "url"))
        year = _meta_year(md)
        try:
            score = float(item.get("score", 0.0))
        except Exception:
            score = 0.0
        doc_id = _first_nonempty(md, ("doc_id", "document_id", "id")) or str(item.get("id") or "").strip()

        if not title:
            if doc_id:
                title = doc_id
            elif source:
                title = source
            else:
                title = "Document"

        entry = {
            "title": title,
            "source": source,
            "section": section,
            "link": link,
            "year": year,
            "score": score,
            "doc_id": doc_id,
            "_text": text,
        }
        entries.append(entry)

    if sort_key is not None:
        entries.sort(key=sort_key, reverse=True)
    if cap is not None and cap >= 0:
        entries = entries[:cap]

    full_chunks: List[str] = []
    for idx, entry in enumerate(entries, start=1):
        text = entry.pop("_text", "")
        if text:
            full_chunks.append(text)
        entry["index"] = idx
    return entries, full_chunks

# ---------------------------------------------------------------------------
# Stream-safe and final cleaners
# ---------------------------------------------------------------------------

_CONFLICTS_HEADING_LINE_RE = re.compile(
    r"^conflicts?(?:\s+section)?(?:\s+info)?\s*:?\s*$",
    re.IGNORECASE,
)
_AGE_OMITTED_PLACEHOLDER_RE = re.compile(
    r"\[?\s*age\s+omitted\s*\]?|"
    r"\[?\s*age\s+not\s+(?:provided|available|documented|calculated)\s*\]?|"
    r"\[?\s*age\s+unknown\s*\]?",
    re.IGNORECASE,
)
_PATIENT_NOTE_START_MARKERS = ("Patient Identification", "Patient ID")


def _is_conflicts_heading_line(line: str) -> bool:
    t = (line or "").strip()
    t = re.sub(r"^#+\s*", "", t)
    t = re.sub(r"\*+", "", t).strip()
    return bool(_CONFLICTS_HEADING_LINE_RE.match(t))


def _is_markdown_section_heading_line(line: str) -> bool:
    return bool(re.match(r"^\s*#{1,3}\s+\S", (line or "").strip()))


def _is_physical_exam_heading_line(line: str) -> bool:
    t = (line or "").strip()
    t = re.sub(r"^#+\s*", "", t)
    t = re.sub(r"\*+", "", t).strip().rstrip(":").lower()
    return t in ("physical exam", "physical examination")


def _plain_physical_exam_line(line: str) -> str:
    return re.sub(r"\*+", "", (line or "").strip()).strip()


def _is_whole_physical_exam_deferred_line(line: str) -> bool:
    plain = _plain_physical_exam_line(line)
    return bool(
        re.match(
            r"(?i)^physical\s+exam(?:ination)?\s*:\s*deferred\s*\.?\s*$",
            plain,
        )
    )


def _is_physical_exam_filler_line(line: str) -> bool:
    """Per-system negatives and whole-section 'not documented' — belong in Conflicts, not PE."""
    if not (line or "").strip():
        return False
    if _is_whole_physical_exam_deferred_line(line):
        return False
    plain = _plain_physical_exam_line(line)
    if re.match(
        r"(?i)^physical\s+exam(?:ination)?\s*:\s*not\s+documented\s*\.?\s*$",
        plain,
    ):
        return True
    return bool(
        re.match(
            r"(?i)^.+?:\s*"
            r"(?:not\s+documented|not\s+assessed|not\s+examined|not\s+performed|"
            r"not\s+completed|none\s+documented|no\s+abnormalit(?:y|ies)\s+documented|"
            r"deferred)\s*\.?\s*$",
            plain,
        )
    )


def _prune_physical_exam_filler_lines(text: str) -> str:
    """Drop per-system 'Not documented' lines from Physical Exam; keep documented findings only."""
    if not text:
        return text
    lines = text.replace("\r\n", "\n").split("\n")
    out: List[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not _is_physical_exam_heading_line(line):
            out.append(line)
            i += 1
            continue
        out.append(line)
        i += 1
        while i < len(lines):
            nxt = lines[i]
            if _is_conflicts_heading_line(nxt) or (
                _is_markdown_section_heading_line(nxt)
                and not _is_physical_exam_heading_line(nxt)
            ):
                break
            if not _is_physical_exam_filler_line(nxt):
                out.append(nxt)
            i += 1
    return "\n".join(out)


def strip_conflicts_section(note_text: str) -> str:
    """Return note body without the Conflicts section (for orders/referrals and plan focus)."""
    if not note_text:
        return ""
    text = note_text.replace("\r\n", "\n")
    lines = text.split("\n")
    start_idx = -1
    for i, line in enumerate(lines):
        if _is_conflicts_heading_line(line):
            start_idx = i
            break
    if start_idx < 0:
        return text.strip()
    main = "\n".join(lines[:start_idx]).rstrip()
    return main.strip()


def _strip_model_reasoning_markup(text: str) -> str:
    """Remove model chain-of-thought wrappers from clinical output."""
    if not text:
        return text
    out = text
    for tag in ("redacted_thinking", "think", "thinking"):
        out = re.sub(
            rf"<{tag}>.*?</{tag}>",
            "",
            out,
            flags=re.IGNORECASE | re.DOTALL,
        )
    return out


def _sanitize_age_omitted_placeholders(text: str) -> str:
    """Remove [age omitted] style placeholders from identification lines; conflicts-only rule."""
    if not text:
        return text
    lines_out: List[str] = []
    for line in text.split("\n"):
        if not _AGE_OMITTED_PLACEHOLDER_RE.search(line):
            lines_out.append(line)
            continue
        low = line.lower()
        if not any(
            k in low
            for k in (
                "patient identification",
                "patient id",
                "presented today",
                "presented for",
                "year-old",
                "year old",
                " y.o.",
            )
        ):
            lines_out.append(line)
            continue
        cleaned = _AGE_OMITTED_PLACEHOLDER_RE.sub("", line)
        cleaned = re.sub(r",\s*,", ",", cleaned)
        cleaned = re.sub(r"\s{2,}", " ", cleaned)
        cleaned = re.sub(r",\s+(?=presented\b)", " ", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r",\s*$", "", cleaned.strip())
        lines_out.append(cleaned)
    return "\n".join(lines_out)


def _trim_leading_non_note_content(cleaned: str) -> str:
    """Drop preamble before Patient Identification / Patient ID when it appears early."""
    first_200 = cleaned[:200]
    for marker in _PATIENT_NOTE_START_MARKERS:
        if marker in first_200:
            parts = cleaned.split(marker, 1)
            if len(parts) > 1:
                return marker + parts[1]
    if re.search(r"^ID\s*:", first_200, re.MULTILINE):
        match = re.search(r"^ID\s*:", cleaned, re.MULTILINE)
        if match and match.start() < 200:
            return cleaned[match.start() :]
    return cleaned


def clean_model_output_chunk(chunk: str) -> str:
    """Minimal, stream-safe cleaner: preserves spaces/newlines exactly.
    Removes NUL characters and converts Unicode to ASCII for EMR compatibility.

    CRITICAL: EMR systems don't support Unicode characters. This function converts
    all special Unicode characters (subscripts, superscripts, special punctuation)
    to ASCII equivalents to prevent them from appearing as question marks in EMRs.
    """
    if not chunk:
        return ""
    s = _strip_model_reasoning_markup(chunk).replace("\x00", "")

    # ====== CRITICAL EMR COMPATIBILITY FIX ======
    # Replace Unicode characters that EMRs replace with question marks

    # 1. Replace subscript digits with normal digits (e.g., FEV₁ → FEV1)
    subscript_map = str.maketrans('₀₁₂₃₄₅₆₇₈₉', '0123456789')
    s = s.translate(subscript_map)

    # 2. Replace superscript digits with normal digits (e.g., 10⁹ → 10^9)
    superscript_map = str.maketrans('⁰¹²³⁴⁵⁶⁷⁸⁹', '0123456789')
    s = s.translate(superscript_map)

    # 3. Replace all Unicode hyphens/dashes with ASCII hyphen
    s = s.replace('\u2010', '-')  # Hyphen
    s = s.replace('\u2011', '-')  # Non-breaking hyphen (MAJOR CULPRIT)
    s = s.replace('\u2012', '-')  # Figure dash
    s = s.replace('\u2013', '-')  # En dash
    s = s.replace('\u2014', '-')  # Em dash
    s = s.replace('\u2015', '-')  # Horizontal bar
    s = s.replace('\u2212', '-')  # Minus sign
    s = s.replace('\u00AD', '')   # Soft hyphen (remove completely)

    # 4. Replace special math symbols with ASCII equivalents
    s = s.replace('\u00D7', 'x')   # Multiplication sign → x
    s = s.replace('\u00F7', '/')   # Division sign → /
    s = s.replace('\u2264', '<=')  # Less than or equal
    s = s.replace('\u2265', '>=')  # Greater than or equal
    s = s.replace('\u2260', '!=')  # Not equal
    s = s.replace('\u2248', '~=')  # Approximately equal
    s = s.replace('\u00B1', '+/-') # Plus-minus

    # 5. Replace smart quotes with straight quotes
    s = s.replace('\u2018', "'")   # Left single quote
    s = s.replace('\u2019', "'")   # Right single quote
    s = s.replace('\u201C', '"')   # Left double quote
    s = s.replace('\u201D', '"')   # Right double quote

    # 6. Replace special spaces with normal space
    s = s.replace('\u00A0', ' ')   # Non-breaking space
    s = s.replace('\u2009', ' ')   # Thin space
    s = s.replace('\u200B', '')    # Zero-width space (remove)
    s = s.replace('\u202F', ' ')   # Narrow no-break space
    s = s.replace('\u2007', ' ')   # Figure space
    s = s.replace('\u2008', ' ')   # Punctuation space

    # Post-processing for display: strip formatting markers and note tags
    # Keep whitespace as-is; do not trim newlines.
    try:
        s = s.replace("<note>", "").replace("</note>", "")
        # Only remove standalone formatting markers, not content markers
        # s = s.replace("#", "").replace("*", "")  # DISABLED - was breaking XML content
        # Do NOT remove the backend stream terminator here; clients rely on it to detect stream end.
    except Exception:
        pass
    return s


def clean_model_output_final(text: str) -> str:
    """Conservative cleanup applied once at the end of streaming (optional).
    Keep paragraph structure; do not remove legitimate spaces/newlines.
    """
    if not text:
        return ""
    cleaned = _strip_model_reasoning_markup(text).replace("\x00", "")
    cleaned = _trim_leading_non_note_content(cleaned)

    # Remove a single leading/trailing code fence if leaked
    cleaned = re.sub(r"^\s*```[a-zA-Z0-9_-]*\s*\n", "", cleaned)
    cleaned = re.sub(r"\n```+\s*$", "", cleaned)

    # Remove specific leaked XML-ish wrappers (case-insensitive)
    cleaned = re.sub(r"</?(?:transcription_data|chart_data)>", "", cleaned, flags=re.IGNORECASE)

    # Strip standalone separator/metadata lines (YAML-ish / markdown hr) that some models emit.
    # Keep this conservative: only remove lines that are *only* separators.
    cleaned = re.sub(r"(?m)^[ \t]*(?:-{3,}|={3,}|_{3,})[ \t]*$", "", cleaned)

    # Remove explicit note tags (do not strip markdown markers — client displays/copies as needed)
    cleaned = cleaned.replace("<note>", "").replace("</note>", "")
    cleaned = cleaned.replace("__STREAM_END__", "")
    cleaned = _strip_note_end_marker(cleaned)

    # Remove leaked chain-of-thought markers and meta-commentary
    # GUARD: Only match these patterns in the FIRST 300 chars to avoid removing legitimate clinical reasoning
    first_300 = cleaned[:300]
    cleaned = re.sub(r'<\|end\|>.*', '', cleaned, flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r'<\|start\|>.*', '', cleaned, flags=re.DOTALL | re.IGNORECASE)

    # Only remove meta-reasoning patterns if they appear very early (first 300 chars)
    for pattern in [
        r'^"We (are asked|have|need to)',
        r'^The question is',
        r'^Wait, that',
        r'^Actually we should',
        r'^So we need to',
        r'^Let\'s (draft|adjust|craft)',
    ]:
        match = re.search(pattern, first_300, re.IGNORECASE | re.MULTILINE)
        if match:
            # Only cut if match is in first 300 chars
            match_full = re.search(pattern, cleaned, re.IGNORECASE | re.MULTILINE)
            if match_full and match_full.start() < 300:
                cleaned = cleaned[match_full.end():].lstrip()
                break

    # Normalize CRLF to LF. Do not insert extra newlines; only cap excessive blank lines.
    cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")
    # If there are 3+ newlines, reduce to 2.
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)

    # Strip markdown emphasis markers (**bold**, __bold__) for EMR/plain compatibility (matches client finalizeNoteText)
    for _ in range(8):
        nxt = re.sub(r"\*\*([^*]+)\*\*", r"\1", cleaned)
        nxt = re.sub(r"__([^_]+)__", r"\1", nxt)
        if nxt == cleaned:
            break
        cleaned = nxt
    cleaned = cleaned.replace("**", "")

    cleaned = _sanitize_age_omitted_placeholders(cleaned)
    cleaned = _prune_physical_exam_filler_lines(cleaned)

    # Trim global ends only
    return cleaned.strip()


def _chunk_text_for_stream(text: str, max_chars: int = 600) -> List[str]:
    """Break a fully generated answer into reasonably sized chunks for streaming."""
    if not text:
        return []

    segments: List[str] = []
    buffer = ""
    for para in text.split("\n\n"):
        para = para.strip("\n")
        if not para:
            continue
        if buffer:
            candidate = buffer + "\n\n" + para
            if len(candidate) <= max_chars:
                buffer = candidate
                continue
            segments.append(buffer)
            buffer = para
        else:
            buffer = para
        while len(buffer) > max_chars:
            segments.append(buffer[:max_chars])
            buffer = buffer[max_chars:]
    if buffer:
        segments.append(buffer)

    # Final pass to ensure no segment exceeds the limit
    out: List[str] = []
    for seg in segments:
        if len(seg) <= max_chars:
            out.append(seg)
            continue
        start = 0
        while start < len(seg):
            out.append(seg[start : start + max_chars])
            start += max_chars
    return out


async def _collect_note_output(
    prompt: str,
    temperature: Optional[float],
    max_tokens: Optional[int],
    stop_tokens: Optional[List[str]] = None,
) -> str:
    """Run completion to finish and return cleaned text (used for QA)."""
    qa_llm = get_simple_note_generator("qa_text")
    text = await qa_llm.collect_completion(
        prompt, temperature or 0.2, max_tokens or 2048, stop=stop_tokens
    )
    return clean_model_output_final(text).strip()


def _extract_marker_sentences(text: str, markers: List[str]) -> List[str]:
    sentences: List[str] = []
    if not text:
        return sentences
    for sentence in re.split(r'(?<=[.!?])\s+', text):
        lowered = sentence.lower()
        if any(marker.lower() in lowered for marker in markers):
            sentences.append(sentence.strip())
    return sentences


def _extract_plan_section(note_text: str) -> str:
    """Try to isolate the Plan (or Assessment & Plan) section for downstream helpers."""
    if not note_text:
        return ""

    # Accept common variants: "Plan:", "## Plan", "## Assessment & Plan", "A/P:", etc.
    # #{0,3}\s* handles markdown ATX headings (##, ###) as well as plain text headers.
    header_re = re.compile(
        r"(?im)^\s*#{0,3}\s*(assessment\s*(?:&|and)?\s*plan|assessment\s*/\s*plan|a/p|plan)\s*(?::|-)?\s*$"
    )
    header_inline_re = re.compile(
        r"(?im)^\s*#{0,3}\s*(assessment\s*(?:&|and)?\s*plan|assessment\s*/\s*plan|a/p|plan)\s*(?::|-)\s*"
    )

    # Any new section heading (## ...) ends the Plan body — e.g. ## Conflicts after ## Plan.
    any_section_re = re.compile(r"(?im)^\s*#{1,3}\s*\S")

    def _end_of_section(rest: str) -> int:
        """Return the index in `rest` where the next section heading starts, or len(rest)."""
        m = any_section_re.search(rest)
        return m.start() if m else len(rest)

    # 1) Inline header with content on the same line
    inline_match = header_inline_re.search(note_text)
    if inline_match:
        start = inline_match.end()
        rest = note_text[start:]
        end = start + _end_of_section(rest)
        return note_text[start:end].strip()

    # 2) Standalone header line, then capture following block
    header_match = header_re.search(note_text)
    if header_match:
        start = header_match.end()
        rest = note_text[start:]
        end = start + _end_of_section(rest)
        return note_text[start:end].strip()

    return ""


def _extract_json_payload(text: str) -> Optional[Dict[str, Any]]:
    if not text:
        return None
    cleaned = text.strip()
    cleaned = re.sub(r"^\s*```[a-zA-Z0-9_-]*\s*\n", "", cleaned)
    cleaned = re.sub(r"\n```+\s*$", "", cleaned)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    snippet = cleaned[start : end + 1]
    try:
        return json.loads(snippet)
    except Exception:
        return None


def _normalize_request_items(items: Any) -> List[Dict[str, str]]:
    if not isinstance(items, list):
        return []
    out: List[Dict[str, str]] = []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        category = str(raw.get("category") or raw.get("type") or "Other").strip() or "Other"
        title = str(raw.get("title") or raw.get("label") or raw.get("order") or "").strip()
        request = str(raw.get("request") or raw.get("text") or raw.get("sentence") or "").strip()
        if not request and title:
            request = title
        if not request:
            continue
        title = clean_model_output_chunk(title) if title else ""
        request = clean_model_output_chunk(request)
        if len(title) > 120:
            title = title[:117].rstrip() + "..."
        if len(request) > 800:
            request = request[:797].rstrip() + "..."
        out.append(
            {
                "category": category[:32],
                "title": title,
                "request": request,
            }
        )
    return out


# Lines that are only empty radiology-form labels (model sometimes emits these despite instructions)
_RAD_FORM_LABEL_LINE = re.compile(
    r"(?im)^\s*(Study Requested|Clinical Indication|Pertinent Findings\s*/\s*History|"
    r"Clinical Question to Answer|Prior Relevant Imaging|Urgency)\s*:\s*$"
)


def _strip_radiology_form_label_lines(text: str) -> str:
    """Remove lines that are nothing but legacy requisition labels (no content after colon)."""
    if not text:
        return ""
    out_lines: List[str] = []
    for ln in text.splitlines():
        stripped = ln.strip()
        if _RAD_FORM_LABEL_LINE.match(stripped):
            continue
        out_lines.append(ln.rstrip())
    return "\n".join(out_lines).strip()


def _format_imaging_request(text: str) -> str:
    """Normalize imaging/procedure request to one concise paragraph (no empty header skeletons)."""
    if not text:
        return ""
    text = _strip_radiology_form_label_lines(text)
    cleaned = clean_model_output_final(text).strip()
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    lines = [ln.strip() for ln in cleaned.splitlines() if ln.strip()]
    if not lines:
        return ""

    # Legacy model output: labeled lines — keep only substantive text after each colon.
    legacy_headers = (
        "study requested:",
        "clinical indication:",
        "pertinent findings",
        "clinical question to answer:",
        "prior relevant imaging:",
        "urgency:",
    )
    parts: List[str] = []
    for ln in lines:
        low = ln.lower()
        matched_header = False
        for h in legacy_headers:
            if low.startswith(h):
                matched_header = True
                rest = ln.split(":", 1)[-1].strip() if ":" in ln else ""
                if rest:
                    parts.append(rest)
                break
        if not matched_header:
            parts.append(ln)

    merged = " ".join(parts)
    merged = re.sub(r"\s+", " ", merged).strip()
    if merged:
        return merged

    # Model returned only empty "Label:" lines — no usable text
    if lines and all(":" in ln and not ln.split(":", 1)[-1].strip() for ln in lines):
        return ""

    return re.sub(r"\s+", " ", " ".join(lines)).strip()


def _merge_medication_items(items: List[Dict[str, str]]) -> List[Dict[str, str]]:
    meds: List[Dict[str, str]] = []
    others: List[Dict[str, str]] = []
    for item in items:
        if (item.get("category") or "").lower() == "medication":
            meds.append(item)
        else:
            others.append(item)

    if not meds:
        return items

    # Build unique medication lines (dedupe by line + approximate drug stem)
    lines: List[str] = []
    seen: set[str] = set()
    seen_drug: set[str] = set()

    def _drug_stem(line: str) -> str:
        l = re.sub(r"\s+", " ", (line or "").strip().lower())
        l = re.sub(r"^[\-•*\d.\)\(\s]+", "", l)
        # cut at first dose/route/frequency marker
        m = re.search(r"\b\d+(?:\.\d+)?\b|\b(po|iv|im|sc|sq|subq|bid|tid|qid|qhs|daily|weekly|prn)\b", l)
        head = l[: m.start()].strip() if m else l
        head = re.sub(r"[^a-z0-9\- ]", "", head)
        return head[:40].strip()

    for item in meds:
        req = (item.get("request") or "").strip()
        for line in req.splitlines():
            line = line.strip()
            if not line:
                continue
            norm = re.sub(r"\s+", " ", line).strip().lower()
            stem = _drug_stem(line)
            if norm in seen:
                continue
            if stem and stem in seen_drug and len(norm) < 90:
                continue
            seen.add(norm)
            if stem:
                seen_drug.add(stem)
            lines.append(line)

    if not lines:
        return others

    merged = {
        "category": "Medication",
        "title": "Medications",
        "request": "\n".join(lines),
    }
    return others + [merged]


def _dedupe_request_items(items: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """Remove duplicate request items and collapse repeated Lab requests."""
    if not items:
        return items

    seen: set[str] = set()
    out: List[Dict[str, str]] = []
    lab_lines: List[str] = []
    lab_seen: set[str] = set()

    def _norm(s: str) -> str:
        s = re.sub(r"\s+", " ", (s or "").strip().lower())
        return s

    for item in items:
        category = (item.get("category") or "").strip()
        title = (item.get("title") or "").strip()
        request = (item.get("request") or "").strip()
        key = _norm(f"{category}|{title}|{request}")
        if not key or key in seen:
            continue
        seen.add(key)

        if category.lower() == "lab":
            for line in request.splitlines():
                line = line.strip()
                if not line:
                    continue
                n = _norm(line)
                if n in lab_seen:
                    continue
                lab_seen.add(n)
                lab_lines.append(line)
            continue

        out.append(item)

    if lab_lines:
        out.append(
            {
                "category": "Lab",
                "title": "Labs",
                "request": "\n".join(lab_lines),
            }
        )

    return out

# Backward-compatible alias if other modules import this name
clean_model_output = clean_model_output_chunk


# ---------------------------------------------------------------------------
# RAG integration helpers
# ---------------------------------------------------------------------------

def _rag_client_from_cfg(cfg: Dict) -> RAGHttpClient:
    base = os.environ.get("RAG_URL")
    if not base:
        raise HTTPException(status_code=500, detail="RAG_URL not set in environment")
    timeout_ms = 90_000
    return RAGHttpClient(base, timeout=timeout_ms)


def _qa_rewrite_prompt(question: str, draft_answer: str, evidence: str) -> str:
    """Prompt template to refine a baseline answer using RAG evidence."""
    header = (
        "You are reviewing a draft clinical answer. Your job is to keep the draft's structure and clarity, "
        "but tighten accuracy using the evidence that follows.\n\n"
        "RULES:\n"
        "- Preserve helpful sentences from the draft unless they conflict with evidence.\n"
        "- If evidence supports clarifying or updating a statement, rewrite that portion succinctly.\n"
        "- If evidence is silent on part of the draft, keep the draft wording (do NOT invent new details).\n"
        "- If evidence contradicts the draft, correct the statement and mention the key evidence.\n"
        "- If evidence is insufficient overall, keep the draft answer and append a short caution such as "
        "'Current evidence review found no additional guidance.'\n"
        "- Output readable prose; bullets or short lists are fine when helpful.\n"
    )
    return (
        f"{header}\n"
        f"CLINICAL QUESTION:\n{question.strip() or 'Not provided.'}\n\n"
        f"DRAFT ANSWER:\n{draft_answer.strip() or 'No draft answer available.'}\n\n"
        f"EVIDENCE CONTEXT:\n{(evidence or '').strip() or 'Evidence context is empty.'}\n\n"
        "REVISED ANSWER:\n"
    )


def _qa_prompt_with_rag(question: str, context_clean: str) -> str:
    header = (
        "You are a senior consultant answering a clinical question. Your response must be direct, concise, and immediately useful.\n\n"
        "CRITICAL GROUNDING REQUIREMENT:\n"
        "- Answer ONLY using information from the Evidence Context provided below.\n"
        "- If the Evidence Context is empty or insufficient to answer the question, respond with: 'Insufficient evidence available to answer this question reliably. Please consult current clinical guidelines or specialist input.'\n"
        "- Do NOT answer from general medical knowledge if evidence is not provided.\n"
        "- Do NOT invent, assume, or speculate beyond what the evidence explicitly states.\n\n"
        "FORMAT REQUIREMENTS:\n"
        "- If the answer is long, write it in 2-6 paragraphs\n"
        "- Separate each paragraph with 2 blank lines\n"
        "- You may use headings, bullets, numbering, bold/italic emphasis, or tables when they improve clarity\n"
        "- Include specific drug names with doses, route, and timing when applicable\n\n"
        "CONTENT REQUIREMENTS:\n"
        "- Cover: assessment, risk stratification, treatment options with dosing, monitoring, special considerations\n"
        "- Do NOT include citations or reference numbers\n"
        "- STOP after your answer - no reasoning, no meta-commentary, no alternative questions\n"
        "- DO NOT ADD ANYTHING ELSE TO THE ANSWER BEYOND WHAT IS REQUESTED\n"
        "- YOU DO NOT HAVE TO USE ALL AVAILABLE TOKENS - BE CONCISE AND FOCUSED\n"
        "- DO NOT REPEAT THE QUESTION IN YOUR ANSWER\n"
        "- DO NOT REPEAT INFORMATION ACROSS PARAGRAPHS\n\n"
    )
    ctx_section = f"Evidence Context:\n{context_clean}\n\n" if context_clean.strip() else "Evidence Context: [No evidence available]\n\n"
    q_section = f"Question: {question.strip()}\n\n"
    instruction = "Answer (2-3 paragraphs with blank lines unless bullets/tables are clearer, or state insufficient evidence if applicable):\n\n"
    return header + ctx_section + q_section + instruction


def _weak_evidence(refs: List[Dict[str, Any]], context: str) -> bool:
    if not refs:
        return True
    try:
        # Simple heuristic: if mean score very low and context short
        scores = [float(r.get("score", 0.0)) for r in refs]
        mean_score = sum(scores) / max(1, len(scores))
        return mean_score < 0.1 or len(context.strip()) < 40
    except Exception:
        return False


# Longest-first so "Assessment and Plan" wins over "Assessment" / "Plan".
_RAG_FOCUS_HEADING_ALIASES: List[Tuple[str, str]] = [
    ("assessment and plan", "Assessment and Plan"),
    ("assessment/plan", "Assessment and Plan"),
    ("assessment & plan", "Assessment and Plan"),
    ("history of present illness", "History of Present Illness"),
    ("physical examination", "Physical Exam"),
    ("physical exam", "Physical Exam"),
    ("review of systems", "Review of Systems"),
    ("impressions", "Impression"),
    ("impression", "Impression"),
    ("assessment", "Assessment"),
    ("subjective", "Subjective"),
    ("objective", "Objective"),
    ("conflicts section", "Conflicts"),
    ("conflict section", "Conflicts"),
    ("conflicts", "Conflicts"),
    ("conflict", "Conflicts"),
    ("plan", "Plan"),
    ("hpi", "HPI"),
    ("ros", "Review of Systems"),
    ("a/p", "Assessment and Plan"),
]


def _strip_leading_list_prefix(line: str) -> str:
    t = line.strip()
    for _ in range(4):
        m = re.match(r"^(?:\d{1,3}\s*[.)]\s*|[(]\s*[a-zA-Z]\s*[)]\s*|[-*•]\s*)", t)
        if not m:
            break
        t = t[m.end() :].lstrip()
    return t


def _rag_focus_heading_match(line: str) -> Optional[Tuple[str, str]]:
    """Match a section heading line (flexible caps, :, bullets, numbers). Returns (canonical, rest_of_line)."""
    raw = line.strip()
    if not raw:
        return None
    core = _strip_leading_list_prefix(raw)
    core = re.sub(r"^#+\s*", "", core).strip()
    if not core:
        return None
    for alias, canonical in _RAG_FOCUS_HEADING_ALIASES:
        m = re.match(rf"(?is)^{re.escape(alias)}\b\s*:?\s*(.*)$", core)
        if m:
            return canonical, (m.group(1) or "").strip()
    return None


def _extract_rag_focus_sections(note_text: str) -> Tuple[str, List[str]]:
    """Pull clinical section bodies for RAG focus; tolerate bullets, numbering, markdown #, case, optional :.

    If nothing reliable is found or the result is too thin, fall back to tail window or the whole note.
    """
    if not (note_text or "").strip():
        return "", []

    text = note_text.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    sections: List[Tuple[str, List[str]]] = []
    cur_title: Optional[str] = None
    cur_body: List[str] = []

    for line in lines:
        hm = _rag_focus_heading_match(line)
        if hm:
            if cur_title is not None:
                sections.append((cur_title, cur_body))
            cur_title, first = hm
            cur_body = []
            if first:
                cur_body.append(first)
        else:
            if cur_title is not None:
                cur_body.append(line)

    if cur_title is not None:
        sections.append((cur_title, cur_body))

    used: List[str] = []
    matches: List[str] = []
    for title, body_lines in sections:
        body = "\n".join(body_lines).strip()
        if body:
            matches.append(f"{title}:\n{body}")
            used.append(title)

    focus = "\n\n".join(matches).strip()

    # Too little structured content — fall back to tail or full note (short notes).
    min_chars = 48
    toks = text.split()
    if not focus.strip() or len(focus.strip()) < min_chars:
        if len(toks) <= 420:
            return text.strip(), ["fallback_whole_note"]
        tw = _rag_tail_window(text, max_tokens=500, min_tokens=200)
        return tw, ["fallback_tail_window"]

    return focus, used


def _rag_tail_window(text: str, *, max_tokens: int = 500, min_tokens: int = 300) -> str:
    """Return a tail window of the note to keep RAG queries compact."""
    tokens = text.split()
    if not tokens:
        return ""
    total = len(tokens)
    # Use up to 50% of the note, but clamp to [min_tokens, max_tokens].
    target = min(max(min_tokens, int(total * 0.5)), max_tokens)
    if total <= target:
        return text.strip()
    return " ".join(tokens[-target:]).strip()


def _rag_head_window(text: str, *, max_tokens: int = 220, min_tokens: int = 60) -> str:
    """Return a head window (HPI / opening) — pairs with tail for consult RAG when full_note strategy is used."""
    tokens = text.split()
    if not tokens:
        return ""
    total = len(tokens)
    target = min(max(min_tokens, int(total * 0.25)), max_tokens)
    if total <= target:
        return text.strip()
    return " ".join(tokens[:target]).strip()


def _fallback_focus_from_note(note_text: str, *, max_lines: int = 16) -> str:
    """Fallback focus extractor when section headers are missing/unreliable."""
    if not note_text:
        return ""
    lines = [ln.strip() for ln in note_text.splitlines() if ln.strip()]
    if not lines:
        return ""

    keep: List[str] = []
    clinical_hint_re = re.compile(
        r"\b(diagnosis|impression|assessment|plan|treat|management|differential|consider|recommend|follow[- ]?up|monitor|start|stop|increase|decrease|admit|discharge|urgent|red flag)\b",
        re.IGNORECASE,
    )
    med_or_value_re = re.compile(r"\b\d+(?:\.\d+)?\s*(mg|mcg|g|kg|mL|ml|L|mmol/?L|units?)\b", re.IGNORECASE)

    for ln in lines:
        if clinical_hint_re.search(ln) or med_or_value_re.search(ln):
            keep.append(ln)
        if len(keep) >= max_lines:
            break

    if not keep:
        # Last-resort compact tail
        return _rag_tail_window(note_text, max_tokens=220, min_tokens=120)
    return "\n".join(keep)


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        val = value.strip().lower()
        if val in ("true", "1", "yes", "y", "on"):
            return True
        if val in ("false", "0", "no", "n", "off", ""):
            return False
    return default


async def _gather_rag_for_qa(question: str, cfg: Dict) -> Dict[str, Any]:
    """Collect RAG context for QA without blocking the main stream."""
    result: Dict[str, Any] = {
        "context_aug": "",
        "context_raw": "",
        "refs_raw": [],
        "norm_refs": [],
        "full_chunks": [],
        "used_filters": {},
        "error": None,
        "weak_evidence": True,
    }
    try:
        rag_client = _rag_client_from_cfg(cfg)
        try:
            rag_timeout_ms = int(cfg.get("rag_timeout_ms", 25000))
        except Exception:
            rag_timeout_ms = 25000
        timeout_seconds = max(1.0, rag_timeout_ms / 1000.0)

        # Loosen filters: allow broad recall; keywords are advisory only
        include_kws: List[str] = []

        rag_context, rag_refs_raw, used_filters = await asyncio.wait_for(
            rag_client.query(
                question,
                top_k=int(cfg.get("rag_top_k", 16)),
            include_keywords=include_kws,
            date_from="2018-01-01",
        ),
        timeout=timeout_seconds,
    )

        norm_refs, full_chunks = _normalize_reference_items(
            rag_refs_raw or [],
            cap=int(cfg.get("rag_top_k", 16)),
            sort_key=lambda x: (x.get("year", 0), x.get("score", 0.0)),
        )

        ctx_aug = rag_context or ""
        try:
            include_snips = _as_bool(cfg.get("rag_include_snippets", True), True)
        except Exception:
            include_snips = True
        if include_snips and not ctx_aug.strip() and full_chunks:
            try:
                cap_chars = int(cfg.get("rag_evidence_clip_chars", 2000))
            except Exception:
                cap_chars = 2000
            snips_all = "\n\n".join(full_chunks)
            if len(snips_all) > cap_chars:
                snips_all = snips_all[:cap_chars] + "\n[...evidence truncated...]"
            ctx_aug = snips_all

        result.update(
            {
                "context_aug": ctx_aug,
                "context_raw": rag_context or "",
                "refs_raw": rag_refs_raw or [],
                "norm_refs": norm_refs,
                "full_chunks": full_chunks,
                "used_filters": used_filters or {},
                "weak_evidence": _weak_evidence(rag_refs_raw or [], ctx_aug or rag_context or ""),
            }
        )
    except asyncio.TimeoutError:
        result["error"] = "timeout"
        result["used_filters"] = {"error": "timeout"}
    except Exception as exc:
        err_txt = str(exc)
        result["error"] = err_txt
        result["used_filters"] = {"error": err_txt[:160]}
    return result


async def _generate_consult_comment(
    gen_id: str,
    note_text: str,
    cfg: Dict,
    *,
    strategy: str = "sections",
    user_speciality: Optional[str] = None,
) -> None:
    await _generate_consult_comment_impl(
        gen_id,
        note_text,
        cfg,
        strategy=strategy,
        consult_store=_consult_comment_store,
        generation_meta=_generation_meta,
        extract_marker_sentences=_extract_marker_sentences,
        extract_focus_sections=_extract_rag_focus_sections,
        fallback_focus_from_note=_fallback_focus_from_note,
        rag_tail_window=_rag_tail_window,
        rag_head_window=_rag_head_window,
        rag_client_from_cfg=_rag_client_from_cfg,
        get_rag_comment_llm=_get_rag_comment_llm,
        normalize_reference_items=_normalize_reference_items,
        clean_model_output_final=clean_model_output_final,
        user_speciality=user_speciality,
    )

# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

def _fill_template(tpl: str, values: dict) -> str:
    return _fill_template_impl(tpl, values)

def _cfg_text(val: Any) -> str:
    return _cfg_text_impl(val)

def _normalize_note_type(note_type: Optional[str]) -> str:
    nt = (note_type or "").strip().lower()
    mapping = {
        "progress_note": "progress",
        "progress note": "progress",
        "follow-up": "followup",
        "follow up": "followup",
        "follow_up": "followup",
        "consultation": "consult",
    }
    return mapping.get(nt, nt or "consult")

def _get_rag_comment_llm(cfg: Dict[str, Any]) -> SimpleNoteGenerator:
    """Consult comment LLM: LLM_RAG_COMMENT_* (service_endpoints.json); else note_gen routing."""
    p, f = resolve_rag_comment_urls(cfg)
    return SimpleNoteGenerator(explicit_urls=(p, f))


def _get_order_request_llm(cfg: Dict[str, Any]) -> SimpleNoteGenerator:
    """Order/imaging extraction: LLM_ORDER_REQUEST_* (service_endpoints.json); else note_gen routing."""
    p, f = resolve_order_request_urls(cfg)
    return SimpleNoteGenerator(explicit_urls=(p, f))


async def _generate_order_requests(gen_id: str, note_text: str, cfg: Dict) -> None:
    await _generate_order_requests_impl(
        gen_id,
        note_text,
        cfg,
        order_store=_order_request_store,
        extract_plan_section=_extract_plan_section,
        strip_conflicts_section=strip_conflicts_section,
        cfg_text=_cfg_text,
        get_order_request_llm=_get_order_request_llm,
        extract_json_payload=_extract_json_payload,
        format_imaging_request=_format_imaging_request,
        clean_model_output_final=clean_model_output_final,
        clean_model_output_chunk=clean_model_output_chunk,
        merge_medication_items=_merge_medication_items,
        dedupe_request_items=_dedupe_request_items,
    )


def build_prompt_v8(
    transcription_text: str,
    old_visits_text: str,
    mixed_other_text: str,
    note_type: str,
    custom_prompt: Optional[str] = None,
    user_speciality: Optional[str] = None,
    merged_user_prompts: Optional[Dict[str, Any]] = None,
    user_location: Optional[str] = None,
    user_display_name: Optional[str] = None,
    user_email: Optional[str] = None,
) -> str:
    return _build_prompt_v8_impl(
        transcription_text=transcription_text,
        old_visits_text=old_visits_text,
        mixed_other_text=mixed_other_text,
        note_type=note_type,
        custom_prompt=custom_prompt,
        user_speciality=user_speciality,
        merged_user_prompts=merged_user_prompts,
        user_location=user_location,
        user_display_name=user_display_name,
        user_email=user_email,
    )


def build_prompt_other(
    transcription_text: str,
    old_visits_text: str,
    mixed_other_text: str,
    note_type: str,
    custom_prompt: Optional[str] = None,
    user_speciality: Optional[str] = None,
    merged_user_prompts_other: Optional[Dict[str, Any]] = None,
    user_location: Optional[str] = None,
    user_display_name: Optional[str] = None,
    user_email: Optional[str] = None,
) -> str:
    return _build_prompt_other_impl(
        transcription_text=transcription_text,
        old_visits_text=old_visits_text,
        mixed_other_text=mixed_other_text,
        note_type=note_type,
        custom_prompt=custom_prompt,
        user_speciality=user_speciality,
        merged_user_prompts_other=merged_user_prompts_other,
        user_location=user_location,
        user_display_name=user_display_name,
        user_email=user_email,
    )





def build_note_prompt_legacy(
    chart_data: str,
    transcription: str,
    note_type: str,
    custom_prompt: Optional[str] = None,
    user_speciality: Optional[str] = None,
    merged_user_prompts: Optional[Dict[str, Any]] = None,
    user_location: Optional[str] = None,
    user_display_name: Optional[str] = None,
    user_email: Optional[str] = None,
) -> str:
    return _build_note_prompt_legacy_impl(
        chart_data=chart_data,
        transcription=transcription,
        note_type=note_type,
        custom_prompt=custom_prompt,
        user_speciality=user_speciality,
        merged_user_prompts=merged_user_prompts,
        user_location=user_location,
        user_display_name=user_display_name,
        user_email=user_email,
    )
# ---------------------------------------------------------------------------
# New endpoints: generation meta, consult_comment
# ---------------------------------------------------------------------------


def _maybe_autostart_order_requests(gen_id: str, note_text: str, cfg: Dict[str, Any]) -> None:
    """Start order/referral extraction in background right after note generation."""
    try:
        if not bool(cfg.get("order_request_autostart", True)):
            return
        if not (note_text or "").strip():
            return
        st = _order_request_store.get(gen_id) or {}
        status = str(st.get("status") or "").lower()
        if status in {"pending", "done"}:
            return
        _order_request_store[gen_id] = {"status": "pending", "autostart": True, "items": []}
        asyncio.create_task(_generate_order_requests(gen_id, note_text, cfg))
    except Exception:
        pass


def _maybe_autostart_consult_comment(gen_id: str, note_text: str, cfg: Dict[str, Any], note_type: str, user_speciality: Optional[str] = None) -> None:
    """Start consult comment generation in background right after note generation (any note type)."""
    try:
        if not bool(cfg.get("consult_comment_autostart", True)):
            return
        if not (note_text or "").strip():
            return

        st = _consult_comment_store.get(gen_id) or {}
        status = str(st.get("status") or "").lower()
        if status in {"pending", "done"}:
            return

        _consult_comment_store[gen_id] = {"status": "pending", "autostart": True}
        asyncio.create_task(_generate_consult_comment(gen_id, note_text, cfg, strategy="sections", user_speciality=user_speciality))
    except Exception:
        pass


def _generation_encounter_guard(gen_id: str, request: Request) -> None:
    """If client sends encounter_id and we stored one for this generation, they must match."""
    q = (request.query_params.get("encounter_id") or "").strip()
    if not q:
        return
    meta = _generation_meta.get(gen_id) or {}
    stored = meta.get("encounter_id")
    if not stored:
        return
    if str(stored).strip() != q:
        raise HTTPException(
            status_code=409,
            detail="This generation does not match the active encounter. Regenerate the note or switch encounters.",
        )


def _truncate_note_for_rehydrate(text: str) -> str:
    cfg = load_config()
    try:
        max_budget = int(cfg.get("note_rehydrate_max_tokens", 100000))
    except (TypeError, ValueError):
        max_budget = 100000
    return truncate_to_context_length_tokens(text, max_budget)


def _populate_generation_stores_from_note(
    gen_id: str,
    note_text: str,
    *,
    encounter_id: Optional[str],
    pipeline: str,
) -> Optional[str]:
    """Populate generation cache + meta from note text. Returns stored body or None if unusable."""
    stored = _truncate_note_for_rehydrate(note_text.strip())
    if not stored or not _has_minimum_signal(stored, min_alnum=10):
        return None
    with _cache_lock:
        _generation_cache[gen_id] = {"prompt": "", "output": stored}
    _generation_meta[gen_id] = {
        "refs": [],
        "used_filters": {},
        "context": "",
        "full_evidence": "",
        "pipeline": pipeline,
        "encounter_id": encounter_id,
        "user_id": None,  # workspace rehydrate — no user binding (transient)
        "qa": {
            "status": "not_applicable",
            "baseline": None,
            "final": None,
            "rewrite_used": False,
            "rag_context_chars": 0,
            "rag_error": None,
        },
    }
    return stored


def _user_id_from_bearer(request: Request) -> Optional[uuid.UUID]:
    auth = (request.headers.get("authorization") or "").strip()
    if not auth.lower().startswith("bearer "):
        return None
    token = auth.split(" ", 1)[1].strip()
    if not token:
        return None
    try:
        payload = decode_access_token(token)
        sub = payload.get("sub")
        if not sub:
            return None
        return uuid.UUID(str(sub))
    except Exception:
        return None


def _try_reseed_generation_from_workspace_encounter(
    session: Session,
    user_id: uuid.UUID,
    gen_id: str,
    encounter_id: Optional[str],
) -> bool:
    """
    When in-memory TTL cleared the generation cache, rebuild it from the user's persisted workspace
    (UserEncounter.state_json) if extras.ui.lastGenerationId matches and draft/generatedNote exist.
    """
    if not encounter_id:
        return False
    try:
        eid = uuid.UUID(str(encounter_id).strip())
    except (ValueError, TypeError):
        return False
    enc = session.get(UserEncounter, eid)
    if not enc or enc.user_id != user_id:
        return False
    st = enc.state_json or {}
    ex = st.get("extras") or {}
    ui = ex.get("ui") if isinstance(ex.get("ui"), dict) else {}
    if str(ui.get("lastGenerationId") or "").strip() != gen_id:
        return False
    draft = (st.get("draft") or "").strip() if isinstance(st.get("draft"), str) else ""
    gen_note = (ex.get("generatedNote") or "").strip() if isinstance(ex.get("generatedNote"), str) else ""
    note_text = (draft or gen_note).strip()
    if not note_text:
        return False
    out = _populate_generation_stores_from_note(
        gen_id,
        note_text,
        encounter_id=str(enc.id),
        pipeline="workspace_db_rehydrate",
    )
    return bool(out)


def _generation_cache_output(gen_id: str) -> str:
    with _cache_lock:
        entry = _generation_cache.get(gen_id) or {}
        return (entry.get("output") or "").strip()


def _ensure_note_text_or_workspace_reseed(
    session: Session,
    request: Request,
    gen_id: str,
) -> str:
    t = _generation_cache_output(gen_id)
    if t:
        return t
    uid = _user_id_from_bearer(request)
    q_enc = (request.query_params.get("encounter_id") or "").strip() or None
    if uid and q_enc:
        _try_reseed_generation_from_workspace_encounter(session, uid, gen_id, q_enc)
    return _generation_cache_output(gen_id)


@router.get("/generation/{gen_id}/meta")
async def generation_meta(
    gen_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    # Ownership check
    meta = _generation_meta.get(gen_id)
    if meta and meta.get("user_id") and str(meta.get("user_id")) != str(current_user.id):
        raise HTTPException(status_code=403, detail="Not authorized to access this generation")
    _generation_encounter_guard(gen_id, request)
    if not meta:
        raise HTTPException(status_code=404, detail="generation not found")
    return meta


@router.get("/generation/{gen_id}/consult_comment")
async def get_consult_comment(
    gen_id: str,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    # Ownership check: generation must belong to this user
    meta = _generation_meta.get(gen_id)
    if meta and meta.get("user_id") and str(meta.get("user_id")) != str(current_user.id):
        raise HTTPException(status_code=403, detail="Not authorized to access this generation")
    _generation_encounter_guard(gen_id, request)
    st = _consult_comment_store.get(gen_id)
    force = (request.query_params.get("force") or "").strip().lower() in {"1", "true", "yes"}
    strategy = (request.query_params.get("strategy") or "sections").strip().lower()

    if force:
        note_text = _ensure_note_text_or_workspace_reseed(session, request, gen_id)
        if not note_text:
            return {"status": "error", "error": "No note output available for retry."}
        _consult_comment_store[gen_id] = {"status": "pending"}
        cfg = load_config()
        meta = _generation_meta.get(gen_id) or {}
        user_speciality = meta.get("user_speciality")
        asyncio.create_task(_generate_consult_comment(gen_id, note_text, cfg, strategy=strategy, user_speciality=user_speciality))
        return {"status": "pending"}

    if not st:
        note_text = _ensure_note_text_or_workspace_reseed(session, request, gen_id)
        if not note_text:
            return {"status": "error", "error": "No note output available."}
        _consult_comment_store[gen_id] = {"status": "pending"}
        cfg = load_config()
        meta = _generation_meta.get(gen_id) or {}
        user_speciality = meta.get("user_speciality")
        asyncio.create_task(_generate_consult_comment(gen_id, note_text, cfg, strategy=strategy, user_speciality=user_speciality))
        return {"status": "pending"}
    return st


@router.get("/generation/{gen_id}/order_requests")
async def get_order_requests(
    gen_id: str,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    # Ownership check: generation must belong to this user
    meta = _generation_meta.get(gen_id)
    if meta and meta.get("user_id") and str(meta.get("user_id")) != str(current_user.id):
        raise HTTPException(status_code=403, detail="Not authorized to access this generation")
    _generation_encounter_guard(gen_id, request)
    st = _order_request_store.get(gen_id)
    force = (request.query_params.get("force") or "").strip().lower() in {"1", "true", "yes"}

    if force:
        note_text = _ensure_note_text_or_workspace_reseed(session, request, gen_id)
        if not note_text:
            return {"status": "error", "error": "No note output available for retry.", "items": []}
        _order_request_store[gen_id] = {"status": "pending", "items": []}
        cfg = load_config()
        asyncio.create_task(_generate_order_requests(gen_id, note_text, cfg))
        return {"status": "pending", "items": []}

    if not st:
        note_text = _ensure_note_text_or_workspace_reseed(session, request, gen_id)
        if not note_text:
            return {"status": "error", "error": "No note output available.", "items": []}
        _order_request_store[gen_id] = {"status": "pending", "items": []}
        cfg = load_config()
        asyncio.create_task(_generate_order_requests(gen_id, note_text, cfg))
        return {"status": "pending", "items": []}
    return st


# ---------------------------------------------------------------------------
# Route: /note_prompts — effective + baseline note templates (Bearer auth)
# ---------------------------------------------------------------------------

@router.get("/note_prompts")
def get_note_prompts(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> JSONResponse:
    try:
        cfg = load_config()

        std_b, oth_b = baseline_templates_from_config(cfg)
        templates_baseline = {**std_b, **oth_b}

        pref_row = ensure_user_preferences(session, current_user)
        merged_std, merged_oth = merge_templates_for_generation(cfg, pref_row.preferences_json)
        templates_effective = {**merged_std, **merged_oth}

        return JSONResponse(
            content={
                "success": True,
                "templates": templates_effective,
                "templates_baseline": templates_baseline,
            }
        )
    except Exception as e:
        print(f"[ERROR] Failed to fetch note prompts: {e}")
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": "Failed to fetch prompts"},
        )

# ---------------------------------------------------------------------------
# Route: /generate_v8_stream - Simple Direct Note Generation (No Extraction)
# ---------------------------------------------------------------------------

@router.post("/generate_v8_stream")
async def generate_v8_stream(request: Request, session: Session = Depends(get_session), current_user: User = Depends(get_current_user)):
    """
    Generate a clinical note using a SIMPLE DIRECT approach (v8).

    This endpoint bypasses the complex extraction/merging pipeline and instead:
    1. Takes the 3-field input (transcription_text, old_visits_text, mixed_other_text)
    2. Organizes them with clear section tags
    3. Passes directly to the LLM for note generation

    This is faster and more reliable than v7 which uses extraction.
    """
    try:
        ctype = (request.headers.get("content-type") or "").lower()
        cfg = load_config()

        if "application/json" in ctype:
            payload = await request.json()
        else:
            form = await request.form()
            payload = {k: str(v) for k, v in form.items()}

        # Extract the 3-field input
        transcription_text = payload.get("transcription_text", "")
        old_visits_text = payload.get("old_visits_text", "")
        mixed_other_text = payload.get("mixed_other_text", "")
        note_type = _normalize_note_type(payload.get("note_type", "consult"))
        custom_prompt = payload.get("custom_prompt", "")
        user_speciality = payload.get("user_speciality", "")
        active_encounter_id_str = (str(payload.get("encounter_id") or "").strip() or None)

        try:
            log_phi_access(
                session,
                user_id=current_user.id,
                action="generate_note",
                resource_type="encounter",
                resource_id=active_encounter_id_str or "unknown",
                encounter_id=(uuid.UUID(active_encounter_id_str) if active_encounter_id_str else None),
                detail=note_type,
            )
            session.commit()
        except Exception:
            session.rollback()

        merged_std: Optional[Dict[str, Any]] = None
        merged_oth: Optional[Dict[str, Any]] = None
        pref_json: Optional[Dict[str, Any]] = None
        user_location: str = ""
        actor_pre = extract_request_actor(request, session)
        uid_raw = actor_pre.get("user_id")
        urow: Optional[User] = None
        if uid_raw:
            try:
                uid = uuid.UUID(str(uid_raw))
                urow = session.get(User, uid)
                if urow:
                    if not (str(user_speciality or "").strip()) and (urow.default_specialty or "").strip():
                        user_speciality = urow.default_specialty or ""
                    pref_row = ensure_user_preferences(session, urow)
                    if isinstance(pref_row.preferences_json, dict):
                        pref_json = pref_row.preferences_json
                    merged_std, merged_oth = merge_templates_for_generation(cfg, pref_row.preferences_json)
            except Exception:
                merged_std, merged_oth = None, None
                pref_json = None
        # Profile-only: drives {USER_LOCATION} and SYSTEM location context (see builder).
        if urow and (urow.default_location or "").strip():
            user_location = (urow.default_location or "").strip()

        user_display_name: Optional[str] = None
        user_email: Optional[str] = None
        if urow:
            user_display_name = urow.display_name
            user_email = urow.email

        # Get temperature and max_tokens
        temp: Optional[float] = None
        if payload.get("temperature") is not None:
            try:
                temp = float(payload.get("temperature"))
            except Exception:
                temp = None

        if temp is None:
            temp = float(cfg.get("default_note_temperature", 0.2))

        raw_max: Optional[int] = None
        if payload.get("max_tokens") is not None:
            try:
                raw_max = int(payload.get("max_tokens"))
            except Exception:
                raw_max = None
        # Zero / negative = treat as omitted (use config default). Empty chart data is unrelated.
        if raw_max is not None and raw_max <= 0:
            raw_max = None
        if raw_max is None:
            try:
                raw_max = int(cfg.get("default_note_max_tokens", DEFAULT_COMPLETION_TOKENS))
            except Exception:
                raw_max = DEFAULT_COMPLETION_TOKENS
        max_tokens = clamp_completion_tokens(raw_max)

        # Build the prompt using the simple direct approach (P3b: custom "other" scope uses other builder)
        if note_type_uses_other_builder(note_type, pref_json):
            prompt = build_prompt_other(
                transcription_text=transcription_text,
                old_visits_text=old_visits_text,
                mixed_other_text=mixed_other_text,
                note_type=note_type,
                custom_prompt=custom_prompt,
                user_speciality=user_speciality,
                merged_user_prompts_other=merged_oth,
                user_location=user_location or None,
                user_display_name=user_display_name,
                user_email=user_email,
            )
        else:
            prompt = build_prompt_v8(
                transcription_text=transcription_text,
                old_visits_text=old_visits_text,
                mixed_other_text=mixed_other_text,
                note_type=note_type,
                custom_prompt=custom_prompt,
                user_speciality=user_speciality,
                merged_user_prompts=merged_std,
                user_location=user_location or None,
                user_display_name=user_display_name,
                user_email=user_email,
            )

        print(f"[V8_DEBUG] Built prompt: {len(prompt)} chars")
        print(f"[V8_DEBUG] Transcription: {len(transcription_text)} chars")
        print(f"[V8_DEBUG] Old visits: {len(old_visits_text)} chars")
        print(f"[V8_DEBUG] Mixed other: {len(mixed_other_text)} chars")

        generation_id = uuid.uuid4().hex
        t0 = time.perf_counter()
        token_count = 0
        created_at = datetime.now(timezone.utc).isoformat()
        output_buf: list[str] = []
        actor = actor_pre

        # Seed cache entry for later feedback
        with _cache_lock:
            _generation_cache[generation_id] = {"prompt": prompt, "output": ""}

        # Initialize metadata
        _generation_meta[generation_id] = {
            "refs": [],
            "used_filters": {},
            "context": "",
            "full_evidence": "",
            "pipeline": "v8_direct",
            "encounter_id": active_encounter_id_str,
            "user_id": str(current_user.id),
            "user_speciality": user_speciality,
            "qa": {
                "status": "not_applicable",
                "baseline": None,
                "final": None,
                "rewrite_used": False,
                "rag_context_chars": 0,
                "rag_error": None,
            },
        }

        async def gen():
            nonlocal token_count
            streamed_any = False
            try:
                try:
                    async for chunk in _stream_response_v8(
                        note_gen=note_gen,
                        prompt=prompt,
                        temperature=temp,
                        max_tokens=max_tokens,
                        stop_tokens=NOTE_STOP_TOKENS,
                        clean_chunk=lambda x: x,
                    ):
                        streamed_any = True
                        cleaned = clean_model_output_chunk(chunk or "")
                        if not cleaned:
                            continue
                        # Handle END_MARKER if it appears mid-chunk
                        if END_MARKER in cleaned:
                            cleaned = cleaned.split(END_MARKER, 1)[0]
                            if cleaned:
                                output_buf.append(cleaned)
                                token_count += len(cleaned.split())
                                yield cleaned
                            yield END_MARKER + "\n"
                            return
                        if cleaned:
                            output_buf.append(cleaned)
                            token_count += len(cleaned.split())
                            yield cleaned
                except ExternalServiceError:
                    if streamed_any:
                        raise
                    # Fallback to collect_completion if streaming fails early
                    note_text = await note_gen.collect_completion(
                        prompt,
                        temperature=temp,
                        max_tokens=max_tokens,
                        stop=NOTE_STOP_TOKENS,
                    )
                    cleaned = clean_model_output_chunk(_strip_note_end_marker(note_text))
                    if cleaned:
                        output_buf.append(cleaned)
                        token_count += len(cleaned.split())
                        yield cleaned

                yield END_MARKER + "\n"

            except asyncio.CancelledError:
                print("Client disconnected - streaming cancelled")
                raise
            except ClinicalOutputRejected as e:
                # Must be caught ahead of the generic RuntimeError branch below:
                # ClinicalOutputRejected subclasses RuntimeError, and its messages
                # (e.g. "output exceeded the hard character limit") can contain
                # the same keywords ("limit") that branch uses to detect a
                # context-length error, which previously misreported every guard
                # rejection as "input is too long" and told clinicians to delete
                # good chart context. The rejected draft (when the guard captured
                # one) is surfaced too, clearly labeled unverified, rather than
                # silently discarding a full encounter's dictation -- clinicians
                # review and sign every note before it's used regardless.
                print(f"Clinical output guard rejected the draft: {e}")
                yield (
                    "NOTE NOT GENERATED: DreamCision's safety guard could not verify "
                    "this draft as clinically grounded, so it was not shown "
                    "automatically as your note.\n\n"
                    f"Reason: {str(e)}\n\n"
                )
                if getattr(e, "draft", ""):
                    yield (
                        "----- UNVERIFIED DRAFT (not validated -- review carefully "
                        "before use) -----\n\n"
                        f"{e.draft}\n\n"
                        "----- END UNVERIFIED DRAFT -----\n"
                    )
            except RuntimeError as e:
                error_msg = str(e).lower()
                if any(keyword in error_msg for keyword in [
                    "context", "ctx", "kv", "slot", "too long", "too large",
                    "exceeds", "limit", "overflow", "n_ctx"
                ]):
                    print(f"Context length error: {e}")
                    yield (
                        "ERROR: The input is too long for the model's context window.\n\n"
                        "Please try reducing the amount of input data.\n\n"
                        f"Technical details: {str(e)}\n"
                    )
                else:
                    print(f"Runtime error during streaming: {e}")
                    yield f"Error: {str(e)}\n"
            except Exception as e:
                print(f"Error during v8 streaming: {e}")
                yield f"Error: {str(e)}\n"
            finally:
                duration = time.perf_counter() - t0
                print(f"[V8_DEBUG] Generation completed in {duration:.2f}s, ~{token_count} tokens")

                if global_metrics is not None:
                    global_metrics.record_note(duration, token_count, getattr(note_gen, 'model_path', None))

                try:
                    combined_output = "".join(output_buf)
                    # P2-7: _log_case_completion de-identifies the prompt/output
                    # (regex-heavy) and then does a blocking file append
                    # (dataset_logger.py, under threading.Lock) -- both on the
                    # event loop otherwise. This runs at the end of every single
                    # note generation, the hottest path in the app; off-load the
                    # whole call rather than block every other doctor's
                    # in-flight request behind one note's cleanup work.
                    await asyncio.to_thread(
                        _log_case_completion,
                        case_id=generation_id,
                        created_at=created_at,
                        duration_s=duration,
                        note_type=note_type,
                        pipeline="v8_direct",
                        prompt=prompt,
                        input_fields={
                            "transcription_text": str(transcription_text or ""),
                            "old_visits_text": str(old_visits_text or ""),
                            "mixed_other_text": str(mixed_other_text or ""),
                            "custom_prompt": str(custom_prompt or ""),
                        },
                        output_text=combined_output,
                        prompt_tokens=len((prompt or "").split()),
                        completion_tokens=token_count,
                        actor=actor,
                    )

                    with _cache_lock:
                        if generation_id in _generation_cache:
                            _generation_cache[generation_id]["output"] = combined_output

                    _maybe_autostart_consult_comment(generation_id, combined_output, cfg, note_type, user_speciality)
                    _maybe_autostart_order_requests(generation_id, combined_output, cfg)
                except Exception as e:
                    print(f"Dataset case logging failed: {e}")

        return StreamingResponse(
            gen(),
            media_type="text/plain",
            headers={"X-Generation-Id": generation_id}
        )

    except Exception as e:
        import traceback
        error_detail = f"Generation failed: {str(e)}\n\nTraceback:\n{traceback.format_exc()}"
        print(f"Error in generate_v8_stream: {error_detail}")
        raise HTTPException(status_code=503, detail=error_detail)


