"""Admin service status, allowlist, and NSSM control gate (phase checks)."""
import uuid

from sqlmodel import Session, select

import server.core.db as db
from server.models.user import User
from server.tests.auth_utils import register_approve_login

_TEST_PW = "Pw-test-adm-svc-1!"


def _make_admin_token(client) -> str:
    email = f"admin_svc_{uuid.uuid4().hex[:10]}@example.com"
    register_approve_login(client, email, _TEST_PW)
    with Session(db.engine) as session:
        user = session.exec(select(User).where(User.email == email)).one()
        user.is_admin = True
        session.add(user)
        session.commit()
    login = client.post("/api/auth/login", json={"email": email, "password": _TEST_PW})
    assert login.status_code == 200
    return login.json()["access_token"]


def _make_user_token(client) -> str:
    email = f"user_svc_{uuid.uuid4().hex[:10]}@example.com"
    return register_approve_login(client, email, _TEST_PW)


def test_services_allowlist_unauthenticated(client):
    r = client.get("/api/admin/services/allowlist/")
    assert r.status_code == 401


def test_services_allowlist_non_admin_forbidden(client):
    token = _make_user_token(client)
    r = client.get("/api/admin/services/allowlist/", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403
    assert "admin" in (r.json().get("detail") or "").lower()


def test_services_allowlist_admin_returns_flags(client):
    token = _make_admin_token(client)
    r = client.get("/api/admin/services/allowlist/", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    data = r.json()
    assert "control_enabled" in data
    assert isinstance(data["control_enabled"], bool)
    assert "windows_services" in data
    assert isinstance(data["windows_services"], dict)


def test_services_status_admin_returns_table(client):
    token = _make_admin_token(client)
    r = client.get("/api/admin/services/status/", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    data = r.json()
    assert "services" in data
    svc = data["services"]
    assert "fastapi" in svc and "rag" in svc
    assert svc["fastapi"].get("id") == "fastapi"


def test_service_control_disabled_by_default(client, monkeypatch):
    """NSSM actions require ADMIN_SERVICE_CONTROL_ENABLED=1 on the API process."""
    monkeypatch.delenv("ADMIN_SERVICE_CONTROL_ENABLED", raising=False)
    token = _make_admin_token(client)
    r = client.post(
        "/api/admin/services/fastapi/restart/",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 403
    detail = r.json().get("detail", "")
    assert "ADMIN_SERVICE_CONTROL_ENABLED" in detail or "disabled" in detail.lower()


def test_service_control_enabled_allows_attempt(client, monkeypatch):
    """With gate on, request reaches Windows layer (may fail in CI without sc.exe)."""
    monkeypatch.setenv("ADMIN_SERVICE_CONTROL_ENABLED", "1")
    token = _make_admin_token(client)
    r = client.post(
        "/api/admin/services/fastapi/restart/",
        headers={"Authorization": f"Bearer {token}"},
    )
    # Mapped service missing / sc fails -> 200 with ok False, or success on dev box.
    assert r.status_code == 200
    body = r.json()
    assert body.get("service_id") == "fastapi"
    assert "ok" in body
