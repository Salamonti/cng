import json

from server.routes.qa_chat import (
    _compute_evidence_max_year,
    _strip_model_reasoning_markup,
    _strip_qa_inline_citation_noise,
    append_qa_turn_for_session,
    format_prior_turns_for_vision,
    get_qa_session_state,
    mixed_session_context_for_vision,
)


def test_evidence_max_year_ignores_invalid_metadata_year():
    refs = [
        {"metadata": {"year": 8211}},
        {"metadata": {"year": 2020}},
        {"metadata": {"year": "2011-2018"}},
    ]
    assert _compute_evidence_max_year(refs) == 2020


def test_evidence_max_year_parses_year_span_string():
    assert _compute_evidence_max_year([{"metadata": {"year": "2011-2018"}}]) == 2018


def test_strip_model_reasoning_markup_removes_think_wrappers():
    raw = "<redacted_thinking>long chain</redacted_thinking>\n\nHello clinician."
    out = _strip_model_reasoning_markup(raw)
    assert "redacted_thinking" not in out.lower()
    assert "Hello clinician" in out


def test_strip_qa_inline_citation_noise_removes_rag_tags():
    s = "Definitions (per [RAG #1], [RAG #2], WHO): hypertension."
    out = _strip_qa_inline_citation_noise(s)
    assert "[RAG" not in out
    assert "Definitions" in out


def test_mixed_session_context_includes_text_not_vision_turns():
    turns = [
        {"q": "text q", "a": "text a", "channel": "text"},
        {"q": "img q", "a": "img a", "channel": "vision"},
    ]
    ctx = mixed_session_context_for_vision(turns, "Summary line.")
    assert "Summary line" in ctx
    assert "text q" in ctx
    assert "img q" not in ctx


def test_unified_qa_session_merges_text_and_vision_turns():
    uid = "user-unify"
    sid = "sess-1"
    append_qa_turn_for_session(uid, sid, "text q", "text a", "text")
    append_qa_turn_for_session(uid, sid, "image q", "image a", "vision")
    st = get_qa_session_state(uid, sid)
    assert len(st["turns"]) == 2
    assert st["turns"][0]["channel"] == "text"
    assert st["turns"][1]["channel"] == "vision"
    prior = format_prior_turns_for_vision(st["turns"])
    assert "User (text):" in prior
    assert "User (image):" in prior


class _FakeLLM:
    async def stream_completion(self, *_args, **_kwargs):
        yield "Direct "
        yield "Answer"

    async def collect_completion(self, *_args, **_kwargs):
        return "summary text"


async def _fake_rag_query(_question, _cfg):
    return "RAG context", [{"metadata": {"title": "Guideline A", "link": "https://example.com/a", "year": 2025}}]


async def _fake_web_search(_question, limit=6):
    return [{"title": "Web Source", "url": "https://example.com/w", "snippet": "Snippet"}][:limit]


def test_qa_chat_stream_returns_chunks_and_meta(client, monkeypatch, tmp_path):
    import server.app as app_mod
    import server.routes.qa_chat as qa_routes
    from server.core.dependencies import require_api_bearer

    monkeypatch.setattr(app_mod._metrics, "http_csv", str(tmp_path / "http_requests_test.csv"))
    app_mod.app.dependency_overrides[require_api_bearer] = lambda: True
    monkeypatch.setattr(qa_routes, "decode_access_token", lambda _t: {"sub": "user-123"})
    monkeypatch.setattr(qa_routes, "_rag_query", _fake_rag_query)
    monkeypatch.setattr(qa_routes, "searx_search", _fake_web_search)
    monkeypatch.setattr(qa_routes, "get_simple_note_generator", lambda *_a, **_k: _FakeLLM())

    resp = client.post(
        "/api/qa/chat_stream",
        headers={"Authorization": "Bearer test-token"},
        json={"message": "What is the diagnosis?", "session_id": "s1"},
    )

    assert resp.status_code == 200
    body = resp.text
    assert "Direct Answer" in body
    assert "__QA_META__" in body

    _, meta_raw = body.split("__QA_META__", 1)
    meta = json.loads(meta_raw.strip())
    assert meta["rag_results_count"] == 1
    assert meta["web_results_count"] == 1
    assert meta["evidence_max_year"] == 2025
    assert isinstance(meta["sources"], list)
    assert meta["sources"][0]["kind"] in {"rag", "web"}
