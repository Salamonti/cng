"""ASR modes endpoint."""
from __future__ import annotations

import os
import uuid

from fastapi.testclient import TestClient

from server.app import app
from auth_utils import register_approve_login


def test_asr_modes_endpoint_requires_auth():
    os.environ.pop("CHUNK_ASR_ENABLED", None)
    client = TestClient(app)
    resp = client.get("/api/asr/modes")
    assert resp.status_code in (401, 403)


def test_asr_modes_endpoint_authenticated():
    os.environ.pop("CHUNK_ASR_ENABLED", None)
    client = TestClient(app)
    email = f"asrmodes-{uuid.uuid4().hex[:8]}@example.com"
    token = register_approve_login(client, email, "Passw0rd!1234")
    headers = {"Authorization": f"Bearer {token}"}
    resp = client.get("/api/asr/modes", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert "chunk_asr_enabled" in body
    assert "batch" in body.get("capture_modes", []) or "batch" in str(body)


def test_asr_capabilities_compat_requires_auth():
    client = TestClient(app)
    resp = client.get("/api/asr/capabilities")
    assert resp.status_code in (401, 403)


def test_asr_capabilities_compat_authenticated():
    client = TestClient(app)
    email = f"asrcaps-{uuid.uuid4().hex[:8]}@example.com"
    token = register_approve_login(client, email, "Passw0rd!1234")
    headers = {"Authorization": f"Bearer {token}"}
    resp = client.get("/api/asr/capabilities", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("streaming_enabled") is False
