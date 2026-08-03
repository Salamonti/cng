# server/routes/feedback.py — thumbs / suggestion logging (split from notes.py, P10b debt)
import asyncio
from datetime import datetime, timezone
import uuid
from typing import Dict

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlmodel import Session

from server.core.db import get_session
from server.core.deid.v1 import deidentify_text
from server.core.http_actor import extract_request_actor
from server.core.logging.dataset_logger import log_case_event
from server.core.stores.generation_store import _generation_cache, cache_lock

router = APIRouter()


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
        actor = extract_request_actor(request, session)

        prompt = output = None
        with cache_lock:
            entry = _generation_cache.get(gen_id)
            if entry:
                prompt = entry.get("prompt")
                output = entry.get("output")

        if prompt is None:
            prompt = payload.get("prompt")
        if output is None:
            output = payload.get("output")

        if not skip_rating_event:
            event_type = "thumbs_up" if rating == 2 else ("thumbs_down" if rating == 0 else "neutral")
            # P2-7: log_case_event() is a blocking file append (dataset_logger.py,
            # under threading.Lock) -- off-load it so this async handler doesn't
            # stall the event loop, same fix as app.py's http_logger middleware.
            await asyncio.to_thread(
                log_case_event,
                {
                    "event_id": uuid.uuid4().hex,
                    "case_id": gen_id,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "event_type": event_type,
                    "rating": rating,
                    "user_id": actor.get("user_id"),
                    "user_email": actor.get("user_email"),
                    "has_cached_generation": bool(prompt) and output is not None,
                },
            )

        if suggestion_raw:
            suggestion_deid = deidentify_text(suggestion_raw)
            await asyncio.to_thread(
                log_case_event,
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
                },
            )
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)[:160]})
