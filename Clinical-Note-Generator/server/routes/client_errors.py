# server/routes/client_errors.py
"""P2-8: client-side error telemetry.

Browser JS errors and unhandled promise rejections are invisible to
server-side logs/alerting -- a doctor hitting a broken note-gen button
today leaves no trace anywhere we'd notice. Reuses the existing ASR
incident store (server/core/asr_incident_store.py) rather than a second
telemetry mechanism, same "reuse what's tested and working" approach as
P2-3's alerting and P2-4's health checks.
"""
import asyncio
import time
from collections import OrderedDict
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from server.core.asr_incident_store import record_asr_incident
from server.core.http_actor import extract_request_actor

router = APIRouter()

MAX_MESSAGE_CHARS = 2000
MAX_STACK_CHARS = 4000
MAX_URL_CHARS = 500
MAX_KIND_CHARS = 32
MAX_EVENTS_PER_REQUEST = 5

# This route is intentionally NOT behind require_api_bearer (see the router
# registration in app.py) -- an expired/broken auth session is exactly one
# of the things worth capturing telemetry for. That openness is why it needs
# its own abuse guard: a per-IP sliding-window cap, independent of and in
# addition to the per-request MAX_EVENTS_PER_REQUEST above. _RATE_LIMIT_MAX_KEYS
# bounds tracked-IP memory the same way P1-6's _BoundedSessionStore bounds
# QA session memory -- an LRU-style cap, not just a TTL, since a flood of
# distinct source IPs could otherwise grow this dict unbounded too.
_RATE_LIMIT_MAX_EVENTS = 20
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


def _clip(value: Any, limit: int) -> str:
    return str(value or "")[:limit]


def _sanitize_event(raw: Any) -> Optional[Dict[str, str]]:
    if not isinstance(raw, dict):
        return None
    message = _clip(raw.get("message"), MAX_MESSAGE_CHARS)
    if not message:
        return None
    return {
        "kind": _clip(raw.get("kind") or "error", MAX_KIND_CHARS),
        "message": message,
        "stack": _clip(raw.get("stack"), MAX_STACK_CHARS),
        "url": _clip(raw.get("url"), MAX_URL_CHARS),
    }


@router.post("/client_errors")
async def record_client_errors(payload: Dict, request: Request) -> JSONResponse:
    client_ip = request.client.host if request.client else "unknown"
    if _rate_limited(client_ip):
        # Silent 200: a client retry loop reacting to a 429 here would just
        # be one more error to report. Nothing gets written past the cap.
        return JSONResponse(content={"ok": True, "recorded": 0})

    # Best-effort actor lookup -- an error report is still worth keeping even
    # from a session whose auth already lapsed (e.g. token expired mid-tab).
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

    user_agent = _clip(request.headers.get("user-agent"), 300)
    for event in events:
        await asyncio.to_thread(
            record_asr_incident,
            trace_id=f"client-{user_id}",
            stage="client_error",
            outcome=event["kind"],
            payload={
                "message": event["message"],
                "stack": event["stack"],
                "url": event["url"],
                "user_agent": user_agent,
            },
        )

    return JSONResponse(content={"ok": True, "recorded": len(events)})
