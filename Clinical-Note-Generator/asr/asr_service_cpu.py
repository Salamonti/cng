# C:\Clinical-Note-Generator\asr\asr_service_cpu.py
import asyncio
import os
import sys
import traceback
from pathlib import Path
from typing import Any, Dict, Optional, cast

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse, JSONResponse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from asr.asr_engine_cpu import WhisperXCPUASREngine  # noqa: E402

app = FastAPI()

asr_engine = WhisperXCPUASREngine()

@app.on_event("startup")
async def warmup_asr() -> None:
    try:
        await asyncio.to_thread(asr_engine.warmup)
        print("[ASR-CPU] Warmup complete")
    except Exception as e:
        print(f"[ASR-CPU] Warmup failed: {e}")


def _expected_api_key() -> str:
    raw = os.environ.get("ASR_API_KEY") or "notegenadmin"
    return raw.strip().strip('"').strip("'")


def _check_auth(request: Request) -> None:
    expected = _expected_api_key()
    auth = request.headers.get("authorization") or request.headers.get("Authorization") or ""
    token = ""
    if auth.lower().startswith("bearer "):
        token = auth.split(" ", 1)[1].strip().strip('"').strip("'")
    if not token or token != expected:
        raise HTTPException(status_code=401, detail="unauthorized")


@app.post("/transcribe_diarized")
@app.post("/transcribe_diarized/")
async def transcribe_diarized(request: Request):
    """
    WhisperX CPU transcription (no diarization).
    Accepts audio file via multipart/form-data with field 'audio'.
    Returns plain text transcription (single speaker).
    """
    try:
        _check_auth(request)
        form = await request.form()
        audio = cast(Any, form.get("audio"))
        if not audio:
            raise HTTPException(status_code=400, detail="missing audio file")

        data = await audio.read() if hasattr(audio, "read") else audio.file.read()
        print(f"[ASR-CPU] Received audio file, size: {len(data)} bytes")

        file_suffix = None
        try:
            ctype = getattr(audio, "content_type", "") or ""
            fname = getattr(audio, "filename", "") or ""
            low = (ctype + " " + fname).lower()
            print(f"[ASR-CPU] Content-type: {ctype}, Filename: {fname}")

            if "webm" in low:
                file_suffix = ".webm"
            elif "ogg" in low or low.endswith(".oga"):
                file_suffix = ".ogg"
            elif "wav" in low:
                file_suffix = ".wav"
            elif "flac" in low:
                file_suffix = ".flac"
        except Exception:
            file_suffix = None

        print(f"[ASR-CPU] Detected file suffix: {file_suffix}")

        try:
            sid = asr_engine.new_session(file_suffix=file_suffix)
        except TypeError:
            sid = asr_engine.new_session()

        asr_engine.append_chunk(sid, data)

        print(f"[ASR-CPU] Starting WhisperX transcription for session {sid}...")
        try:
            text, confidence = await asyncio.wait_for(
                asyncio.to_thread(asr_engine.transcribe, sid),
                timeout=90.0
            )
            print(f"[ASR-CPU] Transcription complete, text length: {len(text)}, confidence: {confidence}")
        except asyncio.TimeoutError:
            print("[ASR-CPU] Transcription timeout after 90 seconds")
            try:
                asr_engine.cleanup_session(sid)
            except Exception:
                pass
            return PlainTextResponse(
                "Transcription timeout - audio may be too long or WhisperX model not loaded",
                status_code=503
            )

        try:
            asr_engine.cleanup_session(sid)
        except Exception:
            pass

        return PlainTextResponse(text)

    except HTTPException:
        raise
    except Exception as e:
        print(f"[ASR-CPU] transcribe_diarized failed: {e}")
        print(traceback.format_exc())
        return PlainTextResponse(
            f"WhisperX transcription error: {str(e)}",
            status_code=503
        )


@app.post("/v1/audio/transcriptions")
@app.post("/v1/audio/transcriptions/")
async def transcribe_openai(request: Request):
    """
    OpenAI-compatible audio transcription endpoint (CPU).
    Accepts multipart/form-data with field 'file' or 'audio'.
    Returns JSON: {"text": "..."}.
    """
    try:
        _check_auth(request)
        form = await request.form()
        audio = cast(Any, form.get("file") or form.get("audio"))
        if not audio:
            raise HTTPException(status_code=400, detail="missing audio file")

        data = await audio.read() if hasattr(audio, "read") else audio.file.read()
        filename = getattr(audio, "filename", "") or ""
        ctype = getattr(audio, "content_type", "") or ""
        low = (ctype + " " + filename).lower()
        file_suffix = None
        if "webm" in low:
            file_suffix = ".webm"
        elif "ogg" in low or low.endswith(".oga"):
            file_suffix = ".ogg"
        elif "wav" in low:
            file_suffix = ".wav"
        elif "flac" in low:
            file_suffix = ".flac"

        try:
            sid = asr_engine.new_session(file_suffix=file_suffix)
        except TypeError:
            sid = asr_engine.new_session()

        asr_engine.append_chunk(sid, data)

        try:
            text, _confidence = await asyncio.wait_for(
                asyncio.to_thread(asr_engine.transcribe, sid),
                timeout=90.0
            )
        except asyncio.TimeoutError:
            try:
                asr_engine.cleanup_session(sid)
            except Exception:
                pass
            return JSONResponse(status_code=503, content={"error": "timeout"})

        try:
            asr_engine.cleanup_session(sid)
        except Exception:
            pass

        return JSONResponse(content={"text": text})
    except HTTPException:
        raise
    except Exception as e:
        return JSONResponse(status_code=503, content={"error": str(e)[:200]})


@app.get("/asr_engine")
async def asr_engine_info() -> Dict[str, Optional[str]]:
    try:
        return asr_engine.get_info()
    except Exception as e:
        return {"engine": "whisperx-cpu", "error": str(e)[:160]}
