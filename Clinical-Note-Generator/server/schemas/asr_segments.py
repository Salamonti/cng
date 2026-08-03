# server/schemas/asr_segments.py
from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel


class AsrRecordingSegmentResponse(BaseModel):
    id: UUID
    client_recording_id: str
    user_id: UUID
    encounter_id: UUID
    sequence_index: int
    status: str
    transcription_status: str
    created_at: datetime
    uploaded_at: datetime
    transcribed_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    stopped_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    file_name: str
    mime_type: str
    file_size: int
    sha256: str
    duration_sec: Optional[float] = None
    server_file_key: str
    recording_session_id: Optional[str] = None
    segment_role: str = "full_backup"
    chunk_index: Optional[int] = None
    window_start_sec: Optional[float] = None
    transcript_text: Optional[str] = None
    committed_text: Optional[str] = None
    refined_committed_text: Optional[str] = None
    merge_method: Optional[str] = None
    error: Optional[str] = None

    class Config:
        from_attributes = True


class EncounterTranscriptResponse(BaseModel):
    encounter_id: UUID
    transcript_text: str
    segments: list[AsrRecordingSegmentResponse]
    whisper_transcript_text: Optional[str] = None
    refine_diarize: bool = False


class AsrPipelineResponse(BaseModel):
    encounter_id: UUID
    recording_session_id: Optional[str] = None
    state: str
    pending_chunks: int
    transcribed_chunks: int
    total_chunks: int
    merged_transcript_text: str
    can_generate_note: bool
    fallback_used: bool = False
    whisper_transcript_text: Optional[str] = None
    refine_diarize: bool = False
