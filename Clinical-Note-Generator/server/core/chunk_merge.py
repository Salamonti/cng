"""Merge Whisper verbose_json chunk transcripts with 25s commit / 5s overlap stride.

Invariant (client + server must match):
  rotation_interval_sec == COMMIT_STRIDE_SEC
  WHISPER_WINDOW_SEC == COMMIT_STRIDE_SEC + OVERLAP_SEC
"""
from __future__ import annotations

import json
import re
from typing import Any, List, Optional

COMMIT_STRIDE_SEC = 25.0
OVERLAP_SEC = 5.0
WHISPER_WINDOW_SEC = 30.0


def _norm_ws(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def parse_verbose_payload(payload: Any) -> tuple[str, list[dict]]:
    """Return (full_text, segments) from whisper verbose_json or json."""
    if not isinstance(payload, dict):
        return "", []
    segments = payload.get("segments")
    if not isinstance(segments, list):
        segments = []
    full = payload.get("text")
    if isinstance(full, str) and full.strip():
        return full.strip(), [s for s in segments if isinstance(s, dict)]
    parts: List[str] = []
    for seg in segments:
        if isinstance(seg, dict):
            txt = seg.get("text")
            if isinstance(txt, str) and txt.strip():
                parts.append(txt.strip())
    return _norm_ws(" ".join(parts)), [s for s in segments if isinstance(s, dict)]


def commit_text_from_verbose(
    payload: Any,
    *,
    window_start_sec: float,
    chunk_index: int,
    commit_start_sec: Optional[float] = None,
    commit_end_sec: Optional[float] = None,
) -> tuple[str, str]:
    """Extract committed slice for chunk_index from verbose_json timestamps.

    commit_start_sec/commit_end_sec, when given, come from the REAL
    cumulative duration of prior chunks in this recording session --
    MediaRecorder's requested timeslice is not a hardware guarantee, and
    actual ondataavailable firing drifts by tens to hundreds of ms per
    chunk. That drift compounds: a 30-minute encounter is ~72 chunks, and
    even 50ms of average drift is 3.6s of accumulated offset by the end,
    enough to push boundary words outside the theoretical [chunk_index*25,
    (chunk_index+1)*25) window entirely. Falls back to that theoretical
    grid when real durations aren't available (e.g. a segment upload that
    predates duration_sec being populated, or a gap in the chain).
    """
    _full, segments = parse_verbose_payload(payload)
    commit_start = commit_start_sec if commit_start_sec is not None else chunk_index * COMMIT_STRIDE_SEC
    commit_end = commit_end_sec if commit_end_sec is not None else (chunk_index + 1) * COMMIT_STRIDE_SEC

    words: List[str] = []
    for seg in segments:
        if not isinstance(seg, dict):
            continue
        seg_words = seg.get("words")
        if isinstance(seg_words, list) and seg_words:
            for w in seg_words:
                if not isinstance(w, dict):
                    continue
                txt = w.get("word") or w.get("text")
                if not isinstance(txt, str) or not txt.strip():
                    continue
                try:
                    start = float(w.get("start", seg.get("start", 0)))
                except (TypeError, ValueError):
                    start = float(seg.get("start") or 0)
                abs_start = window_start_sec + start
                if abs_start >= commit_start and abs_start < commit_end:
                    words.append(txt.strip())
            continue
        txt = seg.get("text")
        if not isinstance(txt, str) or not txt.strip():
            continue
        try:
            start = float(seg.get("start", 0))
            end = float(seg.get("end", start))
        except (TypeError, ValueError):
            start, end = 0.0, WHISPER_WINDOW_SEC
        abs_start = window_start_sec + start
        abs_end = window_start_sec + end
        if abs_end <= commit_start or abs_start >= commit_end:
            continue
        words.append(txt.strip())

    if words:
        committed = _norm_ws(" ".join(words))
        return committed, "timestamp"

    # Fallback: assign full segment text to chunk 0 only; later chunks use fuzzy later.
    if chunk_index == 0 and _full:
        return _full, "full_window"
    return "", "empty"


def merge_committed_segments(committed_parts: List[str]) -> str:
    parts = [_norm_ws(p) for p in committed_parts if _norm_ws(p)]
    return _norm_ws(" ".join(parts))


_TRAILING_PUNCT_RE = re.compile(r"^[^\w]+|[^\w]+$", re.UNICODE)


def _dedup_key(word: str) -> str:
    """Normalize a word for overlap-match comparison only (never for output).

    Whisper doesn't guarantee byte-identical output for the same physical
    audio transcribed as part of two different windows -- casing at a
    window's start, or trailing punctuation right at a cut point, can
    differ between the two passes over the same overlapping audio even
    though the words are the same. An exact list-equality match on raw
    words means a single such difference drops the match to k=0 and the
    whole overlap goes undeduplicated, duplicating a phrase in the note.
    """
    return _TRAILING_PUNCT_RE.sub("", word.lower())


def fuzzy_stitch(previous: str, incoming: str) -> str:
    """Append incoming after removing duplicated overlap with previous tail."""
    prev = _norm_ws(previous)
    inc = _norm_ws(incoming)
    if not prev:
        return inc
    if not inc:
        return prev
    prev_words = prev.split()
    inc_words = inc.split()
    prev_keys = [_dedup_key(w) for w in prev_words]
    inc_keys = [_dedup_key(w) for w in inc_words]
    max_k = min(20, len(prev_words), len(inc_words))
    best = 0
    for k in range(max_k, 0, -1):
        if prev_keys[-k:] == inc_keys[:k]:
            best = k
            break
    if best:
        inc_words = inc_words[best:]
    return _norm_ws(prev + " " + " ".join(inc_words))


def fuzzy_commit_delta(previous: str, incoming: str) -> str:
    """Return only the new text to append for this chunk window."""
    stitched = fuzzy_stitch(previous, incoming)
    prev = _norm_ws(previous)
    if not prev:
        return _norm_ws(incoming)
    if stitched == prev:
        return ""
    if stitched.startswith(prev):
        delta = stitched[len(prev):].strip()
        return _norm_ws(delta)
    return _norm_ws(incoming)


def payload_to_json_str(payload: Any) -> Optional[str]:
    try:
        return json.dumps(payload, ensure_ascii=False)[:500000]
    except Exception:
        return None
