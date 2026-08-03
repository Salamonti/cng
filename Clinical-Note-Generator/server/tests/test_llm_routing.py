# server/tests/test_llm_routing.py
import pytest

from server.core import llm_routing as lr


def test_note_gen_falls_back_to_notegen(monkeypatch):
    monkeypatch.delenv("LLM_NOTE_GEN_URL", raising=False)
    monkeypatch.delenv("LLM_NOTE_GEN_URL_FALLBACK", raising=False)
    monkeypatch.setenv("NOTEGEN_URL_PRIMARY", "http://127.0.0.1:9001")
    monkeypatch.setenv("NOTEGEN_URL_FALLBACK", "http://127.0.0.1:9002")
    p, f = lr.resolve_llm_urls("note_gen")
    assert p == "http://127.0.0.1:9001"
    assert f == "http://127.0.0.1:9002"


def test_note_gen_explicit_overrides_notegen(monkeypatch):
    monkeypatch.setenv("LLM_NOTE_GEN_URL", "http://10.0.0.1:1")
    monkeypatch.setenv("LLM_NOTE_GEN_URL_FALLBACK", "http://10.0.0.2:2")
    monkeypatch.setenv("NOTEGEN_URL_PRIMARY", "http://127.0.0.1:8081")
    p, f = lr.resolve_llm_urls("note_gen")
    assert p == "http://10.0.0.1:1"
    assert f == "http://10.0.0.2:2"


def test_qa_text_independent(monkeypatch):
    monkeypatch.setenv("LLM_QA_TEXT_URL", "http://qa:7777")
    monkeypatch.delenv("LLM_QA_TEXT_URL_FALLBACK", raising=False)
    monkeypatch.setenv("NOTEGEN_URL_PRIMARY", "http://127.0.0.1:8081")
    p, f = lr.resolve_llm_urls("qa_text")
    assert p == "http://qa:7777"
    assert f is None


def test_rag_comment_env_primary(monkeypatch):
    monkeypatch.setenv("LLM_RAG_COMMENT_URL", "http://rag:1")
    monkeypatch.setenv("LLM_RAG_COMMENT_URL_FALLBACK", "http://rag:2")
    p, f = lr.resolve_rag_comment_urls({})
    assert p == "http://rag:1"
    assert f == "http://rag:2"


def test_rag_comment_falls_back_to_note_gen(monkeypatch):
    monkeypatch.delenv("LLM_RAG_COMMENT_URL", raising=False)
    monkeypatch.delenv("LLM_RAG_COMMENT_URL_FALLBACK", raising=False)
    # P2-1: service_endpoints.json now sets LLM_NOTE_GEN_URL_FALLBACK by
    # default (cross-wired to the vision backend), and apply_service_endpoints()
    # loads that into os.environ once for the whole test process -- clear it
    # explicitly so this test verifies the fallthrough *mechanism* (rag_comment
    # with no config of its own reuses note_gen's routing) rather than
    # accidentally asserting on whatever note_gen's real-world default happens
    # to be.
    monkeypatch.delenv("LLM_NOTE_GEN_URL_FALLBACK", raising=False)
    monkeypatch.delenv("NOTEGEN_URL_FALLBACK", raising=False)
    monkeypatch.setenv("NOTEGEN_URL_PRIMARY", "http://127.0.0.1:8081")
    p, f = lr.resolve_rag_comment_urls({})
    assert p == "http://127.0.0.1:8081"
    assert f is None


def test_resolve_unknown_feature():
    with pytest.raises(ValueError):
        lr.resolve_llm_urls("nope")
