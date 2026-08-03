# server/core/asr_incident_store.py
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

_lock = threading.Lock()


def _incident_logging_enabled() -> bool:
    val = str(os.environ.get("ASR_INCIDENT_LOG", "") or "").strip().lower()
    if val in {"0", "false", "no", "off"}:
        return False
    # Default ON: operators can grep files / agents can read last_incident.json without manual copy/paste.
    return True


def _repo_root() -> Path:
    # server/core -> parents[2] == Clinical-Note-Generator package root (contains server/, pyproject, etc.)
    return Path(__file__).resolve().parents[2]


def incident_dir() -> Path:
    # Intentionally NOT under Clinical-Note-Generator/data (gitignored) nor server/logs (gitignored).
    override = str(os.environ.get("ASR_INCIDENT_DIR") or "").strip()
    p = Path(override) if override else (_repo_root() / "asr_diagnostics")
    try:
        p.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
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
            except Exception:
                pass
            try:
                tmp = last_path.with_suffix(last_path.suffix + ".tmp")
                tmp.write_text(json.dumps(rec, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
                tmp.replace(last_path)
            except Exception:
                # Last incident is best-effort; JSONL append is primary.
                try:
                    last_path.write_text(json.dumps(rec, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
                except Exception:
                    pass
    except Exception:
        pass
