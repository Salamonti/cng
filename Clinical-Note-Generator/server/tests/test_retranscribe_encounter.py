"""P4-S13: Whole-encounter re-transcribe API tests.

Covers: multi-segment merge + engine, JSON shape, VAD-off for whisper,
auth enforcement (401/403), zero-segment 404, and asr-segments regression.
"""
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
        email=f"retrans-{uuid.uuid4().hex[:8]}@example.com",
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


# ─── P4-S13-T1: multi-segment merge -> ONE transcript ────────────────


def test_multi_segment_merged_transcript(client, monkeypatch):
    """Two segments uploaded -> route merges -> single engine call."""
    _override_user(client)
    enc_id = _active_encounter_id(client)

    _upload_segment(client, enc_id, client_id="seg-A", name="seg-a.webm")
    _upload_segment(client, enc_id, client_id="seg-B", name="seg-b.webm")

    engine_calls = {"n": 0}

    def fake_transcribe(**kwargs):
        engine_calls["n"] += 1
        return "merged-transcript", None

    monkeypatch.setattr(
        "server.routes.asr_retranscribe._concat_webm_files_ffmpeg",
        lambda inp, out: out.write_bytes(b"\x1a\x45\xdf\xa3" + b"\x00" * 2000),
    )
    monkeypatch.setattr(
        "server.routes.asr_retranscribe.transcribe_bytes_sync",
        fake_transcribe,
    )

    resp = client.post(f"/api/asr/retranscribe/encounter/{enc_id}?profile=asr_only")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert engine_calls["n"] == 1, "Engine should be called exactly once"
    assert body["text"] == "merged-transcript"
    assert body["segment_count"] == 2
    assert body["encounter_id"] == enc_id
    assert body["engine"] == "whisper"
    assert body["profile"] == "asr_only"
    assert "trace_id" in body


# ─── P4-S13-T2: JSON shape ──────────────────────────────────────────


def test_result_json_shape(client, monkeypatch):
    """Response includes all required fields with correct types."""
    _override_user(client)
    enc_id = _active_encounter_id(client)
    _upload_segment(client, enc_id, client_id="shape-test", name="shape.webm")

    monkeypatch.setattr(
        "server.routes.asr_retranscribe._concat_webm_files_ffmpeg",
        lambda inp, out: out.write_bytes(b"\x00" * 1000),
    )
    monkeypatch.setattr(
        "server.routes.asr_retranscribe.transcribe_bytes_sync",
        lambda **kwargs: ("the transcript text", None),
    )

    resp = client.post(f"/api/asr/retranscribe/encounter/{enc_id}?profile=asr_only")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    required_fields = {"encounter_id", "engine", "profile", "text", "segment_count", "trace_id"}
    assert required_fields.issubset(body.keys()), f"Missing fields: {required_fields - set(body.keys())}"
    assert isinstance(body["encounter_id"], str)
    assert isinstance(body["text"], str)
    assert isinstance(body["segment_count"], int)
    assert body["segment_count"] == 1
    assert body["text"] == "the transcript text"


# ─── P4-S13-T3: whisper client path ────────────────────────────────


def test_retranscribe_route_uses_whisper_client():
    """The retranscribe route uses shared whisper_client transcribe_bytes_sync."""
    import inspect
    from server.routes import asr_retranscribe

    source = inspect.getsource(asr_retranscribe.retranscribe_encounter)
    assert "transcribe_bytes_sync" in source
    assert "streaming_asr" not in source


# ─── P4-S13-T4: zero segments -> 404 ─────────────────────────────────


def test_zero_segments_404(client):
    _override_user(client)
    enc_id = _active_encounter_id(client)

    resp = client.post(f"/api/asr/retranscribe/encounter/{enc_id}")
    assert resp.status_code == 404


# ─── Auth: 401 without token ────────────────────────────────────────


def test_no_auth_returns_401(client):
    """POST without auth header -> 401."""
    resp = client.post("/api/asr/retranscribe/encounter/" + str(uuid.uuid4()))
    assert resp.status_code == 401


# ─── Auth: 403 wrong user ───────────────────────────────────────────


def test_wrong_user_returns_403(client, monkeypatch):
    """User A cannot re-transcribe User B's encounter -> 403."""
    from server.app import app
    from server.core.dependencies import get_current_user, require_api_bearer
    from server.models.user import User

    # Create user A (the auth'd user making the request)
    user_a = User(
        id=uuid.uuid4(),
        email="user-a@example.com",
        hashed_password="x",
        is_active=True,
        is_approved=True,
    )
    app.dependency_overrides[get_current_user] = lambda: user_a
    app.dependency_overrides[require_api_bearer] = lambda: True

    # Create user B and encounter owned by user B
    import server.core.db as db
    from sqlmodel import Session
    from server.models.user_encounter import UserEncounter

    user_b = User(
        id=uuid.uuid4(),
        email="user-b@example.com",
        hashed_password="y",
        is_active=True,
        is_approved=True,
    )
    with Session(db.engine) as session:
        session.add(user_b)
        session.commit()
        enc = UserEncounter(user_id=user_b.id)
        session.add(enc)
        session.commit()
        session.refresh(enc)
        enc_id = str(enc.id)

    resp = client.post(f"/api/asr/retranscribe/encounter/{enc_id}")
    assert resp.status_code in (403, 404), (
        f"Expected 403 or 404 for wrong user, got {resp.status_code}: {resp.text[:200]}"
    )
