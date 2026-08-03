#!/usr/bin/env python3
"""Rotate incidents.jsonl — archive entries older than 7 days, keep the file bounded.

Usage:
    python3 rotate_incidents.py /path/to/incidents.jsonl

Keeps the last 7 days in-place, moves older entries to incidents.<YYYY-MM>.jsonl archives.
Generates a daily digest with counts grouped by stage and outcome.

Designed to run as a daily cron job.
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from collections import Counter
from pathlib import Path


def parse_ts(entry: dict) -> float:
    """Extract timestamp in seconds from an incident entry."""
    ts = entry.get("ts_ms")
    if ts is not None:
        return float(ts) / 1000.0
    return 0.0


def rotate(incidents_path: str, max_age_days: int = 7):
    path = Path(incidents_path)
    if not path.exists():
        print(f"No incidents file at {path}")
        return

    now = datetime.now(tz=timezone.utc)
    cutoff = (now - timedelta(days=max_age_days)).timestamp()
    digest_dir = path.parent / "digests"
    digest_dir.mkdir(parents=True, exist_ok=True)

    entries = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    if not entries:
        print("No entries to process")
        return

    # Split: recent vs old
    recent = []
    old = []
    for e in entries:
        if parse_ts(e) >= cutoff:
            recent.append(e)
        else:
            old.append(e)

    # Archive old entries by month
    if old:
        old_by_month = {}
        for e in old:
            ts = parse_ts(e)
            if ts > 0:
                month_key = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m")
            else:
                month_key = "unknown"
            old_by_month.setdefault(month_key, []).append(e)

        for month_key, month_entries in old_by_month.items():
            archive_path = path.parent / f"incidents.{month_key}.jsonl"
            with open(archive_path, "a", encoding="utf-8") as f:
                for e in month_entries:
                    f.write(json.dumps(e, ensure_ascii=False) + "\n")
            print(f"Archived {len(month_entries)} entries -> {archive_path}")

    # Write back recent entries
    with open(path, "w", encoding="utf-8") as f:
        for e in recent:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")

    # Generate daily digest
    today_str = now.strftime("%Y-%m-%d")
    today_entries = [e for e in entries if datetime.fromtimestamp(parse_ts(e), tz=timezone.utc).strftime("%Y-%m-%d") == today_str]

    stage_counts = Counter(e.get("stage", "unknown") for e in today_entries)
    outcome_counts = Counter(e.get("outcome", "unknown") for e in today_entries)
    errors = [
        e for e in today_entries
        if e.get("outcome") in ("error", "failed_all_candidates", "aborted", "unhandled")
    ]

    digest = {
        "date": today_str,
        "total": len(today_entries),
        "recent_kept": len(recent),
        "old_archived": len(old),
        "by_stage": dict(stage_counts),
        "by_outcome": dict(outcome_counts),
        "errors": errors[:20],  # cap at 20 for digest readability
    }

    # Write digest
    digest_path = digest_dir / f"digest_{today_str}.json"
    with open(digest_path, "w", encoding="utf-8") as f:
        json.dump(digest, f, indent=2, ensure_ascii=False)
    print(f"Digest written -> {digest_path}")

    # Alert if error count exceeds threshold
    error_count = sum(1 for e in today_entries if e.get("outcome") in ("error", "failed_all_candidates"))
    if error_count > 10:
        print(f"ALERT: {error_count} ASR errors today - review digest at {digest_path}")

    print(f"Summary: {len(recent)} recent entries kept, {len(old)} archived, {len(today_entries)} today ({error_count} errors)")


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "asr_diagnostics/incidents.jsonl"
    rotate(path)
