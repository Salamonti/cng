# server/models/user_encounter.py
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Column, JSON
from sqlmodel import Field, SQLModel


class UserEncounter(SQLModel, table=True):
    """P5: per-user clinical encounter; holds full workspace-shaped state for that thread."""

    __tablename__ = "user_encounter"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True, index=True)
    user_id: uuid.UUID = Field(foreign_key="user.id", index=True)
    label: str = Field(default="Encounter", max_length=256)
    state_json: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    version: int = Field(default=1)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
