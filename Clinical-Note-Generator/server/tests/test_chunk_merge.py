"""Tests for chunk transcript merge helpers."""
from __future__ import annotations

from server.core.chunk_merge import (
    commit_text_from_verbose,
    fuzzy_stitch,
    fuzzy_commit_delta,
    merge_committed_segments,
    parse_verbose_payload,
)


def test_parse_verbose_payload_text_and_segments():
    payload = {
        "text": "hello world",
        "segments": [{"start": 0.0, "end": 1.0, "text": " hello", "words": [{"word": "hello", "start": 0.0}]}],
    }
    full, segs = parse_verbose_payload(payload)
    assert "hello" in full
    assert len(segs) == 1


def test_commit_text_filters_by_timestamp_window():
    payload = {
        "segments": [
            {
                "start": 0.0,
                "end": 10.0,
                "text": " early late",
                "words": [
                    {"word": "early", "start": 0.5},
                    {"word": "late", "start": 26.0},
                ],
            }
        ]
    }
    committed, method = commit_text_from_verbose(payload, window_start_sec=0.0, chunk_index=1)
    assert method == "timestamp"
    assert "early" not in committed
    assert "late" in committed


def test_fuzzy_stitch_removes_overlap():
    prev = "patient has chest pain and shortness"
    inc = "shortness of breath today"
    out = fuzzy_stitch(prev, inc)
    assert "shortness" in out
    assert out.count("shortness") == 1


def test_fuzzy_stitch_dedupes_despite_casing_and_punctuation_difference():
    # P3-1: Whisper doesn't guarantee byte-identical output for the same
    # physical audio transcribed twice as part of two overlapping windows --
    # a capitalization or trailing-comma difference right at the seam used to
    # drop the overlap match to k=0 entirely (exact word-list equality),
    # duplicating the whole overlapping phrase in the note instead of
    # deduplicating it.
    prev = "Patient reports chest pain, and shortness"
    inc = "shortness of breath today"
    out = fuzzy_stitch(prev, inc)
    assert out.lower().count("shortness") == 1


def test_merge_committed_segments_joins():
    assert merge_committed_segments(["one two", "three"]) == "one two three"


def test_commit_text_uses_real_boundaries_over_theoretical_grid():
    # P3-1: a word spoken right at the true chunk boundary, in a session where
    # real per-chunk duration has drifted below the theoretical 25.0s (jitter
    # compounding over a long encounter). With the theoretical grid, chunk 1's
    # window is exactly [25.0, 50.0) -- a word at absolute time 24.6s (inside
    # the REAL boundary, since the real chunk 0 only ran 24.5s) would fall
    # into neither chunk's window under the old fixed-grid math. Passing the
    # real boundaries explicitly must recover it.
    payload = {
        "segments": [
            {
                "start": 0.0,
                "end": 5.0,
                "text": " boundary word",
                "words": [{"word": "boundary", "start": 0.1}],
            }
        ]
    }
    # Real chunk 0 ran 24.5s (not the theoretical 25.0s), so chunk 1's real
    # window starts at 24.5, not 25.0. window_start_sec anchors this window's
    # own (relative-to-file) timestamps to that real absolute start.
    committed, method = commit_text_from_verbose(
        payload,
        window_start_sec=24.5,
        chunk_index=1,
        commit_start_sec=24.5,
        commit_end_sec=49.5,
    )
    assert method == "timestamp"
    assert "boundary" in committed

    # Prove the theoretical grid actually would have dropped it: with no
    # explicit real boundaries, chunk 1's window is [25.0, 50.0) and this
    # word's real absolute time (24.6) falls just outside it.
    dropped, dropped_method = commit_text_from_verbose(
        payload, window_start_sec=24.5, chunk_index=1
    )
    assert dropped_method != "timestamp"
    assert "boundary" not in dropped


def test_fuzzy_commit_delta_returns_only_new_text():
    prev = "patient has chest pain and shortness"
    inc = "shortness of breath today"
    delta = fuzzy_commit_delta(prev, inc)
    assert "chest pain" not in delta
    assert "breath" in delta
