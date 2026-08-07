"""Step 6 (H-8 / M-1): registration abuse — per-IP rate limit + no
email-enumeration oracle (already-registered emails return a generic 200)."""

import uuid


def _pw():
    return "Passw0rd!1234"


def test_register_existing_email_does_not_leak_oracle(client):
    # Fresh registration -> 200.
    email = f"enum-{uuid.uuid4().hex[:8]}@example.com"
    r1 = client.post("/api/auth/register", json={"email": email, "password": _pw()})
    assert r1.status_code == 200
    assert r1.json()["email"] == email

    # Re-registering the SAME email must return the same 200 with a generic
    # body -- NOT a 400 "email already registered" (that 400 is the
    # enumeration oracle an attacker uses to probe an address list).
    r2 = client.post("/api/auth/register", json={"email": email, "password": _pw()})
    assert r2.status_code == 200
    body = r2.json()
    # Generic placeholder: doesn't reveal the real account's id/created_at.
    assert body["email"] == email
    assert body["is_approved"] is False

    # And a *different* email still returns a matching generic 200 shape, so
    # a probing client can't distinguish "registered" from "not registered".
    r3 = client.post(
        "/api/auth/register", json={"email": f"other-{uuid.uuid4().hex[:8]}@example.com", "password": _pw()}
    )
    assert r3.status_code == 200
    for k in ("email", "is_approved", "is_admin"):
        assert k in r2.json() and k in r3.json()


def test_register_rate_limit_locks_out_ip_after_cap(client, monkeypatch):
    from server.core import security

    # Lower cap so the test doesn't need 20 registers.
    monkeypatch.setattr(security.register_attempts, "max_attempts", 3)
    monkeypatch.setattr(security.register_attempts, "window_sec", 3600)
    monkeypatch.setattr(security.register_attempts, "lockout_sec", 3600)

    for i in range(3):
        email = f"rl-{uuid.uuid4().hex[:8]}@example.com"
        resp = client.post("/api/auth/register", json={"email": email, "password": _pw()})
        assert resp.status_code == 200, f"attempt {i} should be allowed"

    # 4th attempt from the same client IP is locked out.
    locked = client.post(
        "/api/auth/register",
        json={"email": f"rl-{uuid.uuid4().hex[:8]}@example.com", "password": _pw()},
    )
    assert locked.status_code == 429
    assert "too many registration" in locked.json()["detail"].lower()
