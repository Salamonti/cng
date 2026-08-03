#!/usr/bin/env python3
"""Purge stored audio files older than the retention period (NS PHIA: 7 days).

Nova Scotia rule: recorded patient audio must be deleted within 7 days. This
script enforces it on the server. Each run:

  1. Deletes expired queue/ASR segment files from disk and their DB rows.
  2. Sweeps orphan files (on disk under data/queue_files and data/asr_recording_segments, no matching DB row).
  3. Appends a summary to asr_diagnostics/audio_purge_log.jsonl (audit trail).

The transcript / clinical note is stored in the workspace tables, NOT on the
`queued_jobs` row (the model has no text field), so it is unaffected here —
only the audio file and its queue bookkeeping row are removed.

Standalone, dependency-light: imports only the DB engine and the QueuedJob
model, never the FastAPI route modules.

Usage:
    python purge_audio.py [--dry-run] [--retention-days N]

Schedule daily — see tools/run_purge_audio.bat.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Clinical-Note-Generator/ — parent of tools/
PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Run from the project root so a relative sqlite database_url resolves the same
# way it does for the FastAPI server, and put the package root on sys.path.
os.chdir(PROJECT_ROOT)
sys.path.insert(0, str(PROJECT_ROOT))

# Absolute imports, the same style the application uses. No FastAPI route
# modules are pulled in — just the engine and the ORM model.
from sqlalchemy import inspect  # noqa: E402
from sqlmodel import Session, select  # noqa: E402
from server.core.db import engine  # noqa: E402
from server.models.queued_job import QueuedJob  # noqa: E402
from server.models.asr_recording_segment import AsrRecordingSegment  # noqa: E402
# Import FK target models so SQLAlchemy can resolve foreign key dependencies
# during session flush (user/user_encounter tables exist but weren't registered)
from server.models.user import User  # noqa: E402
from server.models.user_encounter import UserEncounter  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("cng.audio_purge")

QUEUE_ROOT = PROJECT_ROOT / "data" / "queue_files"
ASR_SEGMENT_ROOT = PROJECT_ROOT / "data" / "asr_recording_segments"
CONFIG_PATH = PROJECT_ROOT / "config" / "config.json"
AUDIT_LOG = PROJECT_ROOT / "asr_diagnostics" / "audio_purge_log.jsonl"
DEFAULT_RETENTION_DAYS = 7


def load_retention_days() -> int:
    """Read audio_retention_days from config.json (default 7)."""
    try:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        days = int(cfg.get("audio_retention_days", DEFAULT_RETENTION_DAYS))
        return days if days > 0 else DEFAULT_RETENTION_DAYS
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not read audio_retention_days from config: %s", e)
        return DEFAULT_RETENTION_DAYS


def _norm(key: str | None) -> str:
    """Normalise a server_file_key / relative path for cross-platform compare."""
    return str(key or "").replace("\\", "/").strip("/")


def _path_size(p: Path) -> int:
    """Total bytes for a file or a directory tree."""
    try:
        if p.is_file():
            return p.stat().st_size
        if p.is_dir():
            return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
    except OSError:
        pass
    return 0


def _remove(p: Path) -> None:
    """Delete a file or a directory tree."""
    if p.is_dir():
        shutil.rmtree(p, ignore_errors=True)
    elif p.exists():
        p.unlink()


def purge_expired(dry_run: bool = False, retention_days: int | None = None) -> dict:
    """Delete expired queue audio (files + rows) and orphan files.

    Returns a summary dict (also appended to the audit log).
    """
    if retention_days is None:
        retention_days = load_retention_days()

    # created_at is stored as naive UTC (datetime.utcnow) — compare naive-to-naive.
    cutoff = datetime.utcnow() - timedelta(days=retention_days)
    cutoff_ts = cutoff.timestamp()

    expired_count = 0
    expired_segments_count = 0
    deleted_files = 0
    deleted_rows = 0
    orphans_deleted = 0
    errors = 0
    bytes_freed = 0

    # Disable autoflush — SQLAlchemy 2.x resolves FK dependencies during flush
    # to determine table sort order. QueuedJob has FKs to user/user_encounter
    # tables that may not exist in this DB schema, causing a NoReferencedTableError
    # when flush runs before the next query. By committing each table's deletes
    # separately we avoid the cross-table FK resolution entirely.
    session = Session(engine)
    session.autoflush = False

    try:
        existing_tables = set(inspect(engine).get_table_names())
    except Exception:
        existing_tables = set()
    has_segment_table = "asr_recording_segments" in existing_tables

    expired = session.exec(
        select(QueuedJob).where(QueuedJob.created_at < cutoff)
    ).all()
    expired_count = len(expired)

    for job in expired:
        key = _norm(job.server_file_key)
        path = (QUEUE_ROOT / key) if key else None
        try:
            if path is not None and path.exists():
                bytes_freed += _path_size(path)
                if not dry_run:
                    _remove(path)
            deleted_files += 1
            if not dry_run:
                session.delete(job)
            deleted_rows += 1
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to purge job %s (%s): %s", job.id, key, e)
            errors += 1

    # Commit queued_job deletes before touching asr_recording_segments
    if not dry_run:
        session.commit()

    expired_segments = (
        session.exec(
            select(AsrRecordingSegment).where(AsrRecordingSegment.created_at < cutoff)
        ).all()
        if has_segment_table
        else []
    )
    expired_segments_count = len(expired_segments)

    for segment in expired_segments:
        key = _norm(segment.server_file_key)
        path = (ASR_SEGMENT_ROOT / key) if key else None
        try:
            if path is not None and path.exists():
                bytes_freed += _path_size(path)
                if not dry_run:
                    _remove(path)
                deleted_files += 1
            if not dry_run:
                session.delete(segment)
            deleted_rows += 1
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to purge ASR segment %s (%s): %s", segment.id, key, e)
            errors += 1

    # Commit segment deletes before querying surviving rows
    if not dry_run:
        session.commit()

    # Keys still referenced by a surviving row must NOT be swept as orphans.
    surviving_queue = {
        _norm(j.server_file_key)
        for j in session.exec(select(QueuedJob)).all()
        if j.server_file_key
    }
    surviving_segments = (
        {
            _norm(s.server_file_key)
            for s in session.exec(select(AsrRecordingSegment)).all()
            if s.server_file_key
        }
        if has_segment_table
        else set()
    )
    session.close()

    # Orphan sweep: files on disk, older than the cutoff, with no DB row.
    for root, surviving in ((QUEUE_ROOT, surviving_queue), (ASR_SEGMENT_ROOT, surviving_segments)):
        if not root.exists():
            continue
        for f in root.rglob("*"):
            if not f.is_file():
                continue
            rel = _norm(str(f.relative_to(root)))
            if rel in surviving:
                continue
            try:
                if f.stat().st_mtime >= cutoff_ts:
                    continue  # too recent — could be an in-flight upload; leave it
                bytes_freed += f.stat().st_size
                if not dry_run:
                    f.unlink()
                orphans_deleted += 1
            except OSError as e:
                logger.warning("Failed to remove orphan %s: %s", rel, e)
                errors += 1

    summary = {
        "ts": datetime.utcnow().isoformat() + "Z",
        "dry_run": dry_run,
        "retention_days": retention_days,
        "cutoff": cutoff.isoformat() + "Z",
        "expired_jobs": expired_count,
        "expired_segments": expired_segments_count,
        "deleted_files": deleted_files,
        "deleted_rows": deleted_rows,
        "orphans_deleted": orphans_deleted,
        "errors": errors,
        "mb_freed": round(bytes_freed / (1024 * 1024), 2),
    }

    # Compliance audit trail — every run is recorded, dry-run included.
    try:
        AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(AUDIT_LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(summary, ensure_ascii=False) + "\n")
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not write audit log: %s", e)

    return summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Purge expired audio recordings (NS 7-day retention rule)."
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Report what would be deleted without deleting anything.",
    )
    parser.add_argument(
        "--retention-days", type=int, default=None,
        help="Override the retention period (days). Default: config.json value.",
    )
    args = parser.parse_args()

    try:
        s = purge_expired(dry_run=args.dry_run, retention_days=args.retention_days)
    except Exception as e:  # noqa: BLE001
        logger.error("Purge failed: %s", e)
        return 1

    print(f"Audio purge {'DRY RUN — nothing deleted' if s['dry_run'] else 'COMPLETED'}")
    print(f"  Retention      : {s['retention_days']} days (cutoff {s['cutoff'][:10]})")
    print(f"  Expired jobs   : {s['expired_jobs']}")
    print(f"  Expired segments: {s.get('expired_segments', 0)}")
    print(f"  Files deleted  : {s['deleted_files']}")
    print(f"  Rows deleted   : {s['deleted_rows']}")
    print(f"  Orphans deleted: {s['orphans_deleted']}")
    print(f"  Space freed    : {s['mb_freed']} MB")
    print(f"  Errors         : {s['errors']}")
    return 1 if s["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
