"""Regression test: collect_completion() must fail over to the next
candidate URL when a server returns genuinely empty content, instead of
returning "" as if that were a successful result.

Before this fix, collect_completion()'s for-loop over candidate base URLs
only failed over on an HTTP-level ExternalServiceError. If a server
responded 200 with empty content (no exception raised), the function
returned "" (or the legacy-endpoint retry's empty result) immediately --
it never continued the loop to try the fallback URL. A primary server
returning empty content (content filter, sampling glitch, misconfigured
model) silently produced an empty note instead of trying the configured
fallback.
"""
import pytest

from server.services.note_generator_clean import ExternalServiceError, SimpleNoteGenerator


def _make_gen(monkeypatch, primary="http://primary:8080", fallback="http://fallback:8080"):
    gen = SimpleNoteGenerator(explicit_urls=(primary, fallback))
    monkeypatch.setattr(gen, "_resolve_model_id_for_url", lambda base_url: _async_return("model-x"))
    monkeypatch.setattr(gen, "_reset_context", lambda base_url: _async_return(None))
    return gen


async def _async_return(value):
    return value


@pytest.mark.anyio
async def test_empty_content_on_primary_falls_over_to_fallback(monkeypatch):
    gen = _make_gen(monkeypatch)

    async def fake_collect_json_response(payload, endpoint, base_urls=None, timeout_sec=None):
        base_url = base_urls[0]
        if base_url == gen.primary_url:
            return {"content": ""}, base_url  # 200 OK, but empty -- not an HTTP error
        return {"content": "Real note text from fallback"}, base_url

    monkeypatch.setattr(gen, "_collect_json_response", fake_collect_json_response)
    monkeypatch.setattr(gen, "_build_payload", lambda *a, **k: ({}, "/completion", False))

    result = await gen.collect_completion("prompt", 0.2, 512)

    assert result == "Real note text from fallback"


@pytest.mark.anyio
async def test_empty_content_on_both_urls_raises_instead_of_returning_empty_string(monkeypatch):
    gen = _make_gen(monkeypatch)

    async def fake_collect_json_response(payload, endpoint, base_urls=None, timeout_sec=None):
        return {"content": ""}, base_urls[0]

    monkeypatch.setattr(gen, "_collect_json_response", fake_collect_json_response)
    monkeypatch.setattr(gen, "_build_payload", lambda *a, **k: ({}, "/completion", False))

    with pytest.raises(ExternalServiceError):
        await gen.collect_completion("prompt", 0.2, 512)


@pytest.mark.anyio
async def test_chat_empty_then_legacy_fallback_empty_still_tries_next_url(monkeypatch):
    gen = _make_gen(monkeypatch)
    calls = []

    async def fake_collect_json_response(payload, endpoint, base_urls=None, timeout_sec=None):
        base_url = base_urls[0]
        calls.append((base_url, endpoint))
        if base_url == gen.primary_url:
            return {"content": ""}, base_url
        return {"content": "Fallback URL success"}, base_url

    monkeypatch.setattr(gen, "_collect_json_response", fake_collect_json_response)

    # Simulate chat API in use (used_chat=True) so the legacy-retry path executes
    # for the primary URL; force_chat=False marks the legacy /completion retry.
    def fake_build_payload(prompt, temperature, max_tokens, stream, stop, force_chat=None, model_name=None):
        if force_chat is False:
            return {}, "/completion", False
        return {}, "/v1/chat/completions", True

    monkeypatch.setattr(gen, "_build_payload", fake_build_payload)

    result = await gen.collect_completion("prompt", 0.2, 512)

    assert result == "Fallback URL success"
    # Primary tried via chat once, then legacy /completion once, before moving to fallback.
    assert calls == [
        (gen.primary_url, "/v1/chat/completions"),
        (gen.primary_url, "/completion"),
        (gen.fallback_url, "/v1/chat/completions"),
    ]


@pytest.mark.anyio
async def test_first_url_succeeds_without_trying_second(monkeypatch):
    gen = _make_gen(monkeypatch)
    calls = []

    async def fake_collect_json_response(payload, endpoint, base_urls=None, timeout_sec=None):
        calls.append(base_urls[0])
        return {"content": "Primary works fine"}, base_urls[0]

    monkeypatch.setattr(gen, "_collect_json_response", fake_collect_json_response)
    monkeypatch.setattr(gen, "_build_payload", lambda *a, **k: ({}, "/completion", False))

    result = await gen.collect_completion("prompt", 0.2, 512)

    assert result == "Primary works fine"
    assert calls == [gen.primary_url]
