# C:\RAG\log_utils.py
"""
Helper utilities for maintaining fetch_log.jsonl.

Provides append functionality that keeps the log trimmed to a rolling
window (default 7 days) and helper parsing utilities shared across
fetchers.
"""

from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

MAX_AGE_DAYS_DEFAULT = 7


def _parse_timestamp(obj: Dict[str, Any]) -> Optional[_dt.datetime]:
    """Best-effort extraction of a timestamp from a log entry."""
    for key in ("started", "finished", "timestamp"):
        val = obj.get(key)
        if not val:
            continue
        if isinstance(val, (int, float)):
            # treat as unix seconds
            try:
                return _dt.datetime.fromtimestamp(float(val))
            except Exception:
                continue
        if isinstance(val, str):
            txt = val.strip()
            if not txt:
                continue
            if txt.endswith("Z"):
                txt = txt[:-1] + "+00:00"
            try:
                ts = _dt.datetime.fromisoformat(txt)
                if ts.tzinfo is not None:
                    ts = ts.astimezone(_dt.timezone.utc).replace(tzinfo=None)
                return ts
            except Exception:
                continue
    return None


def _should_keep(entry: Dict[str, Any], cutoff: _dt.datetime) -> bool:
    ts = _parse_timestamp(entry)
    if ts is None:
        # If we cannot parse the timestamp, keep the entry to avoid data loss.
        return True
    return ts >= cutoff


def _iter_existing_lines(path: Path) -> Iterable[Union[Dict[str, Any], str]]:
    if not path.exists():
        return ()
    results: List[Union[Dict[str, Any], str]] = []
    with path.open("r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line:
                continue
            try:
                results.append(json.loads(line))
            except Exception:
                # Preserve unknown content verbatim.
                results.append(line)
    return results


def append_recent_log(entry: Dict[str, Any], log_path: Path, *, max_age_days: int = MAX_AGE_DAYS_DEFAULT) -> None:
    """Append a JSON entry while keeping only the most recent window."""
    cutoff = _dt.datetime.now() - _dt.timedelta(days=max_age_days)
    kept: List[Union[Dict[str, Any], str]] = []
    for existing in _iter_existing_lines(log_path):
        if isinstance(existing, dict):
            if _should_keep(existing, cutoff):
                kept.append(existing)
        else:
            # Preserve raw lines (e.g., manual notes) regardless of age.
            kept.append(existing)
    kept.append(entry)
    with log_path.open("w", encoding="utf-8") as f:
        for item in kept:
            if isinstance(item, str):
                f.write(item + "\n")
            else:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")


def load_recent_entries(log_path: Path, *, max_age_days: int = MAX_AGE_DAYS_DEFAULT) -> List[Dict[str, Any]]:
    """Return parsed log entries within the rolling window."""
    cutoff = _dt.datetime.now() - _dt.timedelta(days=max_age_days)
    results: List[Dict[str, Any]] = []
    for item in _iter_existing_lines(log_path):
        if isinstance(item, dict):
            if _should_keep(item, cutoff):
                results.append(item)
    results.sort(key=lambda x: _parse_timestamp(x) or _dt.datetime.min)
    return results
