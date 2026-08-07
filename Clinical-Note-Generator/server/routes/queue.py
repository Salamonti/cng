# server/routes/queue.py
import io
import os
import shutil
import uuid
from pathlib import Path
from typing import List, Dict, Any, Optional
import time
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, status, Form, File, UploadFile, Request, Response
from starlette.datastructures import Headers
from sqlmodel import Session, select

from server.core.db import get_session
from server.core.dependencies import get_current_user
from server.core.encounter_workspace import ensure_encounters_for_user
from server.models.queued_job import QueuedJob
from server.models.user import User
from server.models.user_encounter import UserEncounter
from server.schemas.queue import QueuedJobResponse

# Import processing endpoints
from server.routes.ocr import ocr as ocr_endpoint

router = APIRouter(prefix="/api/queue", tags=["queue"], redirect_slashes=False)

logger = logging.getLogger("cng.queue")
REMOVED_AUDIO_QUEUE_TYPES = {"transcribe", "asr", "asr_recording", "asr_segment"}


def _queue_debug_enabled() -> bool:
    val = str(os.environ.get("QUEUE_DEBUG", "") or "").strip().lower()
    return val in {"1", "true", "yes", "on"}


def _dbg(trace_id: str, msg: str, **fields: Any) -> None:
    if not _queue_debug_enabled():
        return
    payload = {"trace_id": trace_id, **fields}
    try:
        logger.warning("[QUEUE_DEBUG] %s | %s", msg, json.dumps(payload, ensure_ascii=False, default=str)[:8000])
    except Exception:
        logger.warning("[QUEUE_DEBUG] %s | trace_id=%s", msg, trace_id)


def get_queue_storage_root() -> Path:
    """Return absolute path to queue file storage directory."""
    # data directory is at project_root/data (same as user_data.sqlite)
    data_dir = Path(__file__).resolve().parents[2] / "data"
    queue_dir = data_dir / "queue_files"
    queue_dir.mkdir(parents=True, exist_ok=True)
    return queue_dir


def get_user_queue_dir(user_id: uuid.UUID) -> Path:
    """Return user-specific subdirectory."""
    root = get_queue_storage_root()
    user_dir = root / str(user_id)
    user_dir.mkdir(parents=True, exist_ok=True)
    return user_dir


def save_queued_file(user_id: uuid.UUID, job_id: uuid.UUID, file: UploadFile) -> str:
    """Save uploaded file to disk, return relative path (server_file_key)."""
    user_dir = get_user_queue_dir(user_id)
    # Use job_id as filename; preserve extension if possible
    ext = Path(file.filename or "").suffix.lower()
    filename = f"{job_id}{ext}"
    file_path = user_dir / filename
    with open(file_path, "wb") as f:
        shutil.copyfileobj(file.file, f)
    # Return relative path from storage root
    relative = str(Path(str(user_id)) / filename)
    return relative


def delete_queued_file(server_file_key: str) -> None:
    """Delete stored file from disk."""
    root = get_queue_storage_root()
    file_path = root / server_file_key
    try:
        if file_path.exists():
            file_path.unlink()
    except OSError:
        # Best-effort cleanup of a stored queue file. A failed unlink (missing
        # file, permission, in-flight use) must never raise into the caller;
        # the file is reaped by the TTL sweeper anyway. Deliberate silent
        # swallow (cleanup-guard).
        pass


def _create_uploadfile_from_disk(file_path: Path, filename: str, content_type: str) -> UploadFile:
    """Create an UploadFile-like object from a disk file.

    Starlette/FastAPI UploadFile __init__ signature varies by version but generally:
      UploadFile(file, *, filename=..., headers=...)

    content_type is derived from headers, and is not a supported constructor kwarg.
    """
    file_data = file_path.read_bytes()

    headers = Headers({
        "content-type": (content_type or "application/octet-stream")
    })
    return UploadFile(
        io.BytesIO(file_data),
        filename=filename,
        headers=headers,
    )


def _auto_fail_stale_jobs(session: Session, max_age_hours: int = 48) -> int:
    """Auto-fail pending queue jobs older than max_age_hours to prevent infinite retry loops."""
    from datetime import datetime, timedelta, timezone
    from server.models.queued_job import QueuedJob
    from sqlalchemy import update

    cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=max_age_hours)
    stmt = (
        update(QueuedJob)
        .where(
            QueuedJob.status == "pending",
            QueuedJob.created_at < cutoff,
        )
        .values(status="failed", error=f"Auto-failed: pending > {max_age_hours}h")
    )
    result = session.exec(stmt)
    session.commit()
    count = result.rowcount
    if count:
        import logging
        logging.getLogger("cng.queue").warning(
            "Auto-failed %d stale pending jobs (older than %dh)", count, max_age_hours
        )
    return count


@router.post("", response_model=QueuedJobResponse)
def create_queued_job(
    request: Request,
    response: Response,
    file: UploadFile = File(...),
    type: str = Form("ocr"),  # form field, default "ocr"
    encounter_id: Optional[uuid.UUID] = Form(None),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Plain `def`, not `async def`: nothing in this handler actually awaits
    anything (file access below goes through the sync `file.file`/
    save_queued_file() path, not UploadFile.read()), so it was previously
    running its blocking DB queries, file-size probe, and file copy directly
    on the event loop thread for no reason. A plain `def` handler runs in
    Starlette's threadpool instead.
    """
    # Auto-cleanup: fail any pending jobs older than 48 hours so they don't
    # cause infinite poller retries (ClientDisconnect storms).
    _auto_fail_stale_jobs(session)

    hdr_tid = ""
    try:
        hdr_tid = (request.headers.get("x-asr-trace-id") or "").strip()
    except Exception:
        hdr_tid = ""
    trace_id = hdr_tid or str(uuid.uuid4())
    t0 = time.perf_counter()
    _dbg(
        trace_id,
        "queue.create.start",
        type=type,
        filename=file.filename,
        content_type=file.content_type,
        encounter_id=str(encounter_id) if encounter_id else None,
        user_id=str(current_user.id),
    )
    type_norm = (type or "").strip().lower()
    if type_norm in REMOVED_AUDIO_QUEUE_TYPES:
        raise HTTPException(
            status_code=410,
            detail="Audio queue jobs were removed. Use /api/asr/segments for recordings.",
        )

    # Validate file size
    MAX_FILE_SIZE = 100 * 1024 * 1024  # 100 MB
    file.file.seek(0, 2)  # seek to end
    size = file.file.tell()
    file.file.seek(0)  # reset
    if size > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="File too large")

    _, enc = ensure_encounters_for_user(session, current_user.id)
    enc_id = enc.id if enc else None
    if encounter_id is not None:
        requested_enc = session.get(UserEncounter, encounter_id)
        if not requested_enc or requested_enc.user_id != current_user.id:
            raise HTTPException(status_code=404, detail="Encounter not found")
        enc_id = requested_enc.id

    # Create job record only after validating the target encounter, otherwise a
    # rejected upload could leave an orphaned file on disk.
    job_id = uuid.uuid4()
    server_file_key = save_queued_file(current_user.id, job_id, file)

    job = QueuedJob(
        id=job_id,
        user_id=current_user.id,
        encounter_id=enc_id,
        type=type_norm,
        status="pending",
        file_name=file.filename or "unknown",
        mime_type=file.content_type or "application/octet-stream",
        file_size=size,
        server_file_key=server_file_key,
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    try:
        response.headers["X-ASR-Trace-Id"] = trace_id
    except Exception:
        # Best-effort: attaching the trace-id response header is telemetry for
        # cross-system correlation. If the response object can't take it, the
        # job is still created correctly -- never break the request over it.
        logger.debug("Could not set X-ASR-Trace-Id response header", exc_info=True)
    _dbg(
        trace_id,
        "queue.create.ok",
        job_id=str(job.id),
        server_file_key=job.server_file_key,
        size_bytes=size,
        dt_ms=int((time.perf_counter() - t0) * 1000),
        encounter_id=str(job.encounter_id) if job.encounter_id else None,
    )
    return job


@router.get("", response_model=List[QueuedJobResponse])
def list_queued_jobs(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    jobs = session.exec(
        select(QueuedJob).where(QueuedJob.user_id == current_user.id).order_by(QueuedJob.created_at.desc())
    ).all()
    return jobs


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_queued_job(
    job_id: uuid.UUID,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    job = session.exec(
        select(QueuedJob).where(QueuedJob.id == job_id, QueuedJob.user_id == current_user.id)
    ).one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Queued job not found")
    delete_queued_file(job.server_file_key)
    session.delete(job)
    session.commit()
    return None


@router.post("/{job_id}/retry", response_model=QueuedJobResponse)
def retry_queued_job(
    job_id: uuid.UUID,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    job = session.exec(
        select(QueuedJob).where(QueuedJob.id == job_id, QueuedJob.user_id == current_user.id)
    ).one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Queued job not found")
    job.status = "pending"
    job.error = None
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


@router.get("/{job_id}/download")
def download_queued_job(
    job_id: uuid.UUID,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    job = session.exec(
        select(QueuedJob).where(QueuedJob.id == job_id, QueuedJob.user_id == current_user.id)
    ).one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Queued job not found")
    root = get_queue_storage_root()
    file_path = root / job.server_file_key
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Stored file not found")
    # Return file as streaming response
    from fastapi.responses import FileResponse
    return FileResponse(
        path=file_path,
        filename=job.file_name,
        media_type=job.mime_type,
    )


@router.post("/{job_id}/process")
def process_queued_job(
    job_id: uuid.UUID,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """Process a queued job server-side; delete on success; persist on failure."""
    trace_id = (request.headers.get("x-asr-trace-id") or "").strip() or str(uuid.uuid4())
    t0 = time.perf_counter()
    _dbg(trace_id, "queue.process.start", job_id=str(job_id), user_id=str(current_user.id))
    job = session.exec(
        select(QueuedJob).where(QueuedJob.id == job_id, QueuedJob.user_id == current_user.id)
    ).one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Queued job not found")
    _dbg(
        trace_id,
        "queue.process.job_loaded",
        type=job.type,
        mime=job.mime_type,
        file_name=job.file_name,
        server_file_key=job.server_file_key,
        status=job.status,
        encounter_id=str(job.encounter_id) if job.encounter_id else None,
    )
    if (job.type or "").strip().lower() in REMOVED_AUDIO_QUEUE_TYPES:
        raise HTTPException(
            status_code=410,
            detail="Audio queue jobs were removed. Use /api/asr/segments for recordings.",
        )

    root = get_queue_storage_root()
    file_path = root / job.server_file_key
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Stored file not found")

    upload_file = _create_uploadfile_from_disk(
        file_path,
        filename=job.file_name,
        content_type=job.mime_type,
    )

    try:
        if job.type == "ocr":
            result = ocr_endpoint(upload_file)
            extracted_text = (result or {}).get("text", "")
            delete_queued_file(job.server_file_key)
            session.delete(job)
            session.commit()
            return {
                "success": True,
                "job_id": str(job_id),
                "type": "ocr",
                "text": extracted_text,
                "confidence": (result or {}).get("confidence", 0.0),
            }

        raise HTTPException(status_code=400, detail=f"Unsupported job type: {job.type}")

    except HTTPException:
        _dbg(trace_id, "queue.process.http_exception", dt_ms=int((time.perf_counter() - t0) * 1000))
        raise
    except Exception as e:
        _dbg(trace_id, "queue.process.exception", kind=type(e).__name__, message=str(e)[:300], dt_ms=int((time.perf_counter() - t0) * 1000))
        job.status = "failed"
        job.error = str(e)[:500]
        session.add(job)
        session.commit()
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
def clear_all_queued_jobs(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
    encounter_id: Optional[uuid.UUID] = None,
):
    """Delete queued jobs for the current user (optionally scoped to an encounter)."""
    q = select(QueuedJob).where(QueuedJob.user_id == current_user.id)
    if encounter_id is not None:
        q = q.where(QueuedJob.encounter_id == encounter_id)
    jobs = session.exec(q).all()
    for job in jobs:
        delete_queued_file(job.server_file_key)
        session.delete(job)
    session.commit()
    return None
