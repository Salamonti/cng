from server.core.stores.generation_store import (
    _generation_cache,
    _generation_meta,
    _consult_comment_store,
    _order_request_store,
)


class _FakeNoteGenerator:
    async def stream_completion(self, *_args, **_kwargs):
        yield "MODULAR_NOTE_OUTPUT"

    async def collect_completion(self, *_args, **_kwargs):
        return "MODULAR_COLLECT_OUTPUT"


def _clear_all():
    _generation_cache.clear()
    _generation_meta.clear()
    _consult_comment_store.clear()
    _order_request_store.clear()


def test_generation_meta_with_ttl(client, monkeypatch):
    import server.routes.notes as notes_routes
    from server.core.stores import ttl_store as ttl_mod
    from server.app import app
    from server.core.dependencies import require_api_bearer

    app.dependency_overrides[require_api_bearer] = lambda: True

    _clear_all()
    now = [1000.0]
    monkeypatch.setattr(ttl_mod.time, "time", lambda: now[0])
    monkeypatch.setattr(notes_routes, "note_gen", _FakeNoteGenerator())

    resp = client.post(
        "/api/generate_v8_stream",
        json={
            "transcription_text": "hello",
            "old_visits_text": "",
            "mixed_other_text": "",
            "note_type": "consult",
        },
    )
    assert resp.status_code == 200
    gen_id = resp.headers.get("X-Generation-Id")
    assert gen_id

    meta_resp = client.get(f"/api/generation/{gen_id}/meta")
    assert meta_resp.status_code == 200

    now[0] = 1000.0 + 86401.0
    expired_meta = client.get(f"/api/generation/{gen_id}/meta")
    assert expired_meta.status_code == 404


def test_consult_comment_pipeline_modular(client, monkeypatch):
    import server.routes.notes as notes_routes
    from server.app import app
    from server.core.dependencies import require_api_bearer

    app.dependency_overrides[require_api_bearer] = lambda: True

    _clear_all()
    notes_routes._generation_cache["g-consult"] = {"prompt": "p", "output": "note output"}

    called = {"scheduled": False}

    def fake_create_task(coro):
        called["scheduled"] = True
        coro.close()
        return None

    monkeypatch.setattr(notes_routes.asyncio, "create_task", fake_create_task)

    resp = client.get("/api/generation/g-consult/consult_comment")
    assert resp.status_code == 200
    assert resp.json()["status"] == "pending"
    assert called["scheduled"] is True


def test_order_request_pipeline_modular(client, monkeypatch):
    import server.routes.notes as notes_routes
    from server.app import app
    from server.core.dependencies import require_api_bearer

    app.dependency_overrides[require_api_bearer] = lambda: True

    _clear_all()
    notes_routes._generation_cache["g-order"] = {"prompt": "p", "output": "note output"}

    called = {"scheduled": False}

    def fake_create_task(coro):
        called["scheduled"] = True
        coro.close()
        return None

    monkeypatch.setattr(notes_routes.asyncio, "create_task", fake_create_task)

    resp = client.get("/api/generation/g-order/order_requests")
    assert resp.status_code == 200
    assert resp.json()["status"] == "pending"
    assert called["scheduled"] is True


def test_prompt_builder_modular(client):
    from server.app import app
    from server.core.dependencies import require_api_bearer

    app.dependency_overrides[require_api_bearer] = lambda: True

    resp = client.get("/api/note_prompts")
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("success") is True
    assert "templates" in body
