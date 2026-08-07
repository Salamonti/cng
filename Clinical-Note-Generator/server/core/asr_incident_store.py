# server/core/asr_incident_store.py
from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

_log = logging.getLogger("cng.asr_incident_store")
# RLock (not Lock): _note_store_degraded() is called from *inside* the same
# critical section that holds _lock (a write just failed), so the counter
# increment and the write bookkeeping must be able to re-enter the lock.
_lock = threading.RLock()
_degraded_count = 0


def _incident_logging_enabled() -> bool:
    val = str(os.environ.get("ASR_INCIDENT_LOG", "") or "").strip().lower()
    if val in {"0", "false", "no", "off"}:
        return False
    # Default ON: operators can grep files / agents can read last_incident.json without manual copy/paste.
    return True


def _repo_root() -> Path:
    # server/core -> parents[2] == Clinical-Note-Generator package root (contains server/, pyproject, etc.)
    return Path(__file__).resolve().parents[2]


#: Number of incident-store write failures since process start. Exposed on
#: /health so an operator can see that the incident store (whose whole job is
#: recording failures) has itself degraded — otherwise "nothing records that
#: nothing recorded". Read via incident_store_degraded_count().
def incident_store_degraded_count() -> int:
    with _lock:
        return _degraded_count


def _note_store_degraded(reason: str) -> None:
    """A store write failed. Never raise (the incident store is best-effort by
    contract and must never take down the caller), but make the degradation
    VISIBLE: bump a thread-safe counter surfaced on /health and emit an error
    log. Stderr is deliberately used as the ultimate fallback channel too, in
    case the logging subsystem itself is the thing that is failing."""
    global _degraded_count
    with _lock:
        _degraded_count += 1
        count = _degraded_count
    try:
        _log.error("incident store DEGRADED: %s (degraded_count=%d)", reason, count)
    except Exception:
        pass
    try:
        print(f"[asr.incident] incident store DEGRADED: {reason} (degraded_count={count})", file=sys.stderr)
    except Exception:
        pass


def incident_dir() -> Path:
    # Intentionally NOT under Clinical-Note-Generator/data (gitignored) nor server/logs (gitignored).
    override = str(os.environ.get("ASR_INCIDENT_DIR") or "").strip()
    p = Path(override) if override else (_repo_root() / "asr_diagnostics")
    try:
        p.mkdir(parents=True, exist_ok=True)
    except Exception as e:  # directory unusable -> store cannot write; surface, never raise
        _note_store_degraded(f"incident_dir mkdir failed: {e!r}")
    return p


def record_sync_incident(*, outcome: str, payload: Optional[Dict[str, Any]] = None) -> None:
    """G0: workspace save / sync telemetry (reuses ASR incident store)."""
    record_asr_incident(
        trace_id="workspace-sync",
        stage="workspace_sync",
        outcome=outcome,
        payload=payload or {},
    )


def record_asr_incident(
    *,
    trace_id: str,
    stage: str,
    outcome: str,
    payload: Optional[Dict[str, Any]] = None,
) -> None:
    """Persist a structured incident record for later inspection (local file).

    NOTE: Never store raw audio bytes or secrets here.
    """
    if not _incident_logging_enabled():
        return
    tid = str(trace_id or "").strip() or "unknown"
    rec: Dict[str, Any] = {
        "ts_ms": int(time.time() * 1000),
        "trace_id": tid,
        "stage": str(stage or ""),
        "outcome": str(outcome or ""),
        "payload": payload if isinstance(payload, dict) else {},
    }
    line = json.dumps(rec, ensure_ascii=False, default=str) + "\n"
    d = incident_dir()
    log_path = d / "incidents.jsonl"
    last_path = d / "last_incident.json"

    try:
        with _lock:
            try:
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(line)
            except Exception as e:
                # PRIMARY write failed (JSONL append is the durable record).
                # Never propagate, but the whole point of this module is
                # recording failures — a lost primary write must be surfaced.
                _note_store_degraded(f"incidents.jsonl append failed: {e!r}")
            try:
                tmp = last_path.with_suffix(last_path.suffix + ".tmp")
                tmp.write_text(json.dumps(rec, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
                tmp.replace(last_path)
            except Exception as e:
                # Last incident is best-effort; JSONL append is primary. Fall
                # back to a direct write; only surface if that also fails.
                try:
                    last_path.write_text(json.dumps(rec, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
                except Exception as e2:
                    _note_store_degraded(f"last_incident.json write failed (tmp {e!r} / direct {e2!r})")
    except Exception as e:
        # Outer guard for anything unforeseen (e.g. a bug in the accounting
        # helper). Must never propagate.
        _note_store_degraded(f"unexpected record failure: {e!r}")
