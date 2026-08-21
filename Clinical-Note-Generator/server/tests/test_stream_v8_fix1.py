"""Fix 1 (true streaming + salvage) unit tests.

Covers:
  * _LineBuffer line-buffer semantics (chunked delivery, partial-tail holdback,
    flush).
  * _stream_response_v8: accepted note -> exactly one NOTE_FINAL marker with
    salvaged=false and the sanitized authoritative text; markers never leak
    into live lines.
  * _stream_response_v8: two-attempt rejection with a draft -> NOTE_FINAL with
    salvaged=true + reasons, and a NOTE_RETRY marker between attempts.
  * _stream_response_v8: double rejection with no draft text -> raises
    ClinicalOutputRejected (route renders the existing error path).
  * clean_chunk (EMR ASCII translation) is applied to the authoritative text.
"""
import json
from typing import Dict, List, Optional

import pytest

from server.core.clinical_output_guard import (
    ClinicalOutputRejected,
    sanitize_clinical_note,
    validate_clinical_note,
)
from server.core.streaming import helpers
from server.core.streaming.helpers import (
    NOTE_FINAL_MARKER,
    NOTE_RETRY_MARKER,
    _LineBuffer,
    _stream_response_v8,
    build_note_final_marker,
    parse_note_final_marker,
)


# ---------------------------------------------------------------------------
# _LineBuffer
# ---------------------------------------------------------------------------

def test_line_buffer_no_newline_yields_nothing():
    buf = _LineBuffer()
    assert buf.add("abc") == []
    assert buf.flush() == "abc"


def test_line_buffer_complete_line():
    buf = _LineBuffer()
    assert buf.add("abc\n") == ["abc"]
    assert buf.flush() == ""


def test_line_buffer_split_across_chunks():
    buf = _LineBuffer()
    assert buf.add("line one par") == []
    assert buf.add("tial\ntwo") == ["line one partial"]
    assert buf.add("\nthree") == ["two"]
    assert buf.flush() == "three"


def test_line_buffer_multiple_lines_one_chunk():
    buf = _LineBuffer()
    assert buf.add("a\nb\nc") == ["a", "b"]
    assert buf.flush() == "c"


def test_line_buffer_blank_lines_preserved():
    buf = _LineBuffer()
    assert buf.add("a\n\nb\n") == ["a", "", "b"]
    assert buf.flush() == ""


def test_line_buffer_blank_line_held_until_completing():
    buf = _LineBuffer()
    assert buf.add("a\n\nb") == ["a", ""]
    assert buf.flush() == "b"


# ---------------------------------------------------------------------------
# marker round-trip
# ---------------------------------------------------------------------------

def test_note_final_marker_roundtrip():
    line = build_note_final_marker(
        text="SUBJECTIVE: x", salvaged=True, reasons=["r1", "r2"]
    )
    assert line.startswith(NOTE_FINAL_MARKER)
    parsed = parse_note_final_marker(line + "\n")
    assert parsed == {
        "text": "SUBJECTIVE: x",
        "salvaged": True,
        "reasons": ["r1", "r2"],
    }
    assert parse_note_final_marker("just a note line") is None


# ---------------------------------------------------------------------------
# _stream_response_v8 scenarios
# ---------------------------------------------------------------------------

class _FakeAcceptingGenerator:
    """Stream a note that passes the (monkeypatched) guard on attempt 1."""

    async def stream_completion(self, prompt, **_kwargs):
        # Chunked on purpose: exercises line buffering across chunk
        # boundaries, including a marker-free tail.
        for piece in ["SUBJEC", "TIVE: patient OK\n", "ASSESSMENT: stable\n", "PLAN: none"]:
            yield piece

    async def collect_completion(self, prompt, **_kwargs):
        return "SUBJECTIVE: patient OK\nASSESSMENT: stable\nPLAN: none"


class _Verdict:
    def __init__(self, accepted: bool, reasons):
        self.accepted = accepted
        self.reasons = list(reasons)


class _AlwaysRejectingValidator:
    """validate_clinical_note stub: always rejected with fixed reasons."""

    def __init__(self, reasons=("grounding failed",)):
        self.reasons = tuple(reasons)

    def __call__(self, prompt, text):
        return _Verdict(False, self.reasons)


class _AcceptingValidator:
    def __call__(self, prompt, text):
        return _Verdict(True, [])


def _patch_guard(monkeypatch, validator):
    """Makes sanitize a pass-through and installs `validator` on the helpers
    module (where _stream_response_v8 looks it up)."""
    monkeypatch.setattr(
        helpers, "sanitize_clinical_note",
        lambda prompt, output: output,
    )
    monkeypatch.setattr(helpers, "validate_clinical_note", validator)


def test_v8_accepted_yields_lines_then_final_marker(monkeypatch):
    _patch_guard(monkeypatch, _AcceptingValidator())

    chunks: List[str] = []

    async def collect():
        async for chunk in _stream_response_v8(
            note_gen=_FakeAcceptingGenerator(),
            prompt="PROMPT",
            temperature=0.1,
            max_tokens=100,
            stop_tokens=None,
            clean_chunk=lambda x: x,
        ):
            chunks.append(chunk)

    import asyncio
    asyncio.run(collect())

    joined = "".join(chunks)
    final_idx = joined.index(NOTE_FINAL_MARKER)
    # Live lines precede the marker; exactly one marker; no RETRY.
    assert joined.count(NOTE_FINAL_MARKER) == 1
    assert NOTE_RETRY_MARKER not in joined
    live = joined[:final_idx]
    assert "SUBJECTIVE: patient OK" in live
    assert "PLAN: none" in live
    payload = parse_note_final_marker(
        joined[final_idx:].split("\n", 1)[0]
    )
    assert payload["salvaged"] is False
    assert payload["reasons"] == []
    assert payload["text"] == "SUBJECTIVE: patient OK\nASSESSMENT: stable\nPLAN: none"


def test_v8_double_rejection_saluves_draft(monkeypatch):
    _patch_guard(monkeypatch, _AlwaysRejectingValidator(("no grounding on attempt 1",)))

    calls = {"n": 0}

    class _Gen:
        async def stream_completion(self, prompt, **_kwargs):
            calls["n"] += 1
            yield "DRAFT TEXT attempt\n"

        async def collect_completion(self, prompt, **_kwargs):
            raise AssertionError("must stream, not collect")

    chunks: List[str] = []

    async def collect():
        async for chunk in _stream_response_v8(
            note_gen=_Gen(),
            prompt="PROMPT",
            temperature=0.1,
            max_tokens=100,
            stop_tokens=None,
            clean_chunk=lambda x: x,
        ):
            chunks.append(chunk)

    import asyncio
    asyncio.run(collect())

    joined = "".join(chunks)
    assert calls["n"] == 2, "exactly two model attempts, no loop"
    assert joined.count(NOTE_RETRY_MARKER) == 1
    assert joined.count(NOTE_FINAL_MARKER) == 1
    # Retry marker comes after attempt-1 text, before attempt-2 text.
    retry_i = joined.index(NOTE_RETRY_MARKER)
    final_i = joined.index(NOTE_FINAL_MARKER)
    assert joined.index("DRAFT TEXT attempt") < retry_i < final_i
    payload = parse_note_final_marker(joined[final_i:].split("\n", 1)[0])
    assert payload["salvaged"] is True
    assert payload["text"].rstrip() == "DRAFT TEXT attempt"
    assert payload["reasons"] == ["no grounding on attempt 1"]


def test_v8_double_rejection_no_draft_raises(monkeypatch):
    _patch_guard(monkeypatch, _AlwaysRejectingValidator())

    class _EmptyGen:
        async def stream_completion(self, prompt, **_kwargs):
            return
            yield  # pragma: no cover

        async def collect_completion(self, prompt, **_kwargs):
            return ""

    async def collect():
        out = []
        async for chunk in _stream_response_v8(
            note_gen=_EmptyGen(),
            prompt="PROMPT",
            temperature=0.1,
            max_tokens=100,
            stop_tokens=None,
            clean_chunk=lambda x: x,
        ):
            out.append(chunk)
        return out

    import asyncio
    with pytest.raises(ClinicalOutputRejected):
        asyncio.run(collect())


def test_v8_truncation_exception_falls_to_retry_then_salvages(monkeypatch):
    """stream_completion raising ClinicalOutputRejected mid-stream (truncation)
    must trigger the retry path and salvage whatever partial text exists."""
    _patch_guard(monkeypatch, _AlwaysRejectingValidator())

    class _TruncGen:
        async def stream_completion(self, prompt, **_kwargs):
            yield "PARTIAL FIRST\n"
            raise ClinicalOutputRejected("truncated", draft="PARTIAL FIRST")

        async def collect_completion(self, prompt, **_kwargs):
            raise AssertionError("must stream")

    chunks: List[str] = []

    async def collect():
        async for chunk in _stream_response_v8(
            note_gen=_TruncGen(),
            prompt="PROMPT",
            temperature=0.1,
            max_tokens=100,
            stop_tokens=None,
            clean_chunk=lambda x: x,
        ):
            chunks.append(chunk)

    import asyncio
    asyncio.run(collect())
    joined = "".join(chunks)
    assert NOTE_RETRY_MARKER in joined
    final_line = joined.split(NOTE_FINAL_MARKER, 1)[1].split("\n", 1)[0]
    payload = parse_note_final_marker(NOTE_FINAL_MARKER + final_line)
    assert payload["salvaged"] is True
    assert payload["text"].rstrip() == "PARTIAL FIRST"
    assert payload["reasons"] == ["truncated"]


def test_v8_clean_chunk_applied_to_authoritative_text(monkeypatch):
    """EMR ASCII translation (subscripts/dashes) must appear in the final
    authoritative text, not just the live preview."""

    def fake_validate(prompt, text):
        # Record what the validator saw so we can assert EMR-cleaning happened
        # BEFORE validation.
        seen["text"] = text

        class _R:
            accepted = True
            reasons = []
        return _R()

    seen: Dict[str, str] = {}
    monkeypatch.setattr(
        helpers, "sanitize_clinical_note", lambda prompt, output: output
    )
    monkeypatch.setattr(helpers, "validate_clinical_note", fake_validate)

    def emr_clean(text: str) -> str:
        return text.replace("\u2081", "1").replace("\u2014", "-")

    class _Gen:
        async def stream_completion(self, prompt, **_kwargs):
            yield "FEV\u2081 0.7\u20140.8\n"

        async def collect_completion(self, prompt, **_kwargs):
            raise AssertionError

    async def collect():
        out = []
        async for chunk in _stream_response_v8(
            note_gen=_Gen(),
            prompt="PROMPT",
            temperature=0.1,
            max_tokens=100,
            stop_tokens=None,
            clean_chunk=emr_clean,
        ):
            out.append(chunk)
        return out

    import asyncio
    joined = "".join(asyncio.run(collect()))
    final_line = joined.split(NOTE_FINAL_MARKER, 1)[1].split("\n", 1)[0]
    payload = parse_note_final_marker(NOTE_FINAL_MARKER + final_line)
    assert payload["text"].rstrip() == "FEV1 0.7-0.8"
    assert seen["text"].rstrip() == "FEV1 0.7-0.8"
