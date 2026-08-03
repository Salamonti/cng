# server/models/phi_access_log.py
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class PhiAccessLog(SQLModel, table=True):
    """Append-only record of who generated, viewed, downloaded, or deleted
    a PHI-bearing artifact (a note, an encounter, or ASR audio). P1-7:
    there was previously no audit trail of PHI access at all.

    Application code only ever inserts rows here (see
    server/core/phi_audit.py's log_phi_access()) -- nothing in this
    codebase updates or deletes a PhiAccessLog row.
    """

    __tablename__ = "phi_access_log"

    # No foreign_key constraints on user_id/encounter_id: an audit log must
    # outlive whatever it describes (a user removed via the admin panel, an
    # encounter TTL- or manually-deleted) -- these stay indexed opaque
    # identifiers rather than references.
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True, index=True)
    user_id: uuid.UUID = Field(index=True)
    action: str = Field(index=True, max_length=32)
    resource_type: str = Field(index=True, max_length=32)
    resource_id: str = Field(index=True, max_length=64)
    encounter_id: Optional[uuid.UUID] = Field(default=None, index=True)
    detail: Optional[str] = Field(default=None, max_length=256)
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
