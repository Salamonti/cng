from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, List, Optional

from fastapi import HTTPException

from server.routes.asr import (
    _asr_api_key,
    _candidate_urls,
    _extract_whisper_text,
    _looks_retryable_upstream_error,
    _mark_url_down,
    _mark_url_healthy,
    _normalize_audio_to_wav,
)

logger = logging.getLogger("cng.asr_audio")


def _asr_audio_debug_enabled() -> bool:
    val = str(os.environ.get("ASR_DEBUG", "") or os.environ.get("QUEUE_DEBUG", "") or "").strip().lower()
    return val in {"1", "true", "yes", "on"}


def _dbg(trace_id: str, msg: str, **fields: Any) -> None:
    if not _asr_audio_debug_enabled():
        return
    payload = {"trace_id": trace_id, **fields}
    try:
        logger.warning("[ASR_AUDIO_DEBUG] %s | %s", msg, json.dumps(payload, ensure_ascii=False, default=str)[:8000])
    except Exception:
        logger.warning("[ASR_AUDIO_DEBUG] %s | trace_id=%s", msg, trace_id)


def _sha256_short(b: bytes) -> str:
    if not b:
        return "sha256:empty"
    h = hashlib.sha256(b).hexdigest()
    return f"sha256:{h[:16]}"


def _resolve_ffmpeg_bin() -> Optional[str]:
    """Resolve ffmpeg binary path: FFMPEG_BIN env -> system PATH -> config.json ffmpeg_path."""
    from_env = os.environ.get("FFMPEG_BIN", "").strip()
    if from_env:
        return from_env
    from_path = shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")
    if from_path:
        return from_path
    try:
        cfg_file = Path(__file__).resolve().parents[2] / "config" / "config.json"
        if cfg_file.exists():
            cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
            fp = (cfg.get("ffmpeg_path") or cfg.get("ffmpeg_bin") or "").strip()
            if fp and Path(fp).exists():
                return fp
    except Exception:
        pass
    return None


def _concat_webm_files_ffmpeg(input_paths: List[Path], output_path: Path) -> None:
    """Concatenate WebM/Opus parts using the audio concat filter."""
    if not input_paths:
        raise HTTPException(status_code=400, detail="No audio parts to merge")
    if len(input_paths) == 1:
        shutil.copyfile(input_paths[0], output_path)
        return
    ffmpeg_bin = _resolve_ffmpeg_bin()
    if not ffmpeg_bin:
        raise HTTPException(status_code=500, detail="ffmpeg not available for audio merge")
    n = len(input_paths)
    cmd = [ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error"]
    for p in input_paths:
        cmd += ["-i", str(p.resolve())]
    filter_str = "".join(f"[{i}:a]" for i in range(n)) + f"concat=n={n}:v=0:a=1[aout]"
    cmd += [
        "-filter_complex",
        filter_str,
        "-map",
        "[aout]",
        "-c:a",
        "libopus",
        "-b:a",
        "64k",
        str(output_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if proc.returncode != 0 or not output_path.exists():
        msg = (proc.stderr or proc.stdout or "")[:500]
        raise HTTPException(status_code=500, detail=f"ffmpeg merge failed: {msg}")


def _transcribe_audio_bytes_sync(
    *,
    file_data: bytes,
    filename: str,
    content_type: str,
    trace_id: str,
    t0: float,
) -> str:
    """Run retained audio through the normalization + ASR failover path."""
    import requests

    candidates = _candidate_urls()
    if not candidates:
        raise RuntimeError("No ASR endpoints available (set ASR_URL/ASR_URL_FALLBACK or ASR_URLS)")

    api_key = _asr_api_key()
    try:
        timeout_sec = int(str(os.environ.get("ASR_QUEUE_TIMEOUT_SEC") or "95").strip())
    except Exception:
        timeout_sec = 95
    timeout_sec = max(15, min(100, timeout_sec))

    _dbg(
        trace_id,
        "asr_audio.file_read",
        size_bytes=len(file_data or b""),
        sha256=_sha256_short(file_data),
        orig_name=filename,
        orig_mime=content_type,
    )

    t_norm0 = time.perf_counter()
    norm_data, norm_name, norm_type = _normalize_audio_to_wav(
        file_data,
        filename=filename or "audio",
        content_type=content_type or "application/octet-stream",
    )
    _dbg(
        trace_id,
        "asr_audio.normalize_done",
        dt_ms=int((time.perf_counter() - t_norm0) * 1000),
        out_name=norm_name,
        out_mime=norm_type,
        out_size_bytes=len(norm_data or b""),
        out_sha256=_sha256_short(norm_data),
    )

    errors = []
    text = ""
    for base_url in candidates:
        try:
            headers = {}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"

            form_data = {
                "file": (norm_name, norm_data, norm_type),
                "response_format": (None, "json"),
            }

            resp = requests.post(
                f"{base_url}/inference",
                files=form_data,
                headers=headers,
                timeout=timeout_sec,
            )
            if resp.status_code == 200:
                body_text = resp.text or ""
                payload = None
                try:
                    payload = resp.json()
                except Exception:
                    payload = None
                if isinstance(payload, dict):
                    text = _extract_whisper_text(payload) or ""
                    if payload.get("error") and not text.strip():
                        error_msg = str(payload.get("error") or "")
                        if _looks_retryable_upstream_error(error_msg):
                            _mark_url_down(base_url)
                        errors.append(f"{base_url}: upstream_error: {error_msg}")
                        text = ""
                        continue
                if not (text or "").strip():
                    text = body_text.strip()
                if not (text or "").strip():
                    errors.append(f"{base_url}: empty transcription")
                    text = ""
                    continue
                _mark_url_healthy(base_url)
                _dbg(
                    trace_id,
                    "asr_audio.success",
                    base_url=base_url,
                    text_len=len((text or "").strip()),
                    dt_ms=int((time.perf_counter() - t0) * 1000),
                )
                break
            if resp.status_code >= 500 or resp.status_code in (408, 429):
                _mark_url_down(base_url)
            errors.append(f"{base_url}: HTTP {resp.status_code}: {(resp.text or '')[:200]}")
            continue
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            _mark_url_down(base_url)
            errors.append(f"{base_url}: {str(e)[:200]}")
            continue
        except Exception as e:
            errors.append(f"{base_url}: {str(e)[:200]}")
            continue
    else:
        _dbg(trace_id, "asr_audio.failed_all", errors=errors[:20], dt_ms=int((time.perf_counter() - t0) * 1000))
        raise RuntimeError(f"ASR failed: {'; '.join(errors)}")

    if not (text or "").strip():
        raise RuntimeError("ASR returned an empty transcript")
    return text
