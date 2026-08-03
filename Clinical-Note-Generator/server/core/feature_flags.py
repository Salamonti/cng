# server/core/feature_flags.py
"""Runtime feature flags (env). Default OFF until operator enables; safety fixes may default ON."""
from __future__ import annotations

import os


def env_flag(name: str, default: bool = False) -> bool:
    raw = str(os.environ.get(name, "") or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


SYNC_BEACON_FIX = env_flag("SYNC_BEACON_FIX", True)
SYNC_AUTH_FETCH = env_flag("SYNC_AUTH_FETCH", True)
SYNC_ANTI_STOMP = env_flag("SYNC_ANTI_STOMP", True)
ASR_RECORDING_RECONCILE = env_flag("ASR_RECORDING_RECONCILE", True)
ASR_PIPELINE_REFRESH = env_flag("ASR_PIPELINE_REFRESH", True)
CHUNK_ASR_ENABLED = env_flag("CHUNK_ASR_ENABLED", False)
ASR_REFINE_ENABLED = env_flag("ASR_REFINE_ENABLED", True)
