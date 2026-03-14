# server/routes/rag_updates.py
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, HTTPException


router = APIRouter(prefix="/rag")

_CONFIG_CACHE: Dict[str, Any] | None = None
_SUMMARY_CACHE: Dict[str, Any] | None = None
_SUMMARY_CACHE_TS: Optional[datetime] = None
_RECENT_CACHE: Dict[str, Any] | None = None
_RECENT_CACHE_TS: Optional[datetime] = None


def _load_config() -> Dict[str, Any]:
    global _CONFIG_CACHE
    if _CONFIG_CACHE is None:
        cfg_path = Path(__file__).resolve().parents[2] / "config" / "config.json"
        try:
            with cfg_path.open("r", encoding="utf-8") as f:
                _CONFIG_CACHE = json.load(f)
        except Exception:
            _CONFIG_CACHE = {}
    return _CONFIG_CACHE


def _rag_root() -> Path:
    cfg = _load_config()
    custom = cfg.get("rag_root_dir")
    if isinstance(custom, str) and custom.strip():
        return Path(custom.strip())
    return Path(r"C:\RAG")


def _fetch_log_path() -> Path:
    cfg = _load_config()
    custom = cfg.get("rag_fetch_log_path")
    if isinstance(custom, str) and custom.strip():
        return Path(custom.strip())
    return _rag_root() / "fetch_log.jsonl"


def _raw_docs_root() -> Path:
    cfg = _load_config()
    custom = cfg.get("rag_raw_docs_dir")
    if isinstance(custom, str) and custom.strip():
        return Path(custom.strip())
    return _rag_root() / "raw_docs"


def _recent_updates_path() -> Path:
    cfg = _load_config()
    custom = cfg.get("rag_recent_updates_path")
    if isinstance(custom, str) and custom.strip():
        return Path(custom.strip())
    return _rag_root() / "recent_updates.json"


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    txt = ts.strip()
    if not txt:
        return None
    if txt.endswith("Z"):
        txt = txt[:-1] + "+00:00"
    try:
        dt_val = datetime.fromisoformat(txt)
        if dt_val.tzinfo is not None:
            dt_val = dt_val.astimezone(tz=None)
            dt_val = dt_val.replace(tzinfo=None)
        return dt_val
    except ValueError:
        return None


def _load_recent_entries(max_age_days: int = 7) -> List[Dict[str, Any]]:
    log_path = _fetch_log_path()
    if not log_path.exists():
        return []
    cutoff = datetime.now() - timedelta(days=max_age_days)
    entries: List[Dict[str, Any]] = []
    with log_path.open("r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = _parse_iso(obj.get("started")) or _parse_iso(obj.get("finished"))
            if ts and ts < cutoff:
                continue
            entries.append(obj)
    entries.sort(key=lambda x: _parse_iso(x.get("started")) or datetime.min)
    return entries


def _aggregate(entries: List[Dict[str, Any]]) -> Tuple[Dict[str, int], Dict[str, Any]]:
    totals: Dict[str, int] = defaultdict(int)
    latest_run: Dict[str, Any] = {}
    latest_ts = datetime.min
    for entry in entries:
        batches = entry.get("batches") or []
        for batch in batches:
            source = str(batch.get("source") or "unknown")
            if "kept" in batch:
                totals[source] += int(batch.get("kept") or 0)
            elif "count" in batch:
                totals[source] += int(batch.get("count") or 0)
        ts = _parse_iso(entry.get("started")) or _parse_iso(entry.get("finished"))
        if ts and ts > latest_ts:
            latest_ts = ts
            latest_run = entry
    return totals, latest_run


def _format_summary(entries: List[Dict[str, Any]], totals: Dict[str, int], latest_run: Dict[str, Any]) -> str:
    if not entries:
        return "No RAG ingestion runs recorded in the last seven days."
    start_ts = _parse_iso(entries[0].get("started")) or _parse_iso(entries[0].get("finished")) or datetime.now()
    end_ts = _parse_iso(entries[-1].get("finished")) or _parse_iso(entries[-1].get("started")) or start_ts
    total_docs = sum(totals.values())
    lines: List[str] = [
        f"Window: {start_ts.strftime('%Y-%m-%d')} - {end_ts.strftime('%Y-%m-%d')}.",
        f"Runs recorded: {len(entries)}; total documents processed: {total_docs}.",
    ]
    if totals:
        lines.append("Breakdown by source:")
        for source, count in sorted(totals.items()):
            lines.append(f"  - {source}: {count}")
    if latest_run:
        latest_started = latest_run.get("started", "unknown time")
        latest_status = latest_run.get("status", "unknown")
        lines.append(f"Most recent run started {latest_started} (status: {latest_status}).")
    return "\n".join(lines)


def _collect_recent_files(latest_run: Dict[str, Any]) -> List[Dict[str, Any]]:
    files: List[Dict[str, Any]] = []
    if not latest_run:
        return files
    root = _raw_docs_root()
    for batch in latest_run.get("batches") or []:
        rel_path = batch.get("file")
        if not rel_path:
            continue
        candidate = Path(rel_path)
        if not candidate.is_absolute():
            candidate = (root / rel_path).resolve()
        info: Dict[str, Any] = {
            "source": batch.get("source"),
            "path": str(candidate),
            "exists": candidate.exists(),
        }
        if candidate.exists():
            try:
                stat = candidate.stat()
                info["size_bytes"] = stat.st_size
                info["last_modified"] = datetime.fromtimestamp(stat.st_mtime).isoformat()
            except Exception:
                pass
        files.append(info)
    return files


@router.get("/weekly_summary/")
@router.get("/weekly_summary")
async def rag_weekly_summary() -> Dict[str, Any]:
    global _SUMMARY_CACHE, _SUMMARY_CACHE_TS
    now = datetime.now()
    if _SUMMARY_CACHE is not None and _SUMMARY_CACHE_TS and (now - _SUMMARY_CACHE_TS) < timedelta(minutes=15):
        return _SUMMARY_CACHE

    entries = _load_recent_entries()
    totals, latest_run = _aggregate(entries)
    summary = _format_summary(entries, totals, latest_run)
    recent_files = _collect_recent_files(latest_run)

    window_start = None
    window_end = None
    if entries:
        window_start = (
            _parse_iso(entries[0].get("started"))
            or _parse_iso(entries[0].get("finished"))
            or datetime.now()
        ).isoformat()
        window_end = (
            _parse_iso(entries[-1].get("finished"))
            or _parse_iso(entries[-1].get("started"))
            or datetime.now()
        ).isoformat()

    result = {
        "summary_text": summary,
        "window_start": window_start,
        "window_end": window_end,
        "run_count": len(entries),
        "totals": dict(totals),
        "runs": entries,
        "recent_files": recent_files,
        "generated_at": now.isoformat(),
    }
    _SUMMARY_CACHE = result
    _SUMMARY_CACHE_TS = now
    return result

def _load_recent_updates_data() -> Optional[Dict[str, Any]]:
    global _RECENT_CACHE, _RECENT_CACHE_TS
    path = _recent_updates_path()
    if not path.exists():
        _RECENT_CACHE = None
        _RECENT_CACHE_TS = None
        return None
    if _RECENT_CACHE is not None and _RECENT_CACHE_TS:
        try:
            ts = datetime.fromisoformat(_RECENT_CACHE.get('generated_at', ''))
        except Exception:
            ts = None
        if ts and ts == _RECENT_CACHE_TS:
            return _RECENT_CACHE
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        _RECENT_CACHE = None
        _RECENT_CACHE_TS = None
        return None
    generated_at = data.get('generated_at')
    try:
        cache_ts = datetime.fromisoformat(generated_at) if generated_at else datetime.min
    except Exception:
        cache_ts = datetime.min
    _RECENT_CACHE = data
    _RECENT_CACHE_TS = cache_ts
    return data


@router.get("/recent_updates")
async def rag_recent_updates() -> Dict[str, Any]:
    data = _load_recent_updates_data()
    if not data:
        raise HTTPException(
            status_code=503,
            detail="Recent updates cache not available. Run summarize_recent_updates.py after the weekly ingest.",
        )
    generated_at = data.get("generated_at")
    if generated_at:
        try:
            ts = datetime.fromisoformat(generated_at)
            if ts < datetime.now() - timedelta(days=7):
                raise HTTPException(
                    status_code=503,
                    detail="Recent updates cache is older than 7 days. Regenerate summaries.",
                )
        except Exception:
            pass
    return data
