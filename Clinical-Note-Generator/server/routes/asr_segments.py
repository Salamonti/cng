# server/routes/asr_segments.py
from __future__ import annotations

import asyncio
import hashlib
import os
import re
import tempfile
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, Response
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from server.core.asr_audio import _concat_webm_files_ffmpeg, _transcribe_audio_bytes_sync
from server.core.asr_chunk import (
    claim_next_pending_chunk,
    merged_transcript_for_encounter,
    ordered_chunk_segments,
    assert_chunk_ready_for_transcribe,
    transcribe_chunk_segment,
)
from server.core.chunk_merge import COMMIT_STRIDE_SEC, OVERLAP_SEC
from server.core.db import get_session
from server.core.dependencies import get_current_user
from server.core.phi_audit import log_phi_access
from server.core.feature_flags import CHUNK_ASR_ENABLED, ASR_REFINE_ENABLED
from server.core.asr_refine import apply_asr_refine
from server.models.asr_recording_segment import AsrRecordingSegment
from server.models.user import User
from server.models.user_encounter import UserEncounter
from server.schemas.asr_segments import (
    AsrPipelineResponse,
    AsrRecordingSegmentResponse,
    EncounterTranscriptResponse,
)


router = APIRouter(prefix="/api/asr/segments", tags=["asr-segments"], redirect_slashes=False)

MIN_AUDIO_BYTES = 1000
MAX_AUDIO_BYTES = 100 * 1024 * 1024
MAX_DURATION_SEC = 30 * 60
RETENTION_DAYS = 7

# P1-6: MAX_AUDIO_BYTES only bounds a single segment. Nothing previously
# bounded how much a single user could accumulate across many uploads
# within the RETENTION_DAYS window before auto-delete catches up -- a
# scripted or runaway client could upload segments continuously and fill
# the disk. Generous for legitimate use: a full clinic day of chunked
# recording at typical webm/opus bitrates is well under this.
MAX_USER_STORAGE_BYTES = int(
    os.environ.get("ASR_MAX_USER_STORAGE_BYTES", str(5 * 1024 * 1024 * 1024))
)  # 5 GB


def _user_total_stored_bytes(session: Session, user_id: uuid.UUID) -> int:
    total = session.exec(
        select(func.sum(AsrRecordingSegment.file_size)).where(
            AsrRecordingSegment.user_id == user_id,
            AsrRecordingSegment.expires_at > datetime.utcnow(),
        )
    ).one()
    return int(total or 0)


def get_asr_segment_storage_root() -> Path:
    data_dir = Path(__file__).resolve().parents[2] / "data"
    root = data_dir / "asr_recording_segments"
    root.mkdir(parents=True, exist_ok=True)
    return root


def get_asr_segment_user_dir(user_id: uuid.UUID) -> Path:
    user_dir = get_asr_segment_storage_root() / str(user_id)
    user_dir.mkdir(parents=True, exist_ok=True)
    return user_dir


def delete_asr_segment_file(server_file_key: str) -> None:
    if not server_file_key:
        return
    p = get_asr_segment_storage_root() / server_file_key
    try:
        if p.exists():
            p.unlink()
    except OSError:
        pass


def _parse_client_dt(raw: Optional[str]) -> Optional[datetime]:
    s = (raw or "").strip()
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is not None:
            dt = dt.astimezone().replace(tzinfo=None)
        return dt
    except ValueError:
        return None


def _ordered_segments(
    session: Session,
    *,
    user_id: uuid.UUID,
    encounter_id: uuid.UUID,
) -> list[AsrRecordingSegment]:
    return list(
        session.exec(
            select(AsrRecordingSegment)
            .where(
                AsrRecordingSegment.user_id == user_id,
                AsrRecordingSegment.encounter_id == encounter_id,
            )
            .order_by(AsrRecordingSegment.sequence_index.asc(), AsrRecordingSegment.created_at.asc())
        ).all()
    )


def _assert_owned_encounter(session: Session, encounter_id: uuid.UUID, user_id: uuid.UUID) -> UserEncounter:
    enc = session.get(UserEncounter, encounter_id)
    if not enc or enc.user_id != user_id:
        raise HTTPException(status_code=404, detail="Encounter not found")
    return enc


def _chunk_session_ids_for_encounter(segments: list[AsrRecordingSegment]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for seg in segments:
        if seg.segment_role not in ("chunk", "partial_tail"):
            continue
        sess = (seg.recording_session_id or "").strip()[:128]
        if not sess or sess in seen:
            continue
        seen.add(sess)
        ordered.append(sess)
    return ordered


def _full_encounter_transcript_text(
    session: Session,
    *,
    user_id: uuid.UUID,
    encounter_id: uuid.UUID,
) -> str:
    segments = _ordered_segments(session, user_id=user_id, encounter_id=encounter_id)
    parts: list[str] = []
    emitted_sessions: set[str] = set()
    for seg in segments:
        role = str(seg.segment_role or "")
        if role in ("chunk", "partial_tail"):
            sess = (seg.recording_session_id or "").strip()[:128]
            if not sess or sess in emitted_sessions:
                continue
            emitted_sessions.add(sess)
            session_text = merged_transcript_for_encounter(
                session,
                user_id=user_id,
                encounter_id=encounter_id,
                recording_session_id=sess,
            ).strip()
            if session_text:
                parts.append(session_text)
            continue
        text = (seg.committed_text or seg.transcript_text or "").strip()
        if text:
            parts.append(text)
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return "\n\n".join(parts)


def _looks_diarized(text: str) -> bool:
    return bool(re.search(r"^(Doctor|Patient|Unknown):", text or "", re.MULTILINE))


def _whisper_encounter_transcript_text(
    session: Session,
    *,
    user_id: uuid.UUID,
    encounter_id: uuid.UUID,
) -> str:
    segments = _ordered_segments(session, user_id=user_id, encounter_id=encounter_id)
    parts: list[str] = []
    emitted_sessions: set[str] = set()
    for seg in segments:
        role = str(seg.segment_role or "")
        if role in ("chunk", "partial_tail"):
            sess = (seg.recording_session_id or "").strip()[:128]
            if not sess or sess in emitted_sessions:
                continue
            emitted_sessions.add(sess)
            session_text = merged_transcript_for_encounter(
                session,
                user_id=user_id,
                encounter_id=encounter_id,
                recording_session_id=sess,
                source="whisper",
            ).strip()
            if session_text:
                parts.append(session_text)
            continue
        text = (seg.transcript_text or "").strip()
        if text:
            parts.append(text)
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return "\n\n".join(parts)


def _apply_refine_to_pipeline(
    pipeline: AsrPipelineResponse,
    *,
    diarize: bool,
    trace_id: str,
) -> AsrPipelineResponse:
    if not ASR_REFINE_ENABLED or not diarize:
        return pipeline
    whisper = (pipeline.merged_transcript_text or "").strip()
    if not whisper:
        return pipeline

    # Cache check: if the merged text is already diarized (segments already have
    # refined_committed_text), skip the LLM call — it would be a duplicate.
    if _looks_diarized(whisper):
        return pipeline.model_copy(
            update={
                "whisper_transcript_text": pipeline.whisper_transcript_text or whisper,
                "refine_diarize": True,
            }
        )

    raw, refined, refined_ok = apply_asr_refine(whisper, diarize=True, trace_id=trace_id)
    return pipeline.model_copy(
        update={
            "merged_transcript_text": refined,
            "whisper_transcript_text": raw,
            # Report the real outcome -- apply_asr_refine falls back to raw
            # text on any failure/empty-response/severe-truncation, and that
            # must not be reported to the client as a successful diarization.
            "refine_diarize": refined_ok,
        }
    )


def _apply_refine_to_encounter_transcript(
    session: Session,
    *,
    encounter_id: uuid.UUID,
    whisper_text: str,
    segments: list[AsrRecordingSegment],
    diarize: bool,
    trace_id: str,
) -> EncounterTranscriptResponse:
    whisper = (whisper_text or "").strip()
    if not ASR_REFINE_ENABLED or not diarize or not whisper:
        return EncounterTranscriptResponse(
            encounter_id=encounter_id,
            transcript_text=whisper,
            segments=segments,
            whisper_transcript_text=whisper or None,
            refine_diarize=diarize,
        )
    # No per-segment/per-session cache: the diarized text returned here covers
    # the whole requested scope (encounter or a single session), not any one
    # segment's own contribution. Persisting it onto a segment's
    # refined_committed_text previously corrupted later merges, because
    # segment_display_text()/merged_transcript_for_encounter() read that field
    # per-segment and concatenate it with sibling segments' own text — and,
    # separately, using segments[0] to infer "the current session" broke as
    # soon as an encounter had more than one recording session, since segments
    # here always spans the whole encounter. Always recompute fresh instead;
    # diarize=true is an explicit, non-polled request, not a hot path.
    raw, refined, refined_ok = apply_asr_refine(whisper, diarize=True, trace_id=trace_id)
    return EncounterTranscriptResponse(
        encounter_id=encounter_id,
        transcript_text=refined,
        segments=segments,
        whisper_transcript_text=raw,
        # Report the real outcome -- apply_asr_refine falls back to raw text
        # on any failure/empty-response/severe-truncation, and that must not
        # be reported to the client as a successful diarization.
        refine_diarize=refined_ok,
    )


def _retranscribe_one_chunk_session(
    session: Session,
    *,
    user_id: uuid.UUID,
    encounter_id: uuid.UUID,
    recording_session_id: str,
    storage_root: Path,
    trace_id: str,
    diarize: bool = False,
) -> int:
    """Re-transcribe each chunk in order (Whisper + optional LLM per chunk)."""
    chunks = ordered_chunk_segments(
        session,
        user_id=user_id,
        encounter_id=encounter_id,
        recording_session_id=recording_session_id,
    )
    if not chunks:
        return 0

    updated = 0
    for seg in chunks:
        path = storage_root / seg.server_file_key
        if not path.exists():
            seg.transcription_status = "failed"
            seg.error = "Stored recording segment file not found"
            session.add(seg)
            session.commit()
            continue
        try:
            transcribe_chunk_segment(
                session,
                seg,
                storage_root,
                diarize=diarize,
                trace_id=trace_id,
            )
            updated += 1
        except Exception as exc:
            seg.transcription_status = "failed"
            seg.error = str(exc)[:1000]
            session.add(seg)
            session.commit()
    return updated


def _retranscribe_batch_segment(
    session: Session,
    segment: AsrRecordingSegment,
    storage_root: Path,
    *,
    trace_id: str,
    diarize: bool = False,  # Deprecated — global diarization happens at the encounter level
) -> bool:
    path = storage_root / segment.server_file_key
    if not path.exists():
        segment.transcription_status = "failed"
        segment.error = "Stored recording segment file not found"
        session.add(segment)
        session.commit()
        return False

    segment.transcription_status = "transcribing"
    segment.error = None
    session.add(segment)
    session.commit()

    try:
        text = _transcribe_audio_bytes_sync(
            file_data=path.read_bytes(),
            filename=segment.file_name,
            content_type=segment.mime_type,
            trace_id=trace_id,
            t0=time.perf_counter(),
        )
        whisper = text.strip()
        # Store raw Whisper only — global diarization applied at encounter level
        segment.transcript_text = whisper
        segment.committed_text = whisper
        segment.refined_committed_text = None
        segment.transcription_status = "transcribed"
        segment.transcribed_at = datetime.utcnow()
        segment.error = None
        session.add(segment)
        session.commit()
        return bool(whisper)
    except Exception as exc:
        segment.transcription_status = "failed"
        segment.error = str(exc)[:1000]
        session.add(segment)
        session.commit()
        return False


def _window_start_for_chunk(chunk_index: int) -> float:
    if chunk_index <= 0:
        return 0.0
    return max(0.0, chunk_index * COMMIT_STRIDE_SEC - OVERLAP_SEC)


def _compute_pipeline_state(
    session: Session,
    *,
    user_id: uuid.UUID,
    encounter_id: uuid.UUID,
    recording_session_id: Optional[str] = None,
) -> AsrPipelineResponse:
    chunks = ordered_chunk_segments(
        session,
        user_id=user_id,
        encounter_id=encounter_id,
        recording_session_id=recording_session_id,
    )
    pending = sum(1 for c in chunks if c.transcription_status in ("pending", "transcribing", "failed"))
    transcribed = sum(1 for c in chunks if c.transcription_status == "transcribed")
    merged = merged_transcript_for_encounter(
        session,
        user_id=user_id,
        encounter_id=encounter_id,
        recording_session_id=recording_session_id,
    )
    if not chunks:
        state = "idle"
        can_generate = True
    elif pending > 0:
        state = "processing"
        can_generate = False
    elif merged.strip():
        state = "ready"
        can_generate = True
    else:
        state = "failed"
        can_generate = False

    return AsrPipelineResponse(
        encounter_id=encounter_id,
        recording_session_id=recording_session_id,
        state=state,
        pending_chunks=pending,
        transcribed_chunks=transcribed,
        total_chunks=len(chunks),
        merged_transcript_text=merged,
        can_generate_note=can_generate,
        fallback_used=False,
    )


def _fallback_whole_file_transcribe(
    session: Session,
    *,
    user_id: uuid.UUID,
    encounter_id: uuid.UUID,
    recording_session_id: Optional[str],
    trace_id: str,
) -> str:
    q = select(AsrRecordingSegment).where(
        AsrRecordingSegment.user_id == user_id,
        AsrRecordingSegment.encounter_id == encounter_id,
        AsrRecordingSegment.segment_role == "full_backup",
    )
    if recording_session_id:
        q = q.where(AsrRecordingSegment.recording_session_id == recording_session_id)
    q = q.order_by(AsrRecordingSegment.created_at.desc())
    backup = session.exec(q).first()
    if not backup:
        segments = _ordered_segments(session, user_id=user_id, encounter_id=encounter_id)
        if not segments:
            raise HTTPException(status_code=404, detail="No audio available for fallback transcribe")
        storage_root = get_asr_segment_storage_root()
        paths = [storage_root / s.server_file_key for s in segments if (storage_root / s.server_file_key).exists()]
        with tempfile.TemporaryDirectory(prefix="asr_fallback_") as td:
            out_path = Path(td) / "fallback.webm"
            _concat_webm_files_ffmpeg(paths, out_path)
            text = _transcribe_audio_bytes_sync(
                file_data=out_path.read_bytes(),
                filename=out_path.name,
                content_type="audio/webm",
                trace_id=trace_id,
                t0=time.perf_counter(),
            )
        return text.strip()

    path = get_asr_segment_storage_root() / backup.server_file_key
    if not path.exists():
        raise HTTPException(status_code=404, detail="Fallback recording file not found")
    text = _transcribe_audio_bytes_sync(
        file_data=path.read_bytes(),
        filename=backup.file_name,
        content_type=backup.mime_type,
        trace_id=trace_id,
        t0=time.perf_counter(),
    )
    backup.transcript_text = text.strip()
    backup.committed_text = text.strip()
    backup.transcription_status = "transcribed"
    backup.transcribed_at = datetime.utcnow()
    backup.merge_method = "fallback_whole"
    backup.error = None
    session.add(backup)
    session.commit()
    return text.strip()


@router.post("", response_model=AsrRecordingSegmentResponse)
async def upload_asr_recording_segment(
    request: Request,
    file: UploadFile = File(...),
    encounter_id: uuid.UUID = Form(...),
    client_recording_id: str = Form(...),
    expected_file_name: Optional[str] = Form(None),
    expected_size_bytes: Optional[int] = Form(None),
    expected_sha256: Optional[str] = Form(None),
    duration_sec: Optional[float] = Form(None),
    started_at: Optional[str] = Form(None),
    stopped_at: Optional[str] = Form(None),
    recording_session_id: Optional[str] = Form(None),
    segment_role: Optional[str] = Form(None),
    chunk_index: Optional[int] = Form(None),
    window_start_sec: Optional[float] = Form(None),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> AsrRecordingSegment:
    """Upload one immutable audio segment (chunk, partial tail, or full backup).

    This is the highest-frequency endpoint in the whole ASR surface -- hit
    roughly every 25s by every actively-recording doctor. Only the multipart
    body read is genuinely async here (UploadFile.read()); the rest
    (ownership check, hashing, DB queries/commit, file write) is blocking
    sync work that was previously running directly on the event loop thread,
    where it could stall every other concurrent request for its duration.
    Offload it to a worker thread via asyncio.to_thread once the body is in
    hand.
    """
    file_name = (
        file.filename or expected_file_name or f"{(client_recording_id or '').strip()[:128]}.webm"
    ).strip()
    if expected_file_name and file_name != expected_file_name:
        raise HTTPException(status_code=409, detail="Uploaded file name does not match expected_file_name")

    content = await file.read()

    return await asyncio.to_thread(
        _store_asr_recording_segment,
        session,
        current_user,
        encounter_id,
        content,
        file_name,
        file.content_type,
        client_recording_id,
        expected_size_bytes,
        expected_sha256,
        duration_sec,
        started_at,
        stopped_at,
        recording_session_id,
        segment_role,
        chunk_index,
        window_start_sec,
    )


def _store_asr_recording_segment(
    session: Session,
    current_user: User,
    encounter_id: uuid.UUID,
    content: bytes,
    file_name: str,
    content_type: Optional[str],
    client_recording_id: str,
    expected_size_bytes: Optional[int],
    expected_sha256: Optional[str],
    duration_sec: Optional[float],
    started_at: Optional[str],
    stopped_at: Optional[str],
    recording_session_id: Optional[str],
    segment_role: Optional[str],
    chunk_index: Optional[int],
    window_start_sec: Optional[float],
) -> AsrRecordingSegment:
    """Synchronous body of upload_asr_recording_segment; runs in a worker thread."""
    _assert_owned_encounter(session, encounter_id, current_user.id)

    client_id = (client_recording_id or "").strip()[:128]
    if not client_id:
        raise HTTPException(status_code=400, detail="client_recording_id is required")

    role = (segment_role or "full_backup").strip().lower()[:32]
    if role not in ("chunk", "partial_tail", "full_backup"):
        role = "full_backup"

    size = len(content or b"")
    if size < MIN_AUDIO_BYTES:
        raise HTTPException(status_code=400, detail="File too small to contain audio")
    if size > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="File too large")
    if expected_size_bytes is not None and int(expected_size_bytes) != size:
        raise HTTPException(status_code=409, detail="Uploaded file size does not match expected_size_bytes")

    sha256 = hashlib.sha256(content).hexdigest()
    expected_hash = (expected_sha256 or "").strip().lower()
    if expected_hash:
        expected_hash = expected_hash.removeprefix("sha256:")
        if expected_hash != sha256:
            raise HTTPException(status_code=409, detail="Uploaded file hash does not match expected_sha256")

    if duration_sec is not None and float(duration_sec) > MAX_DURATION_SEC + 5:
        raise HTTPException(status_code=413, detail="Recording exceeds 30 minute limit")

    existing = session.exec(
        select(AsrRecordingSegment).where(
            AsrRecordingSegment.user_id == current_user.id,
            AsrRecordingSegment.client_recording_id == client_id,
        )
    ).one_or_none()
    if existing:
        if existing.encounter_id != encounter_id or existing.file_size != size or existing.sha256 != sha256:
            raise HTTPException(status_code=409, detail="client_recording_id already exists with different audio")
        return existing

    total_stored = _user_total_stored_bytes(session, current_user.id)
    if total_stored + size > MAX_USER_STORAGE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Storage quota exceeded: {total_stored / (1024*1024):.0f} MB stored, "
                f"limit is {MAX_USER_STORAGE_BYTES // (1024*1024)} MB. "
                "Older recordings will free space automatically as they expire."
            ),
        )

    current_max = session.exec(
        select(func.max(AsrRecordingSegment.sequence_index)).where(
            AsrRecordingSegment.user_id == current_user.id,
            AsrRecordingSegment.encounter_id == encounter_id,
        )
    ).one()
    sequence_index = int(current_max or 0) + 1

    sess_id = (recording_session_id or "").strip()[:128] or None
    cidx = int(chunk_index) if chunk_index is not None else None
    wstart = float(window_start_sec) if window_start_sec is not None else None
    if role in ("chunk", "partial_tail") and cidx is not None and wstart is None:
        wstart = _window_start_for_chunk(cidx)

    segment_id = uuid.uuid4()
    ext = Path(file_name).suffix.lower() or ".webm"
    user_dir = get_asr_segment_user_dir(current_user.id)
    stored_name = f"{segment_id}{ext}"
    dest = user_dir / stored_name
    dest.write_bytes(content)
    server_file_key = str(Path(str(current_user.id)) / stored_name)

    now = datetime.utcnow()
    segment = AsrRecordingSegment(
        id=segment_id,
        client_recording_id=client_id,
        user_id=current_user.id,
        encounter_id=encounter_id,
        sequence_index=sequence_index,
        status="uploaded",
        transcription_status="pending",
        created_at=now,
        uploaded_at=now,
        started_at=_parse_client_dt(started_at),
        stopped_at=_parse_client_dt(stopped_at),
        expires_at=now + timedelta(days=RETENTION_DAYS),
        file_name=file_name,
        mime_type=content_type or "application/octet-stream",
        file_size=size,
        sha256=sha256,
        duration_sec=float(duration_sec) if duration_sec is not None else None,
        server_file_key=server_file_key,
        recording_session_id=sess_id,
        segment_role=role,
        chunk_index=cidx,
        window_start_sec=wstart,
    )
    session.add(segment)
    try:
        session.commit()
    except IntegrityError:
        # Two near-simultaneous uploads for the same client_recording_id (a
        # client retry firing before the first response arrives) can both
        # miss each other's uncommitted insert in the `existing` check above
        # and both write a file + attempt an insert. The unique constraint
        # (user_id, client_recording_id) lets exactly one commit succeed;
        # the loser used to surface this as a bare 500 and leak the file it
        # had already written to disk. Recover idempotently instead: roll
        # back, clean up our now-orphaned file, and return the winner's row
        # the same way the pre-check above does.
        session.rollback()
        try:
            dest.unlink(missing_ok=True)
        except OSError:
            pass
        winner = session.exec(
            select(AsrRecordingSegment).where(
                AsrRecordingSegment.user_id == current_user.id,
                AsrRecordingSegment.client_recording_id == client_id,
            )
        ).one_or_none()
        if winner and winner.encounter_id == encounter_id and winner.file_size == size and winner.sha256 == sha256:
            return winner
        raise HTTPException(status_code=409, detail="client_recording_id already exists with different audio")
    session.refresh(segment)
    return segment


@router.get("", response_model=list[AsrRecordingSegmentResponse])
def list_asr_recording_segments(
    encounter_id: Optional[uuid.UUID] = None,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> list[AsrRecordingSegment]:
    q = select(AsrRecordingSegment).where(AsrRecordingSegment.user_id == current_user.id)
    if encounter_id is not None:
        _assert_owned_encounter(session, encounter_id, current_user.id)
        q = q.where(AsrRecordingSegment.encounter_id == encounter_id)
    q = q.order_by(AsrRecordingSegment.sequence_index.asc(), AsrRecordingSegment.created_at.asc())
    return list(session.exec(q).all())


@router.get("/encounter/{encounter_id}/pipeline", response_model=AsrPipelineResponse)
def get_encounter_asr_pipeline(
    encounter_id: uuid.UUID,
    recording_session_id: Optional[str] = Query(None),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> AsrPipelineResponse:
    _assert_owned_encounter(session, encounter_id, current_user.id)
    sess = (recording_session_id or "").strip()[:128] or None
    return _compute_pipeline_state(
        session,
        user_id=current_user.id,
        encounter_id=encounter_id,
        recording_session_id=sess,
    )


@router.post("/encounter/{encounter_id}/drain", response_model=AsrPipelineResponse)
def drain_encounter_chunk_queue(
    encounter_id: uuid.UUID,
    request: Request,
    recording_session_id: Optional[str] = Query(None),
    allow_fallback: bool = Query(True),
    diarize: bool = Query(False),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> AsrPipelineResponse:
    """Serially transcribe pending chunk segments; optional whole-file fallback on failure."""
    _assert_owned_encounter(session, encounter_id, current_user.id)
    if not CHUNK_ASR_ENABLED:
        raise HTTPException(status_code=503, detail="Chunk ASR is not enabled on this server")

    sess = (recording_session_id or "").strip()[:128] or None
    trace_id = (request.headers.get("x-asr-trace-id") or "").strip() or str(uuid.uuid4())
    storage_root = get_asr_segment_storage_root()
    had_failure = False
    drain_deadline = time.perf_counter() + float(os.environ.get("ASR_CHUNK_DRAIN_BUDGET_SEC", "600"))

    while time.perf_counter() < drain_deadline:
        # P3-1: claim_next_pending_chunk() atomically flips pending/failed ->
        # transcribing (conditional UPDATE...WHERE, not a separate read then
        # write) so a concurrent /drain call for the same encounter can't
        # claim and transcribe the same chunk twice.
        pending = claim_next_pending_chunk(
            session,
            user_id=current_user.id,
            encounter_id=encounter_id,
            recording_session_id=sess,
        )
        if not pending:
            break
        try:
            transcribe_chunk_segment(
                session,
                pending,
                storage_root,
                diarize=diarize,
                trace_id=trace_id,
            )
        except Exception as e:
            had_failure = True
            pending.transcription_status = "failed"
            pending.error = str(e)[:1000]
            session.add(pending)
            session.commit()
            break

    pipeline = _compute_pipeline_state(
        session,
        user_id=current_user.id,
        encounter_id=encounter_id,
        recording_session_id=sess,
    )

    if had_failure and allow_fallback:
        try:
            text = _fallback_whole_file_transcribe(
                session,
                user_id=current_user.id,
                encounter_id=encounter_id,
                recording_session_id=sess,
                trace_id=trace_id,
            )
            pipeline = AsrPipelineResponse(
                encounter_id=encounter_id,
                recording_session_id=sess,
                state="ready",
                pending_chunks=0,
                transcribed_chunks=pipeline.transcribed_chunks,
                total_chunks=pipeline.total_chunks,
                merged_transcript_text=text,
                can_generate_note=bool(text.strip()),
                fallback_used=True,
            )
        except Exception:
            pass

    if diarize:
        pipeline = _apply_refine_to_pipeline(
            pipeline,
            diarize=True,
            trace_id=trace_id,
        )
    return pipeline


@router.post("/encounter/{encounter_id}/retranscribe-chunks", response_model=AsrPipelineResponse)
def retranscribe_chunk_session(
    encounter_id: uuid.UUID,
    request: Request,
    recording_session_id: Optional[str] = Query(None),
    diarize: bool = Query(False),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> AsrPipelineResponse:
    """Re-transcribe each chunk in a session (Whisper + optional LLM per chunk)."""
    _assert_owned_encounter(session, encounter_id, current_user.id)
    if not CHUNK_ASR_ENABLED:
        raise HTTPException(status_code=503, detail="Chunk ASR is not enabled on this server")

    sess = (recording_session_id or "").strip()[:128] or None
    if not sess:
        raise HTTPException(status_code=400, detail="recording_session_id is required")

    chunks = ordered_chunk_segments(
        session,
        user_id=current_user.id,
        encounter_id=encounter_id,
        recording_session_id=sess,
    )
    if not chunks:
        raise HTTPException(status_code=404, detail="No chunk segments found for this recording session")

    storage_root = get_asr_segment_storage_root()
    trace_id = (request.headers.get("x-asr-trace-id") or "").strip() or str(uuid.uuid4())
    updated = _retranscribe_one_chunk_session(
        session,
        user_id=current_user.id,
        encounter_id=encounter_id,
        recording_session_id=sess,
        storage_root=storage_root,
        trace_id=trace_id,
        diarize=False,  # Per-chunk diarization removed — global pass below
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Stored chunk audio files not found")

    pipeline = _compute_pipeline_state(
        session,
        user_id=current_user.id,
        encounter_id=encounter_id,
        recording_session_id=sess,
    )

    if diarize:
        pipeline = _apply_refine_to_pipeline(
            pipeline,
            diarize=True,
            trace_id=trace_id,
        )

    return pipeline


@router.post("/encounter/{encounter_id}/retranscribe-all", response_model=EncounterTranscriptResponse)
def retranscribe_all_encounter_segments(
    encounter_id: uuid.UUID,
    request: Request,
    diarize: bool = Query(False),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> EncounterTranscriptResponse:
    """Re-transcribe every saved recording on the encounter (all chunk groups + batch segments)."""
    _assert_owned_encounter(session, encounter_id, current_user.id)
    segments = _ordered_segments(session, user_id=current_user.id, encounter_id=encounter_id)
    if not segments:
        raise HTTPException(
            status_code=404,
            detail="No recording segments found for this encounter — nothing to re-transcribe",
        )

    # Hard reset: wipe any previously stored diarized text on EVERY segment so this
    # re-transcribe starts from a clean slate. With diarize off the result must be plain
    # Whisper with no leftover speaker labels; with diarize on we re-diarize fresh below.
    # (Per-segment re-transcribe also clears this, but a segment that fails to re-run
    # would otherwise keep stale labels that the display layer resurrects.)
    for _seg in segments:
        _seg.refined_committed_text = None
    session.add_all(segments)
    session.commit()

    storage_root = get_asr_segment_storage_root()
    trace_id = (request.headers.get("x-asr-trace-id") or "").strip() or str(uuid.uuid4())
    chunk_session_ids = _chunk_session_ids_for_encounter(segments)
    if chunk_session_ids and not CHUNK_ASR_ENABLED:
        raise HTTPException(status_code=503, detail="Chunk ASR is not enabled on this server")

    for sess in chunk_session_ids:
        _retranscribe_one_chunk_session(
            session,
            user_id=current_user.id,
            encounter_id=encounter_id,
            recording_session_id=sess,
            storage_root=storage_root,
            trace_id=trace_id,
            diarize=False,  # Per-chunk diarization removed — global pass below
        )

    for seg in segments:
        if seg.segment_role in ("chunk", "partial_tail"):
            continue
        _retranscribe_batch_segment(
            session,
            seg,
            storage_root,
            trace_id=trace_id,
            diarize=False,  # Per-segment diarization removed — global pass below
        )

    segments = _ordered_segments(session, user_id=current_user.id, encounter_id=encounter_id)
    whisper = _whisper_encounter_transcript_text(
        session,
        user_id=current_user.id,
        encounter_id=encounter_id,
    )
    display = _full_encounter_transcript_text(
        session,
        user_id=current_user.id,
        encounter_id=encounter_id,
    )

    if diarize and whisper.strip():
        return _apply_refine_to_encounter_transcript(
            session,
            encounter_id=encounter_id,
            whisper_text=whisper,
            segments=segments,
            diarize=True,
            trace_id=trace_id,
        )

    return EncounterTranscriptResponse(
        encounter_id=encounter_id,
        transcript_text=display,
        segments=segments,
        whisper_transcript_text=whisper or None,
        refine_diarize=diarize,
    )


@router.get("/encounter/{encounter_id}/transcript", response_model=EncounterTranscriptResponse)
def get_encounter_segment_transcript(
    encounter_id: uuid.UUID,
    recording_session_id: Optional[str] = Query(None),
    diarize: bool = Query(False),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> EncounterTranscriptResponse:
    _assert_owned_encounter(session, encounter_id, current_user.id)
    segments = _ordered_segments(session, user_id=current_user.id, encounter_id=encounter_id)
    sess = (recording_session_id or "").strip()[:128] or None
    if sess:
        display = merged_transcript_for_encounter(
            session,
            user_id=current_user.id,
            encounter_id=encounter_id,
            recording_session_id=sess,
        )
        if not display.strip():
            display = "\n\n".join((s.transcript_text or "").strip() for s in segments if (s.transcript_text or "").strip())
        whisper = merged_transcript_for_encounter(
            session,
            user_id=current_user.id,
            encounter_id=encounter_id,
            recording_session_id=sess,
            source="whisper",
        )
    else:
        display = _full_encounter_transcript_text(
            session,
            user_id=current_user.id,
            encounter_id=encounter_id,
        )
        whisper = _whisper_encounter_transcript_text(
            session,
            user_id=current_user.id,
            encounter_id=encounter_id,
        )

    if diarize and display.strip():
        return _apply_refine_to_encounter_transcript(
            session,
            encounter_id=encounter_id,
            whisper_text=whisper or display,
            segments=segments,
            diarize=True,
            trace_id="",
        )
    return EncounterTranscriptResponse(
        encounter_id=encounter_id,
        transcript_text=display,
        segments=segments,
        whisper_transcript_text=(whisper or None),
        refine_diarize=diarize,
    )


@router.get("/{segment_id}/download")
def download_asr_recording_segment(
    segment_id: uuid.UUID,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> FileResponse:
    segment = session.get(AsrRecordingSegment, segment_id)
    if not segment or segment.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Recording segment not found")
    path = get_asr_segment_storage_root() / segment.server_file_key
    if not path.exists():
        raise HTTPException(status_code=404, detail="Stored recording segment file not found")
    try:
        log_phi_access(
            session,
            user_id=current_user.id,
            action="download_segment",
            resource_type="asr_segment",
            resource_id=str(segment.id),
            encounter_id=segment.encounter_id,
        )
        session.commit()
    except Exception:
        session.rollback()
    return FileResponse(path=path, filename=segment.file_name, media_type=segment.mime_type)


@router.get("/encounter/{encounter_id}/download")
def download_encounter_asr_segments(
    encounter_id: uuid.UUID,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> FileResponse:
    _assert_owned_encounter(session, encounter_id, current_user.id)
    segments = _ordered_segments(session, user_id=current_user.id, encounter_id=encounter_id)
    paths = [get_asr_segment_storage_root() / s.server_file_key for s in segments]
    paths = [p for p in paths if p.exists()]
    if not paths:
        raise HTTPException(status_code=404, detail="No saved recording segments for this encounter")
    if len(paths) == 1:
        segment = segments[0]
        return FileResponse(path=paths[0], filename=segment.file_name, media_type=segment.mime_type)
    with tempfile.TemporaryDirectory(prefix="asr_segments_") as td:
        out_path = Path(td) / f"encounter_{encounter_id.hex}.webm"
        _concat_webm_files_ffmpeg(paths, out_path)
        data = out_path.read_bytes()
    filename = f"encounter_recording_{str(encounter_id)[:8]}.webm"
    return Response(
        content=data,
        media_type="audio/webm",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/{segment_id}/transcribe", response_model=AsrRecordingSegmentResponse)
def transcribe_asr_recording_segment(
    segment_id: uuid.UUID,
    request: Request,
    mode: str = Query("batch", pattern="^(batch|chunk)$"),
    diarize: bool = Query(False),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> AsrRecordingSegment:
    segment = session.get(AsrRecordingSegment, segment_id)
    if not segment or segment.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Recording segment not found")

    path = get_asr_segment_storage_root() / segment.server_file_key
    if not path.exists():
        segment.transcription_status = "failed"
        segment.error = "Stored recording segment file not found"
        session.add(segment)
        session.commit()
        raise HTTPException(status_code=404, detail="Stored recording segment file not found")

    trace_id = (request.headers.get("x-asr-trace-id") or "").strip() or str(uuid.uuid4())

    use_chunk = mode == "chunk" and CHUNK_ASR_ENABLED and segment.segment_role in ("chunk", "partial_tail")
    if use_chunk:
        try:
            assert_chunk_ready_for_transcribe(session, segment)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    segment.transcription_status = "transcribing"
    segment.error = None
    session.add(segment)
    session.commit()

    try:
        if use_chunk:
            segment = transcribe_chunk_segment(
                session,
                segment,
                get_asr_segment_storage_root(),
                diarize=False,  # Per-chunk diarization removed
                trace_id=trace_id,
            )
        else:
            text = _transcribe_audio_bytes_sync(
                file_data=path.read_bytes(),
                filename=segment.file_name,
                content_type=segment.mime_type,
                trace_id=trace_id,
                t0=time.perf_counter(),
            )
            whisper = text.strip()
            # Store raw Whisper only — global diarization applied at encounter level
            segment.transcript_text = whisper
            segment.committed_text = whisper
            segment.refined_committed_text = None
            segment.transcription_status = "transcribed"
            segment.transcribed_at = datetime.utcnow()
            segment.error = None
            session.add(segment)
            session.commit()
            session.refresh(segment)
    except Exception as e:
        segment.transcription_status = "failed"
        segment.error = str(e)[:1000]
        session.add(segment)
        session.commit()
        raise HTTPException(status_code=502, detail=f"ASR failed: {str(e)[:300]}")

    return segment
