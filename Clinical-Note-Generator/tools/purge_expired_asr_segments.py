"""purge_expired_asr_segments.py - P1-7 retention enforcement.

AsrRecordingSegment.expires_at is set on every upload (RETENTION_DAYS,
7 days by default) but nothing previously deleted a segment once it
passed that timestamp -- the field was descriptive, not enforced. Run
nightly via dreamcision-asr-retention.timer.

This is deliberately a real scheduled job rather than the encounter TTL
prune's opportunistic on-access style: a segment's retention clock is
independent of its parent encounter's (an encounter can stay active far
longer than 7 days while individual old segments inside it should still
expire on their own schedule), so it can't ride along on encounter access
the way encounter deletion does.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlmodel import Session, select  # noqa: E402

import server.core.db as db  # noqa: E402
from server.models.asr_recording_segment import AsrRecordingSegment  # noqa: E402
from server.routes.asr_segments import (  # noqa: E402
    delete_asr_segment_file,
    get_asr_segment_storage_root,
)


def purge_expired_segments() -> int:
    """Delete every AsrRecordingSegment row (and its audio file) whose
    expires_at has passed. Returns the count deleted.

    Reads db.engine via the module (not `from server.core.db import
    engine`) so tests that swap db.engine for an isolated per-test
    database are respected -- a direct-name import would bind to
    whatever engine existed at first import and never see the swap.
    """
    db.init_db()
    now = datetime.utcnow()
    deleted = 0
    with Session(db.engine) as session:
        expired = session.exec(
            select(AsrRecordingSegment).where(
                AsrRecordingSegment.expires_at.is_not(None),
                AsrRecordingSegment.expires_at < now,
            )
        ).all()
        for segment in expired:
            delete_asr_segment_file(segment.server_file_key)
            session.delete(segment)
            deleted += 1
        session.commit()
    return deleted


def main() -> None:
    root = get_asr_segment_storage_root()
    print(f"[{datetime.utcnow().isoformat()}] Purging expired ASR segments under {root}")
    deleted = purge_expired_segments()
    print(f"[{datetime.utcnow().isoformat()}] Deleted {deleted} expired segment(s)")


if __name__ == "__main__":
    main()
