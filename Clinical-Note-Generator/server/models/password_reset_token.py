# server/models/password_reset_token.py
from __future__ import annotations

import uuid
from datetime import datetime

from sqlmodel import Field, SQLModel


class PasswordResetToken(SQLModel, table=True):
    """Persisted so a reset link survives an app restart (WO P1-1) --
    previously an in-memory dict that dropped every pending reset token
    on every deploy or crash."""

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True, index=True)
    user_id: uuid.UUID = Field(foreign_key="user.id")
    token_hash: str = Field(index=True)
    used: bool = False
    expires_at: datetime
    created_at: datetime = Field(default_factory=datetime.utcnow)
