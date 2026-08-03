"""Regression test: max_tokens-truncated LLM output must not be reported
as a successful diarization.

Before this fix, refine_asr_transcript() only detected truncation via a
char-length-ratio heuristic gated behind a 192k-char threshold (~48k
tokens). A moderate-length transcript (well under that threshold) that
still exceeds the much lower max_tokens cap would come back from the LLM
with finish_reason="length" -- generation cut off mid-transcript -- and
the old code had no way to notice: it would return the truncated text with
ok=True, silently dropping the back half of the encounter with no flag to
the caller. Separately, even the 192k-char heuristic itself only logged a
warning and still returned ok=True on the path that detected likely
truncation, rather than falling back and reporting failure.
"""
from server.core import asr_refine


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _chat_payload(content, finish_reason="stop"):
    return {"choices": [{"message": {"content": content}, "finish_reason": finish_reason}]}


def _setup(monkeypatch, payload):
    monkeypatch.setattr(asr_refine, "resolve_llm_urls", lambda feature: ("http://fake-llm:9", None))
    monkeypatch.setattr(asr_refine, "_resolve_model_id", lambda base_url: "fake-model")
    monkeypatch.setattr(
        asr_refine.requests, "post", lambda *a, **k: _FakeResponse(payload)
    )


def test_finish_reason_length_falls_back_to_raw_and_reports_failure(monkeypatch):
    raw = "Doctor asks how are you feeling today patient says fine thanks. " * 20
    truncated = "Doctor: How are you feeling today?\nPatient: Fine, thanks."
    _setup(monkeypatch, _chat_payload(truncated, finish_reason="length"))

    text, ok = asr_refine.refine_asr_transcript(raw, trace_id="t1")

    assert ok is False
    assert text == raw.strip()


def test_finish_reason_stop_with_full_output_reports_success(monkeypatch):
    raw = "doctor how are you feeling patient fine thanks"
    refined = "Doctor: How are you feeling?\nPatient: Fine, thanks."
    _setup(monkeypatch, _chat_payload(refined, finish_reason="stop"))

    text, ok = asr_refine.refine_asr_transcript(raw, trace_id="t2")

    assert ok is True
    assert text == refined


def test_long_transcript_length_ratio_truncation_falls_back_to_raw(monkeypatch):
    raw = "word " * 40000  # > 192k chars
    refined = "Doctor: " + ("word " * 100)  # << 70% of raw, no finish_reason signal
    _setup(monkeypatch, _chat_payload(refined, finish_reason="stop"))

    text, ok = asr_refine.refine_asr_transcript(raw, trace_id="t3")

    assert ok is False
    assert text == raw.strip()


def test_severely_short_output_still_falls_back_to_raw(monkeypatch):
    raw = "doctor how are you feeling patient fine thanks today"
    refined = "ok"
    _setup(monkeypatch, _chat_payload(refined, finish_reason="stop"))

    text, ok = asr_refine.refine_asr_transcript(raw, trace_id="t4")

    assert ok is False
    assert text == raw.strip()
