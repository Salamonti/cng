# server/routes/perf.py
import time
import json
from pathlib import Path
from typing import Dict

from fastapi import APIRouter
from metrics import metrics as global_metrics


router = APIRouter()

start_time = time.time()


@router.get("/health")
def health() -> Dict:
    return {
        "status": "ok",
        "uptime_sec": int(time.time() - start_time),
    }


@router.get("/performance")
def performance() -> Dict:
    if global_metrics is None:
        return {"error": "metrics not initialized"}
    return global_metrics.snapshot()


# Public, read-only subset of configuration for client UIs
def _load_cfg() -> Dict:
    try:
        cfg_path = Path(__file__).resolve().parents[2] / "config" / "config.json"
        if cfg_path.exists():
            with open(cfg_path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


@router.get("/qa_config")
def qa_config() -> Dict:
    cfg = _load_cfg()
    return {
        # Input length allowed in QA text area
        "qa_max_user_chars": int(cfg.get("qa_max_user_chars", 1024)),
        # Server-side defaults used for QA responses
        "default_qa_max_tokens": int(cfg.get("default_qa_max_tokens", 512)),
        "qa_context_length": int(cfg.get("qa_context_length", 4096)),
    }
