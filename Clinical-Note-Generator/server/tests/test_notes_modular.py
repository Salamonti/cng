import uuid

from auth_utils import register_approve_login

# Clear stores via the same module object the routes use (avoids duplicate-import drift in tests).
def _clear_all():
    import server.routes.notes as notes_routes

    notes_routes._generation_cache.clear()
    notes_routes._generation_meta.clear()
    notes_routes._consult_comment_store.clear()
    notes_routes._order_request_store.clear()


class _FakeNoteGenerator:
    async def stream_completion(self, *_args, **_kwargs):
        yield "MODULAR_NOTE_OUTPUT"

    async def collect_completion(self, *_args, **_kwargs):
        return "MODULAR_COLLECT_OUTPUT"


def test_generation_meta_with_ttl(client, monkeypatch):
    import server.routes.notes as notes_routes
    from server.core.stores import ttl_store as ttl_mod
    from server.app import app
    from server.core.dependencies import require_api_bearer, get_current_user
    from server.models.user import User

    _test_user = User(id=uuid.uuid4(), email="test@example.com", hashed_password="x", is_active=True)
    app.dependency_overrides[require_api_bearer] = lambda: True
    app.dependency_overrides[get_current_user] = lambda: _test_user

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


def test_generate_v8_stream_routes_custom_note_type_scope(client, monkeypatch):
    """
    Regression: client must send custom note type ids verbatim.
    Server should route to build_prompt_other when a custom note type is scoped
    'other' (so it uses default_note_user_prompts_other / templates_other for
    the USER prompt). Prompt policy v2 unified the SYSTEM prompt across every
    scope via canonical_system_prompt() -- default_note_system_prompt_other is
    dead config and must NOT appear in the built prompt regardless of scope.
    """
    import server.core.db as db
    import server.routes.notes as notes_routes
    from server.app import app
    from server.core.dependencies import require_api_bearer, get_current_user
    from sqlmodel import Session, select

    from server.models.user import User

    app.dependency_overrides[require_api_bearer] = lambda: True
    _test_user = User(id=uuid.uuid4(), email="test@example.com", hashed_password="x", is_active=True)
    app.dependency_overrides[get_current_user] = lambda: _test_user

    _clear_all()
    monkeypatch.setattr(notes_routes, "note_gen", _FakeNoteGenerator())

    # Make builder outputs distinguishable by overriding config at the prompt-builder level.
    # routes.notes imports prompt builder via "core.prompt.builder" (not "server.core...").
    # notes_routes.load_config() must ALSO be patched: the route resolves
    # merged_oth via merge_templates_for_generation(cfg, ...) using its OWN
    # cfg (from notes_routes.load_config()), not pb.load_config() -- without
    # this, the "other" scope's user template never reaches build_prompt_other
    # and it silently falls back to the generic unmatched-note-type template.
    fake_cfg = {
        "default_note_system_prompt": "STD_SYSTEM",
        "default_note_user_prompts": {"custom_other_nt": "STD_USER"},
        "default_note_system_prompt_other": "OTH_SYSTEM",
        "default_note_user_prompts_other": {"custom_other_nt": "OTH_USER"},
        "preprocessing": {"enabled": False},
    }
    monkeypatch.setattr(notes_routes, "load_config", lambda: fake_cfg)
    import server.core.prompt.builder as pb
    monkeypatch.setattr(
        pb,
        "load_config",
        lambda: {
            **fake_cfg,
        },
    )

    # Create a user and set preferences_json to include a custom note type with scope=other
    email = f"nt-{uuid.uuid4().hex[:8]}@example.com"
    token = register_approve_login(client, email, "Passw0rd!1234")
    with Session(db.engine) as session:
        user = session.exec(select(User).where(User.email == email)).one()
        pref = notes_routes.ensure_user_preferences(session, user)
        pj = dict(pref.preferences_json or {})
        pj["schema_version"] = pj.get("schema_version") or 1
        pj["custom_note_types"] = [{"id": "custom_other_nt", "label": "X", "scope": "other"}]
        pj["templates"] = {}
        pj["templates_other"] = {}
        pref.preferences_json = pj
        session.add(pref)
        session.commit()

    # Generate using that note type id (must select other builder)
    resp = client.post(
        "/api/generate_v8_stream",
        json={
            "transcription_text": "hello",
            "old_visits_text": "",
            "mixed_other_text": "",
            "note_type": "custom_other_nt",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    gen_id = resp.headers.get("X-Generation-Id")
    assert gen_id
    meta_resp = client.get(f"/api/generation/{gen_id}/meta")
    assert meta_resp.status_code == 200
    # Notes route sets pipeline="v8_direct". The SYSTEM prompt is unified
    # across scopes in v2 (always the standard config key), but routing to
    # the "other" scope's USER template dict is still real and still tested.
    prompt = notes_routes._generation_cache[gen_id]["prompt"]
    assert "SYSTEM:\nSTD_SYSTEM" in prompt
    assert "OTH_SYSTEM" not in prompt
    assert "OTH_USER" in prompt


def test_consult_comment_pipeline_modular(client, monkeypatch):
    import server.routes.notes as notes_routes
    from server.app import app
    from server.core.dependencies import require_api_bearer, get_current_user
    from server.models.user import User

    _test_user = User(id=uuid.uuid4(), email="test@example.com", hashed_password="x", is_active=True)
    app.dependency_overrides[require_api_bearer] = lambda: True
    app.dependency_overrides[get_current_user] = lambda: _test_user

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


def test_consult_comment_rehydrates_from_workspace_encounter(client, monkeypatch):
    import server.core.db as db
    import server.routes.notes as notes_routes
    from server.app import app
    from server.core.baseline import get_baseline_workspace
    from server.core.dependencies import require_api_bearer, get_current_user
    from server.models.user_encounter import UserEncounter
    from sqlmodel import Session, select

    from server.models.user import User

    app.dependency_overrides[require_api_bearer] = lambda: True
    app.dependency_overrides[get_current_user] = lambda: User(id=uuid.uuid4(), email="test@example.com", hashed_password="x", is_active=True)

    _clear_all()
    called = {"scheduled": False}

    def fake_create_task(coro):
        called["scheduled"] = True
        coro.close()
        return None

    monkeypatch.setattr(notes_routes.asyncio, "create_task", fake_create_task)

    email = f"re-{uuid.uuid4().hex[:8]}@example.com"
    token = register_approve_login(client, email, "Passw0rd!1234")
    with Session(db.engine) as session:
        user = session.exec(select(User).where(User.email == email)).one()

    gen_id = uuid.uuid4().hex
    base = get_baseline_workspace()
    ex = dict(base.get("extras") or {})
    ex["generatedNote"] = (
        "Patient presents with hypertension and diabetes for follow up visit today in clinic."
    )
    ex["ui"] = {"lastGenerationId": gen_id}
    base["extras"] = ex
    enc = UserEncounter(
        user_id=user.id,
        label="E1",
        state_json=base,
        version=1,
    )
    with Session(db.engine) as session:
        session.add(enc)
        session.commit()
        session.refresh(enc)

    resp = client.get(
        f"/api/generation/{gen_id}/consult_comment?encounter_id={enc.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "pending"
    assert called["scheduled"] is True
    with notes_routes._cache_lock:
        assert notes_routes._generation_cache.get(gen_id)


def test_order_request_pipeline_modular(client, monkeypatch):
    import server.routes.notes as notes_routes
    from server.app import app
    from server.core.dependencies import require_api_bearer, get_current_user
    from server.models.user import User

    _test_user = User(id=uuid.uuid4(), email="test@example.com", hashed_password="x", is_active=True)
    app.dependency_overrides[require_api_bearer] = lambda: True
    app.dependency_overrides[get_current_user] = lambda: _test_user

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


def test_prompt_builder_modular(client, monkeypatch):
    from server.app import app
    from server.core.dependencies import require_api_bearer, get_current_user
    from server.models.user import User

    _test_user = User(id=uuid.uuid4(), email="test@example.com", hashed_password="x", is_active=True)
    app.dependency_overrides[require_api_bearer] = lambda: True
    app.dependency_overrides[get_current_user] = lambda: _test_user

    token = register_approve_login(
        client, f"npm-{uuid.uuid4().hex[:8]}@example.com", "Passw0rd!1234"
    )
    resp = client.get("/api/note_prompts", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("success") is True
    assert "templates" in body
