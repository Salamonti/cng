"""Regression test (P1-7): the retention-enforcement job must delete
AsrRecordingSegment rows (and their files) once expires_at has passed,
and must leave non-expired segments untouched. Before this script
existed, expires_at was set on every upload but nothing ever acted on
it -- audio (PHI) accumulated indefinitely past the stated 7-day
retention window.
"""
import hashlib
import uuid
from datetime import datetime, timedelta


def _webm_payload(label=b"audio", size=1600):
    head = bytes([0x1A, 0x45, 0xDF, 0xA3]) + b"webm-segment-" + label
    return head + (b"\x00" * max(0, size - len(head)))


def _upload_segment(client, h, enc_id, client_id):
    payload = _webm_payload(client_id.encode())
    return client.post(
        "/api/asr/segments",
        headers=h,
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


def test_purge_deletes_only_expired_segments(client):
    import server.core.db as db
    from sqlmodel import Session, select
    from server.models.asr_recording_segment import AsrRecordingSegment
    from server.routes.asr_segments import get_asr_segment_storage_root
    from auth_utils import register_approve_login

    from tools.purge_expired_asr_segments import purge_expired_segments

    email = f"p7purge-{uuid.uuid4().hex[:8]}@example.com"
    token = register_approve_login(client, email, "Passw0rd!1234")
    h = {"Authorization": f"Bearer {token}"}

    ws = client.get("/api/workspace/", headers=h)
    enc_id = ws.json()["state"]["extras"]["activeEncounterId"]

    expired_resp = _upload_segment(client, h, enc_id, "expired-seg")
    assert expired_resp.status_code == 200, expired_resp.text
    fresh_resp = _upload_segment(client, h, enc_id, "fresh-seg")
    assert fresh_resp.status_code == 200, fresh_resp.text

    expired_id = uuid.UUID(expired_resp.json()["id"])
    fresh_id = uuid.UUID(fresh_resp.json()["id"])

    with Session(db.engine) as session:
        expired_row = session.get(AsrRecordingSegment, expired_id)
        expired_row.expires_at = datetime.utcnow() - timedelta(days=1)
        session.add(expired_row)
        session.commit()

        expired_path = get_asr_segment_storage_root() / expired_row.server_file_key
        fresh_row = session.get(AsrRecordingSegment, fresh_id)
        fresh_path = get_asr_segment_storage_root() / fresh_row.server_file_key
    assert expired_path.exists()
    assert fresh_path.exists()

    deleted_count = purge_expired_segments()
    assert deleted_count == 1

    with Session(db.engine) as session:
        assert session.get(AsrRecordingSegment, expired_id) is None
        assert session.get(AsrRecordingSegment, fresh_id) is not None
    assert not expired_path.exists()
    assert fresh_path.exists()


def test_purge_is_a_no_op_when_nothing_is_expired(client):
    from tools.purge_expired_asr_segments import purge_expired_segments

    from auth_utils import register_approve_login

    email = f"p7purge-noop-{uuid.uuid4().hex[:8]}@example.com"
    token = register_approve_login(client, email, "Passw0rd!1234")
    h = {"Authorization": f"Bearer {token}"}
    ws = client.get("/api/workspace/", headers=h)
    enc_id = ws.json()["state"]["extras"]["activeEncounterId"]

    resp = _upload_segment(client, h, enc_id, "still-fresh")
    assert resp.status_code == 200, resp.text

    deleted_count = purge_expired_segments()
    assert deleted_count == 0
