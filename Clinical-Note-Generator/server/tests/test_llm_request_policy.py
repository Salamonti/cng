from server.core.llm_request_policy import (
    apply_dreamcision_no_thinking,
    strip_reasoning_markup,
)
from server.services.note_generator_clean import SimpleNoteGenerator


def test_no_thinking_policy_overrides_caller_with_explicit_none():
    payload = apply_dreamcision_no_thinking(
        {
            "reasoning_effort": "high",
            "chat_template_kwargs": {"enable_thinking": True, "custom": "kept"},
        }
    )

    assert payload["reasoning_effort"] == "none"
    assert payload["chat_template_kwargs"] == {
        "enable_thinking": False,
        "thinking": False,
        "reasoning_effort": "none",
        "custom": "kept",
    }


def test_strip_reasoning_markup_handles_closed_and_unclosed_blocks():
    assert strip_reasoning_markup("<think>private</think>Visible") == "Visible"
    assert strip_reasoning_markup("<analysis>private\nmore") == ""
    assert strip_reasoning_markup("private</redacted_thinking>Visible") == "Visible"


def test_vllm_chat_payload_uses_supported_sampler_names_and_no_thinking():
    engine = SimpleNoteGenerator(explicit_urls=("http://127.0.0.1:9", None))
    payload = engine._build_chat_payload(
        "prompt",
        temperature=0.2,
        max_tokens=256,
        stream=True,
        stop=[],
        model_name="model",
    )

    assert payload["reasoning_effort"] == "none"
    assert payload["chat_template_kwargs"]["enable_thinking"] is False
    assert payload["chat_template_kwargs"]["thinking"] is False
    assert payload["chat_template_kwargs"]["reasoning_effort"] == "none"
    assert payload["repetition_penalty"] == engine.config.get(
        "default_repeat_penalty", 1.25
    )
    assert "repeat_penalty" not in payload
    assert "repeat_last_n" not in payload
    assert "n_predict" not in payload
    assert "min_p" not in payload


def test_only_primary_url_remains_available_during_cooldown():
    engine = SimpleNoteGenerator(explicit_urls=("http://primary", None))
    engine._primary_down_until = float("inf")

    assert engine._candidate_urls() == ["http://primary"]


def test_primary_cooldown_prefers_configured_fallback():
    engine = SimpleNoteGenerator(
        explicit_urls=("http://primary", "http://fallback")
    )
    engine._primary_down_until = float("inf")

    assert engine._candidate_urls() == ["http://fallback"]


def test_request_timeout_defaults_to_sixty_seconds(monkeypatch):
    # WO-1 Part D: 600s was three orders of magnitude of useless waiting for a
    # 5-8s typical generation. 60s covers an 8192-token note at solo
    # throughput (~27s) with headroom for shared-load slowdown.
    monkeypatch.delenv("LLM_REQUEST_TIMEOUT_SEC", raising=False)
    engine = SimpleNoteGenerator(explicit_urls=("http://primary", None))
    engine.config.pop("llm_request_timeout_sec", None)

    assert engine._request_timeout_seconds(None) == 60.0
    assert engine._request_timeout_seconds(12.5) == 12.5


def test_request_timeout_accepts_environment_override(monkeypatch):
    monkeypatch.setenv("LLM_REQUEST_TIMEOUT_SEC", "720")
    engine = SimpleNoteGenerator(explicit_urls=("http://primary", None))

    assert engine._request_timeout_seconds(None) == 720.0
