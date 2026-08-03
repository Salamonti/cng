"""Regression test: two near-simultaneous uploads of the same
client_recording_id (a client retry firing before the first response
arrives) must not surface a bare 500. The pre-check ("does a row for this
client_recording_id already exist?") and the insert are not atomic, so both
requests can pass the pre-check before either commits; the unique
constraint then lets only one commit succeed. Before the fix, the loser's
IntegrityError was unhandled and it also leaked the file it had already
written to disk.
"""
import hashlib
import threading
import uuid

import server.core.db as db
from sqlmodel import Session, select

from server.models.asr_recording_segment import AsrRecordingSegment


def _webm_payload(label=b"audio", size=1600):
    head = bytes([0x1A, 0x45, 0xDF, 0xA3]) + b"webm-segment-" + label
    return head + (b"\x00" * max(0, size - len(head)))


def _override_user(client):
    from server.app import app
    from server.core.dependencies import get_current_user, require_api_bearer
    from server.models.user import User

    fake_user = User(
        id=uuid.uuid4(),
        email=f"asr-race-{uuid.uuid4().hex[:8]}@example.com",
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


def test_concurrent_duplicate_upload_is_idempotent_not_500(client):
    _override_user(client)
    enc_id = _active_encounter_id(client)
    payload = _webm_payload(b"race-chunk")
    client_id = f"race-{uuid.uuid4().hex[:8]}"

    def _post():
        return client.post(
            "/api/asr/segments",
            data={
                "encounter_id": enc_id,
                "client_recording_id": client_id,
                "expected_file_name": "race.webm",
                "expected_size_bytes": str(len(payload)),
                "expected_sha256": hashlib.sha256(payload).hexdigest(),
                "duration_sec": "3.5",
            },
            files={"file": ("race.webm", payload, "audio/webm")},
        )

    barrier = threading.Barrier(2)
    results = {}

    def _run(name):
        barrier.wait(timeout=5)
        results[name] = _post()

    t1 = threading.Thread(target=_run, args=("a",))
    t2 = threading.Thread(target=_run, args=("b",))
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    for name, resp in results.items():
        assert resp.status_code == 200, f"request {name} got {resp.status_code}: {resp.text}"

    ids = {resp.json()["id"] for resp in results.values()}
    assert len(ids) == 1, f"both requests should resolve to the same segment id, got {ids}"

    with Session(db.engine) as session:
        rows = session.exec(
            select(AsrRecordingSegment).where(AsrRecordingSegment.client_recording_id == client_id)
        ).all()
        assert len(rows) == 1, f"expected exactly one row for {client_id}, found {len(rows)}"
