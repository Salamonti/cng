"""ASR modes/capabilities metadata (chunk + batch) -- requires auth."""
import uuid

from fastapi.testclient import TestClient

from server.app import app
from auth_utils import register_approve_login


def _auth_headers(client):
    email = f"asrcapmeta-{uuid.uuid4().hex[:8]}@example.com"
    token = register_approve_login(client, email, "Passw0rd!1234")
    return {"Authorization": f"Bearer {token}"}


def test_asr_modes_requires_auth():
    with TestClient(app) as client:
        resp = client.get("/api/asr/modes")
        assert resp.status_code in (401, 403)


def test_asr_modes_json():
    with TestClient(app) as client:
        headers = _auth_headers(client)
        resp = client.get("/api/asr/modes", headers=headers)
        assert resp.status_code == 200
        body = resp.json()
        assert "chunk_asr_enabled" in body
        assert "batch" in body.get("capture_modes", [])
        assert "chunk" in body.get("capture_modes", [])


def test_asr_capabilities_requires_auth():
    with TestClient(app) as client:
        resp = client.get("/api/asr/capabilities")
        assert resp.status_code in (401, 403)


def test_asr_capabilities_compat_alias():
    with TestClient(app) as client:
        headers = _auth_headers(client)
        resp = client.get("/api/asr/capabilities", headers=headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body.get("streaming_enabled") is False
        assert "chunk_asr_enabled" in body
        assert "chunk" in body.get("capture_modes", [])
