import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict


_append_lock = threading.Lock()


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _dataset_dir() -> Path:
    custom = os.environ.get("CNG_DATASET_DIR", "").strip()
    if custom:
        d = Path(custom)
    else:
        d = _repo_root() / "data" / "datasets"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _append_jsonl(path: Path, record: Dict[str, Any]) -> None:
    line = json.dumps(record, ensure_ascii=False)
    with _append_lock:
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")


def log_case_record(record: Dict[str, Any]) -> str:
    path = _dataset_dir() / f"cases_{_today_utc()}.jsonl"
    _append_jsonl(path, record)
    return str(path)


def log_case_event(event: Dict[str, Any]) -> str:
    path = _dataset_dir() / f"case_events_{_today_utc()}.jsonl"
    _append_jsonl(path, event)
    return str(path)

