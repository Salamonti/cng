# server/core/service_endpoints.py
"""Load project-root service_endpoints.json into os.environ (defaults only).

See repository root service_endpoints.json — single operator-facing source for URLs/ports.
Override with real environment variables when needed (CI, containers).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict


def _project_root() -> Path:
    # server/core/service_endpoints.py -> parents[2] = Clinical-Note-Generator, [3] = repo root
    return Path(__file__).resolve().parents[3]


def service_endpoints_path() -> Path:
    override = os.environ.get("SERVICE_ENDPOINTS_PATH", "").strip()
    if override:
        return Path(override)
    return _project_root() / "service_endpoints.json"


def load_service_endpoints() -> Dict[str, Any]:
    p = service_endpoints_path()
    if not p.is_file():
        return {}
    try:
        with p.open(encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def apply_service_endpoints() -> None:
    """Apply defaults from service_endpoints.json (setdefault — explicit env wins)."""
    data = load_service_endpoints()
    env_block = data.get("env")
    if isinstance(env_block, dict):
        for key, val in env_block.items():
            if not isinstance(key, str) or key.startswith("_"):
                continue
            if val is None:
                continue
            s = str(val).strip()
            if not s:
                continue
            if key not in os.environ:
                os.environ[key] = s
    fp = data.get("fastapi_port")
    if fp is not None and "FASTAPI_PORT" not in os.environ:
        try:
            os.environ["FASTAPI_PORT"] = str(int(fp))
        except (TypeError, ValueError):
            pass
