# server/routes/asr.py
from typing import Dict, Optional, Any, cast, List
import io
import os
import time
import json
import shutil
import wave
import subprocess
import tempfile
import asyncio
from pathlib import Path
import uuid
import hashlib
import logging

from fastapi import APIRouter, HTTPException, Request, Depends, Body
from fastapi.responses import PlainTextResponse, JSONResponse
import aiohttp

from server.core.dependencies import require_api_bearer
from server.core.asr_incident_store import record_asr_incident

router = APIRouter()

logger = logging.getLogger("cng.asr")


def _asr_debug_enabled() -> bool:
    val = str(os.environ.get("ASR_DEBUG", "") or "").strip().lower()
    return val in {"1", "true", "yes", "on"}


def _asr_audit_enabled() -> bool:
    """Structured pipeline logs suitable for grep/correlation (lighter than ASR_DEBUG)."""
    val = str(os.environ.get("ASR_AUDIT_LOG", "") or "").strip().lower()
    if val in {"1", "true", "yes", "on"}:
        return True
    if val in {"0", "false", "no", "off"}:
        return False
    # Default: follow ASR_DEBUG so operators get correlated logs when deep-debugging.
    return _asr_debug_enabled()


def _audit(trace_id: str, msg: str, **fields: Any) -> None:
    if not _asr_audit_enabled():
        return
    payload = {"trace_id": trace_id, **fields}
    try:
        logger.warning("[ASR_AUDIT] %s | %s", msg, json.dumps(payload, ensure_ascii=False, default=str)[:8000])
    except Exception:
        logger.warning("[ASR_AUDIT] %s | trace_id=%s", msg, trace_id)


def _asr_debug_body_enabled() -> bool:
    val = str(os.environ.get("ASR_DEBUG_LOG_BODY", "") or "").strip().lower()
    return val in {"1", "true", "yes", "on"}


def _asr_debug_sample_bytes() -> int:
    try:
        n = int(str(os.environ.get("ASR_DEBUG_SAMPLE_BYTES") or "64").strip())
    except Exception:
        n = 64
    return max(0, min(4096, n))


def _asr_max_upload_bytes() -> int:
    """Hard cap on a single ASR upload (bytes). Guards against unbounded
    full-file buffering into RAM (Step 9 M-ASR): an attacker or a buggy
    client must not be able to POST an arbitrarily large body and force us to
    hold it entirely in memory while normalising + forwarding. Configurable
    via ASR_MAX_UPLOAD_BYTES (default 350 MB — comfortably above any real
    consultation recording while bounding memory)."""
    try:
        n = int(str(os.environ.get("ASR_MAX_UPLOAD_BYTES") or "0").strip())
    except Exception:
        n = 0
    return n if n > 0 else 350_000_000


# Read a Starlette UploadFile to bytes with a hard size cap enforced WHILE
# streaming (so we never allocate an unbounded buffer, and never materialise
# an oversized body fully before rejecting it).
async def _read_audio_bounded(upload: Any, trace_id: str) -> bytes:
    max_bytes = _asr_max_upload_bytes()
    buf = bytearray()
    CHUNK = 1 << 20  # 1 MiB
    try:
        while True:
            chunk = await upload.read(CHUNK)
            if not chunk:
                break
            buf.extend(chunk)
            if len(buf) > max_bytes:
                _audit(
                    trace_id,
                    "pipeline.reject_oversize",
                    size_bytes=len(buf),
                    max_bytes=max_bytes,
                    reason="upload_exceeds_max",
                )
                raise HTTPException(
                    status_code=413,
                    detail={
                        "message": (
                            f"Recording exceeds the maximum upload size "
                            f"({max_bytes // (1024 * 1024)} MB)."
                        ),
                        "trace_id": trace_id,
                    },
                )
    finally:
        # Best-effort cleanup guard: closing the upload must never propagate
        # (we've already read everything we need). An error here changes nothing.
        try:
            await upload.close()
        except Exception:
            pass
    return bytes(buf)


def _hex(b: bytes, limit: int = 64) -> str:
    if not b:
        return ""
    lim = max(0, min(len(b), limit))
    return b[:lim].hex()


def _sha256_short(b: bytes) -> str:
    if not b:
        return "sha256:empty"
    h = hashlib.sha256(b).hexdigest()
    return f"sha256:{h[:16]}"


def _sniff_magic(data: bytes) -> Dict[str, Any]:
    """Best-effort magic sniff for debugging invalid/partial uploads."""
    out: Dict[str, Any] = {}
    if not data:
        out["kind"] = "empty"
        return out
    head = data[:64]
    out["head_hex"] = _hex(head, 32)
    # WAV: RIFF....WAVE
    if len(head) >= 12 and head[0:4] == b"RIFF" and head[8:12] == b"WAVE":
        out["kind"] = "wav"
        return out
    # EBML (Matroska/WebM): 1A 45 DF A3
    if len(head) >= 4 and head[0:4] == bytes([0x1A, 0x45, 0xDF, 0xA3]):
        out["kind"] = "ebml_webm"
        return out
    # OggS
    if len(head) >= 4 and head[0:4] == b"OggS":
        out["kind"] = "ogg"
        return out
    # ID3 (mp3)
    if len(head) >= 3 and head[0:3] == b"ID3":
        out["kind"] = "mp3_id3"
        return out
    # MP4/M4A (ISO Base Media): ftyp box at offset 4
    if len(head) >= 12 and head[4:8] == b"ftyp":
        out["kind"] = "mp4"
        return out
    out["kind"] = "unknown"
    return out


def _dbg(trace_id: str, msg: str, **fields: Any) -> None:
    if not _asr_debug_enabled():
        return
    payload = {"trace_id": trace_id, **fields}
    try:
        logger.warning("[ASR_DEBUG] %s | %s", msg, json.dumps(payload, ensure_ascii=False, default=str)[:8000])
    except Exception:
        # Fallback: never let debug logging break ASR.
        logger.warning("[ASR_DEBUG] %s | trace_id=%s", msg, trace_id)


def _trace_headers(trace_id: str) -> Dict[str, str]:
    # Expose trace id on every HTTP response so clients/operators can correlate without parsing bodies.
    return {"X-ASR-Trace-Id": trace_id}


def _append_trace_to_detail(detail: Any, trace_id: str) -> Any:
    """Ensure trace_id is present inside nested JSON detail objects when possible."""
    if isinstance(detail, dict):
        out = dict(detail)
        # service_error_detail() shape: { service, primary, fallback, errors: [...] }
        if "service" in out and "errors" in out:
            out.setdefault("trace_id", trace_id)
            return out
        inner = out.get("detail")
        if isinstance(inner, dict):
            inner = dict(inner)
            inner.setdefault("trace_id", trace_id)
            out["detail"] = inner
        else:
            out.setdefault("trace_id", trace_id)
        return out
    return {"trace_id": trace_id, "detail": detail}


def _json_with_trace(status_code: int, content: Dict[str, Any], trace_id: str) -> JSONResponse:
    body = dict(content)
    body.setdefault("trace_id", trace_id)
    if isinstance(body.get("detail"), dict):
        d = dict(body["detail"])
        d.setdefault("trace_id", trace_id)
        body["detail"] = d
    return JSONResponse(status_code=status_code, content=body, headers=_trace_headers(trace_id))


def _text_with_trace(text: str, *, status_code: int = 200, trace_id: str) -> PlainTextResponse:
    return PlainTextResponse(text, status_code=status_code, headers=_trace_headers(trace_id))


_DEFAULT_ASR_URL = "http://127.0.0.1:8095"


def _asr_url() -> Optional[str]:
    """Resolve primary ASR base URL.

    - Missing ``ASR_URL`` → default ``_DEFAULT_ASR_URL`` unless ``ASR_URLS`` alone is set.
    - ``ASR_URL`` set to a non-blank string → that URL (trimmed, no trailing slash).
    - ``ASR_URL`` set to **exactly** ``""`` with no ``ASR_URLS`` → ``None`` (intentional disable; tests rely on this).
    - ``ASR_URL`` present but only whitespace → treat like unset (use default or ``ASR_URLS``-only mode) so stray
      env lines do not wipe the whole candidate pool.
    """
    urls_only = _asr_urls_env()
    if "ASR_URL" in os.environ:
        raw = os.environ.get("ASR_URL")
        if raw is None:
            stripped: Optional[str] = None
        else:
            stripped = str(raw).strip().rstrip("/") or None
        if stripped:
            return stripped
        # Exact empty string disables ASR when there is no explicit multi-URL pool.
        if raw is not None and str(raw) == "" and not urls_only:
            return None
        if urls_only:
            return None
        return _DEFAULT_ASR_URL.rstrip("/")
    if urls_only:
        return None
    return _DEFAULT_ASR_URL.rstrip("/")


def _asr_urls_env() -> List[str]:
    """Optional comma/semicolon-delimited pool override for ASR endpoints."""
    raw = str(os.environ.get("ASR_URLS") or "").strip()
    if not raw:
        return []
    urls: List[str] = []
    for token in raw.replace(";", ",").split(","):
        u = token.strip().rstrip("/")
        if u and u not in urls:
            urls.append(u)
    return urls


def _asr_fallback_url() -> Optional[str]:
    val = os.environ.get("ASR_URL_FALLBACK")
    if val and str(val).strip():
        return str(val).strip().rstrip("/") or None
    # Local dev: primary on 127.0.0.1:8095 → try 8096 when fallback env is unset
    primary = (_asr_url() or "").strip()
    if not primary:
        return None
    try:
        from urllib.parse import urlparse, urlunparse

        u = urlparse(primary.rstrip("/"))
        if u.port == 8095 and u.hostname in ("127.0.0.1", "localhost"):
            u2 = u._replace(netloc=f"{u.hostname}:8096")
            return urlunparse(u2).rstrip("/")
    except Exception:
        # Best-effort local-dev convenience: if deriving the :8096 fallback URL
        # from the primary fails, fall back to "no auto-fallback" rather than
        # crash -- the explicit ASR_URL_FALLBACK path still stands.
        pass
    return None


def _resolve_ffmpeg_bin() -> Optional[str]:
    return os.environ.get("FFMPEG_BIN") or shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")

def _asr_api_key() -> Optional[str]:
    # No hardcoded fallback: a well-known default credential defeats the
    # point of authenticating to the ASR box, which now sits on a separate
    # machine on the network (see service_endpoints.json) rather than
    # localhost-only. Fail closed -- no Authorization header -- if unset,
    # rather than silently authenticating with a guessable value.
    return os.environ.get("ASR_API_KEY")


def _normalize_to_wav_enabled() -> bool:
    # Default ON: whisper.cpp's built-in decoder often fails on browser-recorded
    # webm/opus blobs.  ffmpeg normalization to 16 kHz mono WAV is the safest path.
    val = (os.environ.get("ASR_NORMALIZE_TO_WAV") or "1").strip().lower()
    return val not in {"0", "false", "no", "off"}

_primary_down_until = 0.0
_cooldown_sec = 10.0
_rr_counter = 0

# Per-URL cooldown: any candidate that fails (5xx, timeout, connection error) is parked briefly
# so a failing instance is not hammered. Map: url -> epoch_until.
_url_down_until: Dict[str, float] = {}
_url_cooldown_sec = 10.0


def _candidate_pool_urls() -> List[str]:
    """
    Build a pool of whisper.cpp base URLs.

    Supports:
    - Explicit pool: ASR_URLS (comma/semicolon delimited)
    - Legacy: ASR_URL + ASR_URL_FALLBACK
    - Local dev convenience: if primary is localhost:8095 and fallback unset, auto-add 8096.
    """
    primary = _asr_url()
    fallback = _asr_fallback_url()

    # Prefer explicit ASR_URLS ordering when provided, while staying backward-compatible
    # with ASR_URL / ASR_URL_FALLBACK.
    urls: List[str] = _asr_urls_env()
    if primary:
        if primary not in urls:
            urls.append(primary)
    if fallback and fallback not in urls:
        urls.append(fallback)

    # Local dev: primary on 127.0.0.1:8095 → include 8096 automatically when unset
    try:
        from urllib.parse import urlparse, urlunparse

        if primary:
            u = urlparse(primary.rstrip("/"))
            if u.port == 8095 and u.hostname in ("127.0.0.1", "localhost"):
                for port in (8096,):
                    u2 = u._replace(netloc=f"{u.hostname}:{port}")
                    cand = urlunparse(u2).rstrip("/")
                    if cand not in urls:
                        urls.append(cand)
    except Exception:
        # Best-effort local-dev convenience: failure to derive the :8096
        # candidate just skips that extra fallback; the explicit pool/URLs still
        # stand. Never crash candidate discovery over this.
        pass

    return urls


def _candidate_urls(*, preferred_index: Optional[int] = None) -> List[str]:
    """Return candidate ASR URLs with light load-spread + fallback behavior.

    - URLs in per-URL cooldown are pushed to the BACK of the order (still tried as last resort).
    - If primary is in cooldown, prefer non-primary first but keep it in the list.
    - If multiple are healthy, alternate starting URL per request (round-robin) unless preferred_index is provided.
    """
    global _rr_counter
    now = time.time()
    primary = _asr_url()
    pool = _candidate_pool_urls()
    if not pool:
        return []

    # Apply primary cooldown (legacy) by deprioritizing primary if recently failed.
    if primary and now < _primary_down_until and primary in pool:
        pool = [u for u in pool if u != primary] + [primary]

    if len(pool) > 1:
        # Sticky start index when provided (client session pinning).
        if preferred_index is not None and preferred_index >= 0:
            idx = preferred_index % len(pool)
            ordered = pool[idx:] + pool[:idx]
        else:
            idx = _rr_counter % len(pool)
            _rr_counter += 1
            ordered = pool[idx:] + pool[:idx]
    else:
        ordered = pool

    # Per-URL cooldown: deprioritize URLs that recently failed but never drop them
    # entirely (so a fully-cold pool still gets tried as last resort).
    healthy = [u for u in ordered if _url_down_until.get(u, 0.0) <= now]
    cooling = [u for u in ordered if _url_down_until.get(u, 0.0) > now]
    return healthy + cooling


def _mark_primary_down() -> None:
    global _primary_down_until
    if _asr_url():
        _primary_down_until = time.time() + _cooldown_sec


def _mark_url_down(base_url: str) -> None:
    """Park a specific upstream after a real failure (5xx, timeout, connect error)."""
    if not base_url:
        return
    _url_down_until[base_url] = time.time() + _url_cooldown_sec
    if base_url == _asr_url():
        _mark_primary_down()


def _mark_url_healthy(base_url: str) -> None:
    """Clear cooldown for a base_url after a confirmed success."""
    if not base_url:
        return
    _url_down_until.pop(base_url, None)


def _looks_retryable_upstream_error(error_text: str) -> bool:
    """Classify 200+error payloads that indicate transient capacity issues."""
    msg = str(error_text or "").lower()
    if not msg:
        return False
    retryable_markers = (
        "busy",
        "overload",
        "too many",
        "try again",
        "temporar",
        "timeout",
        "timed out",
        "queue full",
        "resource exhausted",
        "capacity",
        "unavailable",
    )
    return any(marker in msg for marker in retryable_markers)


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
        # Best-effort suffix inference: an unusual filename must not crash
        # suffix selection -- fall through to the generic ".bin" default.
        pass
    return ".bin"


def _suffix_for_normalization(data: bytes, filename: str, content_type: str) -> str:
    """Pick ffmpeg input suffix from actual container magic, not only client filename/MIME.

    Mislabeled uploads (RIFF/WAVE bytes named ``recording.webm``) used to force the WebM
    demuxer and produced 415 \"fragmented webm / no EBML\". Sniffing avoids that class of
    failure for valid WAV/Ogg/WebM inputs.
    """
    sniff = _sniff_magic(data)
    kind = str(sniff.get("kind") or "")
    if kind == "wav":
        return ".wav"
    if kind == "ebml_webm":
        return ".webm"
    if kind == "ogg":
        return ".ogg"
    if kind == "mp3_id3":
        return ".mp3"
    if kind == "mp4":
        # iOS Safari records audio/mp4, but the frontend still names the upload
        # recording_<ts>.webm — so filename/MIME sniffing alone would pick the
        # WebM demuxer and fail. Trust the container magic here.
        return ".m4a"
    return _infer_file_suffix(filename, content_type)


class AudioNormalizationError(Exception):
    """Raised when ffmpeg normalization fails in strict mode (optional callers only).

    The transcribe proxy uses non-strict normalization so resilient fall-through can reach whisper-server.
    """

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


def _normalize_audio_to_wav(
    data: bytes,
    filename: str,
    content_type: str,
    *,
    strict: bool = False,
) -> tuple[bytes, str, str]:
    """Synchronous ffmpeg normalization. Used by the queue (sync) path.

    When ``strict=True`` the function raises ``AudioNormalizationError`` on any failure
    instead of silently returning the original bytes. The live transcribe proxy uses
    ``strict=False`` (pass-through on normalization failure).
    """
    if not data:
        return data, filename, content_type
    # Configuration short-circuits: operator turned normalization off, or ffmpeg isn't
    # installed. These are NOT processing failures, so they pass through in every mode.
    # Strict mode only triggers when ffmpeg actually runs and fails — that's the case
    # where silent fall-through would launder a real error to whisper-server.
    if not _normalize_to_wav_enabled():
        logger.warning("ASR normalize disabled by config — sending original bytes (requires --convert on whisper-server)")
        return data, filename, content_type
    ffmpeg_bin = _resolve_ffmpeg_bin()
    if not ffmpeg_bin:
        logger.warning("ASR normalize skipped: ffmpeg not found — sending original bytes (requires --convert on whisper-server)")
        return data, filename, content_type

    suffix = _suffix_for_normalization(data, filename, content_type)
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
            stderr = (proc.stderr or "").strip().splitlines()
            tail = " | ".join(stderr[-3:])[:400] if stderr else ""
            if strict:
                raise AudioNormalizationError(
                    f"ffmpeg failed (rc={proc.returncode}); {tail or 'no stderr'}"
                )
            logger.warning(
                "ASR normalize failed (rc=%s) — sending original bytes; %s",
                proc.returncode,
                tail or "no stderr",
            )
            return data, filename, content_type

        with open(dst_path, "rb") as f:
            wav_data = f.read()
        wav_name = (Path(filename).stem if filename else "recording") + ".wav"
        return wav_data, wav_name, "audio/wav"
    except AudioNormalizationError:
        raise
    except Exception as exc:
        if strict:
            raise AudioNormalizationError(f"ffmpeg crashed: {exc}") from exc
        logger.warning("ASR normalize crashed — sending original bytes; %s", exc)
        return data, filename, content_type
    finally:
        # Best-effort temp-file cleanup guard: leaving a temp file on remove
        # failure is preferable to raising over the normalization result we
        # already produced (and /tmp is swept by the OS anyway).
        for p in (src_path, dst_path):
            try:
                if p and os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass


async def _normalize_audio_to_wav_async(
    data: bytes,
    filename: str,
    content_type: str,
    *,
    strict: bool = False,
) -> tuple[bytes, str, str]:
    """Run ffmpeg normalization in a worker thread so it never blocks the event loop.

    The live proxy is async; running subprocess.run synchronously here would stall every
    other requests for the duration of ffmpeg.
    """
    return await asyncio.to_thread(
        _normalize_audio_to_wav, data, filename, content_type, strict=strict
    )


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

    # Correlation / trace id for debugging: client can pass x-asr-trace-id, otherwise we generate.
    trace_id = (request.headers.get("x-asr-trace-id") or "").strip() or str(uuid.uuid4())
    t_req0 = time.perf_counter()
    _audit(
        trace_id,
        "request.received",
        path=str(request.url.path),
        primary=primary,
        fallback=fallback,
    )
    candidates = _candidate_urls()
    if not candidates:
        detail = _service_error_detail(
            primary,
            fallback,
            ["ASR endpoints are not configured (set ASR_URL/ASR_URL_FALLBACK or ASR_URLS)."],
        )
        record_asr_incident(
            trace_id=trace_id,
            stage="asr.config",
            outcome="no_candidates",
            payload={"http_status": 503, "primary": primary, "fallback": fallback},
        )
        return _json_with_trace(
            503,
            {"error": "service_unavailable", "detail": _append_trace_to_detail(detail, trace_id)},
            trace_id,
        )

    _dbg(
        trace_id,
        "request.start",
        method=request.method,
        path=str(request.url.path),
        client=str(getattr(request.client, "host", "") or ""),
        candidates=candidates,
        primary=primary,
        fallback=fallback,
        normalize_enabled=_normalize_to_wav_enabled(),
        ffmpeg_bin=_resolve_ffmpeg_bin(),
    )

    try:
        form = await request.form()
        audio = cast(Any, form.get('audio'))
        if not audio:
            raise HTTPException(
                status_code=400,
                detail={"message": "missing audio file", "trace_id": trace_id},
            )

        prompt = str(form.get("prompt") or "").strip()

        # Step 9 (M-ASR): read the upload with a hard size cap enforced while
        # streaming, so an oversized body is rejected (413) before it is ever
        # fully buffered into memory.
        data = await _read_audio_bounded(audio, trace_id)
        filename = getattr(audio, 'filename', 'audio') or 'audio'
        content_type = getattr(audio, 'content_type', None) or 'application/octet-stream'
        sniff = _sniff_magic(data)
        _audit(
            trace_id,
            "pipeline.audio_in",
            filename=filename,
            content_type=content_type,
            size_bytes=len(data or b""),
            sha256=_sha256_short(data),
            sniff_kind=str(sniff.get("kind") or ""),
        )
        _dbg(
            trace_id,
            "request.audio_read",
            prompt_len=len(prompt or ""),
            filename=filename,
            content_type=content_type,
            size_bytes=len(data or b""),
            sha256=_sha256_short(data),
            sniff=sniff,
        )
        if _asr_debug_body_enabled():
            n = _asr_debug_sample_bytes()
            _dbg(trace_id, "request.audio_sample", sample_bytes=n, sample_hex=_hex(data, n))

        # Early reject: impossibly small audio — don't waste FFmpeg + Whisper cycles.
        # We do NOT reject on unrecognised format alone — FFmpeg handles MP4/M4A and
        # many other containers that _sniff_magic doesn't explicitly recognise.
        MIN_AUDIO_BYTES = 1000
        if len(data or b"") < MIN_AUDIO_BYTES:
            _audit(trace_id, "pipeline.reject_empty", size_bytes=len(data or b""), reason="recording_too_small")
            raise HTTPException(
                status_code=400,
                detail={
                    "message": "Recording is too small to contain audio. The microphone may not have captured any data.",
                    "trace_id": trace_id,
                },
            )

        # Normalize to 16kHz mono PCM WAV before forwarding (worker thread — never blocks the loop).
        # Non-strict: if ffmpeg fails, pass original bytes through so whisper-server may still decode.
        t_norm0 = time.perf_counter()
        data, filename, content_type = await _normalize_audio_to_wav_async(
            data, filename, content_type, strict=False
        )
        _audit(
            trace_id,
            "pipeline.normalize_ok",
            dt_ms=int((time.perf_counter() - t_norm0) * 1000),
            out_filename=filename,
            out_content_type=content_type,
            out_size_bytes=len(data or b""),
            out_sha256=_sha256_short(data),
            out_sniff_kind=str(_sniff_magic(data).get("kind") or ""),
        )
        _dbg(
            trace_id,
            "normalize.ok",
            dt_ms=int((time.perf_counter() - t_norm0) * 1000),
            out_filename=filename,
            out_content_type=content_type,
            out_size_bytes=len(data or b""),
            out_sha256=_sha256_short(data),
            out_sniff=_sniff_magic(data),
        )

        errors: List[str] = []
        # Long recordings: allow up to 5 min per upstream attempt.
        per_request_timeout = aiohttp.ClientTimeout(total=300, connect=8, sock_connect=8, sock_read=300)
        async with aiohttp.ClientSession(timeout=per_request_timeout) as session:
            headers = {}
            api_key = _asr_api_key()
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            # Propagate trace id to upstream (harmless for whisper-server; useful if proxied/logged).
            headers["x-asr-trace-id"] = trace_id
            for base_url in candidates:
                try:
                    form_data = aiohttp.FormData()
                    form_data.add_field('file', data, filename=filename, content_type=content_type)
                    form_data.add_field('response_format', 'json')
                    if prompt:
                        form_data.add_field("prompt", prompt)
                    t_up0 = time.perf_counter()
                    _dbg(
                        trace_id,
                        "upstream.attempt",
                        base_url=base_url,
                        timeout=str(per_request_timeout),
                        send_size_bytes=len(data or b""),
                        send_content_type=content_type,
                    )
                    async with session.post(f"{base_url}/inference", data=form_data, headers=headers) as resp:
                        if resp.status != 200:
                            txt = await resp.text()
                            _dbg(
                                trace_id,
                                "upstream.http_error",
                                base_url=base_url,
                                status=resp.status,
                                dt_ms=int((time.perf_counter() - t_up0) * 1000),
                                body_snippet=(txt or "")[:400],
                            )
                            # Park this URL briefly on real upstream failures/capacity issues.
                            # 400-level errors are client errors (bad request format) — do NOT park
                            # the URL (cooldown), but log them so operators can diagnose config issues
                            # (e.g. missing --convert on whisper-server, wrong audio format).
                            if resp.status >= 500 or resp.status in (408, 429):
                                _mark_url_down(base_url)
                            record_asr_incident(
                                trace_id=trace_id,
                                stage="asr.upstream",
                                outcome="http_error",
                                payload={
                                    "http_status": resp.status,
                                    "base_url": base_url,
                                    "body_snippet": (txt or "")[:300],
                                },
                            )
                            raise RuntimeError(f"HTTP {resp.status}: {txt[:200]}")
                        body = await resp.text()
                        _dbg(
                            trace_id,
                            "upstream.http_ok",
                            base_url=base_url,
                            status=resp.status,
                            dt_ms=int((time.perf_counter() - t_up0) * 1000),
                            body_len=len(body or ""),
                            body_snippet=(body or "")[:220] if _asr_debug_body_enabled() else "",
                        )
                        text = ""
                        payload: Any = None
                        try:
                            payload = json.loads(body)
                            if isinstance(payload, dict):
                                text = _extract_whisper_text(payload)
                            else:
                                text = str(payload).strip()
                        except Exception:
                            text = body.strip()
                            payload = None
                        _dbg(
                            trace_id,
                            "upstream.parsed",
                            base_url=base_url,
                            payload_kind=("dict" if isinstance(payload, dict) else type(payload).__name__),
                            extracted_len=len((text or "").strip()),
                            has_error_field=bool(isinstance(payload, dict) and payload.get("error")),
                        )
                        if isinstance(payload, dict) and payload.get("error") and not (text or "").strip():
                            # Upstream surfaced an error in the JSON body — don't mark down based on this
                            # alone (e.g. "no speech detected"), but skip to next candidate.
                            error_msg = str(payload.get("error") or "")
                            if _looks_retryable_upstream_error(error_msg):
                                _mark_url_down(base_url)
                            errors.append(f"{base_url}: upstream_error: {error_msg}")
                            continue
                        if not (text or "").strip():
                            errors.append(f"{base_url}: empty transcription")
                            continue
                        # Success — clear cooldown.
                        _mark_url_healthy(base_url)
                        _dbg(
                            trace_id,
                            "request.success",
                            base_url=base_url,
                            total_dt_ms=int((time.perf_counter() - t_req0) * 1000),
                            text_len=len((text or "").strip()),
                        )
                        _audit(
                            trace_id,
                            "pipeline.success",
                            stage="upstream",
                            base_url=base_url,
                            total_dt_ms=int((time.perf_counter() - t_req0) * 1000),
                            text_len=len((text or "").strip()),
                        )
                        return _text_with_trace(text, status_code=200, trace_id=trace_id)
                except (aiohttp.ClientConnectionError, aiohttp.ServerTimeoutError, asyncio.TimeoutError, TimeoutError) as e:
                    _mark_url_down(base_url)
                    _dbg(
                        trace_id,
                        "upstream.exception",
                        base_url=base_url,
                        kind=type(e).__name__,
                        message=str(e)[:260],
                    )
                    errors.append(f"{base_url}: {str(e)[:200]}")
                except Exception as e:
                    _dbg(
                        trace_id,
                        "upstream.exception",
                        base_url=base_url,
                        kind=type(e).__name__,
                        message=str(e)[:260],
                    )
                    errors.append(f"{base_url}: {str(e)[:200]}")
                    continue
        _dbg(
            trace_id,
            "request.failed_all_candidates",
            total_dt_ms=int((time.perf_counter() - t_req0) * 1000),
            errors=errors[:20],
        )
        detail = _service_error_detail(primary, fallback, errors or ["ASR service failed."])
        _audit(
            trace_id,
            "pipeline.failed_all_candidates",
            total_dt_ms=int((time.perf_counter() - t_req0) * 1000),
            errors=(errors or [])[:20],
        )
        record_asr_incident(
            trace_id=trace_id,
            stage="asr.upstream",
            outcome="failed_all_candidates",
            payload={
                "http_status": 503,
                "errors": (errors or [])[:30],
                "primary": primary,
                "fallback": fallback,
            },
        )
        return _json_with_trace(503, {"error": "service_unavailable", "detail": _append_trace_to_detail(detail, trace_id)}, trace_id)

    except HTTPException as he:
        # Convert to a JSONResponse so the same trace_id is present in the body (not just headers),
        # which is what the client error UI usually displays.
        _dbg(trace_id, "request.http_exception", total_dt_ms=int((time.perf_counter() - t_req0) * 1000), status=he.status_code, detail=he.detail)
        try:
            record_asr_incident(
                trace_id=trace_id,
                stage="asr.http",
                outcome="http_exception",
                payload={"http_status": int(he.status_code), "detail": he.detail},
            )
        except Exception as e:
            # Best-effort: the user-facing HTTP error is already determined, so a
            # failure to record the incident must not alter this response. But
            # losing the incident record silently hides operator telemetry, so
            # surface it. The store itself is hardened in Step 3; this only
            # covers a defensive failure path.
            logger.warning(
                "[asr.incident] record_asr_incident failed "
                "(http exception still returned) | trace_id=%s | stage=asr.http | err=%s",
                trace_id,
                e,
            )
        d = he.detail
        if isinstance(d, str):
            content: Dict[str, Any] = {"error": "http_error", "trace_id": trace_id, "detail": {"message": d, "trace_id": trace_id}}
        elif isinstance(d, dict):
            content = {"error": "http_error", "trace_id": trace_id, "detail": {**d, "trace_id": trace_id}}
        else:
            content = {"error": "http_error", "trace_id": trace_id, "detail": {"message": str(d), "trace_id": trace_id}}
        return JSONResponse(status_code=int(he.status_code), content=content, headers=_trace_headers(trace_id))
    except Exception as e:
        _dbg(
            trace_id,
            "request.exception",
            total_dt_ms=int((time.perf_counter() - t_req0) * 1000),
            kind=type(e).__name__,
            message=str(e)[:300],
        )
        detail = _service_error_detail(primary, fallback, [str(e)[:200]])
        _audit(trace_id, "pipeline.exception", kind=type(e).__name__, message=str(e)[:300])
        record_asr_incident(
            trace_id=trace_id,
            stage="asr.exception",
            outcome="unhandled",
            payload={"http_status": 503, "kind": type(e).__name__, "message": str(e)[:500]},
        )
        return _json_with_trace(503, {"error": "service_unavailable", "detail": _append_trace_to_detail(detail, trace_id)}, trace_id)


def _minimal_probe_wav_bytes() -> bytes:
    """~100ms silence, 16 kHz mono PCM — valid WAV for ffmpeg / whisper-server."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x00" * 3200)
    return buf.getvalue()


async def probe_asr_upstream_inference_compat() -> Dict[str, Any]:
    """
    POST minimal WAV to each configured ASR base URL /inference.
    Use from admin to verify the pool is reachable and returns a parseable response.
    """
    candidates = _candidate_pool_urls()
    if not candidates:
        return {
            "ok": False,
            "error": "No ASR endpoints configured (set ASR_URL and/or ASR_URLS).",
            "results": [],
        }
    wav = _minimal_probe_wav_bytes()
    headers: Dict[str, str] = {"x-asr-trace-id": f"probe_{uuid.uuid4().hex[:16]}"}
    api_key = _asr_api_key()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    per_request_timeout = aiohttp.ClientTimeout(total=30, connect=6, sock_connect=6, sock_read=25)
    results: List[Dict[str, Any]] = []
    async with aiohttp.ClientSession(timeout=per_request_timeout) as session:
        for base_url in candidates:
            row: Dict[str, Any] = {"base_url": base_url, "ok": False}
            t0 = time.perf_counter()
            try:
                form_data = aiohttp.FormData()
                form_data.add_field("file", wav, filename="probe.wav", content_type="audio/wav")
                form_data.add_field("response_format", "json")

                async with session.post(f"{base_url.rstrip('/')}/inference", data=form_data, headers=headers) as resp:
                    body = await resp.text()
                    row["http_status"] = resp.status
                    row["dt_ms"] = int((time.perf_counter() - t0) * 1000)
                    if resp.status != 200:
                        row["error"] = (body or "")[:400]
                        results.append(row)
                        continue
                    text = ""
                    try:
                        payload = json.loads(body)
                        if isinstance(payload, dict):
                            text = _extract_whisper_text(payload)
                        else:
                            text = str(payload).strip()
                    except Exception:
                        text = (body or "").strip()
                    row["ok"] = True
                    row["text_len"] = len((text or "").strip())
                    row["body_preview"] = (body or "")[:200]
            except Exception as e:
                row["error"] = str(e)[:300]
                row["dt_ms"] = int((time.perf_counter() - t0) * 1000)
            results.append(row)
    any_ok = any(r.get("ok") for r in results)
    return {"ok": any_ok, "results": results, "pool": candidates}


@router.post("/asr_client_incident", dependencies=[Depends(require_api_bearer)])
async def asr_client_incident(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """Client-side ASR incidents (PCM tap stalled, fetch failures, etc.) — persisted like server incidents."""
    trace_id = str(payload.get("trace_id") or payload.get("traceId") or "").strip()
    if not trace_id:
        trace_id = str(uuid.uuid4())
    stage = str(payload.get("stage") or "client.unknown")
    outcome = str(payload.get("outcome") or "reported")
    safe: Dict[str, Any] = {}
    for k in (
        "message",
        "seq",
        "url",
        "status",
        "ctx_state",
        "pcm_frames_received",
        "pending",
        "running",
        "user_agent",
    ):
        if k in payload:
            safe[k] = payload.get(k)
    record_asr_incident(trace_id=trace_id, stage=stage, outcome=outcome, payload={"source": "client", **safe})
    return {"ok": True, "trace_id": trace_id}


@router.get("/asr_engine", dependencies=[Depends(require_api_bearer)])
async def asr_engine_info() -> Dict[str, Optional[str]]:
    candidates = _candidate_urls()
    if not candidates:
        return {"engine": "whisper.cpp", "error": "ASR endpoints are not configured (ASR_URL/ASR_URLS)."}

    def _local_probe_meta() -> Dict[str, Optional[str]]:
        return {
            "normalize_to_wav": "1" if _normalize_to_wav_enabled() else "0",
            "ffmpeg_bin": _resolve_ffmpeg_bin(),
        }

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
                                payload.update(_local_probe_meta())
                            return cast(Dict[str, Optional[str]], payload)
                except Exception as e:
                    # Expected-failure probe path: many whisper.cpp builds don't
                    # serve GET /asr_engine, so this falls through to the
                    # /inference probe. Log at debug only (normal, not an error).
                    logger.debug(
                        "ASR /asr_engine probe failed (probing /inference next) | base_url=%s | err=%s",
                        base_url,
                        str(e)[:200],
                    )
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
                                **_local_probe_meta(),
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
