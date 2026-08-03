# server/models/refresh_token.py
from __future__ import annotations

import uuid
from datetime import datetime

from sqlmodel import Field, SQLModel


class RefreshToken(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True, index=True)
    user_id: uuid.UUID = Field(foreign_key="user.id")
    token_hash: str
    user_agent: str | None = None
    expires_at: datetime
    revoked: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)
