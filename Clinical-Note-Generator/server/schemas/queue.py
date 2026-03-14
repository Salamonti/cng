# server/schemas/queue.py
from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel


class QueuedJobCreate(BaseModel):
    type: str  # "ocr", "transcribe", "asr"
    # file is uploaded separately, not part of JSON


class QueuedJobResponse(BaseModel):
    id: UUID
    user_id: UUID
    type: str
    status: str
    created_at: datetime
    processed_at: Optional[datetime] = None
    file_name: str
    mime_type: str
    file_size: int
    server_file_key: str
    error: Optional[str] = None

    class Config:
        from_attributes = True  # For SQLModel compatibility (Pydantic v2)
