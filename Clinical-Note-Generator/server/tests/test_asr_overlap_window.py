"""Tests for chunk overlap window math and ffmpeg filter construction."""
from __future__ import annotations

from server.core.asr_chunk import overlap_tail_start_sec
from server.core.chunk_merge import COMMIT_STRIDE_SEC, OVERLAP_SEC, WHISPER_WINDOW_SEC


def test_stride_window_invariant():
    assert WHISPER_WINDOW_SEC == COMMIT_STRIDE_SEC + OVERLAP_SEC


def test_overlap_tail_start_uses_last_seconds():
    assert overlap_tail_start_sec(25.0) == 20.0
    assert overlap_tail_start_sec(10.0) == 5.0
    assert overlap_tail_start_sec(3.0) == 0.0


def test_client_rotation_should_match_stride():
    """Document the required client timer (25s) alongside server stride."""
    assert COMMIT_STRIDE_SEC == 25.0
    assert int(COMMIT_STRIDE_SEC * 1000) == 25000
