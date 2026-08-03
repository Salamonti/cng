"""CORS policy for FastAPI (tighten from allow-everywhere in production)."""
from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional, Tuple


_DEFAULT_REGEX = (
    r"^https?://"  # http(s) only
    r"("
    r"127\.0\.0\.1(:\d+)?"
    r"|localhost(:\d+)?"
    r"|([\w-]+\.)*ieissa\.(com|ca)(:\d+)?"
    r"|([\w-]+\.)*eissa\.ca(:\d+)?"
    r")$"
)


def build_cors_params() -> Tuple[List[str], bool, Optional[str]]:
    """Return (allow_origins, allow_credentials, allow_origin_regex).

    - ``CORS_ALLOW_ALL=1`` restores permissive dev behavior (origins *, credentials off).
    - ``CORS_ALLOWED_ORIGINS`` comma-separated explicit origins (e.g. https://notes.ieissa.com:3443).
    - ``CORS_ALLOW_ORIGIN_REGEX`` overrides the default host regex when set (non-empty).
    """
    if os.environ.get("CORS_ALLOW_ALL", "").strip().lower() in {"1", "true", "yes", "on"}:
        return ["*"], False, None

    raw = os.environ.get("CORS_ALLOWED_ORIGINS", "").strip()
    origins = [o.strip() for o in raw.split(",") if o.strip()]

    rx = os.environ.get("CORS_ALLOW_ORIGIN_REGEX", "").strip()
    if not rx:
        rx = _DEFAULT_REGEX
    # Fail safe: invalid regex falls back to localhost-only
    try:
        re.compile(rx)
    except re.error:
        rx = r"^https?://(127\.0\.0\.1|localhost)(:\d+)?$"

    return origins, True, rx


def cors_middleware_kwargs() -> Dict[str, Any]:
    origins, creds, rx = build_cors_params()
    return {
        "allow_origins": origins,
        "allow_credentials": creds,
        "allow_origin_regex": rx,
        "allow_methods": ["*"],
        "allow_headers": ["*"],
        "expose_headers": ["X-Generation-Id", "X-ASR-Trace-Id"],
    }
