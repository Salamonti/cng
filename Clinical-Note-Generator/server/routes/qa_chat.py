import asyncio
import hashlib
import json
import logging
import os
import re
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, AsyncIterator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from server.core.security import decode_access_token
from server.services.note_generator_clean import get_simple_note_generator
from server.core.deid.v1 import deidentify_text as _deid_v1
from server.core.llm_request_policy import strip_reasoning_markup
from server.services.qa_web_search import searx_search
from server.services.rag_http_client import RAGHttpClient

router = APIRouter(prefix="/qa", tags=["qa-chat"])
security = HTTPBearer(auto_error=False)
logger = logging.getLogger(__name__)


class _BoundedSessionStore:
    """In-memory session store with a hard entry cap (LRU eviction) and a
    TTL. _QA_STATE and _QA_VISION_IMAGE were plain unbounded dicts keyed by
    (user_id, session_id) with nothing that ever removed an entry except a
    few explicit call sites -- over a long-running process (this service
    restarts rarely) they grow forever, holding PHI-adjacent QA turns and
    raw uploaded images in memory indefinitely.
    """

    def __init__(self, max_entries: int, ttl_seconds: float) -> None:
        self._max_entries = max_entries
        self._ttl_seconds = ttl_seconds
        self._data: "OrderedDict[Tuple[str, str], Tuple[float, Dict[str, Any]]]" = OrderedDict()

    def _prune_expired(self) -> None:
        if self._ttl_seconds <= 0:
            return
        now = time.time()
        expired = [k for k, (ts, _v) in self._data.items() if now - ts > self._ttl_seconds]
        for k in expired:
            self._data.pop(k, None)

    def get(self, key: Tuple[str, str]) -> Optional[Dict[str, Any]]:
        self._prune_expired()
        row = self._data.get(key)
        if row is None:
            return None
        self._data.move_to_end(key)
        return row[1]

    def set(self, key: Tuple[str, str], value: Dict[str, Any]) -> None:
        self._prune_expired()
        self._data[key] = (time.time(), value)
        self._data.move_to_end(key)
        while len(self._data) > self._max_entries:
            self._data.popitem(last=False)

    def pop(self, key: Tuple[str, str]) -> None:
        self._data.pop(key, None)

    def __len__(self) -> int:
        return len(self._data)


_QA_SESSION_MAX_ENTRIES = int(os.environ.get("QA_SESSION_MAX_ENTRIES", "500"))
_QA_SESSION_TTL_SECONDS = float(os.environ.get("QA_SESSION_TTL_SECONDS", str(24 * 3600)))

_QA_STATE = _BoundedSessionStore(_QA_SESSION_MAX_ENTRIES, _QA_SESSION_TTL_SECONDS)

# Last uploaded image per QA session (bytes + mime) for vision follow-ups without re-uploading.
_QA_VISION_IMAGE = _BoundedSessionStore(_QA_SESSION_MAX_ENTRIES, _QA_SESSION_TTL_SECONDS)

_MAX_PRIOR_VISION_CHARS = 8000

QA_MAX_USER_CHARS_DEFAULT = 8192
QA_MAX_USER_CHARS_CEILING = 32000


def qa_max_user_chars(cfg: Dict[str, Any]) -> int:
    try:
        v = int(cfg.get("qa_max_user_chars", QA_MAX_USER_CHARS_DEFAULT))
    except (TypeError, ValueError):
        v = QA_MAX_USER_CHARS_DEFAULT
    return max(64, min(QA_MAX_USER_CHARS_CEILING, v))


def enforce_qa_user_message_length(message: str, cfg: Dict[str, Any]) -> None:
    limit = qa_max_user_chars(cfg)
    if len(message) > limit:
        raise HTTPException(
            status_code=400,
            detail=f"Message exceeds maximum length ({limit} characters)",
        )


def set_qa_vision_session_image(user_id: str, session_id: str, data: bytes, mime: str) -> None:
    """Store image for /qa/vision follow-up requests that omit the file."""
    state_key = (user_id, session_id)
    fingerprint = hashlib.sha256(data).hexdigest()
    existing = _QA_VISION_IMAGE.get(state_key) or {}
    evidence = existing.get("evidence") if existing.get("fingerprint") == fingerprint else None
    _QA_VISION_IMAGE.set(state_key, {
        "bytes": data,
        "mime": (mime or "image/jpeg").strip(),
        "fingerprint": fingerprint,
        "evidence": evidence,
    })


def get_qa_vision_session_image(user_id: str, session_id: str) -> Optional[Tuple[bytes, str]]:
    row = _QA_VISION_IMAGE.get((user_id, session_id))
    if not row:
        return None
    b = row.get("bytes")
    m = row.get("mime") or "image/jpeg"
    if not isinstance(b, (bytes, bytearray)) or not b:
        return None
    return bytes(b), str(m)


def clear_qa_vision_session_image(user_id: str, session_id: str) -> None:
    _QA_VISION_IMAGE.pop((user_id, session_id))


def get_qa_vision_session_evidence(user_id: str, session_id: str) -> Optional[Dict[str, Any]]:
    row = _QA_VISION_IMAGE.get((user_id, session_id)) or {}
    evidence = row.get("evidence")
    return dict(evidence) if isinstance(evidence, dict) else None


def set_qa_vision_session_evidence(
    user_id: str,
    session_id: str,
    evidence: Dict[str, Any],
) -> None:
    row = _QA_VISION_IMAGE.get((user_id, session_id))
    if row is not None:
        row["evidence"] = dict(evidence)


def get_qa_session_state(user_id: str, session_id: str) -> Dict[str, Any]:
    """Return the stored QA session (summary + turns) for text and vision combined."""
    return _QA_STATE.get((user_id, session_id)) or {"summary": "", "turns": []}


def append_qa_turn_for_session(
    user_id: str,
    session_id: str,
    q: str,
    a: str,
    channel: str = "text",
) -> Dict[str, Any]:
    """Append a turn and persist. channel is 'text' or 'vision' for mixed-session context."""
    state_key = (user_id, session_id)
    state = _QA_STATE.get(state_key) or {"summary": "", "turns": []}
    state["turns"].append({"q": q, "a": a, "channel": channel})
    state["turns"] = state["turns"][-12:]
    _QA_STATE.set(state_key, state)
    return state


def format_prior_turns_for_vision(turns: List[Dict[str, Any]], max_chars: int = _MAX_PRIOR_VISION_CHARS) -> str:
    """Format prior text + vision Q&A for the vision model prompt."""
    parts: List[str] = []
    for t in turns[-6:]:
        q = (t.get("q") or "").strip()
        a = (t.get("a") or "").strip()
        if not q and not a:
            continue
        ch = (t.get("channel") or "text").lower()
        label = "image" if ch == "vision" else "text"
        a = a[:4000] if len(a) > 4000 else a
        parts.append(f"User ({label}): {q}\nAssistant: {a}")
    text = "\n\n".join(parts)
    if len(text) > max_chars:
        return text[-max_chars:]
    return text


def mixed_session_context_for_vision(
    prior_turns: List[Dict[str, Any]],
    session_summary: str,
    max_chars: int = 4000,
) -> str:
    """Non-image context for the vision model when the user mixed text Q&A with image Q&A in the same session.

    Vision multi-turn chat only includes prior *vision* turns; this string carries text-only discussion + summary
    so the first image question (or a new image after text) is not blind to earlier text messages.
    """
    parts: List[str] = []
    sm = (session_summary or "").strip()
    if sm:
        cap = min(2000, max(800, max_chars // 2))
        parts.append("## Session summary (may include text Q&A)\n" + sm[:cap])
    nv = [t for t in prior_turns if (t.get("channel") or "text").lower() != "vision"]
    if nv:
        nv_text = format_prior_turns_for_vision(nv, max_chars=min(3000, max_chars))
        if nv_text.strip():
            parts.append("## Prior text-only turns in this topic\n" + nv_text)
    out = "\n\n".join(parts).strip()
    if len(out) > max_chars:
        return out[-max_chars:]
    return out


def _format_turn_for_prompt(t: Dict[str, Any]) -> str:
    ch = (t.get("channel") or "text").lower()
    q = t.get("q", "")
    a = t.get("a", "")
    if ch == "vision":
        return f"Q (image Q&A): {q}\nA: {a}"
    return f"Q: {q}\nA: {a}"


class QAChatRequest(BaseModel):
    message: str = Field(..., min_length=3, max_length=32000)  # P8: doubled vs legacy 8k
    session_id: str = Field(default="default", max_length=64)


class QAChatResponse(BaseModel):
    answer: str
    summary: str
    sources: List[Dict[str, Any]]
    deid_counts: Dict[str, int]
    web_results_count: int = 0
    rag_results_count: int = 0
    used_knowledge_fallback: bool = False
    evidence_max_year: Optional[int] = None


def _build_sources(rag_refs: List[Dict[str, Any]], web_items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    sources: List[Dict[str, Any]] = []
    for r in rag_refs[:6]:
        md = r.get("metadata", {}) if isinstance(r, dict) else {}
        sources.append({
            "kind": "rag",
            "title": md.get("title") or "",
            "url": md.get("link") or "",
            "year": md.get("year"),
            "tier": r.get("tier") or "web",
            "score": round(float(r.get("score", 0.0)), 4),
        })
    for w in web_items[:6]:
        sources.append({
            "kind": "web",
            "title": w.get("title"),
            "url": w.get("url"),
            "tier": "web",
            "score": 0.5,
        })
    return sources


_YEAR_TOKEN_RE = re.compile(r"\b(19|20)\d{2}\b")
# Reject junk years from bad metadata (e.g. doc IDs stored as "year").
_YEAR_MIN = 1990
_YEAR_MAX = 2035


def _parse_evidence_year_value(y: Any) -> Optional[int]:
    """Extract a plausible publication year from RAG metadata; ignore garbage (e.g. 8211)."""
    if y is None or isinstance(y, bool):
        return None
    if isinstance(y, (int, float)):
        yi = int(y)
        if _YEAR_MIN <= yi <= _YEAR_MAX:
            return yi
        return None
    s = str(y).strip()
    if not s:
        return None
    found = [int(m.group(0)) for m in _YEAR_TOKEN_RE.finditer(s)]
    if found:
        good = [x for x in found if _YEAR_MIN <= x <= _YEAR_MAX]
        return max(good) if good else None
    try:
        yi = int(float(s.replace(",", "")))
        if _YEAR_MIN <= yi <= _YEAR_MAX:
            return yi
    except (ValueError, TypeError, OverflowError):
        # Unparseable/out-of-range year token -> treat as "no year" (None) so
        # one malformed RAG metadata value can't crash evidence-max-year
        # computation. Deliberate silent swallow (safe parse guard).
        pass
    return None


def _compute_evidence_max_year(rag_refs: List[Dict[str, Any]]) -> Optional[int]:
    years: List[int] = []
    for r in rag_refs[:20]:
        md = r.get("metadata", {}) if isinstance(r, dict) else {}
        py = _parse_evidence_year_value(md.get("year"))
        if py is not None:
            years.append(py)
    return max(years) if years else None


def _strip_model_reasoning_markup(answer: str) -> str:
    """Remove Qwen/DeepSeek-style reasoning blocks so the UI shows the answer, not only 'thinking'.

    Qwen3.x often fills max_tokens with `<think>`...`</think>` or `<think>`...`</think>`; note generation
    already strips `<think>` in notes.py — QA chat must do the same or users see thinking then only Evidence sources.
    """
    return strip_reasoning_markup(answer)



def _finalize_qa_answer(answer: str) -> str:
    """Strip model wrappers/noise; return clinical answer text only."""
    return _strip_qa_inline_citation_noise(_strip_model_reasoning_markup((answer or "").strip()))


def _qa_empty_answer_fallback() -> str:
    return (
        "The model did not return answer text for this question. "
        "Please try again or rephrase slightly."
    )


def _strip_qa_inline_citation_noise(answer: str) -> str:
    """Remove [RAG #n]-style markers; UI already lists sources. Tidy leftover '(per , …)' from stripped tags."""
    if not answer:
        return answer
    answer = re.sub(r"\[RAG\s*#\d+\]", "", answer, flags=re.I)
    answer = re.sub(r"\[WEB\s*#\d+\]", "", answer, flags=re.I)
    answer = re.sub(
        r"\(\s*per\s*,\s*([^)]+)\)",
        lambda m: "(" + m.group(1).strip().lstrip(",").strip() + ")",
        answer,
    )
    answer = re.sub(r"\(\s*\)", "", answer)
    answer = re.sub(r"[ \t]{2,}", " ", answer)
    return answer.strip()


def _postprocess_answer_year_disclaimer(answer: str, evidence_max_year: Optional[int]) -> str:
    if evidence_max_year and evidence_max_year >= 2025:
        answer = answer.replace("(Note: Dosing reflects FDA/EMA labels as of 2023; local regulations may vary.)", "")
        answer = answer.replace("as of 2023", f"as of {evidence_max_year}")
        answer = answer.replace("since 2023", f"since {evidence_max_year}")
    return answer


def _knowledge_fallback_needed(message: str, weak_evidence: bool) -> bool:
    return bool(weak_evidence and message.lower().strip().startswith(("what is the dose", "dose of", "dosing of", "ozempic")))


def _knowledge_fallback_text() -> str:
    return (
        "\n\nKnowledge fallback: Provided from model core knowledge due limited indexed/web evidence in this turn; "
        "verify against latest label/guideline updates."
    )


async def _resolve_request_context(
    req: QAChatRequest,
    creds: Optional[HTTPAuthorizationCredentials],
) -> Tuple[
    Dict[str, Any],
    str,
    Dict[str, Any],
    Dict[str, int],
    str,   # deid_q (de-identified question — use this everywhere, never req.message)
    str,   # rag_ctx
    List[Dict[str, Any]],  # rag_refs
    List[Dict[str, Any]],  # web_items
    bool,  # weak_evidence
]:
    cfg = _load_cfg()
    if not bool(cfg.get("qa_chat_v2_enabled", True)):
        raise HTTPException(status_code=503, detail="qa_chat_v2 is disabled")

    token = creds.credentials if creds else ""
    if not token:
        raise HTTPException(status_code=401, detail="Missing bearer token")
    try:
        payload = decode_access_token(token)
        user_id = str(payload.get("sub") or "")
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid bearer token")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token subject")

    enforce_qa_user_message_length(req.message, cfg)

    deid_result = _deid_v1(req.message)
    deid_q = deid_result["text"]
    counts = deid_result["redaction_counts"]
    state_key = (user_id, req.session_id)
    state = _QA_STATE.get(state_key) or {"summary": "", "turns": []}

    rag_ctx, rag_refs, web_items, weak_evidence = await retrieve_qa_evidence(deid_q, cfg)
    return cfg, state_key, state, counts, deid_q, rag_ctx, rag_refs, web_items, weak_evidence


def _load_cfg() -> Dict[str, Any]:
    try:
        cfg_path = Path(__file__).resolve().parents[2] / "config" / "config.json"
        if cfg_path.exists():
            return json.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception:
        # Config file present but unreadable/corrupt: fall back to {} (defaults)
        # rather than crash the QA flow. A corrupt *present* config is a real
        # misconfiguration, so surface it instead of swallowing invisibly.
        logger.warning("Failed to load QA config/config.json; using defaults", exc_info=True)
    return {}


async def _rag_query(question: str, cfg: Dict[str, Any]) -> Tuple[str, List[Dict[str, Any]]]:
    base = os.environ.get("RAG_URL") or cfg.get("rag_service_url")
    if not base:
        return "", []
    rag = RAGHttpClient(str(base), timeout=int(cfg.get("rag_timeout_ms", 12000)))
    ctx, refs, _used = await rag.query(question, top_k=int(cfg.get("qa_chat_rag_top_k", 8)))
    return ctx or "", refs or []


async def retrieve_qa_evidence(
    question: str,
    cfg: Dict[str, Any],
) -> Tuple[str, List[Dict[str, Any]], List[Dict[str, Any]], bool]:
    """Retrieve local and web evidence concurrently; one failed source must not block QA."""

    async def safe_rag() -> Tuple[str, List[Dict[str, Any]]]:
        try:
            return await _rag_query(question, cfg)
        except Exception as exc:
            logger.warning("QA RAG retrieval failed: %s", exc)
            return "", []

    async def safe_web() -> List[Dict[str, Any]]:
        try:
            return await searx_search(question, limit=int(cfg.get("qa_chat_web_k", 6)))
        except Exception as exc:
            logger.warning("QA web retrieval failed: %s", exc)
            return []

    (rag_ctx, rag_refs), web_items = await asyncio.gather(safe_rag(), safe_web())
    evidence_chars = len((rag_ctx or "").strip()) + sum(
        len((item.get("snippet") or "")) for item in web_items
    )
    weak_evidence = evidence_chars < int(cfg.get("qa_chat_min_evidence_chars", 260))
    return rag_ctx, rag_refs, web_items, weak_evidence


def _truncate_for_summary_block(text: str, max_chars: int) -> str:
    t = (text or "").strip()
    if len(t) <= max_chars:
        return t
    return t[: max_chars - 60].rstrip() + "\n\n[Earlier text truncated for summarization…]"


def _build_prompt(message: str, state: Dict[str, Any], rag_ctx: str, web_items: List[Dict[str, Any]], allow_knowledge_fallback: bool = False) -> str:
    summary = state.get("summary", "")
    recent = state.get("turns", [])[-6:]
    recent_text = "\n".join([_format_turn_for_prompt(t) for t in recent])
    web_ctx = "\n\n".join([f"[{i+1}] {w.get('title','')}\n{w.get('snippet','')}\n{w.get('url','')}" for i, w in enumerate(web_items[:6])])
    fallback_rule = (
        "If indexed/web evidence is weak, you MAY use stable core medical knowledge; label clearly as knowledge-based "
        "when appropriate and avoid fake citations.\n"
        if allow_knowledge_fallback else
        "If indexed evidence is thin, still give a complete clinically useful answer from established practice; "
        "keep uncertainty explicit and never invent specific study citations.\n"
    )
    return (
        "You are a clinical Q&A assistant for healthcare professionals.\n\n"
        "Evidence policy:\n"
        "- Local RAG snippets and web snippets below are SUPPLEMENTARY. They are not the only allowed content.\n"
        "- For management, dosing, drug choice, algorithms, and stepwise care: give the full detail a clinician would "
        "expect (drug classes, typical first-line agents, when to add second agents, monitoring, key contraindications) "
        "even if snippets are short or incomplete. Use them to support or update specifics where relevant.\n"
        "- Never fabricate journal citations or URLs. Do not imply a specific paper unless it appears in the snippets.\n\n"
        "Formatting:\n"
        "- Use clear structure (numbered steps or short headings) for complex topics. Keep numbering consistent (1. 2. 3.).\n"
        "- Citations: do NOT write [RAG #1], [WEB #2], or 'Definitions (per [RAG #1], …)'. Either omit inline refs, or use "
        "superscript footnote numbers ¹ ² ³ that match the source order in the UI (Unicode superscripts only).\n"
        "- Do NOT add a separate 'Evidence base', 'References', or 'Sources' section in your answer — a source list is "
        "shown automatically in the app UI below your reply. Avoid duplicating that list in prose.\n"
        "- Avoid repeating the same citation block twice.\n\n"
        "Never fabricate citations.\n"
        + fallback_rule +
        "\n"
        f"Conversation Summary:\n{summary}\n\n"
        f"Recent Turns:\n{recent_text}\n\n"
        f"Local RAG Evidence (supplementary):\n{rag_ctx}\n\n"
        f"Web Evidence (allowlisted, supplementary):\n{web_ctx}\n\n"
        f"Current User Question:\n{message}\n\n"
        "Answer:\n"
    )


def _summary_params(cfg: Dict[str, Any]) -> Tuple[int, int]:
    mt = int(cfg.get("qa_chat_summary_max_tokens", 480))
    cc = int(cfg.get("qa_chat_summary_convo_max_chars", 12000))
    return max(120, min(mt, 2000)), max(2000, min(cc, 32000))


async def _update_summary(state: Dict[str, Any], llm, max_tokens: int, convo_max_chars: int) -> str:
    turns = state.get("turns", [])[-10:]
    convo = "\n".join([_format_turn_for_prompt(t) for t in turns])
    convo = _truncate_for_summary_block(convo, convo_max_chars)
    prompt = (
        "Summarize the ongoing clinical chat state clearly (paragraphs and bullets allowed).\n"
        "Include: active question focus, key conclusions, open questions, red flags.\n"
        "Stay within the token budget implied by the model call.\n\n"
        f"Conversation:\n{convo}\n\nSummary:\n"
    )
    try:
        text = await llm.collect_completion(prompt, temperature=0.1, max_tokens=max_tokens, stop=[])
        return (text or "").strip()[:2400]
    except Exception:
        return (state.get("summary") or "")[:2400]


@router.post("/chat", response_model=QAChatResponse)
async def qa_chat(req: QAChatRequest, creds: Optional[HTTPAuthorizationCredentials] = Depends(security)):
    cfg, state_key, state, counts, deid_q, rag_ctx, rag_refs, web_items, weak_evidence = await _resolve_request_context(req, creds)

    llm = get_simple_note_generator("qa_text")
    # Use de-identified question — never send raw PHI to LLM or store in session state
    prompt = _build_prompt(deid_q, state, rag_ctx, web_items, allow_knowledge_fallback=weak_evidence)
    raw_answer = await llm.collect_completion(
        prompt,
        temperature=float(cfg.get("qa_chat_temperature", 0.2)),
        max_tokens=int(cfg.get("qa_chat_max_tokens", 3072)),
        stop=[],
    )
    answer = _finalize_qa_answer(raw_answer)
    if not answer:
        answer = _finalize_qa_answer(
            await llm.collect_completion(
                prompt,
                temperature=float(cfg.get("qa_chat_temperature", 0.2)),
                max_tokens=int(cfg.get("qa_chat_max_tokens", 3072)),
                stop=[],
            )
        )
    if not answer:
        answer = _qa_empty_answer_fallback()

    used_knowledge_fallback = _knowledge_fallback_needed(deid_q, weak_evidence)
    if used_knowledge_fallback:
        answer = answer + _knowledge_fallback_text()

    state["turns"].append({"q": deid_q, "a": answer, "channel": "text"})
    state["turns"] = state["turns"][-12:]
    sm_tok, sm_chars = _summary_params(cfg)
    state["summary"] = await _update_summary(state, llm, sm_tok, sm_chars)
    _QA_STATE.set(state_key, state)

    evidence_max_year = _compute_evidence_max_year(rag_refs)
    answer = _postprocess_answer_year_disclaimer(answer, evidence_max_year)
    sources = _build_sources(rag_refs, web_items)

    return QAChatResponse(
        answer=answer,
        summary=state.get("summary", ""),
        sources=sources,
        deid_counts=counts,
        web_results_count=len(web_items),
        rag_results_count=len(rag_refs),
        used_knowledge_fallback=used_knowledge_fallback,
        evidence_max_year=evidence_max_year,
    )


@router.post("/chat_stream")
async def qa_chat_stream(req: QAChatRequest, creds: Optional[HTTPAuthorizationCredentials] = Depends(security)):
    cfg, state_key, state, counts, deid_q, rag_ctx, rag_refs, web_items, weak_evidence = await _resolve_request_context(req, creds)
    llm = get_simple_note_generator("qa_text")
    prompt = _build_prompt(deid_q, state, rag_ctx, web_items, allow_knowledge_fallback=weak_evidence)
    used_knowledge_fallback = _knowledge_fallback_needed(deid_q, weak_evidence)

    sm_tok, sm_chars = _summary_params(cfg)

    async def generate() -> AsyncIterator[str]:
        answer_parts: List[str] = []
        async for chunk in llm.stream_completion(
            prompt,
            temperature=float(cfg.get("qa_chat_temperature", 0.2)),
            max_tokens=int(cfg.get("qa_chat_max_tokens", 3072)),
            stop=[],
        ):
            answer_parts.append(chunk)
            yield chunk

        if used_knowledge_fallback:
            tail = _knowledge_fallback_text()
            answer_parts.append(tail)
            yield tail

        answer = _finalize_qa_answer("".join(answer_parts))
        if not answer:
            answer = _qa_empty_answer_fallback()
        state["turns"].append({"q": deid_q, "a": answer, "channel": "text"})
        state["turns"] = state["turns"][-12:]
        state["summary"] = await _update_summary(state, llm, sm_tok, sm_chars)
        _QA_STATE.set(state_key, state)

        evidence_max_year = _compute_evidence_max_year(rag_refs)
        sources = _build_sources(rag_refs, web_items)
        meta = {
            "summary": state.get("summary", ""),
            "sources": sources,
            "deid_counts": counts,
            "web_results_count": len(web_items),
            "rag_results_count": len(rag_refs),
            "used_knowledge_fallback": used_knowledge_fallback,
            "evidence_max_year": evidence_max_year,
        }
        yield f"\n\n__QA_META__{json.dumps(meta, ensure_ascii=True)}"

    return StreamingResponse(
        generate(),
        media_type="text/plain",
        headers={
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-cache",
        },
    )
