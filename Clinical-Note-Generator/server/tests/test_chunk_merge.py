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


def test_merge_committed_segments_joins():
    assert merge_committed_segments(["one two", "three"]) == "one two three"


def test_fuzzy_commit_delta_returns_only_new_text():
    prev = "patient has chest pain and shortness"
    inc = "shortness of breath today"
    delta = fuzzy_commit_delta(prev, inc)
    assert "chest pain" not in delta
    assert "breath" in delta
