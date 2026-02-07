# C:\Clinical-Note-Generator\server\routes\asr.py
from typing import Dict, Optional, Any, cast, List
import asyncio
import os
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse, JSONResponse
import aiohttp

router = APIRouter()


def _asr_url() -> Optional[str]:
    val = os.environ.get("ASR_URL")
    if not val:
        return None
    return val.strip().rstrip("/") or None

def _asr_fallback_url() -> Optional[str]:
    val = os.environ.get("ASR_URL_FALLBACK")
    if not val:
        return None
    return val.strip().rstrip("/") or None

def _asr_api_key() -> Optional[str]:
    return os.environ.get("ASR_API_KEY") or "notegenadmin"

_primary_down_until = 0.0
_cooldown_sec = 20.0


def _candidate_urls() -> List[str]:
    urls: List[str] = []
    now = time.time()
    primary = _asr_url()
    fallback = _asr_fallback_url()
    if primary and now >= _primary_down_until:
        urls.append(primary)
    if fallback and fallback not in urls:
        urls.append(fallback)
    return urls


def _mark_primary_down() -> None:
    global _primary_down_until
    if _asr_url():
        _primary_down_until = time.time() + _cooldown_sec


def _service_error_detail(primary: Optional[str], fallback: Optional[str], errors: List[str]) -> Dict[str, Any]:
    return {
        "service": "asr",
        "primary": primary,
        "fallback": fallback,
        "errors": errors,
    }


@router.post("/transcribe_diarized")
@router.post("/transcribe_diarized/")
async def transcribe_diarized(request: Request):
    """
    Proxy WhisperX transcription to external ASR service.
    Accepts audio file via multipart/form-data with field 'audio'.
    Returns plain text transcription with speaker labels.
    """
    primary = _asr_url()
    fallback = _asr_fallback_url()
    candidates = _candidate_urls()
    if not candidates:
        return JSONResponse(
            status_code=503,
            content={
                "error": "service_unavailable",
                "detail": _service_error_detail(primary, fallback, ["ASR_URL not set (and no fallback configured)."]),
            },
        )

    try:
        form = await request.form()
        audio = cast(Any, form.get('audio'))
        if not audio:
            raise HTTPException(status_code=400, detail="missing audio file")

        data = await audio.read() if hasattr(audio, 'read') else audio.file.read()
        filename = getattr(audio, 'filename', 'audio') or 'audio'
        content_type = getattr(audio, 'content_type', None) or 'application/octet-stream'

        errors: List[str] = []
        timeout = aiohttp.ClientTimeout(total=90)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            headers = {}
            api_key = _asr_api_key()
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            for base_url in candidates:
                try:
                    form_data = aiohttp.FormData()
                    form_data.add_field('audio', data, filename=filename, content_type=content_type)
                    async with session.post(f"{base_url}/transcribe_diarized", data=form_data, headers=headers) as resp:
                        if resp.status != 200:
                            txt = await resp.text()
                            raise RuntimeError(f"HTTP {resp.status}: {txt[:200]}")
                        text = await resp.text()
                        return PlainTextResponse(text)
                except Exception as e:
                    if base_url == primary:
                        _mark_primary_down()
                    errors.append(f"{base_url}: {str(e)[:200]}")
                    continue
        return JSONResponse(
            status_code=503,
            content={
                "error": "service_unavailable",
                "detail": _service_error_detail(primary, fallback, errors or ["ASR service failed."]),
            },
        )

    except HTTPException:
        raise
    except Exception as e:
        return JSONResponse(
            status_code=503,
            content={"error": "service_unavailable", "detail": _service_error_detail(primary, fallback, [str(e)[:200]])},
        )


@router.get("/asr_engine")
async def asr_engine_info() -> Dict[str, Optional[str]]:
    primary = _asr_url()
    fallback = _asr_fallback_url()
    candidates = _candidate_urls()
    if not candidates:
        return {"engine": "whisperx", "error": "ASR_URL not set (and no fallback configured)."}

    timeout = aiohttp.ClientTimeout(total=15)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            errors: List[str] = []
            for base_url in candidates:
                try:
                    async with session.get(f"{base_url}/asr_engine") as resp:
                        if resp.status != 200:
                            txt = await resp.text()
                            raise RuntimeError(f"HTTP {resp.status}: {txt[:200]}")
                        return await resp.json()
                except Exception as e:
                    if base_url == primary:
                        _mark_primary_down()
                    errors.append(f"{base_url}: {str(e)[:200]}")
                    continue
            return {"engine": "whisperx", "error": "; ".join(errors)[:200]}
    except Exception as e:
        return {"engine": "whisperx", "error": str(e)[:160]}
