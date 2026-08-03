"""Chunk ASR: overlap audio assembly + verbose_json transcribe + merge."""
from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from datetime import datetime
from uuid import UUID
from pathlib import Path
from typing import Any, Optional

from sqlmodel import Session, select

from server.core.asr_audio import _resolve_ffmpeg_bin
from server.core.chunk_merge import (
    COMMIT_STRIDE_SEC,
    OVERLAP_SEC,
    WHISPER_WINDOW_SEC,
    merge_committed_segments,
)
from server.core.whisper_client import preferred_pool_index_for_session, transcribe_bytes_sync
from server.models.asr_recording_segment import AsrRecordingSegment

logger = logging.getLogger(__name__)


def _segment_path(storage_root: Path, segment: AsrRecordingSegment) -> Path:
    return storage_root / segment.server_file_key


def _resolve_ffprobe_bin() -> Optional[str]:
    ffmpeg_bin = _resolve_ffmpeg_bin()
    if ffmpeg_bin:
        ffprobe = Path(ffmpeg_bin).with_name("ffprobe.exe" if ffmpeg_bin.lower().endswith(".exe") else "ffprobe")
        if ffprobe.exists():
            return str(ffprobe)
    return shutil.which("ffprobe")


def probe_media_duration_sec(path: Path) -> float:
    """Return media duration in seconds via ffprobe."""
    ffprobe_bin = _resolve_ffprobe_bin()
    if not ffprobe_bin:
        raise RuntimeError("ffprobe not available for chunk overlap assembly")
    cmd = [
        ffprobe_bin,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path.resolve()),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    if proc.returncode != 0:
        msg = (proc.stderr or proc.stdout or "")[:500]
        raise RuntimeError(f"ffprobe failed: {msg}")
    try:
        dur = float((proc.stdout or "").strip())
    except ValueError as exc:
        raise RuntimeError("ffprobe returned non-numeric duration") from exc
    if dur <= 0:
        raise RuntimeError(f"invalid media duration: {dur}")
    return dur


def overlap_tail_start_sec(prev_duration_sec: float, overlap_sec: float = OVERLAP_SEC) -> float:
    """Start time (seconds) for the last overlap_sec of a previous segment."""
    return max(0.0, float(prev_duration_sec) - float(overlap_sec))


def build_overlap_window_ffmpeg(
    prev_path: Path,
    curr_path: Path,
    out_path: Path,
    *,
    overlap_sec: float = OVERLAP_SEC,
    window_sec: float = WHISPER_WINDOW_SEC,
    prev_duration_sec: Optional[float] = None,
) -> None:
    """Decode last overlap_sec of prev + first (window_sec-overlap_sec) of curr → mono 16k wav."""
    ffmpeg_bin = _resolve_ffmpeg_bin()
    if not ffmpeg_bin:
        raise RuntimeError("ffmpeg not available for chunk overlap assembly")
    tail_sec = overlap_sec
    head_sec = max(0.1, window_sec - overlap_sec)
    if prev_duration_sec is None:
        prev_duration_sec = probe_media_duration_sec(prev_path)
    tail_start = overlap_tail_start_sec(prev_duration_sec, tail_sec)
    filter_str = (
        f"[0:a]atrim=start={tail_start:.6f},asetpts=PTS-STARTPTS[a0];"
        f"[1:a]atrim=start=0:duration={head_sec:.6f},asetpts=PTS-STARTPTS[a1];"
        f"[a0][a1]concat=n=2:v=0:a=1,aresample=16000,aformat=sample_fmts=s16:channel_layouts=mono[aout]"
    )
    cmd = [
        ffmpeg_bin,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(prev_path.resolve()),
        "-i",
        str(curr_path.resolve()),
        "-filter_complex",
        filter_str,
        "-map",
        "[aout]",
        str(out_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if proc.returncode != 0 or not out_path.exists() or out_path.stat().st_size < 100:
        msg = (proc.stderr or proc.stdout or "")[:500]
        raise RuntimeError(f"ffmpeg overlap window failed: {msg}")


def _prev_chunk_segment(
    session: Session,
    segment: AsrRecordingSegment,
) -> Optional[AsrRecordingSegment]:
    if segment.chunk_index is None or segment.chunk_index <= 0:
        return None
    if not segment.recording_session_id:
        return None
    prev_idx = int(segment.chunk_index) - 1
    return session.exec(
        select(AsrRecordingSegment).where(
            AsrRecordingSegment.user_id == segment.user_id,
            AsrRecordingSegment.encounter_id == segment.encounter_id,
            AsrRecordingSegment.recording_session_id == segment.recording_session_id,
            AsrRecordingSegment.segment_role.in_(("chunk", "partial_tail")),
            AsrRecordingSegment.chunk_index == prev_idx,
        )
    ).one_or_none()


def assert_chunk_ready_for_transcribe(session: Session, segment: AsrRecordingSegment) -> None:
    """Ensure prior chunk in the same session is transcribed before chunk i > 0."""
    idx = int(segment.chunk_index or 0)
    if idx <= 0:
        return
    prev = _prev_chunk_segment(session, segment)
    if not prev:
        raise ValueError(f"Missing prior chunk for index {idx}")
    if prev.transcription_status != "transcribed":
        raise ValueError(f"Prior chunk {idx - 1} is not transcribed yet")


def _carry_prompt(session: Session, segment: AsrRecordingSegment) -> str:
    prev = _prev_chunk_segment(session, segment)
    if not prev:
        return ""
    # Whisper prompt must be raw ASR text, not LLM-diarized labels.
    src = (prev.committed_text or prev.transcript_text or "").strip()
    carried = src[-220:] if len(src) > 220 else src
    # Prepend medical context so it survives the API prompt override
    # (whisper-server replaces --prompt flag when API sends a prompt field)
    return "Breztri, Trelegy, Advair, Symbicort, Spiriva, tiotropium, umeclidinium, vilanterol. " + carried


def segment_display_text(segment: AsrRecordingSegment) -> str:
    """Text shown in merged encounter / pipeline output."""
    return (
        (segment.refined_committed_text or segment.committed_text or segment.transcript_text or "")
        .strip()
    )


def segment_whisper_commit_text(segment: AsrRecordingSegment) -> str:
    """Raw incremental commit used for chunk merge math and whisper prompts."""
    return (segment.committed_text or segment.transcript_text or "").strip()


def _prev_committed_text(session: Session, segment: AsrRecordingSegment) -> str:
    prev = _prev_chunk_segment(session, segment)
    if not prev:
        return ""
    return segment_whisper_commit_text(prev)


def resolve_committed_text(
    *,
    segment_role: str,
    chunk_index: int,
    window_start_sec: float,
    payload: Any,
    full_text: str,
    prev_text: str,
) -> tuple[str, str]:
    """Derive incremental committed_text for a chunk or final partial_tail segment."""
    from server.core.chunk_merge import commit_text_from_verbose, fuzzy_commit_delta, fuzzy_stitch

    inc = (full_text or "").strip()
    is_tail = segment_role == "partial_tail"

    if is_tail:
        if not inc:
            return "", "partial_tail_empty"
        delta = fuzzy_commit_delta(prev_text, inc)
        if delta:
            return delta.strip(), "partial_tail_fuzzy"
        stitched = fuzzy_stitch(prev_text, inc)
        prev_norm = (prev_text or "").strip()
        if stitched and prev_norm and stitched.startswith(prev_norm):
            suffix = stitched[len(prev_norm):].strip()
            if suffix:
                return suffix, "partial_tail_suffix"
        if inc and inc not in prev_norm:
            return inc, "partial_tail_direct"
        return "", "partial_tail_no_delta"

    committed, merge_method = commit_text_from_verbose(
        payload,
        window_start_sec=window_start_sec,
        chunk_index=chunk_index,
    )
    if not committed and inc:
        if chunk_index == 0:
            return inc, "full_text"
        return fuzzy_commit_delta(prev_text, inc).strip(), "fuzzy_delta"
    return committed.strip(), merge_method


def transcribe_chunk_segment(
    session: Session,
    segment: AsrRecordingSegment,
    storage_root: Path,
    *,
    diarize: bool = False,
    trace_id: str = "",
) -> AsrRecordingSegment:
    path = _segment_path(storage_root, segment)
    if not path.exists():
        segment.transcription_status = "failed"
        segment.error = "Stored recording segment file not found"
        session.add(segment)
        session.commit()
        raise FileNotFoundError("Stored recording segment file not found")

    assert_chunk_ready_for_transcribe(session, segment)

    chunk_index = int(segment.chunk_index or 0)
    is_tail = segment.segment_role == "partial_tail"
    window_start = float(
        segment.window_start_sec
        if segment.window_start_sec is not None
        else max(0, chunk_index * COMMIT_STRIDE_SEC - (OVERLAP_SEC if chunk_index else 0))
    )

    prev = _prev_chunk_segment(session, segment)
    prompt = _carry_prompt(session, segment)
    prev_text = _prev_committed_text(session, segment)
    pool_index = preferred_pool_index_for_session(segment.recording_session_id)

    audio_bytes = path.read_bytes()
    filename = segment.file_name
    content_type = segment.mime_type
    used_overlap = False

    if prev and chunk_index >= 1 and not is_tail:
        prev_path = _segment_path(storage_root, prev)
        if prev_path.exists():
            try:
                with tempfile.TemporaryDirectory(prefix="asr_chunk_") as td:
                    wav_out = Path(td) / "window.wav"
                    build_overlap_window_ffmpeg(prev_path, path, wav_out)
                    audio_bytes = wav_out.read_bytes()
                    filename = "chunk_window.wav"
                    content_type = "audio/wav"
                    used_overlap = True
            except Exception as exc:
                logger.warning(
                    "chunk overlap ffmpeg failed segment=%s chunk_index=%s: %s",
                    segment.id,
                    chunk_index,
                    exc,
                )

    text, payload = transcribe_bytes_sync(
        file_data=audio_bytes,
        filename=filename,
        content_type=content_type,
        trace_id=trace_id,
        response_format="verbose_json",
        skip_normalize=True,
        prompt=prompt or None,
        preferred_index=pool_index,
    )

    # FIX A: When ffmpeg overlap assembly fails, we fall back to the raw ~25s
    # segment whose Whisper timestamps start at 0.  Use the raw chunk boundary
    # (chunk_index * STRIDE) as window_start so word timestamps fall in the
    # correct commit window.  When overlap succeeds, use the overlap-offset.
    effective_window_start = (
        chunk_index * COMMIT_STRIDE_SEC
        if not used_overlap
        else window_start
    )

    committed, merge_method = resolve_committed_text(
        segment_role=str(segment.segment_role or "chunk"),
        chunk_index=chunk_index,
        window_start_sec=effective_window_start,
        payload=payload,
        full_text=text,
        prev_text=prev_text,
    )

    # Per-chunk diarization has been removed in favor of a single global
    # pass on the full merged Whisper text (see asr_segments.py). This
    # guarantees consistent speaker labels across chunk boundaries and
    # eliminates GPU contention during recording.
    whisper_text = text.strip()
    raw_committed = committed.strip()
    segment.transcript_text = whisper_text
    segment.committed_text = raw_committed
    segment.refined_committed_text = None
    segment.transcript_json = None
    segment.merge_method = merge_method
    segment.transcription_status = "transcribed"
    segment.transcribed_at = datetime.utcnow()
    segment.error = None
    session.add(segment)
    session.commit()
    session.refresh(segment)
    return segment


def ordered_chunk_segments(
    session: Session,
    *,
    user_id: UUID,
    encounter_id: UUID,
    recording_session_id: Optional[str] = None,
) -> list[AsrRecordingSegment]:
    q = select(AsrRecordingSegment).where(
        AsrRecordingSegment.user_id == user_id,
        AsrRecordingSegment.encounter_id == encounter_id,
        AsrRecordingSegment.segment_role.in_(("chunk", "partial_tail")),
    )
    if recording_session_id:
        q = q.where(AsrRecordingSegment.recording_session_id == recording_session_id)
    q = q.order_by(AsrRecordingSegment.chunk_index.asc(), AsrRecordingSegment.created_at.asc())
    return list(session.exec(q).all())


def merged_transcript_for_encounter(
    session: Session,
    *,
    user_id: UUID,
    encounter_id: UUID,
    recording_session_id: Optional[str] = None,
    source: str = "committed",
) -> str:
    chunks = ordered_chunk_segments(
        session,
        user_id=user_id,
        encounter_id=encounter_id,
        recording_session_id=recording_session_id,
    )
    parts = []
    for c in chunks:
        if c.transcription_status != "transcribed":
            continue
        if source == "whisper":
            t = segment_whisper_commit_text(c)
        else:
            t = segment_display_text(c)
        if t:
            parts.append(t)
    return merge_committed_segments(parts)


def next_pending_chunk(
    session: Session,
    *,
    user_id: UUID,
    encounter_id: UUID,
    recording_session_id: Optional[str] = None,
) -> Optional[AsrRecordingSegment]:
    chunks = ordered_chunk_segments(
        session,
        user_id=user_id,
        encounter_id=encounter_id,
        recording_session_id=recording_session_id,
    )
    for seg in chunks:
        if seg.transcription_status in ("pending", "failed"):
            idx = int(seg.chunk_index or 0)
            if idx == 0:
                return seg
            prev = _prev_chunk_segment(session, seg)
            if prev and prev.transcription_status == "transcribed":
                return seg
    return None
