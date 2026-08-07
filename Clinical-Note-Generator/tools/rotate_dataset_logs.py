"""rotate_dataset_logs.py - M-3 retention enforcement for dataset JSONL stores.

`data/datasets/cases_*.jsonl` / `case_events_*.jsonl` accumulate one
date-stamped file per day and are never deleted -- an unbounded, ever-growing
PHI-adjacent store. Likewise the quarantine store introduced by Step 8
(cases_quarantine_*.jsonl). And `server/logs/rag_missed_questions.jsonl` is a
single unbounded file the app appends to without ever rotating.

Run nightly via dreamcision-dataset-retention.timer. Deletes dataset files
older than DATASET_RETENTION_DAYS (default 90), and rotates
rag_missed_questions.jsonl when it exceeds RAG_MISSED_MAX_BYTES (default 100MB).
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from server.core.logging.dataset_logger import _dataset_dir  # noqa: E402


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


def _logs_dir() -> Path:
    return PROJECT_ROOT / "server" / "logs"


def purge_dataset_files() -> int:
    retention_days = _int_env("DATASET_RETENTION_DAYS", 90)
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    cutoff_str = cutoff.strftime("%Y-%m-%d")
    deleted = 0
    d = _dataset_dir()
    for p in sorted(d.glob("*.jsonl")):
        # Filenames carry a YYYY-MM-DD date (cases_2026-08-03.jsonl). Compare
        # that embedded date; fall back to mtime if the name has no date.
        date_str = None
        for token in p.name.split("_"):
            # token may be like "2026-08-03.jsonl"
            t = token.rsplit(".jsonl", 1)[0]
            if len(t) == 10 and t[4] == "-" and t[7] == "-":
                date_str = t
                break
        if date_str is None:
            if p.stat().st_mtime < cutoff.timestamp():
                try:
                    p.unlink()
                    deleted += 1
                except OSError:
                    pass
            continue
        if date_str < cutoff_str:
            try:
                p.unlink()
                deleted += 1
            except OSError:
                pass
    return deleted


def rotate_missed_questions() -> bool:
    """Rotate rag_missed_questions.jsonl past a size cap via copytruncate-free
    manual rename (the app reopens by path on each append, so renaming is safe
    and logrotate's copytruncate is unnecessary complexity here)."""
    cap = _int_env("RAG_MISSED_MAX_BYTES", 100 * 1024 * 1024)
    p = _logs_dir() / "rag_missed_questions.jsonl"
    if not p.exists():
        return False
    if p.stat().st_size < cap:
        return False
    ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    rotated = p.with_name(f"rag_missed_questions.{ts}.jsonl.old")
    try:
        p.rename(rotated)
        # Keep the last RAG_MISSED_ROTATIONS old files (default 10).
        keep = _int_env("RAG_MISSED_ROTATIONS", 10)
        olds = sorted(_logs_dir().glob("rag_missed_questions.*.jsonl.old"))
        for stale in olds[:-keep]:
            stale.unlink()
    except OSError:
        return False
    return True


def main() -> int:
    deleted = purge_dataset_files()
    rotated = rotate_missed_questions()
    print(
        f"Dataset retention: deleted {deleted} file(s) old than "
        f"{_int_env('DATASET_RETENTION_DAYS', 90)}d; "
        f"rag_missed_questions rotated={rotated}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
