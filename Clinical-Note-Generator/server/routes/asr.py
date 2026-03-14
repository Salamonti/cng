# server/routes/asr.py
from typing import Dict, Optional, Any, cast, List
import os
import time
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Depends
from fastapi.responses import PlainTextResponse, JSONResponse
import aiohttp

from server.core.dependencies import require_api_bearer

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

def _whispercpp_vad() -> str:
    return (os.environ.get("ASR_WHISPERCPP_VAD") or "0").strip() or "0"

def _whispercpp_no_speech_thold() -> str:
    return (os.environ.get("ASR_WHISPERCPP_NO_SPEECH_THOLD") or "1.0").strip() or "1.0"

def _normalize_to_wav_enabled() -> bool:
    # Default OFF: whisper.cpp handles webm/mp3 natively; avoid unnecessary re-encoding side-effects.
    val = (os.environ.get("ASR_NORMALIZE_TO_WAV") or "0").strip().lower()
    return val not in {"0", "false", "no", "off"}

_primary_down_until = 0.0
_cooldown_sec = 20.0
_rr_counter = 0


def _candidate_urls() -> List[str]:
    """Return candidate ASR URLs with light load-spread + fallback behavior.

    - If primary is in cooldown, prefer fallback only.
    - If both are healthy, alternate starting URL per request (round-robin).
    - Always include both unique URLs so failures can fall through.
    """
    global _rr_counter
    now = time.time()
    primary = _asr_url()
    fallback = _asr_fallback_url()

    if not primary and not fallback:
        return []

    # Primary cooling down: prefer fallback first, but still try primary second
    # to avoid hard failure when fallback is unhealthy.
    if primary and now < _primary_down_until:
        if fallback and fallback != primary:
            return [fallback, primary]
        return [primary]

    urls: List[str] = []
    if primary:
        urls.append(primary)
    if fallback and fallback not in urls:
        urls.append(fallback)

    if len(urls) <= 1:
        return urls

    # Two-node round-robin start index
    idx = _rr_counter % len(urls)
    _rr_counter += 1
    return urls[idx:] + urls[:idx]


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


def _extract_whisper_text(payload: Any) -> str:
    if isinstance(payload, dict):
        for key in ("text", "transcription", "result", "output"):
            val = payload.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
        segments = payload.get("segments")
        if isinstance(segments, list):
            parts: List[str] = []
            for seg in segments:
                if isinstance(seg, dict):
                    txt = seg.get("text")
                    if isinstance(txt, str) and txt.strip():
                        parts.append(txt.strip())
            if parts:
                return " ".join(parts).strip()
    return ""


def _infer_file_suffix(filename: str, content_type: str) -> str:
    low = f"{content_type or ''} {filename or ''}".lower()
    if "webm" in low:
        return ".webm"
    if "ogg" in low or ".oga" in low:
        return ".ogg"
    if "wav" in low:
        return ".wav"
    if "mp4" in low or "m4a" in low:
        return ".m4a"
    if "mp3" in low:
        return ".mp3"
    try:
        ext = Path(filename or "").suffix
        if ext:
            return ext
    except Exception:
        pass
    return ".bin"


def _normalize_audio_to_wav(data: bytes, filename: str, content_type: str) -> tuple[bytes, str, str]:
    if not data or not _normalize_to_wav_enabled():
        return data, filename, content_type
    ffmpeg_bin = os.environ.get("FFMPEG_BIN") or shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")
    if not ffmpeg_bin:
        return data, filename, content_type

    suffix = _infer_file_suffix(filename, content_type)
    src_path = ""
    dst_path = ""
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as src:
            src.write(data)
            src_path = src.name
        dst_path = src_path + ".wav"

        cmd = [
            ffmpeg_bin,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-threads",
            "0",  # Use all CPU cores
            "-i",
            src_path,
            "-c:a",
            "pcm_s16le",  # Explicit PCM codec
            "-ar",
            "16000",
            "-ac",
            "1",
            "-af",
            "aresample=async=1000",  # Faster resampling
            "-f",
            "wav",
            dst_path,
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if proc.returncode != 0 or not os.path.exists(dst_path):
            return data, filename, content_type

        with open(dst_path, "rb") as f:
            wav_data = f.read()
        wav_name = (Path(filename).stem if filename else "recording") + ".wav"
        return wav_data, wav_name, "audio/wav"
    except Exception:
        return data, filename, content_type
    finally:
        for p in (src_path, dst_path):
            try:
                if p and os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass


@router.post("/transcribe_diarized", dependencies=[Depends(require_api_bearer)])
@router.post("/transcribe_diarized/", dependencies=[Depends(require_api_bearer)])
async def transcribe_diarized(request: Request):
    """
    Proxy transcription to whisper.cpp server(s).
    Accepts audio file via multipart/form-data with field 'audio'.
    Returns plain text transcription.
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
        data, filename, content_type = _normalize_audio_to_wav(data, filename, content_type)

        errors: List[str] = []
        timeout = aiohttp.ClientTimeout(total=180, connect=8, sock_connect=8, sock_read=180)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            headers = {}
            api_key = _asr_api_key()
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            for base_url in candidates:
                try:
                    form_data = aiohttp.FormData()
                    form_data.add_field('file', data, filename=filename, content_type=content_type)
                    form_data.add_field('response_format', 'json')
                    form_data.add_field('vad', _whispercpp_vad())
                    form_data.add_field('no_speech_thold', _whispercpp_no_speech_thold())
                    async with session.post(f"{base_url}/inference", data=form_data, headers=headers) as resp:
                        if resp.status != 200:
                            txt = await resp.text()
                            if base_url == primary and resp.status >= 500:
                                _mark_primary_down()
                            raise RuntimeError(f"HTTP {resp.status}: {txt[:200]}")
                        body = await resp.text()
                        text = ""
                        try:
                            payload = json.loads(body)
                            text = _extract_whisper_text(payload)
                        except Exception:
                            text = body.strip()
                        return PlainTextResponse(text)
                except (aiohttp.ClientConnectionError, aiohttp.ServerTimeoutError, TimeoutError) as e:
                    if base_url == primary:
                        _mark_primary_down()
                    errors.append(f"{base_url}: {str(e)[:200]}")
                except Exception as e:
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


@router.get("/asr_engine", dependencies=[Depends(require_api_bearer)])
async def asr_engine_info() -> Dict[str, Optional[str]]:
    primary = _asr_url()
    fallback = _asr_fallback_url()
    candidates = _candidate_urls()
    if not candidates:
        return {"engine": "whisper.cpp", "error": "ASR_URL not set (and no fallback configured)."}

    timeout = aiohttp.ClientTimeout(total=15, connect=4, sock_connect=4, sock_read=8)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            errors: List[str] = []
            for base_url in candidates:
                try:
                    async with session.get(f"{base_url}/asr_engine") as resp:
                        if resp.status == 200:
                            payload = await resp.json()
                            if isinstance(payload, dict):
                                payload = dict(payload)
                                payload["base_url"] = base_url
                            return cast(Dict[str, Optional[str]], payload)
                except Exception:
                    pass
                try:
                    async with session.get(f"{base_url}/inference") as resp:
                        # Different whisper.cpp builds behave differently for GET /inference.
                        # 400/405/415 are expected for a POST-only endpoint. Some builds return 404
                        # on GET while still serving POST /inference correctly.
                        if resp.status in (200, 400, 401, 403, 404, 405, 415):
                            return {
                                "engine": "whisper.cpp",
                                "base_url": base_url,
                                "endpoint": "/inference",
                                "probe_status": str(resp.status),
                            }
                        txt = await resp.text()
                        raise RuntimeError(f"HTTP {resp.status}: {txt[:200]}")
                except Exception as e:
                    # Do not mark primary as down from this informational probe.
                    # Primary cooldown should only be triggered by real transcription failures.
                    errors.append(f"{base_url}: {str(e)[:200]}")
                    continue
            return {"engine": "whisper.cpp", "error": "; ".join(errors)[:200]}
    except Exception as e:
        return {"engine": "whisper.cpp", "error": str(e)[:160]}

