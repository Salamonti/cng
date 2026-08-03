# Shared helpers for authenticated API tests.
from sqlmodel import Session, select

import server.core.db as db
from server.models.user import User


def register_approve_login(client, email: str, password: str) -> str:
    assert client.post("/api/auth/register", json={"email": email, "password": password}).status_code == 200
    with Session(db.engine) as session:
        user = session.exec(select(User).where(User.email == email)).one()
        user.is_approved = True
        session.add(user)
        session.commit()
    login = client.post("/api/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200
    return login.json()["access_token"]
