# server/routes/asr_retranscribe.py
from __future__ import annotations

import tempfile
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session

from server.core.db import get_session
from server.core.dependencies import get_current_user
from server.core.whisper_client import transcribe_bytes_sync
from server.models.user import User
from server.routes.asr_segments import (
    _assert_owned_encounter,
    _concat_webm_files_ffmpeg,
    _ordered_segments,
    get_asr_segment_storage_root,
)

router = APIRouter(
    prefix="/api/asr/retranscribe",
    tags=["asr-retranscribe"],
    redirect_slashes=False,
)


@router.post("/encounter/{encounter_id}")
def retranscribe_encounter(
    encounter_id: uuid.UUID,
    profile: str = Query("asr_only", pattern="^(asr_only|asr_diarize|full)$"),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Merge all ordered segments and re-transcribe with Whisper (whole-file path).

    Plain `def`, not `async def`: this does a blocking ffmpeg subprocess (up
    to 180s) and a blocking Whisper HTTP call (up to 100s) with nothing to
    await, so it was previously running directly on the event loop thread --
    a single call could stall every other concurrent request (uploads,
    health checks, other doctors' in-flight streams) for that whole window.
    A plain `def` handler runs in Starlette's threadpool instead, matching
    the pattern already used correctly by the other blocking ASR handlers in
    this codebase (drain_encounter_chunk_queue, transcribe_asr_recording_segment).
    """
    _assert_owned_encounter(session, encounter_id, current_user.id)
    segments = _ordered_segments(session, user_id=current_user.id, encounter_id=encounter_id)

    if not segments:
        raise HTTPException(
            status_code=404,
            detail="No recording segments found for this encounter — nothing to re-transcribe",
        )

    storage_root = get_asr_segment_storage_root()
    paths = [storage_root / s.server_file_key for s in segments]
    paths = [p for p in paths if p.exists()]

    if not paths:
        raise HTTPException(
            status_code=404,
            detail="No stored audio files for this encounter's segments",
        )

    trace_id = uuid.uuid4().hex
    t0 = time.perf_counter()

    with tempfile.TemporaryDirectory(prefix="retranscribe_") as td:
        out_path = Path(td) / f"encounter_{encounter_id.hex}.webm"
        _concat_webm_files_ffmpeg(paths, out_path)
        merged_bytes = out_path.read_bytes()

    text, _payload = transcribe_bytes_sync(
        file_data=merged_bytes,
        filename=out_path.name,
        content_type="audio/webm",
        trace_id=trace_id,
        response_format="json",
        skip_normalize=False,
    )

    return {
        "encounter_id": str(encounter_id),
        "engine": "whisper",
        "profile": profile,
        "text": text.strip(),
        "segment_count": len(segments),
        "trace_id": trace_id,
        "elapsed_sec": round(time.perf_counter() - t0, 3),
    }
