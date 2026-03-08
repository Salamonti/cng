import uuid

from sqlmodel import Session, select


def test_auth_register_login_me_refresh(client):
    import server.core.db as db
    from server.models.user import User

    email = f"smoke-{uuid.uuid4().hex[:8]}@example.com"
    password = "Passw0rd!1234"  # 12 characters minimum

    register_resp = client.post(
        "/api/auth/register",
        json={"email": email, "password": password},
    )
    assert register_resp.status_code == 200
    assert register_resp.json()["email"] == email

    # Not approved users cannot login.
    login_resp = client.post(
        "/api/auth/login",
        json={"email": email, "password": password},
    )
    assert login_resp.status_code == 403

    with Session(db.engine) as session:
        user = session.exec(select(User).where(User.email == email)).one()
        user.is_approved = True
        session.add(user)
        session.commit()

    login_resp = client.post(
        "/api/auth/login",
        json={"email": email, "password": password},
    )
    assert login_resp.status_code == 200
    token_payload = login_resp.json()
    access_token = token_payload["access_token"]
    refresh_token = token_payload["refresh_token"]

    me_resp = client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert me_resp.status_code == 200
    assert me_resp.json()["email"] == email

    refresh_resp = client.post(
        "/api/auth/refresh",
        json={"refresh_token": refresh_token},
    )
    assert refresh_resp.status_code == 200
    assert refresh_resp.json()["access_token"]
