"""Regression test (P2-1): refine_asr_transcript() must fail over to the
configured fallback backend when the primary is unreachable, instead of
giving up entirely. Before this fix, resolve_llm_urls("asr_refine")
hardcoded None for the fallback regardless of any env var, and even the
one call site that used the tuple threw the second value away
(`base_url, _fallback = resolve_llm_urls(...)`) -- there was no failover
support at all for this feature, unlike note_generator_clean.py.
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


def test_falls_over_to_fallback_backend_when_primary_is_unreachable(monkeypatch):
    monkeypatch.setattr(
        asr_refine, "resolve_llm_urls", lambda feature: ("http://primary:9", "http://fallback:9")
    )
    monkeypatch.setattr(asr_refine, "_resolve_model_id", lambda base_url: "fake-model")

    calls = []

    def fake_post(url, json=None, timeout=None):
        calls.append(url)
        if "primary" in url:
            raise ConnectionError("primary is down")
        return _FakeResponse(_chat_payload("Doctor: Hi.\nPatient: Hi.", finish_reason="stop"))

    monkeypatch.setattr(asr_refine.requests, "post", fake_post)

    raw = "doctor hi patient hi"
    text, ok = asr_refine.refine_asr_transcript(raw, trace_id="failover-1")

    assert ok is True
    assert text == "Doctor: Hi.\nPatient: Hi."
    assert any("primary" in c for c in calls)
    assert any("fallback" in c for c in calls)


def test_reports_failure_when_both_primary_and_fallback_are_unreachable(monkeypatch):
    monkeypatch.setattr(
        asr_refine, "resolve_llm_urls", lambda feature: ("http://primary:9", "http://fallback:9")
    )
    monkeypatch.setattr(asr_refine, "_resolve_model_id", lambda base_url: "fake-model")

    def fake_post(url, json=None, timeout=None):
        raise ConnectionError(f"{url} is down")

    monkeypatch.setattr(asr_refine.requests, "post", fake_post)

    raw = "doctor hi patient hi"
    text, ok = asr_refine.refine_asr_transcript(raw, trace_id="failover-2")

    assert ok is False
    assert text == raw.strip()


def test_no_fallback_configured_behaves_exactly_as_before(monkeypatch):
    monkeypatch.setattr(asr_refine, "resolve_llm_urls", lambda feature: ("http://only-primary:9", None))
    monkeypatch.setattr(asr_refine, "_resolve_model_id", lambda base_url: "fake-model")

    calls = []

    def fake_post(url, json=None, timeout=None):
        calls.append(url)
        return _FakeResponse(_chat_payload("Doctor: Hi.\nPatient: Hi.", finish_reason="stop"))

    monkeypatch.setattr(asr_refine.requests, "post", fake_post)

    text, ok = asr_refine.refine_asr_transcript("doctor hi patient hi", trace_id="no-fallback")

    assert ok is True
    assert calls == ["http://only-primary:9/v1/chat/completions"]
