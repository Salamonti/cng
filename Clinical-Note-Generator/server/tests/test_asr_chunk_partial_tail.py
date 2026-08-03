"""Tests for partial_tail committed text resolution."""
from __future__ import annotations

from server.core.asr_chunk import resolve_committed_text


def test_partial_tail_appends_novel_words():
    prev = "patient has chest pain and shortness of breath"
    full = "thank you goodbye"
    committed, method = resolve_committed_text(
        segment_role="partial_tail",
        chunk_index=6,
        window_start_sec=145.0,
        payload={"text": full},
        full_text=full,
        prev_text=prev,
    )
    assert method.startswith("partial_tail")
    assert "thank you" in committed or "goodbye" in committed


def test_partial_tail_timestamp_window_would_be_empty():
    """Tail segments must not rely on absolute timestamp windows."""
    prev = "one two three four five"
    full = "six seven eight"
    committed, method = resolve_committed_text(
        segment_role="partial_tail",
        chunk_index=6,
        window_start_sec=145.0,
        payload={
            "segments": [
                {
                    "start": 0.0,
                    "end": 2.0,
                    "text": " six seven eight",
                    "words": [
                        {"word": "six", "start": 0.1},
                        {"word": "seven", "start": 0.5},
                        {"word": "eight", "start": 1.0},
                    ],
                }
            ]
        },
        full_text=full,
        prev_text=prev,
    )
    assert committed
    assert "six" in committed or "seven" in committed


def test_partial_tail_overlap_with_prev_returns_empty():
    prev = "patient says thank you goodbye"
    full = "thank you goodbye"
    committed, method = resolve_committed_text(
        segment_role="partial_tail",
        chunk_index=3,
        window_start_sec=70.0,
        payload={"text": full},
        full_text=full,
        prev_text=prev,
    )
    assert committed == ""
    assert method == "partial_tail_no_delta"
