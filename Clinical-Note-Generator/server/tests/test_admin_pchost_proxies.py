"""P3: notes_proxy row and service_control rules."""

import uuid

from sqlmodel import Session, select

import server.core.db as db
from server.models.user import User
from server.tests.auth_utils import register_approve_login

_TEST_PW = "Pw-test-pchost-1!"


def _make_admin_token(client) -> str:
    email = f"admin_ph_{uuid.uuid4().hex[:10]}@example.com"
    register_approve_login(client, email, _TEST_PW)
    with Session(db.engine) as session:
        user = session.exec(select(User).where(User.email == email)).one()
        user.is_admin = True
        session.add(user)
        session.commit()
    login = client.post("/api/auth/login", json={"email": email, "password": _TEST_PW})
    assert login.status_code == 200
    return login.json()["access_token"]


def test_services_status_includes_pchost_proxies(client, monkeypatch):
    token = _make_admin_token(client)
    h = {"Authorization": f"Bearer {token}"}

    import server.routes.admin as am

    def fake_eps():
        return {
            "office_stack_processes": {
                "notes_proxy": {"ports": [3000, 3443]},
            },
            "windows_services": {},
        }

    monkeypatch.setattr(am, "_load_service_endpoints_or_empty", fake_eps)

    r = client.get("/api/admin/services/status/", headers=h)
    assert r.status_code == 200
    svc = r.json().get("services") or {}
    assert "notes_proxy" in svc
    assert svc["notes_proxy"].get("id") == "notes_proxy"
    assert "3000" in str(svc["notes_proxy"].get("port") or "")


def test_service_control_rejects_notes_proxy(client, monkeypatch):
    monkeypatch.setenv("ADMIN_SERVICE_CONTROL_ENABLED", "1")
    token = _make_admin_token(client)
    h = {"Authorization": f"Bearer {token}"}
    r = client.post("/api/admin/services/notes_proxy/restart/", headers=h)
    assert r.status_code == 400
    assert "status" in (r.json().get("detail") or "").lower()