"""Resolve authenticated user id/email from a Bearer token + DB (shared by notes, feedback, etc.)."""
from __future__ import annotations

import uuid
from typing import Dict, Optional

from fastapi import Request
from sqlmodel import Session, select

from server.core.security import decode_access_token
from server.models.user import User


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
