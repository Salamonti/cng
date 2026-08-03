# server/routes/client_usage.py
"""P4-4: pilot instrumentation -- feature usage + note edit-distance telemetry.

Two signals the pilot needs that nothing captures today:
1. Feature usage -- which parts of the app doctors actually touch (Tools
   sheet items, patient-material categories, bottom-nav targets). The
   existing metrics.py / dataset_logger only see note-generation events,
   not client-only interactions.
2. Note edit distance -- how much a doctor edits the generated note
   before moving on, a proxy for note quality. Computed client-side
   (js/client_usage_reporter.js); only a 0-1 ratio reaches this route,
   never the note text itself.

Shape mirrors server/routes/client_errors.py closely (open registration,
no require_api_bearer, per-IP rate limit, batched-events envelope) for
the same reason: telemetry needs to get through even from a session whose
auth just lapsed. It differs in destination and in what it accepts --
this is pilot analysis data, not incidents, so it goes to the existing
de-identified dataset_logger.log_case_event() JSONL stream (the same
store feedback.py already writes rating/suggestion events into) rather
than the ASR incident store. And because free-text here would be an easy
way to smuggle arbitrary (possibly PHI-bearing) strings through an
unauthenticated route, "kind" and "meta" are both closed vocabularies,
not clipped free text like client_errors' message field.
"""
import asyncio
import time
import uuid
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from server.core.http_actor import extract_request_actor
from server.core.logging.dataset_logger import log_case_event

router = APIRouter()

MAX_EVENTS_PER_REQUEST = 10
MAX_CASE_ID_CHARS = 64

ALLOWED_KINDS = {
    "nav_chart",
    "nav_tools",
    "nav_note",
    "nav_qa",
    "nav_encounters",
    "tools_camera",
    "tools_files",
    "tools_ai_prompts",
    "tools_settings",
    "tools_clear_queue",
    "tools_encounters",
    "tools_literature",
    "patient_materials_generate",
    "note_edit_distance",
}
ALLOWED_META = {
    "diagnosis",
    "medications",
    "issues_plan",
    "diet",
    "exercise",
    "full_report",
}

_RATE_LIMIT_MAX_EVENTS = 60
_RATE_LIMIT_WINDOW_SEC = 300.0
_RATE_LIMIT_MAX_KEYS = 2000
_rate_limit_hits: "OrderedDict[str, List[float]]" = OrderedDict()


def _rate_limited(key: str) -> bool:
    now = time.time()
    hits = _rate_limit_hits.get(key)
    if hits is None:
        if len(_rate_limit_hits) >= _RATE_LIMIT_MAX_KEYS:
            _rate_limit_hits.popitem(last=False)
    else:
        _rate_limit_hits.move_to_end(key)
    recent = [t for t in (hits or []) if now - t < _RATE_LIMIT_WINDOW_SEC]
    recent.append(now)
    _rate_limit_hits[key] = recent
    return len(recent) > _RATE_LIMIT_MAX_EVENTS


def _sanitize_event(raw: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    kind = str(raw.get("kind") or "")
    if kind not in ALLOWED_KINDS:
        return None
    event: Dict[str, Any] = {"kind": kind}

    value = raw.get("value")
    if value is not None:
        try:
            event["value"] = max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            pass

    meta = raw.get("meta")
    if isinstance(meta, str) and meta in ALLOWED_META:
        event["meta"] = meta

    case_id = str(raw.get("case_id") or "").strip()
    if case_id:
        event["case_id"] = case_id[:MAX_CASE_ID_CHARS]

    return event


@router.post("/client_usage")
async def record_client_usage(payload: Dict, request: Request) -> JSONResponse:
    client_ip = request.client.host if request.client else "unknown"
    if _rate_limited(client_ip):
        # Silent 200, same reasoning as client_errors: nothing useful comes
        # from surfacing this to the client as an error.
        return JSONResponse(content={"ok": True, "recorded": 0})

    try:
        actor = extract_request_actor(request, None)
    except Exception:
        actor = {}
    user_id = str(actor.get("user_id") or "anonymous")

    raw_events = payload.get("events")
    if not isinstance(raw_events, list):
        raw_events = [payload]
    events = [e for e in (_sanitize_event(r) for r in raw_events[:MAX_EVENTS_PER_REQUEST]) if e]
    if not events:
        return JSONResponse(status_code=400, content={"ok": False, "error": "no valid events"})

    now_iso = datetime.now(timezone.utc).isoformat()
    for event in events:
        await asyncio.to_thread(
            log_case_event,
            {
                "event_id": uuid.uuid4().hex,
                "case_id": event.get("case_id", ""),
                "created_at": now_iso,
                "event_type": f"usage.{event['kind']}",
                "value": event.get("value"),
                "meta": event.get("meta"),
                "user_id": user_id,
            },
        )

    return JSONResponse(content={"ok": True, "recorded": len(events)})
