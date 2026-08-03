"""Shared whisper-server /inference client (sync)."""
from __future__ import annotations

import hashlib
import os
import time
from typing import Any, Optional, Tuple

import requests

from server.routes.asr import (
    _asr_api_key,
    _candidate_urls,
    _extract_whisper_text,
    _looks_retryable_upstream_error,
    _mark_url_down,
    _mark_url_healthy,
    _normalize_audio_to_wav,
)


def _chunk_skip_normalize() -> bool:
    raw = str(os.environ.get("ASR_CHUNK_SKIP_NORMALIZE", "1") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def preferred_pool_index_for_session(recording_session_id: Optional[str]) -> Optional[int]:
    """Pin a recording session to one whisper pool member for consistent chunk routing."""
    from server.routes.asr import _candidate_pool_urls

    pool = _candidate_pool_urls()
    if not pool or not recording_session_id:
        return None
    digest = hashlib.sha256(str(recording_session_id).encode()).hexdigest()
    return int(digest[:8], 16) % len(pool)


def transcribe_bytes_sync(
    *,
    file_data: bytes,
    filename: str,
    content_type: str,
    trace_id: str = "",
    response_format: str = "json",
    skip_normalize: Optional[bool] = None,
    prompt: Optional[str] = None,
    t0: Optional[float] = None,
    preferred_index: Optional[int] = None,
) -> Tuple[str, Any]:
    """POST audio to whisper pool. Returns (plain_text, parsed_json_or_none)."""
    candidates = _candidate_urls(preferred_index=preferred_index)
    if not candidates:
        raise RuntimeError("No ASR endpoints available")

    if skip_normalize is None:
        skip_normalize = _chunk_skip_normalize()

    api_key = _asr_api_key()
    try:
        timeout_sec = int(str(os.environ.get("ASR_QUEUE_TIMEOUT_SEC") or "95").strip())
    except Exception:
        timeout_sec = 95
    timeout_sec = max(15, min(120, timeout_sec))

    norm_data = file_data
    norm_name = filename or "audio.webm"
    norm_type = content_type or "application/octet-stream"

    if not skip_normalize:
        norm_data, norm_name, norm_type = _normalize_audio_to_wav(
            file_data,
            filename=filename or "audio",
            content_type=content_type or "application/octet-stream",
        )

    errors: list[str] = []
    text = ""
    raw_payload: Any = None

    for base_url in candidates:
        try:
            headers = {}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"

            form_data: list[tuple] = [
                ("file", (norm_name, norm_data, norm_type)),
                ("response_format", (None, response_format)),
            ]
            if prompt and str(prompt).strip():
                form_data.append(("prompt", (None, str(prompt).strip())))

            resp = requests.post(
                f"{base_url}/inference",
                files=form_data,
                headers=headers,
                timeout=timeout_sec,
            )
            if resp.status_code != 200:
                if resp.status_code >= 500 or resp.status_code in (408, 429):
                    _mark_url_down(base_url)
                errors.append(f"{base_url}: HTTP {resp.status_code}: {(resp.text or '')[:200]}")
                continue

            body_text = resp.text or ""
            payload = None
            try:
                payload = resp.json()
            except Exception:
                payload = None

            if isinstance(payload, dict):
                raw_payload = payload
                if payload.get("error") and not _extract_whisper_text(payload):
                    error_msg = str(payload.get("error") or "")
                    if _looks_retryable_upstream_error(error_msg):
                        _mark_url_down(base_url)
                    errors.append(f"{base_url}: upstream_error: {error_msg}")
                    continue
                text = _extract_whisper_text(payload) or ""
            if not text.strip():
                text = body_text.strip()

            if not text.strip():
                errors.append(f"{base_url}: empty transcription")
                continue

            _mark_url_healthy(base_url)
            return text.strip(), raw_payload

        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            _mark_url_down(base_url)
            errors.append(f"{base_url}: {str(e)[:200]}")
            continue
        except Exception as e:
            errors.append(f"{base_url}: {str(e)[:200]}")
            continue

    _ = trace_id, t0 or time.perf_counter()
    raise RuntimeError(f"ASR failed: {'; '.join(errors)}")
