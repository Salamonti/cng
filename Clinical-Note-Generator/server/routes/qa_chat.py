import asyncio
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from server.core.security import decode_access_token
from server.services.note_generator_clean import get_simple_note_generator
from server.services.qa_deid import deidentify_text
from server.services.qa_web_search import searx_search
from server.services.rag_http_client import RAGHttpClient

router = APIRouter(prefix="/qa", tags=["qa-chat"])
security = HTTPBearer(auto_error=False)

_QA_STATE: Dict[Tuple[str, str], Dict[str, Any]] = {}


class QAChatRequest(BaseModel):
    message: str = Field(..., min_length=3, max_length=8000)
    session_id: str = Field(default="default", max_length=64)


class QAChatResponse(BaseModel):
    answer: str
    summary: str
    sources: List[Dict[str, Any]]
    deid_counts: Dict[str, int]


def _load_cfg() -> Dict[str, Any]:
    try:
        cfg_path = Path(__file__).resolve().parents[2] / "config" / "config.json"
        if cfg_path.exists():
            return json.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


async def _rag_query(question: str, cfg: Dict[str, Any]) -> Tuple[str, List[Dict[str, Any]]]:
    base = cfg.get("rag_service_url")
    if not base:
        return "", []
    rag = RAGHttpClient(str(base), timeout=int(cfg.get("rag_timeout_ms", 12000)))
    ctx, refs, _used = await rag.query(question, top_k=int(cfg.get("qa_chat_rag_top_k", 8)))
    return ctx or "", refs or []


def _build_prompt(message: str, state: Dict[str, Any], rag_ctx: str, web_items: List[Dict[str, Any]], allow_knowledge_fallback: bool = False) -> str:
    summary = state.get("summary", "")
    recent = state.get("turns", [])[-4:]
    recent_text = "\n".join([f"Q: {t.get('q','')}\nA: {t.get('a','')}" for t in recent])
    web_ctx = "\n\n".join([f"[{i+1}] {w.get('title','')}\n{w.get('snippet','')}\n{w.get('url','')}" for i, w in enumerate(web_items[:6])])
    fallback_rule = (
        "If RAG/web evidence is weak, you MAY use stable core medical knowledge, but label it as 'Knowledge fallback' and mention likely knowledge cutoff uncertainty.\n"
        if allow_knowledge_fallback else
        "Prefer evidence-grounded answer. If evidence is weak, keep uncertainty explicit and avoid fabricated citations.\n"
    )
    return (
        "You are a clinical Q&A assistant for healthcare professionals.\n"
        "Provide a practical answer first, then concise supporting details.\n"
        "Never fabricate citations.\n"
        + fallback_rule +
        "Output concise plain text with practical clinical actions.\n\n"
        f"Conversation Summary:\n{summary}\n\n"
        f"Recent Turns:\n{recent_text}\n\n"
        f"Local RAG Evidence:\n{rag_ctx}\n\n"
        f"Web Evidence (allowlisted):\n{web_ctx}\n\n"
        f"Current User Question:\n{message}\n\n"
        "Answer with sections when relevant: Direct Answer, Differential, Workup, Management, Safety.\n"
    )


async def _update_summary(state: Dict[str, Any], llm, max_tokens: int = 240) -> str:
    turns = state.get("turns", [])[-8:]
    convo = "\n".join([f"Q: {t.get('q','')}\nA: {t.get('a','')}" for t in turns])
    prompt = (
        "Summarize the ongoing clinical chat state in <=220 tokens.\n"
        "Include: active question focus, key conclusions, open questions, red flags.\n"
        "Plain text only.\n\n"
        f"Conversation:\n{convo}\n\nSummary:\n"
    )
    try:
        text = await llm.collect_completion(prompt, temperature=0.1, max_tokens=max_tokens, stop=[])
        return (text or "").strip()[:1600]
    except Exception:
        return (state.get("summary") or "")[:1600]


@router.post("/chat", response_model=QAChatResponse)
async def qa_chat(req: QAChatRequest, creds: Optional[HTTPAuthorizationCredentials] = Depends(security)):
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

    deid_q, counts = deidentify_text(req.message)
    state_key = (user_id, req.session_id)
    state = _QA_STATE.get(state_key) or {"summary": "", "turns": []}

    rag_task = asyncio.create_task(_rag_query(deid_q, cfg))
    web_task = asyncio.create_task(searx_search(deid_q, limit=int(cfg.get("qa_chat_web_k", 6))))
    rag_ctx, rag_refs = await rag_task
    web_items = await web_task

    evidence_chars = len((rag_ctx or '').strip()) + sum(len((w.get('snippet') or '')) for w in web_items)
    weak_evidence = evidence_chars < int(cfg.get("qa_chat_min_evidence_chars", 260))

    llm = get_simple_note_generator()
    prompt = _build_prompt(req.message, state, rag_ctx, web_items, allow_knowledge_fallback=weak_evidence)
    answer = await llm.collect_completion(
        prompt,
        temperature=float(cfg.get("qa_chat_temperature", 0.2)),
        max_tokens=int(cfg.get("qa_chat_max_tokens", 700)),
        stop=[],
    )
    answer = (answer or "").strip()

    if weak_evidence and req.message.lower().strip().startswith(("what is the dose", "dose of", "dosing of", "ozempic")):
        answer = answer + "\n\nKnowledge fallback: Provided from model core knowledge due limited indexed/web evidence in this turn; verify against latest label/guideline updates."

    state["turns"].append({"q": req.message, "a": answer})
    state["turns"] = state["turns"][-12:]
    state["summary"] = await _update_summary(state, llm)
    _QA_STATE[state_key] = state

    sources: List[Dict[str, Any]] = []
    for r in rag_refs[:6]:
        md = r.get("metadata", {}) if isinstance(r, dict) else {}
        sources.append({"kind": "rag", "title": md.get("title") or "", "url": md.get("link") or "", "year": md.get("year")})
    for w in web_items[:6]:
        sources.append({"kind": "web", "title": w.get("title"), "url": w.get("url")})

    return QAChatResponse(
        answer=answer,
        summary=state.get("summary", ""),
        sources=sources,
        deid_counts=counts,
    )
