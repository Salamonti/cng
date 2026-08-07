# server/routes/rag_updates.py
from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter


router = APIRouter(prefix="/rag")

logger = logging.getLogger("cng.rag_updates")

_CONFIG_CACHE: Dict[str, Any] | None = None
_SUMMARY_CACHE: Dict[str, Any] | None = None
_SUMMARY_CACHE_TS: Optional[datetime] = None
_RECENT_CACHE: Dict[str, Any] | None = None
_RECENT_CACHE_TS: Optional[datetime] = None
_RECENT_CACHE_FILE_TOKEN: Optional[Tuple[int, int]] = None  # (mtime_ns, size) to detect disk changes


def _load_config() -> Dict[str, Any]:
    global _CONFIG_CACHE
    if _CONFIG_CACHE is None:
        cfg_path = Path(__file__).resolve().parents[2] / "config" / "config.json"
        try:
            with cfg_path.open("r", encoding="utf-8") as f:
                _CONFIG_CACHE = json.load(f)
        except Exception:
            # Corrupt/unreadable *present* config: fall back to defaults rather
            # than break RAG endpoints, but surface the misconfiguration instead
            # of swallowing it invisibly. (Missing config stays silent - normal.)
            logger.warning("Failed to load RAG config/config.json; using defaults", exc_info=True)
            _CONFIG_CACHE = {}
    return _CONFIG_CACHE


def _rag_root() -> Path:
    cfg = _load_config()
    custom = cfg.get("rag_root_dir")
    if isinstance(custom, str) and custom.strip():
        return Path(custom.strip())
    # Monorepo default: <repo>/RAG next to Clinical-Note-Generator (replaces legacy C:\RAG-only installs).
    cng_root = Path(__file__).resolve().parents[2]
    sibling = cng_root.parent / "RAG"
    if sibling.is_dir():
        return sibling
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
    cutoff = datetime.utcnow() - timedelta(days=max_age_days)
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
    start_ts = _parse_iso(entries[0].get("started")) or _parse_iso(entries[0].get("finished")) or datetime.utcnow()
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


_IMPACT_TITLE_RE = re.compile(
    r"randomi[sz]ed|clinical\s+trial|rct\b|meta-?analysis|systematic\s+review|"
    r"double-?blind|placebo-?controlled|multicenter|multicentre|guideline|"
    r"cohort\s+study|jama\b|nejm|lancet|bmj\b|annals\s+of\s+internal",
    re.I,
)


def _looks_like_letter_title(title: str) -> bool:
    lower = title.strip().lower()
    if not lower:
        return False
    if "letter to the editor" in lower:
        return True
    if lower.startswith("letter to "):
        return True
    if lower.startswith("letter:"):
        return True
    if lower.startswith("reply to letter"):
        return True
    return False


def _impact_score(title: str) -> int:
    if not title:
        return 0
    return 10 if _IMPACT_TITLE_RE.search(title) else 0


def _display_title(doc: Dict[str, Any]) -> str:
    t = str(doc.get("title") or "").strip()
    if t and t.lower() != "login":
        return t
    c = str(doc.get("citation") or "").strip()
    if c:
        return c[:240]
    return "Untitled record"


def _snippet_from_doc(doc: Dict[str, Any], max_chars: int = 560) -> str:
    preferred = ("abstract", "text", "summary", "description", "content")
    for key in preferred:
        val = doc.get(key)
        if isinstance(val, str) and val.strip():
            s = " ".join(val.split())
            return s[:max_chars] + ("…" if len(s) > max_chars else "")
    journal = str(doc.get("journal") or "").strip()
    date = str(doc.get("date") or "").strip()
    cond = doc.get("conditions")
    bits: List[str] = []
    if journal:
        bits.append(journal)
    if date and date.isdigit() is False and len(date) < 40:
        bits.append(date)
    if isinstance(cond, list) and cond:
        bits.append("Conditions: " + ", ".join(str(x) for x in cond[:6]))
    elif isinstance(cond, str) and cond.strip():
        bits.append(cond.strip())
    soc = str(doc.get("society") or "").strip()
    if soc:
        bits.append(soc)
    line = " · ".join(bits)
    if line:
        return line[:max_chars]
    return "Open the link for the full record."


def _choose_weekly_ingest_entry(entries: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not entries:
        return None
    candidates: List[Dict[str, Any]] = []
    for entry in entries:
        days = entry.get("days")
        if isinstance(days, int) and days >= 7:
            candidates.append(entry)
    if candidates:
        return sorted(candidates, key=lambda e: str(e.get("started") or ""))[-1]
    return entries[-1]


def _load_all_fetch_log_entries() -> List[Dict[str, Any]]:
    log_path = _fetch_log_path()
    if not log_path.exists():
        return []
    out: List[Dict[str, Any]] = []
    with log_path.open("r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _parse_batch_file(doc_path: Path) -> List[Dict[str, Any]]:
    if not doc_path.exists():
        return []
    suf = doc_path.suffix.lower()
    if suf == ".jsonl":
        rows: List[Dict[str, Any]] = []
        with doc_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return rows
    try:
        raw = json.loads(doc_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    if isinstance(raw, list):
        return [x for x in raw if isinstance(x, dict)]
    if isinstance(raw, dict):
        return [raw]
    return []


def _build_documents_from_ingest_batches(entry: Dict[str, Any], max_docs: int = 50) -> List[Dict[str, Any]]:
    """When recent_updates.json is missing, show real titles + snippets from the same raw files the summarizer uses."""
    rag_root = _rag_root()
    scored: List[Tuple[int, Dict[str, Any]]] = []
    seen: set[str] = set()

    for batch in entry.get("batches") or []:
        rel = batch.get("file")
        if not rel:
            continue
        p = Path(str(rel))
        if not p.is_absolute():
            p = (rag_root / p).resolve()
        batch_source = str(batch.get("source") or "unknown")
        for doc in _parse_batch_file(p):
            title = _display_title(doc)
            if _looks_like_letter_title(title):
                continue
            if title.strip().lower() == "login":
                continue
            source = str(doc.get("source") or batch_source)
            doc_id = str(doc.get("id") or doc.get("link") or title)[:200]
            dedupe_key = f"{source}:{doc_id}"
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            link = doc.get("link")
            score = _impact_score(title) + (2 if source in ("pubmed", "guidelines", "clinicaltrials") else 0)
            snippet = _snippet_from_doc(doc)
            summary = f"{snippet}" if snippet else "Open the source link for details."
            scored.append(
                (
                    score,
                    {
                        "title": title,
                        "source": source,
                        "link": link,
                        "summary": summary,
                    },
                )
            )

    scored.sort(key=lambda x: (-x[0], x[1]["title"].lower()))
    return [item[1] for item in scored[:max_docs]]


def _recent_updates_has_documents(data: Optional[Dict[str, Any]]) -> bool:
    if not data:
        return False
    docs = data.get("documents")
    return isinstance(docs, list) and len(docs) > 0


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
                # Best-effort file metadata enrichment (stat race / perms): on
                # failure omit size/last_modified rather than break the listing.
                pass
        files.append(info)
    return files


@router.get("/weekly_summary/")
@router.get("/weekly_summary")
async def rag_weekly_summary() -> Dict[str, Any]:
    global _SUMMARY_CACHE, _SUMMARY_CACHE_TS
    now = datetime.utcnow()
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
            or datetime.utcnow()
        ).isoformat()
        window_end = (
            _parse_iso(entries[-1].get("finished"))
            or _parse_iso(entries[-1].get("started"))
            or datetime.utcnow()
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
    """Load RAG/recent_updates.json. Reload from disk whenever the file changes (mtime/size).

    Previous logic cached forever after the first read, so weekly jobs that update the file on
    disk were invisible until the FastAPI process restarted.
    """
    global _RECENT_CACHE, _RECENT_CACHE_TS, _RECENT_CACHE_FILE_TOKEN
    path = _recent_updates_path()
    if not path.exists():
        _RECENT_CACHE = None
        _RECENT_CACHE_TS = None
        _RECENT_CACHE_FILE_TOKEN = None
        return None
    try:
        st = path.stat()
        token: Tuple[int, int] = (int(st.st_mtime_ns), int(st.st_size))
    except OSError:
        token = (0, 0)
    if (
        _RECENT_CACHE is not None
        and _RECENT_CACHE_FILE_TOKEN == token
        and _RECENT_CACHE_TS is not None
    ):
        return _RECENT_CACHE
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        # Corrupt/unreadable recent-report cache -> treat as a cache miss and
        # recompute/regenerate rather than serve stale or crash the endpoint.
        # (Absent file is the normal first-run case.) Regenerable, so silent.
        _RECENT_CACHE = None
        _RECENT_CACHE_TS = None
        _RECENT_CACHE_FILE_TOKEN = None
        return None
    generated_at = data.get('generated_at')
    try:
        cache_ts = datetime.fromisoformat(generated_at) if generated_at else datetime.min
    except Exception:
        # Unparseable timestamp -> treat as oldest-possible so the entry is
        # recomputed; safe parse guard.
        cache_ts = datetime.min
    _RECENT_CACHE = data
    _RECENT_CACHE_TS = cache_ts
    _RECENT_CACHE_FILE_TOKEN = token
    return data


def _empty_recent_updates_message() -> Dict[str, Any]:
    return {
        "generated_at": datetime.utcnow().isoformat(),
        "fallback": "empty",
        "documents": [
            {
                "title": "No literature batch found",
                "source": "RAG",
                "summary": "Run the weekly ingest, then `RAG/summarize_recent_updates.py` (requires llama on 127.0.0.1:8081) for AI-written digests.",
            }
        ],
    }


@router.get("/recent_updates")
async def rag_recent_updates() -> Dict[str, Any]:
    data = _load_recent_updates_data()
    if _recent_updates_has_documents(data):
        assert data is not None
        generated_at = data.get("generated_at")
        if generated_at:
            try:
                tstr = str(generated_at).replace("Z", "+00:00")
                ts = datetime.fromisoformat(tstr)
                if ts.tzinfo is not None:
                    ts = ts.astimezone(tz=None).replace(tzinfo=None)
                if ts < datetime.utcnow() - timedelta(days=7):
                    data = {**data, "stale": True}
            except Exception:
                pass
        return data

    entries = _load_all_fetch_log_entries()
    target = _choose_weekly_ingest_entry(entries) if entries else None
    if target:
        ingest_docs = _build_documents_from_ingest_batches(target, max_docs=50)
        if ingest_docs:
            return {
                "generated_at": datetime.utcnow().isoformat(),
                "run_started": target.get("started"),
                "fallback": "ingest_snippets",
                "documents": ingest_docs,
            }
    return _empty_recent_updates_message()
