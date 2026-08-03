"""Admin filesystem browse API (model path picker)."""
import os
import uuid
from pathlib import Path

from sqlmodel import Session, select

import server.core.db as db
from server.models.user import User

_TEST_PW = "Pw-test-fs-br-1!"


def _make_admin_token(client) -> str:
    from server.tests.auth_utils import register_approve_login

    email = f"fsbrowse_{uuid.uuid4().hex[:10]}@example.com"
    register_approve_login(client, email, _TEST_PW)
    with Session(db.engine) as session:
        user = session.exec(select(User).where(User.email == email)).one()
        user.is_admin = True
        session.add(user)
        session.commit()
    login = client.post("/api/auth/login", json={"email": email, "password": _TEST_PW})
    assert login.status_code == 200
    return login.json()["access_token"]


def test_fs_browse_requires_auth(client):
    r = client.get("/api/admin/fs/browse")
    assert r.status_code == 401


def test_fs_browse_roots_and_listing(client):
    token = _make_admin_token(client)
    h = {"Authorization": f"Bearer {token}"}
    r = client.get("/api/admin/fs/browse", headers=h)
    assert r.status_code == 200
    data = r.json()
    assert data.get("path") is None
    assert isinstance(data.get("roots"), list)
    assert len(data["roots"]) >= 1

    # App root (Clinical-Note-Generator) is always allowlisted
    app_root = Path(__file__).resolve().parents[2]
    assert app_root.name == "Clinical-Note-Generator"
    r2 = client.get("/api/admin/fs/browse", headers=h, params={"path": str(app_root)})
    assert r2.status_code == 200
    inner = r2.json()
    assert inner.get("path")
    assert "directories" in inner and "files" in inner

    # A path that exists on the host but sits outside every allowlisted root
    # (app checkout + project parent + models dirs) -- must 403, not just 404,
    # so an admin can't tell allowlisted-but-missing apart from never-allowlisted.
    outside_path = "C:\\\\Windows\\\\System32" if os.name == "nt" else "/etc"
    outside = client.get("/api/admin/fs/browse", headers=h, params={"path": outside_path})
    assert outside.status_code == 403

    r3 = client.get("/api/admin/fs/browse", headers=h, params={"preset": "llama_models"})
    assert r3.status_code == 200
    d3 = r3.json()
    assert "roots" in d3
    if d3.get("path"):
        assert isinstance(d3.get("directories"), list) and isinstance(d3.get("files"), list)
