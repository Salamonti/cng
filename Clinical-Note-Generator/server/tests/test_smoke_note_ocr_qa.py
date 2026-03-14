import uuid


class _FakeNoteGenerator:
    async def stream_completion(self, *_args, **_kwargs):
        yield "SMOKE_NOTE_CHUNK"

    async def collect_completion(self, *_args, **_kwargs):
        return "SMOKE_QA_ANSWER"


class _FakeOCRClient:
    model_name = "fake-ocr"

    def ocr_image_bytes(self, _img_bytes, mime_type="image/png"):
        return "SMOKE_OCR_TEXT", 0.99


async def _fake_rag_query(_question, _cfg):
    return "RAG context", [{"metadata": {"title": "Guideline", "link": "https://example.com", "year": 2025}}]


async def _fake_web_search(_question, limit=6):
    return [{"title": "Web Source", "url": "https://example.com", "snippet": "Snippet"}][:limit]


def test_smoke_note_ocr_qa(client, monkeypatch):
    from server.app import app
    from server.core.dependencies import require_api_bearer
    from server.core.security import create_access_token
    import server.routes.notes as notes_routes
    import server.routes.ocr as ocr_routes
    import server.routes.qa_chat as qa_routes

    app.dependency_overrides[require_api_bearer] = lambda: True
    monkeypatch.setattr(notes_routes, "note_gen", _FakeNoteGenerator())
    monkeypatch.setattr(ocr_routes, "_get_ocr_client", lambda: _FakeOCRClient())
    monkeypatch.setattr(qa_routes, "_rag_query", _fake_rag_query)
    monkeypatch.setattr(qa_routes, "searx_search", _fake_web_search)
    monkeypatch.setattr(qa_routes, "get_simple_note_generator", lambda: _FakeNoteGenerator())

    note_resp = client.post(
        "/api/generate_v8_stream",
        json={
            "transcription_text": "short encounter text",
            "old_visits_text": "",
            "mixed_other_text": "",
            "note_type": "consult",
        },
    )
    assert note_resp.status_code == 200
    assert "SMOKE_NOTE_CHUNK" in note_resp.text

    # Minimal valid PNG payload (1x1 pixel).
    png_bytes = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDAT\x08\x99c`\x00\x00"
        b"\x00\x02\x00\x01\xe2!\xbc3\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    ocr_resp = client.post(
        "/api/ocr",
        files={"file": ("smoke.png", png_bytes, "image/png")},
    )
    assert ocr_resp.status_code == 200
    assert ocr_resp.json()["text"] == "SMOKE_OCR_TEXT"

    token = create_access_token(str(uuid.uuid4()))
    qa_resp = client.post(
        "/api/qa/chat",
        headers={"Authorization": f"Bearer {token}"},
        json={"message": "What is the dose?", "session_id": "smoke"},
    )
    assert qa_resp.status_code == 200
    body = qa_resp.json()
    assert "answer" in body
    assert body["sources"]
