# C:\Clinical-Note-Generator\server\routes\asr.py
# asr.py - WhisperX ASR Service with Speaker Diarization
from typing import Dict, Optional, Any, cast
import asyncio

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse
import traceback

# Import WhisperX engine
from services.asr_whisperx import WhisperXASREngine

router = APIRouter()

# Initialize WhisperX engine (models load lazily on first use)
asr_engine = WhisperXASREngine()


@router.post("/transcribe_diarized")
@router.post("/transcribe_diarized/")
async def transcribe_diarized(request: Request):
    """
    WhisperX transcription with speaker diarization.
    Accepts audio file via multipart/form-data with field 'audio'.
    Returns plain text transcription with speaker labels (e.g., [SPEAKER_00]).
    """
    try:
        # Parse multipart form data
        form = await request.form()
        audio = cast(Any, form.get('audio'))
        if not audio:
            raise HTTPException(status_code=400, detail="missing audio file")

        # Read uploaded audio bytes
        data = await audio.read() if hasattr(audio, 'read') else audio.file.read()
        
        print(f"[ASR] Received audio file, size: {len(data)} bytes")

        # Detect file format from content-type or filename
        file_suffix = None
        try:
            ctype = getattr(audio, 'content_type', '') or ''
            fname = getattr(audio, 'filename', '') or ''
            low = (ctype + ' ' + fname).lower()
            print(f"[ASR] Content-type: {ctype}, Filename: {fname}")
            
            if 'webm' in low:
                file_suffix = '.webm'
            elif 'ogg' in low or low.endswith('.oga'):
                file_suffix = '.ogg'
            elif 'wav' in low:
                file_suffix = '.wav'
            elif 'flac' in low:
                file_suffix = '.flac'
        except Exception:
            file_suffix = None

        print(f"[ASR] Detected file suffix: {file_suffix}")

        # Create ASR session
        try:
            sid = asr_engine.new_session(file_suffix=file_suffix)
        except TypeError:
            # Fallback if file_suffix parameter not supported
            sid = asr_engine.new_session()
            
        # Append audio data to session
        asr_engine.append_chunk(sid, data)

        # Run transcription with timeout (offload to thread to avoid blocking)
        print(f"[ASR] Starting WhisperX transcription for session {sid}...")
        try:
            text, confidence = await asyncio.wait_for(
                asyncio.to_thread(asr_engine.transcribe, sid),
                timeout=120.0  # 2 minute timeout
            )
            print(f"[ASR] Transcription complete, text length: {len(text)}, confidence: {confidence}")
        except asyncio.TimeoutError:
            print(f"[ASR] Transcription timeout after 120 seconds")
            try:
                asr_engine.cleanup_session(sid)
            except Exception:
                pass
            return PlainTextResponse(
                "Transcription timeout - audio may be too long or WhisperX model not loaded", 
                status_code=503
            )

        # Cleanup session resources
        try:
            asr_engine.cleanup_session(sid)
        except Exception:
            pass

        # Return transcribed text with speaker labels
        return PlainTextResponse(text)

    except HTTPException:
        raise
    except Exception as e:
        print(f"[ASR] transcribe_diarized failed: {e}")
        print(traceback.format_exc())
        return PlainTextResponse(
            f"WhisperX transcription error: {str(e)}", 
            status_code=503
        )


@router.get("/asr_engine")
async def asr_engine_info() -> Dict[str, Optional[str]]:
    """Returns WhisperX engine information and status"""
    try:
        return asr_engine.get_info()
    except Exception as e:
        return {"engine": "whisperx", "error": str(e)[:160]}