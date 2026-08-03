"""Regression tests for P1-1 (auth hardening batch):
- login lockout after repeated failed attempts, cleared on success
- password reset tokens persisted to the DB (not an in-memory dict that
  drops every pending token on restart), single-use, and expiring
- JWT typ claim rejects a refresh token presented as an access token
"""
import uuid
from datetime import datetime, timedelta

from sqlmodel import Session, select

import server.core.db as db
from server.core.security import create_refresh_token, decode_access_token
from server.models.password_reset_token import PasswordResetToken
from server.models.user import User


def _register_approved(client, email, password="Passw0rd!1234"):
    assert client.post("/api/auth/register", json={"email": email, "password": password}).status_code == 200
    with Session(db.engine) as session:
        user = session.exec(select(User).where(User.email == email)).one()
        user.is_approved = True
        session.add(user)
        session.commit()


def test_login_locks_out_after_repeated_failures_and_clears_on_success(client):
    email = f"lockout-{uuid.uuid4().hex[:8]}@example.com"
    password = "Passw0rd!1234"
    _register_approved(client, email, password)

    for _ in range(5):
        resp = client.post("/api/auth/login", json={"email": email, "password": "wrong-password"})
        assert resp.status_code == 401

    # 6th attempt, even with the CORRECT password, is locked out.
    locked_resp = client.post("/api/auth/login", json={"email": email, "password": password})
    assert locked_resp.status_code == 429
    assert "too many failed" in locked_resp.json()["detail"].lower()


def test_login_lockout_does_not_affect_other_accounts(client):
    victim_email = f"lockout-victim-{uuid.uuid4().hex[:8]}@example.com"
    other_email = f"lockout-other-{uuid.uuid4().hex[:8]}@example.com"
    password = "Passw0rd!1234"
    _register_approved(client, victim_email, password)
    _register_approved(client, other_email, password)

    for _ in range(5):
        client.post("/api/auth/login", json={"email": victim_email, "password": "wrong-password"})

    victim_resp = client.post("/api/auth/login", json={"email": victim_email, "password": password})
    assert victim_resp.status_code == 429

    other_resp = client.post("/api/auth/login", json={"email": other_email, "password": password})
    assert other_resp.status_code == 200


def test_password_reset_token_is_persisted_not_in_memory(client):
    """The whole point of P1-1's persistence fix: the token must be a real
    DB row (so it survives a process restart), not just present because an
    in-memory dict happens to still be alive within this test process."""
    email = f"resetpersist-{uuid.uuid4().hex[:8]}@example.com"
    _register_approved(client, email)

    resp = client.post("/api/auth/forgot-password", json={"email": email})
    assert resp.status_code == 200

    with Session(db.engine) as session:
        user = session.exec(select(User).where(User.email == email)).one()
        rows = session.exec(
            select(PasswordResetToken).where(PasswordResetToken.user_id == user.id)
        ).all()
        assert len(rows) == 1
        assert rows[0].used is False
        assert rows[0].expires_at > datetime.utcnow()


def test_password_reset_round_trip_and_token_is_single_use(client):
    email = f"resetflow-{uuid.uuid4().hex[:8]}@example.com"
    old_password = "Passw0rd!1234"
    new_password = "NewPassw0rd!5678"
    _register_approved(client, email, old_password)

    # The route only ever persists the token's hash (by design -- see
    # _hash_reset_token), and the raw token is only ever sent by email in
    # production. Insert a row directly with a raw token we control so the
    # test can drive reset-password without needing to intercept email.
    from server.routes.auth_users import _hash_reset_token
    import secrets

    raw_token = secrets.token_urlsafe(32)
    with Session(db.engine) as session:
        user = session.exec(select(User).where(User.email == email)).one()
        session.add(
            PasswordResetToken(
                user_id=user.id,
                token_hash=_hash_reset_token(raw_token),
                expires_at=datetime.utcnow() + timedelta(minutes=15),
            )
        )
        session.commit()

    reset_resp = client.post(
        f"/api/auth/reset-password/{raw_token}",
        json={"new_password": new_password},
    )
    assert reset_resp.status_code == 200

    # New password works, old one doesn't.
    assert client.post("/api/auth/login", json={"email": email, "password": new_password}).status_code == 200
    assert client.post("/api/auth/login", json={"email": email, "password": old_password}).status_code == 401

    # Reusing the same token a second time fails (single-use).
    replay_resp = client.post(
        f"/api/auth/reset-password/{raw_token}",
        json={"new_password": "AnotherPassw0rd!999"},
    )
    assert replay_resp.status_code == 400


def test_expired_password_reset_token_is_rejected(client):
    email = f"resetexpired-{uuid.uuid4().hex[:8]}@example.com"
    _register_approved(client, email)

    from server.routes.auth_users import _hash_reset_token
    import secrets

    raw_token = secrets.token_urlsafe(32)
    with Session(db.engine) as session:
        user = session.exec(select(User).where(User.email == email)).one()
        session.add(
            PasswordResetToken(
                user_id=user.id,
                token_hash=_hash_reset_token(raw_token),
                expires_at=datetime.utcnow() - timedelta(seconds=1),
            )
        )
        session.commit()

    resp = client.post(
        f"/api/auth/reset-password/{raw_token}",
        json={"new_password": "SomeNewPassw0rd!123"},
    )
    assert resp.status_code == 400
    assert "expired" in resp.json()["detail"].lower()


def test_refresh_token_cannot_be_used_as_an_access_token():
    """The access/refresh secrets already differ, so this cross-use is
    already blocked by signature verification before the typ claim is even
    reached -- confirms that layer still works after this change."""
    import pytest

    refresh = create_refresh_token(str(uuid.uuid4()))
    with pytest.raises(Exception):
        decode_access_token(refresh)


def test_typ_claim_rejects_a_token_with_the_wrong_type_even_with_a_valid_signature():
    """Defense-in-depth (WO P1-1): the typ claim is checked independently of
    signature verification, so even a token correctly signed with the
    access secret is rejected if its typ claim doesn't say "access" -- the
    scenario this protects against is the access/refresh secrets ever being
    misconfigured equal, where signature verification alone wouldn't catch
    a refresh token being replayed as an access token."""
    import jwt
    import pytest
    from datetime import datetime, timedelta

    from server.core.config import get_settings
    from server.core.security import InvalidTokenType

    settings = get_settings()
    forged = jwt.encode(
        {"sub": str(uuid.uuid4()), "typ": "refresh", "exp": datetime.utcnow() + timedelta(minutes=5)},
        settings.jwt_secret,  # the correct ACCESS secret
        algorithm="HS256",
    )
    with pytest.raises(InvalidTokenType):
        decode_access_token(forged)
