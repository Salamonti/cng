"""Resolve authenticated user id/email from a Bearer token + DB (shared by notes, feedback, etc.)."""
from __future__ import annotations

import logging
import uuid
from typing import Dict, Optional

from fastapi import Request
from sqlmodel import Session, select

from server.core.security import decode_access_token
from server.models.user import User

logger = logging.getLogger("cng.http_actor")


def extract_request_actor(request: Request, session: Session) -> Dict[str, Optional[str]]:
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
        # Legitimate: an invalid/expired token simply means "anonymous" to the
        # caller (audit columns stay null). Log at debug so a misbehaving client
        # sending garbage tokens is diagnosable without spamming on routine
        # token expiry.
        logger.debug("Bearer token failed to decode; treating request as anonymous")
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
    except Exception as exc:
        # Unsafe to swallow silently: the user_id is set, so lookup failure is a
        # real DB/parse problem (not a normal "no such user" case) that silently
        # drops the audit email. Surface it.
        logger.warning("Failed to resolve user for actor (user_id=%s): %r", user_id, exc)
    return actor
