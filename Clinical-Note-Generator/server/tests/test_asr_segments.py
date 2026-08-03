import hashlib
import uuid


def _webm_payload(label=b"audio", size=1600):
    head = bytes([0x1A, 0x45, 0xDF, 0xA3]) + b"webm-segment-" + label
    return head + (b"\x00" * max(0, size - len(head)))


def _override_user(client):
    from server.app import app
    from server.core.dependencies import get_current_user, require_api_bearer
    from server.models.user import User

    fake_user = User(
        id=uuid.uuid4(),
        email=f"asr-seg-{uuid.uuid4().hex[:8]}@example.com",
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


def _upload_segment(client, enc_id, client_id="rec-1", payload=None, name="rec-1.webm"):
    payload = payload if payload is not None else _webm_payload(client_id.encode())
    return client.post(
        "/api/asr/segments",
        data={
            "encounter_id": enc_id,
            "client_recording_id": client_id,
            "expected_file_name": name,
            "expected_size_bytes": str(len(payload)),
            "expected_sha256": hashlib.sha256(payload).hexdigest(),
            "duration_sec": "3.5",
        },
        files={"file": (name, payload, "audio/webm")},
    )


def test_segment_upload_verifies_identity_lists_and_is_idempotent(client):
    _override_user(client)
    enc_id = _active_encounter_id(client)
    payload = _webm_payload(b"verified")

    r1 = _upload_segment(client, enc_id, client_id="client-rec-1", payload=payload, name="verified.webm")
    assert r1.status_code == 200, r1.text
    body = r1.json()
    assert body["client_recording_id"] == "client-rec-1"
    assert body["encounter_id"] == enc_id
    assert body["file_name"] == "verified.webm"
    assert body["file_size"] == len(payload)
    assert body["sha256"] == hashlib.sha256(payload).hexdigest()
    assert body["sequence_index"] == 1
    assert body["transcription_status"] == "pending"

    r2 = _upload_segment(client, enc_id, client_id="client-rec-1", payload=payload, name="verified.webm")
    assert r2.status_code == 200, r2.text
    assert r2.json()["id"] == body["id"]

    listed = client.get(f"/api/asr/segments?encounter_id={enc_id}")
    assert listed.status_code == 200
    assert [x["id"] for x in listed.json()] == [body["id"]]


def test_segment_upload_rejects_hash_or_size_mismatch(client):
    _override_user(client)
    enc_id = _active_encounter_id(client)
    payload = _webm_payload(b"mismatch")

    bad_hash = client.post(
        "/api/asr/segments",
        data={
            "encounter_id": enc_id,
            "client_recording_id": "bad-hash",
            "expected_file_name": "bad.webm",
            "expected_size_bytes": str(len(payload)),
            "expected_sha256": "0" * 64,
        },
        files={"file": ("bad.webm", payload, "audio/webm")},
    )
    assert bad_hash.status_code == 409

    bad_size = client.post(
        "/api/asr/segments",
        data={
            "encounter_id": enc_id,
            "client_recording_id": "bad-size",
            "expected_file_name": "bad-size.webm",
            "expected_size_bytes": str(len(payload) + 1),
            "expected_sha256": hashlib.sha256(payload).hexdigest(),
        },
        files={"file": ("bad-size.webm", payload, "audio/webm")},
    )
    assert bad_size.status_code == 409


def test_segment_transcription_is_replaced_per_segment_and_ordered(client, monkeypatch):
    import server.routes.asr_segments as seg_routes

    _override_user(client)
    enc_id = _active_encounter_id(client)
    s1 = _upload_segment(client, enc_id, client_id="seg-1", payload=_webm_payload(b"one"), name="one.webm")
    s2 = _upload_segment(client, enc_id, client_id="seg-2", payload=_webm_payload(b"two"), name="two.webm")
    assert s1.status_code == 200
    assert s2.status_code == 200
    assert s1.json()["sequence_index"] == 1
    assert s2.json()["sequence_index"] == 2

    calls = {"n": 0}

    def fake_transcribe(**kwargs):
        calls["n"] += 1
        return f"transcript {calls['n']}"

    monkeypatch.setattr(seg_routes, "_transcribe_audio_bytes_sync", fake_transcribe)

    tx1 = client.post(f"/api/asr/segments/{s1.json()['id']}/transcribe")
    assert tx1.status_code == 200, tx1.text
    assert tx1.json()["transcript_text"] == "transcript 1"
    tx2 = client.post(f"/api/asr/segments/{s2.json()['id']}/transcribe")
    assert tx2.status_code == 200, tx2.text
    assert tx2.json()["transcript_text"] == "transcript 2"

    transcript = client.get(f"/api/asr/segments/encounter/{enc_id}/transcript")
    assert transcript.status_code == 200
    assert transcript.json()["transcript_text"] == "transcript 1\n\ntranscript 2"

    tx2b = client.post(f"/api/asr/segments/{s2.json()['id']}/transcribe")
    assert tx2b.status_code == 200
    assert tx2b.json()["transcript_text"] == "transcript 3"

    transcript2 = client.get(f"/api/asr/segments/encounter/{enc_id}/transcript")
    assert transcript2.status_code == 200
    assert transcript2.json()["transcript_text"] == "transcript 1\n\ntranscript 3"


def test_deleting_encounter_removes_segment_rows_and_files(client):
    from pathlib import Path

    from sqlmodel import Session, select

    import server.core.db as db
    from server.models.asr_recording_segment import AsrRecordingSegment
    from server.routes.asr_segments import get_asr_segment_storage_root

    _override_user(client)
    enc_id = _active_encounter_id(client)
    r = _upload_segment(client, enc_id, client_id="delete-me", payload=_webm_payload(b"delete"), name="delete.webm")
    assert r.status_code == 200, r.text
    seg_id = r.json()["id"]

    with Session(db.engine) as session:
        segment = session.get(AsrRecordingSegment, uuid.UUID(seg_id))
        assert segment is not None
        path = get_asr_segment_storage_root() / segment.server_file_key
        assert path.exists()

    deleted = client.request("DELETE", f"/api/encounters/{enc_id}", json={"confirm": True})
    assert deleted.status_code == 200, deleted.text

    with Session(db.engine) as session:
        segment = session.exec(select(AsrRecordingSegment).where(AsrRecordingSegment.id == uuid.UUID(seg_id))).one_or_none()
        assert segment is None
        assert not Path(path).exists()
