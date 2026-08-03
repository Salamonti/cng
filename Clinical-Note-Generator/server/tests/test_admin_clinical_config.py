"""Admin clinical config.json envelope + secret merge (P2)."""

import uuid

from sqlmodel import Session, select

import server.core.db as db
from server.models.user import User
from server.tests.auth_utils import register_approve_login

_TEST_PW = "Pw-test-cln-cfg-1!"


def _make_admin_token(client) -> str:
    email = f"admin_cfg_{uuid.uuid4().hex[:10]}@example.com"
    register_approve_login(client, email, _TEST_PW)
    with Session(db.engine) as session:
        user = session.exec(select(User).where(User.email == email)).one()
        user.is_admin = True
        session.add(user)
        session.commit()
    login = client.post("/api/auth/login", json={"email": email, "password": _TEST_PW})
    assert login.status_code == 200
    return login.json()["access_token"]


def test_config_get_returns_path_and_redacts_secrets(client, monkeypatch):
    token = _make_admin_token(client)
    h = {"Authorization": f"Bearer {token}"}

    fake_cfg = {
        "default_note_temperature": 0.2,
        "jwt_secret": "super-secret-value-not-placeholder",
        "jwt_refresh_secret": "another-secret",
        "admin_api_key": "key123",
        "auth_database_url": "sqlite:///./data/user_data.sqlite",
    }

    import server.routes.admin as admin_mod

    monkeypatch.setattr(admin_mod, "_load_cfg", lambda: dict(fake_cfg))

    r = client.get("/api/admin/config/", headers=h)
    assert r.status_code == 200
    payload = r.json()
    assert "path" in payload and "data" in payload
    data = payload["data"]
    assert data["default_note_temperature"] == 0.2
    assert data["jwt_secret"] == admin_mod._REDACTED_PLACEHOLDER
    assert data["jwt_refresh_secret"] == admin_mod._REDACTED_PLACEHOLDER
    assert data["admin_api_key"] == admin_mod._REDACTED_PLACEHOLDER
    assert data["auth_database_url"] == fake_cfg["auth_database_url"]


def test_config_save_preserves_redacted_secrets(client, monkeypatch, tmp_path):
    token = _make_admin_token(client)
    h = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    existing = {
        "default_note_temperature": 0.1,
        "jwt_secret": "disk-secret",
        "jwt_refresh_secret": "disk-refresh",
        "admin_api_key": "disk-key",
    }
    saved = {}

    import server.routes.admin as admin_mod

    monkeypatch.setattr(admin_mod, "_load_cfg", lambda: dict(existing))

    def _capture_save(cfg):
        saved.clear()
        saved.update(cfg)

    monkeypatch.setattr(admin_mod, "_save_cfg", _capture_save)
    monkeypatch.setattr(admin_mod, "_reload_llm_clients_from_disk", lambda: None)

    incoming_data = {
        "default_note_temperature": 0.35,
        "jwt_secret": admin_mod._REDACTED_PLACEHOLDER,
        "jwt_refresh_secret": admin_mod._REDACTED_PLACEHOLDER,
        "admin_api_key": admin_mod._REDACTED_PLACEHOLDER,
    }
    r = client.post("/api/admin/config/save/", headers=h, json={"data": incoming_data})
    assert r.status_code == 200
    assert saved["default_note_temperature"] == 0.35
    assert saved["jwt_secret"] == "disk-secret"
    assert saved["jwt_refresh_secret"] == "disk-refresh"
    assert saved["admin_api_key"] == "disk-key"


def test_config_save_rejects_empty_body(client, monkeypatch):
    token = _make_admin_token(client)
    h = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    import server.routes.admin as admin_mod

    monkeypatch.setattr(admin_mod, "_load_cfg", lambda: {"a": 1, "b": 2})
    monkeypatch.setattr(admin_mod, "_save_cfg", lambda c: None)
    monkeypatch.setattr(admin_mod, "_reload_llm_clients_from_disk", lambda: None)

    r = client.post("/api/admin/config/save/", headers=h, json={"data": {}})
    assert r.status_code == 400
    assert "empty" in (r.json().get("detail") or "").lower()


def test_config_save_rejects_truncated_payload(client, monkeypatch):
    token = _make_admin_token(client)
    h = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    import server.routes.admin as admin_mod

    large_existing = {f"key_{i}": i for i in range(20)}
    monkeypatch.setattr(admin_mod, "_load_cfg", lambda: dict(large_existing))
    monkeypatch.setattr(admin_mod, "_save_cfg", lambda c: None)
    monkeypatch.setattr(admin_mod, "_reload_llm_clients_from_disk", lambda: None)

    r = client.post("/api/admin/config/save/", headers=h, json={"data": {"only": "few"}})
    assert r.status_code == 400
    assert "truncated" in (r.json().get("detail") or "").lower()


def test_config_save_legacy_flat_body_still_works(client, monkeypatch):
    token = _make_admin_token(client)
    h = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    existing = {"default_note_temperature": 0.1, "jwt_secret": "x"}
    saved = {}

    import server.routes.admin as admin_mod

    monkeypatch.setattr(admin_mod, "_load_cfg", lambda: dict(existing))

    def _capture_save(cfg):
        saved.clear()
        saved.update(cfg)

    monkeypatch.setattr(admin_mod, "_save_cfg", _capture_save)
    monkeypatch.setattr(admin_mod, "_reload_llm_clients_from_disk", lambda: None)

    r = client.post("/api/admin/config/save/", headers=h, json={"default_note_temperature": 0.5})
    assert r.status_code == 200
    assert saved["default_note_temperature"] == 0.5
    assert saved["jwt_secret"] == "x"
