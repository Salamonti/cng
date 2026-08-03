# server/models/asr_recording_segment.py
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Column, Text, UniqueConstraint
from sqlmodel import Field, SQLModel


class AsrRecordingSegment(SQLModel, table=True):
    __tablename__ = "asr_recording_segments"
    __table_args__ = (
        UniqueConstraint("user_id", "client_recording_id", name="uq_asr_segment_user_client_recording"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True, index=True)
    client_recording_id: str = Field(index=True)
    user_id: uuid.UUID = Field(foreign_key="user.id", index=True)
    encounter_id: uuid.UUID = Field(foreign_key="user_encounter.id", index=True)
    sequence_index: int = Field(default=1, index=True)

    status: str = Field(default="uploaded", index=True)
    transcription_status: str = Field(default="pending", index=True)

    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    uploaded_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    transcribed_at: Optional[datetime] = Field(default=None, nullable=True)
    started_at: Optional[datetime] = Field(default=None, nullable=True)
    stopped_at: Optional[datetime] = Field(default=None, nullable=True)
    expires_at: Optional[datetime] = Field(default=None, nullable=True, index=True)

    file_name: str
    mime_type: str
    file_size: int
    sha256: str = Field(index=True)
    duration_sec: Optional[float] = Field(default=None, nullable=True)
    server_file_key: str = Field(index=True, unique=True)

    recording_session_id: Optional[str] = Field(default=None, index=True, max_length=128)
    segment_role: str = Field(default="full_backup", index=True, max_length=32)
    chunk_index: Optional[int] = Field(default=None, index=True)
    window_start_sec: Optional[float] = Field(default=None, nullable=True)

    transcript_text: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    committed_text: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    refined_committed_text: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    transcript_json: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    merge_method: Optional[str] = Field(default=None, max_length=32)
    error: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
