import os
import platform
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

import fastapi
import uvicorn

router = APIRouter()

# Build stamp is captured once when the process imports this module.
BUILD_TIMESTAMP_UTC = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _resolve_commit_hash() -> str:
    try:
        repo_root = Path(__file__).resolve().parents[3]
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_root),
            stderr=subprocess.DEVNULL,
            timeout=2,
        )
        commit = out.decode("utf-8", errors="ignore").strip()
        return commit or "unknown"
    except Exception:
        return "unknown"


COMMIT_HASH = _resolve_commit_hash()


@router.get("/version")
def version() -> JSONResponse:
    payload: Dict[str, Any] = {
        "commit_hash": COMMIT_HASH,
        "build_timestamp_utc": BUILD_TIMESTAMP_UTC,
        "versions": {
            "python": platform.python_version(),
            "fastapi": getattr(fastapi, "__version__", "unknown"),
            "uvicorn": getattr(uvicorn, "__version__", "unknown"),
        },
        "environment": os.environ.get("ENV", "unknown"),
    }
    return JSONResponse(
        content=payload,
        headers={
            "Cache-Control": "no-store",
            "Pragma": "no-cache",
        },
    )
