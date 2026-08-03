"""ASR capture modes exposed to the client settings UI."""
from __future__ import annotations

from fastapi import APIRouter

from server.core.feature_flags import ASR_REFINE_ENABLED, CHUNK_ASR_ENABLED

router = APIRouter(prefix="/api/asr", tags=["asr-modes"], redirect_slashes=False)


def _diarization_health_ok() -> bool:
    """Check if the LLM endpoint for diarization is actually reachable."""
    import requests as _requests
    from server.core.llm_routing import resolve_llm_urls

    base_url, _fallback = resolve_llm_urls("asr_refine")
    if not base_url:
        return False
    try:
        resp = _requests.get(f"{base_url.rstrip('/')}/v1/models", timeout=3)
        return resp.status_code == 200
    except Exception:
        return False


def _modes_payload() -> dict:
    refine_available = ASR_REFINE_ENABLED
    return {
        "chunk_asr_enabled": CHUNK_ASR_ENABLED,
        "refine_available": refine_available,
        "diarization_available": refine_available and _diarization_health_ok(),
        "profiles": ["asr_only", "asr_diarize"],
        "capture_modes": ["batch", "chunk"],
    }


@router.get("/modes")
def get_asr_modes() -> dict:
    return _modes_payload()


# Backward-compatible alias for older clients during rollout.
@router.get("/capabilities")
def get_asr_capabilities_compat() -> dict:
    payload = _modes_payload()
    payload["streaming_enabled"] = False
    payload["vad_enabled"] = False
    return payload
