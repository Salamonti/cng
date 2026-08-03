"""Regression test (P1-6): a user's cumulative stored audio must be capped.

Before this fix, MAX_AUDIO_BYTES only bounded a single upload -- nothing
stopped one user from uploading segments indefinitely and filling the
disk within the RETENTION_DAYS window before auto-delete catches up.
"""
import hashlib
import uuid

import server.routes.asr_segments as seg_routes


def _webm_payload(label=b"audio", size=1600):
    head = bytes([0x1A, 0x45, 0xDF, 0xA3]) + b"webm-segment-" + label
    return head + (b"\x00" * max(0, size - len(head)))


def _override_user(client):
    from server.app import app
    from server.core.dependencies import get_current_user, require_api_bearer
    from server.models.user import User

    fake_user = User(
        id=uuid.uuid4(),
        email=f"asr-quota-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="x",
        is_active=True,
        is_approved=True,
    )
    app.dependency_overrides[get_current_user] = lambda: fake_user
    app.dependency_overrides[require_api_bearer] = lambda: True
    return fake_user


def _active_encounter_id(client):
    ws = client.get("/api/workspace/")
    assert ws.status_code == 200, ws.text
    return ws.json()["state"]["extras"]["activeEncounterId"]


def _upload_segment(client, enc_id, client_id, payload):
    return client.post(
        "/api/asr/segments",
        data={
            "encounter_id": enc_id,
            "client_recording_id": client_id,
            "expected_file_name": f"{client_id}.webm",
            "expected_size_bytes": str(len(payload)),
            "expected_sha256": hashlib.sha256(payload).hexdigest(),
            "duration_sec": "3.5",
        },
        files={"file": (f"{client_id}.webm", payload, "audio/webm")},
    )


def test_upload_rejected_once_user_storage_quota_is_exceeded(client, monkeypatch):
    # A small quota so the test doesn't need to actually move gigabytes.
    monkeypatch.setattr(seg_routes, "MAX_USER_STORAGE_BYTES", 4000)
    monkeypatch.setattr(seg_routes, "MIN_AUDIO_BYTES", 100)

    _override_user(client)
    enc_id = _active_encounter_id(client)

    first = _upload_segment(client, enc_id, "quota-1", _webm_payload(b"one", size=1600))
    assert first.status_code == 200, first.text

    second = _upload_segment(client, enc_id, "quota-2", _webm_payload(b"two", size=1600))
    assert second.status_code == 200, second.text

    # 1600 + 1600 + 1600 = 4800 > 4000 quota -- must be rejected.
    third = _upload_segment(client, enc_id, "quota-3", _webm_payload(b"three", size=1600))
    assert third.status_code == 413
    assert "quota" in third.text.lower()


def test_upload_within_quota_still_succeeds(client, monkeypatch):
    monkeypatch.setattr(seg_routes, "MAX_USER_STORAGE_BYTES", 100_000)
    monkeypatch.setattr(seg_routes, "MIN_AUDIO_BYTES", 100)

    _override_user(client)
    enc_id = _active_encounter_id(client)

    resp = _upload_segment(client, enc_id, "within-quota", _webm_payload(b"ok", size=1600))
    assert resp.status_code == 200, resp.text
