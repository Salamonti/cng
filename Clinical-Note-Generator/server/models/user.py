# C:\Clinical-Note-Generator\server\models\user.py
from __future__ import annotations

import uuid
from datetime import datetime

from sqlmodel import Field, SQLModel


class User(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True, index=True)
    email: str = Field(index=True, unique=True)
    hashed_password: str
    is_active: bool = True
    is_admin: bool = False
    is_approved: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)
