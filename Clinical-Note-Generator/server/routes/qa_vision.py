"""Two-stage vision QA: grounded Qwen evidence, then a DeepSeek clinical answer."""

import json
from typing import Any, AsyncIterator, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from server.core.deid.v1 import deidentify_text as _deid_v1
from server.core.security import decode_access_token
from server.routes.qa_chat import (
    _QA_STATE,
    _build_prompt,
    _build_sources,
    _compute_evidence_max_year,
    _finalize_qa_answer,
    _load_cfg,
    _summary_params,
    _update_summary,
    append_qa_turn_for_session,
    enforce_qa_user_message_length,
    get_qa_session_state,
    get_qa_vision_session_evidence,
    get_qa_vision_session_image,
    retrieve_qa_evidence,
    set_qa_vision_session_evidence,
    set_qa_vision_session_image,
)
from server.services.note_generator_clean import get_simple_note_generator
from server.services.vision_qa_client import (
    VisionQAEngine,
    format_visual_evidence,
    normalize_visual_evidence,
)

router = APIRouter(prefix="/qa", tags=["qa-vision"])
security = HTTPBearer(auto_error=False)


class VisionQAResponse(BaseModel):
    """Reserved non-streaming response contract."""

    answer: str
    confidence: float = 0.0
    model_used: str = ""


def build_vision_retrieval_query(question: str, evidence: Dict[str, Any]) -> str:
    """Build a de-identified evidence query without sending verbatim image text to web search."""
    normalized = normalize_visual_evidence(evidence)
    searchable = {
        "image_type": normalized.get("image_type"),
        "observations": normalized.get("observations"),
        "measurements": normalized.get("measurements"),
        "uncertainties": normalized.get("uncertainties"),
    }
    evidence_text = json.dumps(searchable, ensure_ascii=True)
    safe_evidence = _deid_v1(evidence_text)["text"]
    return (question.strip() + "\nVisual evidence terms: " + safe_evidence)[:4000]


def build_vision_reasoning_prompt(
    question: str,
    evidence: Dict[str, Any],
    state: Dict[str, Any],
    rag_ctx: str,
    web_items: List[Dict[str, Any]],
    weak_evidence: bool,
) -> str:
    """Build the thinking-disabled DeepSeek prompt from Qwen's bounded evidence."""
    visual_block = format_visual_evidence(evidence)
    message = (
        "Answer the clinician's question using the visual evidence object below. The object was produced by a "
        "separate vision extractor and may be incomplete or wrong. Treat its entries as reported observations, "
        "not as diagnoses. Distinguish visible evidence from clinical interpretation, respect the listed quality "
        "limitations, and do not claim to have inspected image details that are absent from the object.\n\n"
        f"Clinician question:\n{question}\n\n"
        f"Visual evidence object:\n{visual_block}"
    )
    return _build_prompt(
        message,
        state,
        rag_ctx,
        web_items,
        allow_knowledge_fallback=weak_evidence,
    )


@router.post("/vision")
async def qa_vision(
    question: Optional[str] = Form(default=None, max_length=8192),
    session_id: str = Form(default="default", max_length=64),
    image: Optional[UploadFile] = File(None),
    file: Optional[UploadFile] = File(None),
    upload: Optional[UploadFile] = File(None),
    document: Optional[UploadFile] = File(None),
    creds: HTTPAuthorizationCredentials = Depends(security),
):
    """Stream a DeepSeek answer grounded in Qwen-extracted evidence for one cached image."""
    image = next(
        (candidate for candidate in (image, file, upload, document) if candidate is not None),
        None,
    )

    token = creds.credentials if creds else ""
    if not token:
        raise HTTPException(status_code=401, detail="Missing bearer token")
    try:
        auth_payload = decode_access_token(token)
        user_id = str(auth_payload.get("sub") or "")
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid bearer token")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token subject")

    question = (question or "").strip()
    if not question:
        if image is None:
            raise HTTPException(status_code=400, detail="Type a follow-up question about the image.")
        question = "Describe this medical image."

    cfg = _load_cfg()
    enforce_qa_user_message_length(question, cfg)
    deid_result = _deid_v1(question)
    deid_question = deid_result["text"]

    state_key = (user_id, session_id)
    state = get_qa_session_state(user_id, session_id)
    prior_turns = list(state.get("turns") or [])
    vision_prior = [
        turn
        for turn in prior_turns
        if (turn.get("channel") or "text").lower() == "vision"
    ]

    allowed_types = {
        "image/jpeg",
        "image/jpg",
        "image/png",
        "image/webp",
        "image/bmp",
        "image/gif",
    }
    max_size = 20 * 1024 * 1024
    raw_upload = await image.read() if image is not None else None

    if raw_upload:
        mime_used = (image.content_type or "image/jpeg").strip()
        if mime_used not in allowed_types:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported image type: {mime_used}. Allowed: {', '.join(sorted(allowed_types))}",
            )
        if len(raw_upload) > max_size:
            raise HTTPException(
                status_code=400,
                detail=f"Image too large ({len(raw_upload)} bytes). Maximum is {max_size} bytes.",
            )
        image_data = raw_upload
        set_qa_vision_session_image(user_id, session_id, image_data, mime_used)

        if vision_prior:
            text_turns = [
                turn
                for turn in prior_turns
                if (turn.get("channel") or "text").lower() != "vision"
            ]
            state = {"summary": "", "turns": text_turns[-12:]}
            _QA_STATE[state_key] = state
            prior_turns = text_turns
            vision_prior = []
    else:
        cached_image = get_qa_vision_session_image(user_id, session_id)
        if not cached_image:
            raise HTTPException(
                status_code=400,
                detail="No image: upload an image for the first question, or use the same session for follow-ups.",
            )
        if not vision_prior:
            raise HTTPException(
                status_code=400,
                detail="No prior vision exchange in this session; upload an image first.",
            )
        image_data, mime_used = cached_image

    async def generate() -> AsyncIterator[str]:
        evidence = get_qa_vision_session_evidence(user_id, session_id)
        if evidence is None:
            try:
                evidence = await VisionQAEngine(url="").extract_visual_evidence(
                    image_bytes=image_data,
                    mime_type=mime_used,
                    question=deid_question,
                )
                set_qa_vision_session_evidence(user_id, session_id, evidence)
            except Exception as exc:
                yield f"[ERROR: Visual evidence extraction failed: {exc}]"
                return

        retrieval_query = build_vision_retrieval_query(deid_question, evidence)
        rag_ctx, rag_refs, web_items, weak_evidence = await retrieve_qa_evidence(
            retrieval_query,
            cfg,
        )
        prompt = build_vision_reasoning_prompt(
            deid_question,
            evidence,
            state,
            rag_ctx,
            web_items,
            weak_evidence,
        )

        llm = get_simple_note_generator("qa_text")
        answer_parts: List[str] = []
        try:
            async for chunk in llm.stream_completion(
                prompt,
                temperature=float(cfg.get("qa_chat_temperature", 0.2)),
                max_tokens=int(cfg.get("qa_chat_max_tokens", 3072)),
                stop=[],
            ):
                answer_parts.append(chunk)
                yield chunk
        except Exception as exc:
            if not answer_parts:
                yield f"[ERROR: Clinical answer generation failed: {exc}]"
            return

        answer = _finalize_qa_answer("".join(answer_parts))
        if not answer:
            yield "[ERROR: Clinical answer generation returned no visible answer text]"
            return

        new_state = append_qa_turn_for_session(
            user_id,
            session_id,
            deid_question,
            answer,
            "vision",
        )
        summary_tokens, summary_chars = _summary_params(cfg)
        new_state["summary"] = await _update_summary(
            new_state,
            llm,
            summary_tokens,
            summary_chars,
        )
        _QA_STATE[state_key] = new_state

        meta = {
            "summary": new_state.get("summary", ""),
            "sources": _build_sources(rag_refs, web_items),
            "deid_counts": deid_result.get("redaction_counts") or {},
            "web_results_count": len(web_items),
            "rag_results_count": len(rag_refs),
            "used_knowledge_fallback": False,
            "evidence_max_year": _compute_evidence_max_year(rag_refs),
            "vision_pipeline": "qwen-evidence+deepseek-answer",
        }
        yield f"\n\n__QA_META__{json.dumps(meta, ensure_ascii=True)}"

    return StreamingResponse(
        generate(),
        media_type="text/plain",
        headers={
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-cache",
            "X-Vision-Pipeline": "qwen-evidence+deepseek-answer",
        },
    )
