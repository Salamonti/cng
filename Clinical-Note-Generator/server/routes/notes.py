# server/routes/notes.py
import asyncio
from datetime import datetime, timezone
import logging
import time
import re
import json
import os
import uuid
import threading
from typing import Dict, Optional, Any, List, Tuple
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse, JSONResponse
from sqlmodel import Session, select

from services.note_generator_clean import get_simple_note_generator, SimpleNoteGenerator, ExternalServiceError
from services.rag_http_client import RAGHttpClient
from services.clinical_text_normalizer import normalize_clinical_note_output
from metrics import metrics as global_metrics
from core.db import get_session
from core.deid.v1 import deidentify_text
from core.logging.dataset_logger import log_case_event, log_case_record
from core.security import decode_access_token
from core.stores.generation_store import (
    _generation_cache,
    _generation_meta,
    _consult_comment_store,
    _order_request_store,
)
from core.prompt.builder import (
    build_prompt_v8 as _build_prompt_v8_impl,
    build_prompt_other as _build_prompt_other_impl,
    build_note_prompt_legacy as _build_note_prompt_legacy_impl,
    _fill_template as _fill_template_impl,
    _cfg_text as _cfg_text_impl,
)
from core.streaming.helpers import _stream_response, _stream_response_v8, _stream_qa_response
from core.consult.pipeline import _generate_consult_comment as _generate_consult_comment_impl
from core.order.pipeline import _generate_order_requests as _generate_order_requests_impl
from core.qa_rag.helpers import _qa_rewrite_with_rag
from server.models.user import User


router = APIRouter()
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# In-memory cache for generation feedback + metadata
# ---------------------------------------------------------------------------

_cache_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------
CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "config.json"

OTHER_NOTE_TYPES = {"referral", "summarize", "custom", "procedure"}


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
note_gen = get_simple_note_generator()

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


def _extract_actor(request: Request, session: Session) -> Dict[str, Optional[str]]:
    actor: Dict[str, Optional[str]] = {"user_id": None, "user_email": None}
    auth = (request.headers.get("authorization") or "").strip()
    if not auth.lower().startswith("bearer "):
        return actor
    token = auth.split(" ", 1)[1].strip()
    if not token:
        return actor
    try:
        payload = decode_access_token(token)
    except Exception:
        return actor

    user_id = str(payload.get("sub") or "").strip()
    if not user_id:
        return actor
    actor["user_id"] = user_id
    try:
        user_uuid = uuid.UUID(user_id)
        user = session.exec(select(User).where(User.id == user_uuid)).one_or_none()
        if user and user.email:
            actor["user_email"] = str(user.email)
    except Exception:
        pass
    return actor


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
    """
    Truncate text to approximate token count.

    We treat max_tokens as an approximate token limit and use a 1:1
    word-to-token mapping (max_tokens ≈ max_words). This behaves like
    a hard token cap (e.g. 32k) instead of scaling down by 0.75×.
    """
    words = text.split()
    max_words = max_tokens
    if len(words) > max_words:
        truncated = " ".join(words[:max_words])
        return truncated + "\n\n[Content truncated to fit context length...]"
    return text


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

def clean_model_output_chunk(chunk: str) -> str:
    """Minimal, stream-safe cleaner: preserves spaces/newlines exactly.
    Removes NUL characters and converts Unicode to ASCII for EMR compatibility.

    CRITICAL: EMR systems don't support Unicode characters. This function converts
    all special Unicode characters (subscripts, superscripts, special punctuation)
    to ASCII equivalents to prevent them from appearing as question marks in EMRs.
    """
    if not chunk:
        return ""
    s = chunk.replace("\x00", "")

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
    cleaned = text.replace("\x00", "")

    # Remove anything before the actual note content starts
    # GUARD: Only trim if patterns appear in first 200 chars to avoid cutting legitimate content
    first_200 = cleaned[:200]
    if "Patient ID" in first_200:
        # Split at "Patient ID" and keep everything from that point
        parts = cleaned.split("Patient ID", 1)
        if len(parts) > 1:
            cleaned = "Patient ID" + parts[1]
    elif re.search(r'^ID\s*:', first_200, re.MULTILINE):
        # Look for standalone "ID" at the beginning of a line in first 200 chars only
        match = re.search(r'^ID\s*:', cleaned, re.MULTILINE)
        if match and match.start() < 200:
            cleaned = cleaned[match.start():]

    # Remove a single leading/trailing code fence if leaked
    cleaned = re.sub(r"^\s*```[a-zA-Z0-9_-]*\s*\n", "", cleaned)
    cleaned = re.sub(r"\n```+\s*$", "", cleaned)

    # Remove specific leaked XML-ish wrappers (case-insensitive)
    cleaned = re.sub(r"</?(?:transcription_data|chart_data)>", "", cleaned, flags=re.IGNORECASE)

    # Remove any leaked think blocks
    cleaned = re.sub(r"<think>.*?</think>", "", cleaned, flags=re.IGNORECASE | re.DOTALL)

    # Remove explicit note tags and simple formatting markers
    cleaned = cleaned.replace("<note>", "").replace("</note>", "")
    # Remove markdown bold/italic markers
    cleaned = re.sub(r'\*\*([^*]+)\*\*', r'\1', cleaned)  # Remove **bold**
    cleaned = re.sub(r'\*([^*]+)\*', r'\1', cleaned)  # Remove *italic*
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

    # Normalize CRLF to LF for consistent paragraph spacing rules.
    cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")
    # If the model uses single newlines between paragraphs, add extra spacing after sentence end.
    cleaned = re.sub(r'([.!?])\s*\n(?!\s*\n)([A-Za-z0-9])', r'\1\n\n\2', cleaned)
    # If there are 3+ newlines, reduce to 2.
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)

    # Conservative clinical normalization pass:
    # - number words + units -> compact numeric notation
    # - optional RxNorm medication name canonicalization (confidence-gated)
    try:
        norm = normalize_clinical_note_output(cleaned)
        cleaned = norm.text
    except Exception:
        pass

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
    text = await note_gen.collect_completion(
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

    # Accept common variants: "Plan:", "Plan -", "PLAN", "A/P:", "Assessment & Plan"
    header_re = re.compile(
        r"(?im)^\s*(assessment\s*(?:&|and)?\s*plan|assessment\s*/\s*plan|a/p|plan)\s*(?::|-)?\s*$"
    )
    header_inline_re = re.compile(
        r"(?im)^\s*(assessment\s*(?:&|and)?\s*plan|assessment\s*/\s*plan|a/p|plan)\s*(?::|-)\s*"
    )

    # 1) Inline header with content on the same line
    inline_match = header_inline_re.search(note_text)
    if inline_match:
        start = inline_match.end()
        rest = note_text[start:]
        next_header = header_re.search(rest)
        end = start + (next_header.start() if next_header else len(rest))
        return note_text[start:end].strip()

    # 2) Standalone header line, then capture following block
    header_match = header_re.search(note_text)
    if header_match:
        start = header_match.end()
        rest = note_text[start:]
        next_header = header_re.search(rest)
        end = start + (next_header.start() if next_header else len(rest))
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


def _format_imaging_request(text: str) -> str:
    """Normalize imaging request into a radiology-friendly concise block."""
    if not text:
        return ""
    cleaned = clean_model_output_final(text).strip()
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    lines = [ln.strip() for ln in cleaned.splitlines() if ln.strip()]
    if not lines:
        return ""

    # Keep useful headered format when present.
    wanted = [
        "Study Requested:",
        "Clinical Indication:",
        "Pertinent Findings / History:",
        "Clinical Question to Answer:",
        "Prior Relevant Imaging:",
        "Urgency:",
    ]
    header_hits = [ln for ln in lines if any(ln.lower().startswith(h.lower()) for h in wanted)]
    if header_hits:
        out: List[str] = []
        for h in wanted:
            for ln in lines:
                if ln.lower().startswith(h.lower()):
                    out.append(ln)
                    break
        if out:
            return "\n".join(out[:6])

    # Fallback: wrap to readable lines
    wrapped: List[str] = []
    buffer = " ".join(lines)
    while buffer and len(wrapped) < 6:
        if len(buffer) <= 100:
            wrapped.append(buffer)
            break
        cut = buffer.rfind(" ", 0, 100)
        if cut <= 25:
            cut = 100
        wrapped.append(buffer[:cut].strip())
        buffer = buffer[cut:].strip()
    return "\n".join(wrapped[:6])


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
        "- Output plain text only; no bullet lists or citations are required.\n"
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
        "- Use ONLY plain text - no markdown, no bold (**), no italics, no bullets, no special formatting\n"
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
    instruction = "Answer (plain text only, 2-3 paragraphs with blank lines, or state insufficient evidence if applicable):\n\n"
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


def _extract_rag_focus_sections(note_text: str) -> Tuple[str, List[str]]:
    headings = [
        "Impression",
        "Assessment",
        "Assessment and Plan",
        "Assessment/Plan",
        "Plan",
        "History of Present Illness",
        "HPI",
        "Subjective",
        "Objective",
    ]
    patterns = "|".join(re.escape(h) for h in headings)
    matches = []
    used = []
    for m in re.finditer(
        rf"(?im)^\s*({patterns})\s*:?\s*(.*?)(?=^\s*({patterns})\s*:?\s*|\Z)",
        note_text,
        flags=re.DOTALL | re.MULTILINE,
    ):
        title = m.group(1).strip()
        body = m.group(2).strip()
        if body:
            matches.append(f"{title}: {body}")
            used.append(title)
    focus = "\n\n".join(matches).strip()
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
        rag_client_from_cfg=_rag_client_from_cfg,
        get_rag_comment_llm=_get_rag_comment_llm,
        normalize_reference_items=_normalize_reference_items,
        clean_model_output_final=clean_model_output_final,
    )

# Backward-compatible alias if other modules import this name
clean_model_output = clean_model_output_chunk


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

def truncate_to_context_length(text: str, max_tokens: int) -> str:
    """Truncate text to approximate token count (rough estimate: 1 token ≈ 0.75 words)."""
    words = text.split()
    max_words = int(max_tokens * 0.75)  # Conservative estimate
    if len(words) > max_words:
        truncated = " ".join(words[:max_words])
        return truncated + "\n\n[Content truncated to fit context length...]"
    return text


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

def _llm_with_primary_url(url: Optional[str]) -> SimpleNoteGenerator:
    llm = get_simple_note_generator()
    if url and str(url).strip():
        llm = SimpleNoteGenerator()
        llm.primary_url = str(url).strip().rstrip("/")
        llm.fallback_url = None
    return llm


def _get_rag_comment_llm(cfg: Dict[str, Any]) -> SimpleNoteGenerator:
    # Default consult-comment model endpoint (typically 8036).
    return _llm_with_primary_url(cfg.get("rag_comment_llm_url"))


def _get_order_request_llm(cfg: Dict[str, Any]) -> SimpleNoteGenerator:
    """Use dedicated endpoint for orders when configured (typically 8081)."""
    return _llm_with_primary_url(cfg.get("order_request_llm_url"))


async def _generate_order_requests(gen_id: str, note_text: str, cfg: Dict) -> None:
    await _generate_order_requests_impl(
        gen_id,
        note_text,
        cfg,
        order_store=_order_request_store,
        extract_plan_section=_extract_plan_section,
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
) -> str:
    return _build_prompt_v8_impl(
        transcription_text=transcription_text,
        old_visits_text=old_visits_text,
        mixed_other_text=mixed_other_text,
        note_type=note_type,
        custom_prompt=custom_prompt,
        user_speciality=user_speciality,
    )


def build_prompt_other(
    transcription_text: str,
    old_visits_text: str,
    mixed_other_text: str,
    note_type: str,
    custom_prompt: Optional[str] = None,
    user_speciality: Optional[str] = None,
) -> str:
    return _build_prompt_other_impl(
        transcription_text=transcription_text,
        old_visits_text=old_visits_text,
        mixed_other_text=mixed_other_text,
        note_type=note_type,
        custom_prompt=custom_prompt,
        user_speciality=user_speciality,
    )


def build_qa_prompt(
    chart_data: str,
    transcription: str,
) -> str:
    header = (
        "You are an expert medical assistant with comprehensive knowledge of clinical medicine, diagnosis, and treatment.\n"
        "Provide accurate, clinically useful guidance (assessment, risks, options with dosing/route/timing, monitoring, contraindications).\n"
        "Write 2-4 tight paragraphs separated by blank lines; be concise but include key specifics.\n"
        "Be direct and practical. Avoid fluff or repetition.\n\n"
    )

    question_section = f"Medical Question: {transcription}\n\n" if transcription.strip() else ""
    context_section = f"Patient Context:\n{chart_data}\n\n" if chart_data.strip() else ""

    full_prompt = header + context_section + question_section + "Detailed Medical Response:\n"
    return full_prompt


def build_note_prompt_legacy(
    chart_data: str,
    transcription: str,
    note_type: str,
    custom_prompt: Optional[str] = None,
    user_speciality: Optional[str] = None,
) -> str:
    return _build_note_prompt_legacy_impl(
        chart_data=chart_data,
        transcription=transcription,
        note_type=note_type,
        custom_prompt=custom_prompt,
        user_speciality=user_speciality,
    )


# ---------------------------------------------------------------------------
# Deprecated internal generator path (unrouted)
# Canonical note route is /generate_v8_stream
# ---------------------------------------------------------------------------

async def generate_stream(request: Request, session: Session = Depends(get_session)):
    try:
        chart = trans = custom_prompt = ""
        note_type = "consult"
        temp: Optional[float] = None
        max_tokens = None

        # Support both JSON and multipart/form-data
        ctype = (request.headers.get("content-type") or "").lower()
        cfg = load_config()
        if "application/json" in ctype:
            payload = await request.json()
            chart = payload.get("chart_data", "")
            trans = payload.get("transcription", "")
            note_type = _normalize_note_type(payload.get("note_type", "consult"))
            custom_prompt = payload.get("custom_prompt", "")
            user_speciality = payload.get("user_speciality", "")
            if "temperature" in payload and payload.get("temperature") is not None:
                try:
                    temp = float(payload.get("temperature"))
                except Exception:
                    temp = None
            max_tokens = payload.get("max_tokens")
        else:
            form = await request.form()
            chart = str(form.get("chart_data", "") or "")
            trans = str(form.get("transcription", "") or "")
            note_type = _normalize_note_type(str(form.get("note_type", "consult") or "consult"))
            custom_prompt = str(form.get("custom_prompt", "") or "")
            user_speciality = str(form.get("user_speciality", "") or "")
            t_val = form.get("temperature")
            if t_val is not None and str(t_val).strip() != "":
                try:
                    temp = float(str(t_val))
                except Exception:
                    temp = None
            try:
                max_tokens_val = form.get("max_tokens")
                max_tokens = int(str(max_tokens_val)) if max_tokens_val else None
            except Exception:
                max_tokens = None

        # If temperature not provided by client, honor admin-configured defaults
        if temp is None:
            if note_type == "qa":
                temp = float(cfg.get("default_qa_temperature", 0.2))
            else:
                temp = float(cfg.get("default_note_temperature", 0.2))

        # Defaults for max_tokens
        if max_tokens is None:
            cfg = load_config()
            if note_type == "qa":
                max_tokens = cfg.get("default_qa_max_tokens", 512)
            else:
                max_tokens = cfg.get("default_note_max_tokens", 2048)

        # Build prompt; sanitize chart data to avoid accidental stop triggers
        if note_type == "qa":
            chart = _sanitize_chart_text(chart)
            trans = _sanitize_transcription_text(trans)
        if note_type == "qa":
            prompt = build_qa_prompt(chart, trans)
        else:
            prompt = build_note_prompt_legacy(chart, trans, note_type, custom_prompt, user_speciality)
        if note_type != "qa":
            print(f"[NOTE_PROMPT_DEBUG] chart_len={len(chart)}, trans_len={len(trans)}, prompt_len={len(prompt)}")
        t0 = time.perf_counter()
        token_count = 0
        generation_id = uuid.uuid4().hex
        created_at = datetime.now(timezone.utc).isoformat()
        output_buf: list[str] = []
        raw_note_buf: list[str] = []
        actor = _extract_actor(request, session)

        # seed cache entry for later feedback
        with _cache_lock:
            _generation_cache[generation_id] = {"prompt": prompt, "output": ""}

        # IMPORTANT: Populate metadata BEFORE streaming starts to avoid race condition
        cfg2 = load_config()
        is_qa = note_type == "qa"
        qa_question_for_verify: Optional[str] = None
        qa_source_excerpt = (trans or chart or "")[:2000]
        qa_baseline_temp = temp
        qa_rewrite_temp = temp
        qa_baseline_fallback = "Insufficient information to answer this question."
        qa_enhancement_label = "\n\n[Evidence-based update]:\n"
        qa_rag_enabled = False
        rag_task: Optional[asyncio.Task] = None

        if is_qa:
            qa_question_for_verify = (trans or chart or "").strip()
            try:
                qa_question_for_verify = re.sub(r"\[IMPORTANT:.*\]$", "", qa_question_for_verify, flags=re.S).strip()
            except Exception:
                pass

            qa_baseline_temp = float(cfg2.get("qa_baseline_temperature", temp))
            qa_rewrite_temp = float(cfg2.get("qa_rag_rewrite_temperature", temp))
            qa_baseline_fallback = str(
                cfg2.get(
                    "qa_baseline_fallback",
                    "Insufficient information to answer this question.",
                )
            )
            qa_enhancement_label = str(
                cfg2.get("qa_rag_enhancement_label", "\n\n[Evidence-based update]:\n")
            )
            qa_rag_enabled = _as_bool(cfg2.get("qa_rag_rewrite_enable", True), True)

            _generation_meta[generation_id] = {
                "refs": [],
                "used_filters": {},
                "context": "",
                "full_evidence": "",
                "qa": {
                    "baseline": None,
                    "final": None,
                    "rewrite_used": False,
                    "status": "generating_baseline",
                    "rag_context_chars": 0,
                    "rag_error": None,
                },
            }

            if qa_rag_enabled and qa_question_for_verify:
                rag_task = asyncio.create_task(_gather_rag_for_qa(qa_question_for_verify, cfg2))
        else:
            _generation_meta[generation_id] = {
                "refs": [],
                "used_filters": {},
                "context": "",
                "full_evidence": "",
                "qa": {
                    "status": "not_applicable",
                    "baseline": None,
                    "final": None,
                    "rewrite_used": False,
                    "rag_context_chars": 0,
                    "rag_error": None,
                },
            }
            print(f"[RAG] Skipping RAG for note generation - will run for consult comment after")

        async def gen():
            nonlocal token_count
            try:
                # Check approximate prompt size and provide early warning
                prompt_size = len(prompt.split())
                if prompt_size > 100000:  # ~100k words is very large
                    logger.warning(f"[PROMPT_SIZE] Very large prompt: ~{prompt_size} words")

                if is_qa:
                    # Always generate baseline first, but don't stream it yet; we may replace it with RAG rewrite
                    baseline_raw = await note_gen.collect_completion(
                        prompt,
                        temperature=qa_baseline_temp,
                        max_tokens=max_tokens,
                        stop=[],
                    )
                    baseline_text = clean_model_output_final(baseline_raw).strip()
                    if not baseline_text:
                        baseline_text = clean_model_output_final(qa_baseline_fallback).strip() or qa_baseline_fallback.strip()
                    final_text = baseline_text
                    rewrite_used = False

                    meta_entry = _generation_meta.get(generation_id)
                    if not isinstance(meta_entry, dict):
                        meta_entry = {}
                        _generation_meta[generation_id] = meta_entry
                    qa_meta = meta_entry.get("qa")
                    if not isinstance(qa_meta, dict):
                        qa_meta = {}
                        meta_entry["qa"] = qa_meta

                    qa_meta["baseline"] = baseline_text
                    qa_meta["final"] = baseline_text
                    qa_meta["status"] = "baseline_done" if qa_rag_enabled else "done"
                    qa_meta["rewrite_used"] = False
                    qa_meta["rag_context_chars"] = 0
                    qa_meta["rag_error"] = None

                    rag_out = await _qa_rewrite_with_rag(
                        baseline_text=baseline_text,
                        qa_question_for_verify=qa_question_for_verify or "",
                        cfg=cfg2,
                        max_tokens=max_tokens,
                        rag_task=rag_task,
                        qa_rewrite_prompt=_qa_rewrite_prompt,
                        collect_note_output=_collect_note_output,
                        clean_model_output_final=clean_model_output_final,
                        append_missed_question=_append_missed_question,
                        qa_source_excerpt=qa_source_excerpt,
                        qa_rewrite_temp=qa_rewrite_temp,
                        qa_enhancement_label=qa_enhancement_label,
                    )
                    final_text = rag_out.get("final_text", baseline_text)
                    rewrite_used = bool(rag_out.get("rewrite_used", False))
                    used_filters = rag_out.get("used_filters", {}) or {}
                    norm_refs = rag_out.get("norm_refs", []) or []
                    full_chunks = rag_out.get("full_chunks", []) or []
                    rag_context_aug = rag_out.get("rag_context_aug", "") or ""
                    rag_error = rag_out.get("rag_error")

                    meta_entry["refs"] = norm_refs
                    meta_entry["used_filters"] = used_filters
                    meta_entry["context"] = rag_context_aug
                    meta_entry["full_evidence"] = "\n\n".join(full_chunks)
                    qa_meta["final"] = final_text
                    qa_meta["rewrite_used"] = rewrite_used
                    qa_meta["rag_context_chars"] = len(rag_context_aug.strip())
                    qa_meta["rag_error"] = rag_error
                    qa_meta["status"] = "done"
                    _generation_meta[generation_id] = meta_entry

                    # Stream the final text (baseline or rewritten) now
                    async for cleaned_segment in _stream_qa_response(
                        final_text=final_text,
                        chunker=_chunk_text_for_stream,
                        clean_chunk=clean_model_output_chunk,
                    ):
                        if cleaned_segment:
                            output_buf.append(cleaned_segment)
                            token_count += len(cleaned_segment.split())
                            yield cleaned_segment

                    yield END_MARKER + "\n"
                    return

                async for cleaned_note in _stream_response(
                    note_gen=note_gen,
                    prompt=prompt,
                    temperature=temp,
                    max_tokens=max_tokens,
                    stop_tokens=[],
                    clean_chunk=lambda x: x,
                ):
                    raw_note_buf.append(cleaned_note)
                    output_buf.append(cleaned_note)
                    token_count += len(cleaned_note.split())
                    yield cleaned_note
                yield END_MARKER + "\n"
            except asyncio.CancelledError:
                print("Client disconnected - streaming cancelled")
                raise
            except RuntimeError as e:
                error_msg = str(e).lower()
                # Check if this is a context length error from llama-server
                if any(keyword in error_msg for keyword in [
                    "context", "ctx", "kv", "slot", "too long", "too large",
                    "exceeds", "limit", "overflow", "n_ctx"
                ]):
                    print(f"Context length error: {e}")
                    yield (
                        "ERROR: The input is too long for the model's context window.\n\n"
                        "This note cannot be generated because the combined chart data and transcription "
                        "exceed the model's maximum context length.\n\n"
                        "Please try one of the following:\n"
                        "- Reduce the amount of chart data\n"
                        "- Shorten the transcription\n"
                        "- Use a model with a larger context window\n\n"
                        f"Technical details: {str(e)}\n"
                    )
                else:
                    print(f"Runtime error during streaming: {e}")
                    yield f"Error: {str(e)}\n"
            except Exception as e:
                print(f"Error during streaming: {e}")
                yield f"Error: {str(e)}\n"
            finally:
                if global_metrics is not None:
                    duration = time.perf_counter() - t0
                    # Note: NoteGeneratorServer may not expose model_path; use getattr guard
                    global_metrics.record_note(duration, token_count, getattr(note_gen, 'model_path', None))
                try:
                    combined_output = "".join(output_buf)
                    raw_final_output = "".join(raw_note_buf).strip()
                    if is_qa:
                        final_output = clean_model_output_final(combined_output)
                    else:
                        final_output = combined_output
                    if not is_qa:
                        _log_case_completion(
                            case_id=generation_id,
                            created_at=created_at,
                            duration_s=(time.perf_counter() - t0),
                            note_type=note_type,
                            pipeline="legacy_stream",
                            prompt=prompt,
                            input_fields={
                                "chart_data": chart,
                                "transcription": trans,
                                "custom_prompt": custom_prompt,
                            },
                            output_text=(raw_final_output or final_output),
                            prompt_tokens=len((prompt or "").split()),
                            completion_tokens=token_count,
                            actor=actor,
                        )
                    with _cache_lock:
                        if generation_id in _generation_cache:
                            _generation_cache[generation_id]["output"] = final_output
                except Exception as e:
                    print(f"Dataset case logging failed: {e}")

        return StreamingResponse(gen(), media_type="text/plain", headers={"X-Generation-Id": generation_id})

    except Exception as e:
        import traceback
        error_detail = f"Generation unavailable: {str(e)}\n\nTraceback:\n{traceback.format_exc()}"
        print(f"Error in generate_stream: {error_detail}")
        raise HTTPException(status_code=503, detail=error_detail)


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


def _maybe_autostart_consult_comment(gen_id: str, note_text: str, cfg: Dict[str, Any], note_type: str) -> None:
    """Start consult comment generation in background right after note generation."""
    try:
        if _normalize_note_type(note_type or "") != "consult":
            return
        if not bool(cfg.get("consult_comment_autostart", True)):
            return
        if not (note_text or "").strip():
            return

        st = _consult_comment_store.get(gen_id) or {}
        status = str(st.get("status") or "").lower()
        if status in {"pending", "done"}:
            return

        _consult_comment_store[gen_id] = {"status": "pending", "autostart": True}
        asyncio.create_task(_generate_consult_comment(gen_id, note_text, cfg, strategy="sections"))
    except Exception:
        pass


@router.get("/generation/{gen_id}/meta")
async def generation_meta(gen_id: str) -> Dict[str, Any]:
    meta = _generation_meta.get(gen_id)
    if not meta:
        raise HTTPException(status_code=404, detail="generation not found")
    return meta


@router.get("/generation/{gen_id}/consult_comment")
async def get_consult_comment(gen_id: str, request: Request) -> Dict[str, Any]:
    st = _consult_comment_store.get(gen_id)
    force = (request.query_params.get("force") or "").strip().lower() in {"1", "true", "yes"}
    strategy = (request.query_params.get("strategy") or "sections").strip().lower()

    if force:
        note_text = ""
        with _cache_lock:
            entry = _generation_cache.get(gen_id) or {}
            note_text = entry.get("output") or ""
        if not note_text:
            return {"status": "error", "error": "No note output available for retry."}
        _consult_comment_store[gen_id] = {"status": "pending"}
        cfg = load_config()
        asyncio.create_task(_generate_consult_comment(gen_id, note_text, cfg, strategy=strategy))
        return {"status": "pending"}

    if not st:
        note_text = ""
        with _cache_lock:
            entry = _generation_cache.get(gen_id) or {}
            note_text = entry.get("output") or ""
        if not note_text:
            return {"status": "error", "error": "No note output available."}
        _consult_comment_store[gen_id] = {"status": "pending"}
        cfg = load_config()
        asyncio.create_task(_generate_consult_comment(gen_id, note_text, cfg, strategy=strategy))
        return {"status": "pending"}
    return st


@router.get("/generation/{gen_id}/order_requests")
async def get_order_requests(gen_id: str, request: Request) -> Dict[str, Any]:
    st = _order_request_store.get(gen_id)
    force = (request.query_params.get("force") or "").strip().lower() in {"1", "true", "yes"}

    if force:
        note_text = ""
        with _cache_lock:
            entry = _generation_cache.get(gen_id) or {}
            note_text = entry.get("output") or ""
        if not note_text:
            return {"status": "error", "error": "No note output available for retry.", "items": []}
        _order_request_store[gen_id] = {"status": "pending", "items": []}
        cfg = load_config()
        asyncio.create_task(_generate_order_requests(gen_id, note_text, cfg))
        return {"status": "pending", "items": []}

    if not st:
        note_text = ""
        with _cache_lock:
            entry = _generation_cache.get(gen_id) or {}
            note_text = entry.get("output") or ""
        if not note_text:
            return {"status": "error", "error": "No note output available.", "items": []}
        _order_request_store[gen_id] = {"status": "pending", "items": []}
        cfg = load_config()
        asyncio.create_task(_generate_order_requests(gen_id, note_text, cfg))
        return {"status": "pending", "items": []}
    return st


# ---------------------------------------------------------------------------
# Route: /note_prompts - fetch default note templates (authorized via API key)
# ---------------------------------------------------------------------------

@router.get("/note_prompts")
async def get_note_prompts() -> JSONResponse:
    try:
        cfg = load_config()

        system_prompt = _cfg_text(cfg.get("default_note_system_prompt", ""))
        system_prompt_other = _cfg_text(cfg.get("default_note_system_prompt_other", ""))

        raw_templates = cfg.get("default_note_user_prompts", {})
        raw_templates_other = cfg.get("default_note_user_prompts_other", {})
        templates_out = {}

        if isinstance(raw_templates, dict):
            for k, v in raw_templates.items():
                templates_out[k] = _cfg_text(v)
        if isinstance(raw_templates_other, dict):
            for k, v in raw_templates_other.items():
                templates_out[k] = _cfg_text(v)

        return JSONResponse(
            content={
                "success": True,
                "system": system_prompt,
                "system_other": system_prompt_other,
                "templates": templates_out,
            }
        )
    except Exception as e:
        print(f"[ERROR] Failed to fetch note prompts: {e}")
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": "Failed to fetch prompts"},
        )



# ---------------------------------------------------------------------------
# Route: /feedback - record thumbs up/down for a generation
# ---------------------------------------------------------------------------

@router.post("/feedback")
async def record_feedback(payload: Dict, request: Request, session: Session = Depends(get_session)):
    try:
        gen_id = (payload.get("generation_id") or "").strip()
        if not gen_id:
            raise HTTPException(status_code=400, detail="generation_id is required")
        rating = int(payload.get("rating", 1))
        if rating not in (0, 1, 2):
            raise HTTPException(status_code=400, detail="rating must be 0, 1, or 2")
        suggestion_raw = str(payload.get("suggestion") or "").strip()
        skip_rating_event = bool(payload.get("skip_rating_event", False))
        actor = _extract_actor(request, session)

        # Prefer cache (authoritative prompt/output captured at generation)
        prompt = output = None
        with _cache_lock:
            entry = _generation_cache.get(gen_id)
            if entry:
                prompt = entry.get("prompt")
                output = entry.get("output")

        # Allow client to provide prompt/output if cache entry is gone
        if prompt is None:
            prompt = payload.get("prompt")
        if output is None:
            output = payload.get("output")

        if not skip_rating_event:
            event_type = "thumbs_up" if rating == 2 else ("thumbs_down" if rating == 0 else "neutral")
            log_case_event(
                {
                    "event_id": uuid.uuid4().hex,
                    "case_id": gen_id,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "event_type": event_type,
                    "rating": rating,
                    "user_id": actor.get("user_id"),
                    "user_email": actor.get("user_email"),
                    "has_cached_generation": bool(prompt) and output is not None,
                }
            )

        if suggestion_raw:
            suggestion_deid = deidentify_text(suggestion_raw)
            log_case_event(
                {
                    "event_id": uuid.uuid4().hex,
                    "case_id": gen_id,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "event_type": "suggestion",
                    "user_id": actor.get("user_id"),
                    "user_email": actor.get("user_email"),
                    "suggestion": suggestion_deid.get("text", ""),
                    "redaction_counts": suggestion_deid.get("redaction_counts", {}),
                    "leak_flags": suggestion_deid.get("leak_flags", {}),
                }
            )
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)[:160]})
# ---------------------------------------------------------------------------
# Route: /generate_v8_stream - Simple Direct Note Generation (No Extraction)
# ---------------------------------------------------------------------------

@router.post("/generate_v8_stream")
async def generate_v8_stream(request: Request, session: Session = Depends(get_session)):
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

        # Get temperature and max_tokens
        temp: Optional[float] = None
        if payload.get("temperature") is not None:
            try:
                temp = float(payload.get("temperature"))
            except Exception:
                temp = None

        if temp is None:
            temp = float(cfg.get("default_note_temperature", 0.2))

        max_tokens = None
        if payload.get("max_tokens"):
            try:
                max_tokens = int(payload.get("max_tokens"))
            except Exception:
                max_tokens = None

        if max_tokens is None:
            max_tokens = cfg.get("default_note_max_tokens", 4096)

        # Build the prompt using the simple direct approach
        if note_type in OTHER_NOTE_TYPES:
            prompt = build_prompt_other(
                transcription_text=transcription_text,
                old_visits_text=old_visits_text,
                mixed_other_text=mixed_other_text,
                note_type=note_type,
                custom_prompt=custom_prompt,
                user_speciality=user_speciality,
            )
        else:
            prompt = build_prompt_v8(
                transcription_text=transcription_text,
                old_visits_text=old_visits_text,
                mixed_other_text=mixed_other_text,
                note_type=note_type,
                custom_prompt=custom_prompt,
                user_speciality=user_speciality,
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
        actor = _extract_actor(request, session)

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
                    _log_case_completion(
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

                    _maybe_autostart_consult_comment(generation_id, combined_output, cfg, note_type)
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


async def generate_v8(request: Request, session: Session = Depends(get_session)):
    """
    Non-streaming version of the v8 direct note generation.
    Returns JSON with the complete note.
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

        # Get temperature and max_tokens
        temp: Optional[float] = None
        if payload.get("temperature") is not None:
            try:
                temp = float(payload.get("temperature"))
            except Exception:
                temp = None

        if temp is None:
            temp = float(cfg.get("default_note_temperature", 0.2))

        max_tokens = None
        if payload.get("max_tokens"):
            try:
                max_tokens = int(payload.get("max_tokens"))
            except Exception:
                max_tokens = None

        if max_tokens is None:
            max_tokens = cfg.get("default_note_max_tokens", 4096)

        # Build the prompt using the simple direct approach
        if note_type in OTHER_NOTE_TYPES:
            prompt = build_prompt_other(
                transcription_text=transcription_text,
                old_visits_text=old_visits_text,
                mixed_other_text=mixed_other_text,
                note_type=note_type,
                custom_prompt=custom_prompt,
                user_speciality=user_speciality,
            )
        else:
            prompt = build_prompt_v8(
                transcription_text=transcription_text,
                old_visits_text=old_visits_text,
                mixed_other_text=mixed_other_text,
                note_type=note_type,
                custom_prompt=custom_prompt,
                user_speciality=user_speciality,
            )

        generation_id = uuid.uuid4().hex
        created_at = datetime.now(timezone.utc).isoformat()
        actor = _extract_actor(request, session)
        with _cache_lock:
            _generation_cache[generation_id] = {"prompt": prompt, "output": ""}

        _generation_meta[generation_id] = {
            "refs": [],
            "used_filters": {},
            "context": "",
            "full_evidence": "",
            "pipeline": "v8_direct",
        }

        t0 = time.perf_counter()

        # Direct LLM call
        try:
            note_text = await note_gen.collect_completion(
                prompt,
                temperature=temp,
                max_tokens=max_tokens,
                stop=NOTE_STOP_TOKENS,
            )
        except ExternalServiceError as e:
            return JSONResponse(status_code=503, content={"error": "service_unavailable", "detail": _service_error_detail(e)})

        duration = time.perf_counter() - t0
        cleaned = clean_model_output_final(_strip_note_end_marker(note_text))

        with _cache_lock:
            if generation_id in _generation_cache:
                _generation_cache[generation_id]["output"] = cleaned

        _log_case_completion(
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
            output_text=cleaned,
            prompt_tokens=len((prompt or "").split()),
            completion_tokens=len((cleaned or "").split()),
            actor=actor,
        )

        _maybe_autostart_consult_comment(generation_id, cleaned, cfg, note_type)
        _maybe_autostart_order_requests(generation_id, cleaned, cfg)

        return JSONResponse(content={
            "generation_id": generation_id,
            "note": cleaned,
            "pipeline": "v8_direct",
            "stats": {
                "duration_seconds": round(duration, 2),
                "input_chars": len(transcription_text) + len(old_visits_text) + len(mixed_other_text),
                "output_chars": len(cleaned),
            }
        })

    except Exception as e:
        import traceback
        error_detail = f"Generation failed: {str(e)}"
        print(f"Error in generate_v8: {error_detail}\n{traceback.format_exc()}")
        return JSONResponse(
            status_code=500,
            content={"error": str(e)[:500], "type": "generation_error"}
        )


# trigger reload
