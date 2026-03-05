# C:\Clinical-Note-Generator\server\routes\asr.py
from typing import Dict, Optional, Any, cast, List
import os
import time
import json
import shutil
import subprocess
import tempfile
import uuid
import asyncio
from collections import defaultdict
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, WebSocket
from fastapi.websockets import WebSocketDisconnect
from fastapi.responses import PlainTextResponse, JSONResponse
import aiohttp
from sqlmodel import Session, select

from server.core.db import engine
from server.core.security import decode_access_token
from server.models.user import User

router = APIRouter()

# WebSocket streaming configuration
_ASR_WS_MAX_CONNECTIONS = int(os.environ.get("ASR_WS_MAX_CONNECTIONS", "8"))
_ASR_WS_MAX_PER_USER = int(os.environ.get("ASR_WS_MAX_PER_USER", "1"))
_ASR_WS_MAX_MSG_BYTES = int(os.environ.get("ASR_WS_MAX_MSG_BYTES", str(512 * 1024)))
_ASR_WS_MAX_PROCESSES = int(os.environ.get("ASR_WS_MAX_PROCESSES", "2"))
_ASR_WS_SEM = asyncio.Semaphore(_ASR_WS_MAX_PROCESSES)
_ASR_WS_ACTIVE_TOTAL = 0
_ASR_WS_ACTIVE_BY_USER: Dict[str, int] = defaultdict(int)
_ASR_WS_LOCK = asyncio.Lock()

# WebSocket streaming sessions (legacy, will be migrated)
_whisper_stream_sessions: Dict[str, "WhisperStreamSession"] = {}


class WhisperStreamSession:
    """Manages a single whisper.cpp stream process for a WebSocket connection."""
    
    def __init__(self, websocket: WebSocket, session_id: str):
        self.websocket = websocket
        self.session_id = session_id
        self.process: Optional[asyncio.subprocess.Process] = None
        self.running = False
        self._stdout_task: Optional[asyncio.Task] = None
        self._stderr_task: Optional[asyncio.Task] = None
        
    async def start(self):
        """Start whisper.cpp stream subprocess."""
        cmd = _whisper_stream_cmd()
        if not cmd:
            raise RuntimeError("Failed to build whisper stream command")
        
        # Check if binary exists
        stream_bin = cmd[0]
        if not os.path.exists(stream_bin):
            # Try with .exe extension on Windows
            if not stream_bin.endswith(".exe"):
                stream_bin_exe = stream_bin + ".exe"
                if os.path.exists(stream_bin_exe):
                    cmd[0] = stream_bin_exe
                else:
                    raise RuntimeError(f"whisper.cpp stream binary not found at {stream_bin} or {stream_bin_exe}")
            else:
                raise RuntimeError(f"whisper.cpp stream binary not found at {stream_bin}")
        
        print(f"[WhisperStream] Starting process: {' '.join(cmd)}")
        self.process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=1024 * 1024  # 1MB buffer
        )
        self.running = True
        
        # Start stdout/stderr readers
        self._stdout_task = asyncio.create_task(self._read_stdout())
        self._stderr_task = asyncio.create_task(self._read_stderr())
        
        await self.websocket.send_json({
            "type": "session_started",
            "session_id": self.session_id
        })
    
    async def _read_stdout(self):
        """Read whisper.cpp stdout and send transcriptions via WebSocket."""
        while self.running and self.process and self.process.stdout:
            try:
                line = await self.process.stdout.readline()
                if not line:
                    break
                line = line.decode('utf-8', errors='ignore').strip()
                text = _extract_stream_text(line)
                if text:
                    await self.websocket.send_json({
                        "type": "transcription",
                        "text": text,
                        "session_id": self.session_id
                    })
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[WhisperStream] Error reading stdout: {e}")
                break
    
    async def _read_stderr(self):
        """Read stderr for debugging."""
        while self.running and self.process and self.process.stderr:
            try:
                line = await self.process.stderr.readline()
                if line:
                    print(f"[WhisperStream stderr] {line.decode('utf-8', errors='ignore').strip()}")
            except asyncio.CancelledError:
                break
            except Exception:
                break
    
    async def write_audio(self, chunk: bytes):
        """Write audio chunk to whisper.cpp stdin."""
        if self.running and self.process and self.process.stdin:
            try:
                self.process.stdin.write(chunk)
                await self.process.stdin.drain()
            except Exception as e:
                print(f"[WhisperStream] Error writing audio: {e}")
                self.running = False
    
    async def stop(self):
        """Stop the whisper.cpp process."""
        self.running = False
        if self._stdout_task:
            self._stdout_task.cancel()
        if self._stderr_task:
            self._stderr_task.cancel()
        if self.process:
            try:
                self.process.terminate()
                await asyncio.wait_for(self.process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self.process.kill()
                await self.process.wait()
            except ProcessLookupError:
                pass
            self.process = None


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
    val = (os.environ.get("ASR_NORMALIZE_TO_WAV") or "1").strip().lower()
    return val not in {"0", "false", "no", "off"}

_primary_down_until = 0.0
_cooldown_sec = 20.0

# WebSocket streaming (legacy - using new constants after router)

def _extract_ws_token(ws: WebSocket) -> str:
    auth = ws.headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth.split(" ", 1)[1].strip()
    # preferred browser path: subprotocol "bearer."
    proto = ws.headers.get("sec-websocket-protocol") or ""
    for p in [x.strip() for x in proto.split(",") if x.strip()]:
        if p.startswith("bearer."):
            return p[len("bearer."):]
    # fallback path
    return (ws.query_params.get("access_token") or "").strip()

def _validate_user_token(token: str) -> str:
    payload = decode_access_token(token)
    sub = str(payload.get("sub") or "")
    user_uuid = uuid.UUID(sub)
    with Session(engine) as session:
        user = session.exec(select(User).where(User.id == user_uuid)).one_or_none()
        if not user or not user.is_active or not user.is_approved:
            raise ValueError("user_not_authorized")
        return sub

def _whisper_stream_cmd() -> List[str]:
    # Keep using existing ASR env knobs for VAD/no_speech thresholds
    stream_bin = os.environ.get("ASR_WHISPERCPP_STREAM_BIN") or "C:/projects/whisper.cpp/build/bin/stream"
    model = os.environ.get("ASR_WHISPERCPP_MODEL") or "C:/projects/whisper.cpp/models/ggml-large-v3-turbo.bin"
    return [
        stream_bin, "-m", model,
        "--step", os.environ.get("ASR_STREAM_STEP_MS", "300"),
        "--length", os.environ.get("ASR_STREAM_LENGTH_MS", "3000"),
        "-vth", _whispercpp_vad(),
        "-nth", _whispercpp_no_speech_thold(),
        "--stdin", # verify exact flag against your local stream --help
    ]

def _extract_stream_text(line: str) -> str:
    s = (line or "").strip()
    if not s or s.startswith("whisper_"):
        return ""
    if "]" in s:
        s = s.split("]", 1)[1].strip()
    return s


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


@router.post("/transcribe_diarized")
@router.post("/transcribe_diarized/")
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


@router.get("/asr_engine")
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


@router.websocket("/asr/ws")
async def websocket_asr(websocket: WebSocket):
    """
    WebSocket endpoint for real‑time ASR streaming.
    Accepts token via Authorization header, Sec-WebSocket-Protocol: bearer.<token>,
    or access_token query parameter.
    """
    global _ASR_WS_ACTIVE_TOTAL
    
    # Extract token using Codex's improved method
    token = _extract_ws_token(websocket)
    if not token:
        await websocket.close(code=4401, reason="missing_bearer")
        return
    
    user_id = None
    # Try JWT validation first
    try:
        user_id = _validate_user_token(token)
    except ValueError as e:
        if str(e) == "user_not_authorized":
            await websocket.close(code=4403, reason="user_not_authorized")
            return
        # If JWT validation fails, fall back to ASR_API_KEY (for backward compatibility)
        expected_key = _asr_api_key()
        if token != expected_key:
            await websocket.close(code=4401, reason="invalid_token")
            return
    except Exception:
        # Any other error
        await websocket.close(code=4401, reason="invalid_token")
        return
    
    # Check global and per-user connection limits
    async with _ASR_WS_LOCK:
        if _ASR_WS_ACTIVE_TOTAL >= _ASR_WS_MAX_CONNECTIONS:
            await websocket.close(code=4429, reason="too_many_connections")
            return
        if user_id and _ASR_WS_ACTIVE_BY_USER.get(user_id, 0) >= _ASR_WS_MAX_PER_USER:
            await websocket.close(code=4429, reason="user_connection_limit")
            return
        
        # Update counters
        _ASR_WS_ACTIVE_TOTAL += 1
        if user_id:
            _ASR_WS_ACTIVE_BY_USER[user_id] = _ASR_WS_ACTIVE_BY_USER.get(user_id, 0) + 1
    
    await websocket.accept()
    
    try:
        # Limit concurrent whisper processes using semaphore
        async with _ASR_WS_SEM:
            session_id = str(hash(websocket))
            session = WhisperStreamSession(websocket, session_id)
            
            async with _ASR_WS_LOCK:
                _whisper_stream_sessions[session_id] = session
            
            try:
                await session.start()
                
                # Handle incoming audio chunks
                while True:
                    try:
                        data = await websocket.receive_bytes()
                        # Basic message size limit
                        if len(data) > _ASR_WS_MAX_MSG_BYTES:
                            print(f"[ASR_WS] Message too large: {len(data)} bytes")
                            continue
                        await session.write_audio(data)
                    except WebSocketDisconnect:
                        break
                    except RuntimeError:
                        break
            finally:
                async with _ASR_WS_LOCK:
                    _whisper_stream_sessions.pop(session_id, None)
                await session.stop()
    finally:
        # Decrement connection counters
        async with _ASR_WS_LOCK:
            _ASR_WS_ACTIVE_TOTAL -= 1
            if user_id and user_id in _ASR_WS_ACTIVE_BY_USER:
                _ASR_WS_ACTIVE_BY_USER[user_id] -= 1
                if _ASR_WS_ACTIVE_BY_USER[user_id] <= 0:
                    del _ASR_WS_ACTIVE_BY_USER[user_id]
