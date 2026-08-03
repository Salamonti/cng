# server/core/asr_refine.py
"""Post-Whisper LLM diarization (normalize + punctuation + Doctor/Patient/Unknown in one pass)."""
from __future__ import annotations

import concurrent.futures
import logging
import os

import requests

from server.core.clinical_output_guard import detect_degenerate_output
from server.core.llm_routing import resolve_llm_urls
from server.core.llm_request_policy import (
    apply_dreamcision_generation_policy,
    strip_reasoning_markup,
)
from server.services.asr_medication_corrector import correct_asr_medication_errors

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SEC = float(os.environ.get("ASR_REFINE_TIMEOUT_SEC", "120"))
_MODEL_ID_CACHE: dict[str, str] = {}

# Thread pool for blocking HTTP calls — keeps sync handlers from hogging
# the FastAPI worker thread for up to 120s.
_thread_pool = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="asr_refine")

_SYSTEM_PROMPT = (
    "You refine clinical encounter dictation transcripts produced by speech recognition. "
    "Preserve all clinical facts; do not invent symptoms, diagnoses, medications, or plans. "
    "Fix punctuation, capitalization, obvious ASR word errors, and spacing. "
    "Keep medical terms and drug names accurate. "
    "Diarize: prefix each speaker turn with exactly one label at line start: "
    "Doctor:, Patient:, or Unknown:. "
    "Merge consecutive lines from the same speaker under one label block. "
    "Return ONLY the refined transcript text — no preamble or markdown fences."
)


def _resolve_model_id(base_url: str) -> str:
    """Use env override or discover model id from the vLLM /v1/models endpoint."""
    override = (
        os.environ.get("ASR_REFINE_MODEL")
        or os.environ.get("NOTEGEN_MODEL")
        or os.environ.get("LLM_CHAT_MODEL")
        or os.environ.get("NOTEGEN_CHAT_MODEL")
    )
    if isinstance(override, str) and override.strip():
        return override.strip()

    cached = _MODEL_ID_CACHE.get(base_url)
    if cached:
        return cached

    url = f"{base_url.rstrip('/')}/v1/models"
    try:
        future = _thread_pool.submit(requests.get, url, timeout=5)
        resp = future.result()
        resp.raise_for_status()
        data = resp.json()
        items = data.get("data") if isinstance(data, dict) else None
        if isinstance(items, list) and items:
            item = items[0] if isinstance(items[0], dict) else {}
            mid = str(item.get("id") or item.get("name") or "").strip()
            if mid:
                _MODEL_ID_CACHE[base_url] = mid
                logger.info("asr_refine model for %s -> %s", base_url, mid)
                return mid
    except Exception as exc:
        logger.warning("asr_refine model discovery failed for %s: %s", base_url, exc)

    return "local-model"


def refine_asr_transcript(
    text: str,
    *,
    trace_id: str = "",
) -> tuple[str, bool]:
    """Normalize + diarize Whisper output in a single LLM call.

    Returns (text, ok). ok=False means this fell back to returning the input
    unchanged (no LLM configured, empty/failed response, output truncated by
    the max_tokens cap, or the hard truncation-ratio fallback) -- callers
    must not report this as a successful diarization when ok is False.
    """
    raw = (text or "").strip()
    if not raw:
        return raw, False

    # Layer 1: Pre-LLM dictionary correction (programmatic, zero hallucination risk)
    corrected, correction_count = correct_asr_medication_errors(raw)
    if correction_count > 0:
        logger.info("ASR medication corrections: %d for trace=%s", correction_count, trace_id)
        raw = corrected

    base_url, fallback_url = resolve_llm_urls("asr_refine")
    if not base_url:
        logger.warning("asr_refine skipped: no LLM_ASR_REFINE_URL configured trace=%s", trace_id)
        return raw, False

    user = "Raw ASR transcript:\n\n" + raw

    # Token-based cap: rough estimate of 4 chars per token, allow 2x growth for
    # diarization labels. Hard cap at 16384 tokens to avoid runaway generation.
    estimated_tokens = max(128, len(raw) // 4)
    max_tokens = min(16384, estimated_tokens * 2)

    payload = {
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user},
        ],
        "temperature": 0.1,
        "max_tokens": max_tokens,
        "stream": False,
    }
    apply_dreamcision_generation_policy(payload, profile="asr")

    # P2-1: try the fallback backend (if configured) when the primary is
    # unreachable, rather than failing the whole refine step outright --
    # this previously had no failover at all, unlike note_generator_clean.py's
    # candidate-URL pattern.
    candidates = [base_url] + ([fallback_url] if fallback_url and fallback_url != base_url else [])
    resp = None
    last_exc = None
    used_url = base_url
    for candidate_url in candidates:
        try:
            model = _resolve_model_id(candidate_url)
            candidate_payload = dict(payload, model=model)
            url = f"{candidate_url.rstrip('/')}/v1/chat/completions"
            future = _thread_pool.submit(requests.post, url, json=candidate_payload, timeout=DEFAULT_TIMEOUT_SEC)
            resp = future.result()
            resp.raise_for_status()
            used_url = candidate_url
            break
        except Exception as exc:
            last_exc = exc
            logger.warning("asr_refine request failed on %s trace=%s: %s", candidate_url, trace_id, exc)
            resp = None
            continue

    if resp is None:
        logger.warning("asr_refine all candidates failed trace=%s: %s", trace_id, last_exc)
        return raw, False

    if used_url != base_url:
        logger.warning("asr_refine used fallback backend %s trace=%s", used_url, trace_id)

    data = resp.json()
    content, finish_reason = _extract_message_content(data)
    refined = strip_reasoning_markup(content or "")
    if not refined:
        logger.warning("asr_refine empty LLM response trace=%s", trace_id)
        return raw, False

    # Authoritative truncation signal: the API itself reports finish_reason
    # "length" when generation stopped because max_tokens was hit, not
    # because the model finished naturally. This catches truncation the
    # char-ratio heuristic below misses entirely -- a moderate-length
    # transcript (well under the 192k-char heuristic threshold) can still
    # exceed the max_tokens cap once diarization labels and punctuation
    # fixes are added, and would otherwise be reported as a full success.
    if finish_reason == "length":
        logger.warning(
            "asr_refine output truncated by max_tokens cap (%d refined vs %d raw chars) trace=%s — falling back to raw, reporting as incomplete",
            len(refined),
            len(raw),
            trace_id,
        )
        return raw, False

    # Truncation guard: for very long transcripts (48k+ tokens ≈ 192k chars),
    # the LLM may truncate even without hitting finish_reason=="length" (e.g.
    # some gateways don't propagate it). Fall back to raw rather than serve a
    # transcript that's silently missing its back half.
    _TRUNCATION_CHAR_THRESHOLD = 192_000  # ~48k tokens at 4 chars/token
    if len(raw) > _TRUNCATION_CHAR_THRESHOLD and len(refined) < len(raw) * 0.7:
        logger.warning(
            "asr_refine output likely truncated (%d vs %d chars, %s%%) trace=%s — falling back to raw, reporting as incomplete",
            len(refined),
            len(raw),
            round(len(refined) / len(raw) * 100),
            trace_id,
        )
        return raw, False

    # Hard fallback: for ANY transcript, if output is < 50% of input,
    # something is very wrong (e.g. LLM returned only a preamble). Fall back to raw.
    if len(refined) < len(raw) * 0.5:
        logger.warning(
            "asr_refine output extremely short (%d vs %d chars, %s%% — likely error), falling back to raw trace=%s",
            len(refined),
            len(raw),
            round(len(refined) / len(raw) * 100),
            trace_id,
        )
        return raw, False

    degeneration = detect_degenerate_output(
        refined,
        max_chars=max(24000, int(len(raw) * 2.5)),
    )
    if degeneration:
        logger.warning(
            "asr_refine rejected degenerate output trace=%s reasons=%s",
            trace_id,
            "; ".join(degeneration),
        )
        return raw, False

    return refined, True


def _extract_message_content(data: dict) -> tuple[str, str]:
    """Return (content, finish_reason) from the first choice."""
    try:
        choices = data.get("choices") or []
        if not choices:
            return "", ""
        choice = choices[0]
        msg = choice.get("message") or {}
        content = str(msg.get("content") or "")
        finish_reason = str(choice.get("finish_reason") or "")
        return content, finish_reason
    except (AttributeError, IndexError, KeyError, TypeError):
        return "", ""


def apply_asr_refine(
    text: str,
    *,
    diarize: bool,
    trace_id: str = "",
) -> tuple[str, str, bool]:
    """Return (whisper_text, display_text, refined_ok).

    refined_ok is False whenever display_text is just the raw Whisper text
    because diarization wasn't attempted or fell back silently -- callers
    must use this (not just "diarize was requested") to decide whether to
    report a successful diarization to the client.
    """
    raw = (text or "").strip()
    if not raw or not diarize:
        return raw, raw, False
    try:
        refined, ok = refine_asr_transcript(raw, trace_id=trace_id)
        return raw, (refined if ok else raw), ok
    except Exception as exc:
        logger.warning("asr_refine failed trace=%s err=%s", trace_id, exc)
        return raw, raw, False
