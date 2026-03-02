# C:\Clinical-Note-Generator\server\routes\queue.py
import io
import os
import shutil
import uuid
from pathlib import Path
from typing import List, Dict, Any

from fastapi import APIRouter, Depends, HTTPException, status, Form
from fastapi import File
from starlette.datastructures import UploadFile
from sqlmodel import Session, select

from server.core.db import get_session
from server.core.dependencies import get_current_user
from server.models.queued_job import QueuedJob
from server.models.user import User
from server.schemas.queue import QueuedJobCreate, QueuedJobResponse

# Import processing endpoints
from server.routes.ocr import ocr as ocr_endpoint
from server.routes.asr import transcribe_diarized as asr_endpoint

router = APIRouter(prefix="/api/queue", tags=["queue"], redirect_slashes=False)


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
        pass  # ignore missing files


def _create_uploadfile_from_disk(file_path: Path, filename: str, content_type: str) -> UploadFile:
    """Create an UploadFile object from a disk file."""
    file_data = file_path.read_bytes()
    upload = UploadFile(
        filename=filename,
        file=io.BytesIO(file_data)
    )
    upload.content_type = content_type or ""
    return upload


@router.post("", response_model=QueuedJobResponse)
async def create_queued_job(
    file: UploadFile = File(...),
    type: str = Form("ocr"),  # form field, default "ocr"
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    # Debug logging
    import sys
    print(f"[Queue] Creating job: type={type}, filename={file.filename}, content_type={file.content_type}", file=sys.stderr)

    # Validate file size (optional)
    MAX_FILE_SIZE = 100 * 1024 * 1024  # 100 MB
    file.file.seek(0, 2)  # seek to end
    size = file.file.tell()
    file.file.seek(0)  # reset
    if size > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="File too large")

    # Create job record
    job_id = uuid.uuid4()
    server_file_key = save_queued_file(current_user.id, job_id, file)

    job = QueuedJob(
        id=job_id,
        user_id=current_user.id,
        type=type,
        status="pending",
        file_name=file.filename or "unknown",
        mime_type=file.content_type or "application/octet-stream",
        file_size=size,
        server_file_key=server_file_key,
    )
    session.add(job)
    session.commit()
    session.refresh(job)
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
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """Process a queued job server-side; delete on success; persist on failure."""
    import sys
    print(f"[Queue] Processing job {job_id}", file=sys.stderr)
    job = session.exec(
        select(QueuedJob).where(QueuedJob.id == job_id, QueuedJob.user_id == current_user.id)
    ).one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Queued job not found")
    print(f"[Queue] Job type={job.type}, mime={job.mime_type}, file={job.file_name}", file=sys.stderr)

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

        if job.type == "transcribe":
            # Use internal HTTP to reuse existing ASR proxy behavior.
            import requests

            upload_file.file.seek(0)
            resp = requests.post(
                "http://127.0.0.1:7860/api/transcribe_diarized",
                files={"audio": (job.file_name, upload_file.file, job.mime_type)},
                timeout=180,
            )
            if resp.status_code != 200:
                raise RuntimeError(f"ASR HTTP {resp.status_code}: {resp.text[:200]}")

            delete_queued_file(job.server_file_key)
            session.delete(job)
            session.commit()
            return {
                "success": True,
                "job_id": str(job_id),
                "type": "transcribe",
                "text": resp.text,
            }

        raise HTTPException(status_code=400, detail=f"Unsupported job type: {job.type}")

    except HTTPException:
        raise
    except Exception as e:
        job.status = "failed"
        job.error = str(e)[:500]
        session.add(job)
        session.commit()
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
def clear_all_queued_jobs(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Delete all queued jobs for the current user (called on New Case / Clear)."""
    jobs = session.exec(
        select(QueuedJob).where(QueuedJob.user_id == current_user.id)
    ).all()
    for job in jobs:
        delete_queued_file(job.server_file_key)
        session.delete(job)
    session.commit()
    return None