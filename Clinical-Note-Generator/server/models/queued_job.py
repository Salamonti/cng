# server/models/queued_job.py
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Column, Text
from sqlmodel import Field, SQLModel


class QueuedJob(SQLModel, table=True):
    __tablename__ = "queued_jobs"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True, index=True)
    user_id: uuid.UUID = Field(foreign_key="user.id", index=True)
    type: str = Field(index=True)  # "ocr", "transcribe", "asr", etc.
    status: str = Field(default="pending", index=True)  # pending, processing, failed, done
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    processed_at: Optional[datetime] = Field(default=None, nullable=True)
    file_name: str
    mime_type: str
    file_size: int  # bytes
    server_file_key: str = Field(index=True, unique=True)  # path relative to queue storage root
    error: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
