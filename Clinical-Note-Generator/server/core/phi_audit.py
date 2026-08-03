# server/core/phi_audit.py
"""P1-7: append-only PHI access audit trail.

Call log_phi_access() at the point a PHI-bearing artifact (a note, an
encounter, or ASR audio) is generated, viewed, downloaded, or deleted.
Never wraps the caller's own commit -- if the caller's session is already
open mid-request, this just adds the row to the same unit of work so it
either lands with the real action or rolls back with it; callers that
need it committed independently should call session.commit() themselves
afterward.
"""
from __future__ import annotations

import logging
import uuid
from typing import Optional

from sqlmodel import Session

from server.models.phi_access_log import PhiAccessLog

logger = logging.getLogger("phi_audit")


def log_phi_access(
    session: Session,
    *,
    user_id: uuid.UUID,
    action: str,
    resource_type: str,
    resource_id: str,
    encounter_id: Optional[uuid.UUID] = None,
    detail: Optional[str] = None,
) -> None:
    """Record one PHI access event. Best-effort: a logging failure must
    never break the caller's actual request, so exceptions are caught and
    logged rather than propagated."""
    try:
        entry = PhiAccessLog(
            user_id=user_id,
            action=action[:32],
            resource_type=resource_type[:32],
            resource_id=str(resource_id)[:64],
            encounter_id=encounter_id,
            detail=(detail[:256] if detail else None),
        )
        session.add(entry)
    except Exception:
        logger.exception("Failed to record PHI access log entry (action=%s, resource=%s)", action, resource_type)
