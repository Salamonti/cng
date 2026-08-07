"""Step 5 regression tests (H-7 / M-6): refresh-token cookie Secure flag and
security headers on responses."""

import os
import uuid

from sqlmodel import Session, select


def _register_approve_login(client) -> "tuple[object, object, object]":
    """Return (email, password, access_token) with an approved user."""
    from server.models.user import User
    import server.core.db as db

    email = f"sec-{uuid.uuid4().hex[:8]}@example.com"
    password = "Passw0rd!1234"
    client.post("/api/auth/register", json={"email": email, "password": password})
    with Session(db.engine) as session:
        user = session.exec(select(User).where(User.email == email)).one()
        user.is_approved = True
        session.add(user)
        session.commit()
    login = client.post("/api/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200
    return email, password, login.json()["access_token"]


def test_refresh_cookie_is_secure_by_default(client):
    email, pwd, _ = _register_approve_login(client)
    login = client.post("/api/auth/login", json={"email": email, "password": pwd})
    # httpx TestClient exposes the Set-Cookie header.
    set_cookie = login.headers.get("set-cookie", "")
    assert "refresh_token=" in set_cookie
    assert "Secure" in set_cookie
    assert "HttpOnly" in set_cookie


def test_refresh_cookie_not_secure_when_override(client, monkeypatch):
    monkeypatch.setenv("COOKIE_SECURE", "0")
    # Force re-import of the helper's env read by exercising a fresh login.
    from server.routes import auth_users

    email = f"sec-{uuid.uuid4().hex[:8]}@example.com"
    pwd = "Passw0rd!1234"
    client.post("/api/auth/register", json={"email": email, "password": pwd})
    import server.core.db as db
    from server.models.user import User

    with Session(db.engine) as session:
        u = session.exec(select(User).where(User.email == email)).one()
        u.is_approved = True
        session.add(u)
        session.commit()
    login = client.post("/api/auth/login", json={"email": email, "password": pwd})
    set_cookie = login.headers.get("set-cookie", "")
    assert "refresh_token=" in set_cookie
    assert "Secure" not in set_cookie


def test_security_headers_on_api_response(client):
    email, pwd, token = _register_approve_login(client)
    r = client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 200
    h = r.headers
    assert h.get("x-content-type-options") == "nosniff"
    assert h.get("x-frame-options") == "DENY"
    assert h.get("referrer-policy") == "no-referrer"
    assert h.get("content-security-policy", "")  # present
    assert "frame-ancestors 'none'" in h.get("content-security-policy", "")


def test_security_headers_on_public_route(client):
    # Even open/health-ish routes get headers (middleware runs for all).
    r = client.get("/api/version")
    assert r.status_code == 200
    assert r.headers.get("x-content-type-options") == "nosniff"
