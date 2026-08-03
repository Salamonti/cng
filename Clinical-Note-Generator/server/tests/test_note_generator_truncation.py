"""Regression test (WO-1 Part C): collect_completion() must not return a
max_tokens-truncated note as if it were a complete one.

Before this fix, note_generator_clean.py never read finish_reason from the
LLM response -- unlike asr_refine.py (test_asr_refine_truncation.py) and
ocr_llm_client.py, which already treat finish_reason == "length" as a
truncation signal. A note that hit the token cap mid-plan came back from
_extract_stream_content() looking like a normal, complete result.
"""
import pytest

from server.core.clinical_output_guard import ClinicalOutputRejected
from server.services.note_generator_clean import SimpleNoteGenerator


def _make_gen(monkeypatch, primary="http://primary:8080", fallback="http://fallback:8080"):
    gen = SimpleNoteGenerator(explicit_urls=(primary, fallback))
    monkeypatch.setattr(gen, "_resolve_model_id_for_url", lambda base_url: _async_return("model-x"))
    monkeypatch.setattr(gen, "_reset_context", lambda base_url: _async_return(None))
    monkeypatch.setattr(gen, "_build_payload", lambda *a, **k: ({}, "/v1/chat/completions", True))
    return gen


async def _async_return(value):
    return value


def _chat_response(content, finish_reason):
    return {"choices": [{"message": {"content": content}, "finish_reason": finish_reason}]}


@pytest.mark.anyio
async def test_finish_reason_length_raises_instead_of_returning_truncated_note(monkeypatch):
    gen = _make_gen(monkeypatch)

    async def fake_collect_json_response(payload, endpoint, base_urls=None, timeout_sec=None):
        base_url = base_urls[0]
        return _chat_response("SUBJECTIVE: patient reports... PLAN: increase dose of", "length"), base_url

    monkeypatch.setattr(gen, "_collect_json_response", fake_collect_json_response)

    with pytest.raises(ClinicalOutputRejected, match="truncated"):
        await gen.collect_completion("prompt", 0.0, 8192)


@pytest.mark.anyio
async def test_finish_reason_stop_with_full_note_returns_normally(monkeypatch):
    gen = _make_gen(monkeypatch)

    async def fake_collect_json_response(payload, endpoint, base_urls=None, timeout_sec=None):
        base_url = base_urls[0]
        return _chat_response("SUBJECTIVE: patient reports feeling well. PLAN: continue current regimen.", "stop"), base_url

    monkeypatch.setattr(gen, "_collect_json_response", fake_collect_json_response)

    result = await gen.collect_completion("prompt", 0.0, 8192)

    assert result == "SUBJECTIVE: patient reports feeling well. PLAN: continue current regimen."
