"""Regression test (P1-7): TTL-expiring an encounter (the opportunistic,
on-access 9-day auto-delete in encounter_workspace.py) must also delete
its ASR audio segments -- rows and files -- the same way the manual
DELETE /api/encounters/{id} route already does.

Before this fix, _prune_user_encounters_by_ttl deleted the UserEncounter
and its QueuedJob rows but never touched AsrRecordingSegment. SQLite
doesn't enforce the encounter_id foreign key here (no PRAGMA
foreign_keys=ON), so the segment row and its audio file -- PHI -- were
silently orphaned rather than raising an error: the parent encounter
gone, the recording left behind forever.
"""
import hashlib
import uuid
from datetime import datetime, timedelta


def _webm_payload(label=b"audio", size=1600):
    head = bytes([0x1A, 0x45, 0xDF, 0xA3]) + b"webm-segment-" + label
    return head + (b"\x00" * max(0, size - len(head)))


def test_ttl_expired_encounter_deletes_its_asr_segment_row_and_file(client):
    import server.core.db as db
    from sqlmodel import Session, select
    from server.models.asr_recording_segment import AsrRecordingSegment
    from server.models.user_encounter import UserEncounter
    from server.routes.asr_segments import get_asr_segment_storage_root
    from auth_utils import register_approve_login

    email = f"p7ttl-{uuid.uuid4().hex[:8]}@example.com"
    token = register_approve_login(client, email, "Passw0rd!1234")
    h = {"Authorization": f"Bearer {token}"}

    # Baseline encounter, then a second one that will expire (mirrors
    # test_auto_delete_encounters_after_9_days's need for >1 encounter).
    client.get("/api/workspace/", headers=h)
    cr = client.post("/api/encounters/", headers=h, json={"label": "Expiring"})
    assert cr.status_code == 201
    enc_id = cr.json()["id"]

    payload = _webm_payload(b"ttl-seg")
    up = client.post(
        "/api/asr/segments",
        headers=h,
        data={
            "encounter_id": enc_id,
            "client_recording_id": "ttl-seg-1",
            "expected_file_name": "ttl-seg.webm",
            "expected_size_bytes": str(len(payload)),
            "expected_sha256": hashlib.sha256(payload).hexdigest(),
            "duration_sec": "3.5",
        },
        files={"file": ("ttl-seg.webm", payload, "audio/webm")},
    )
    assert up.status_code == 200, up.text
    seg_id = up.json()["id"]

    with Session(db.engine) as session:
        segment = session.get(AsrRecordingSegment, uuid.UUID(seg_id))
        assert segment is not None
        file_path = get_asr_segment_storage_root() / segment.server_file_key
        assert file_path.exists()

        # Force the encounter (and thus its segment) past the TTL cutoff.
        enc = session.exec(select(UserEncounter).where(UserEncounter.id == uuid.UUID(enc_id))).one()
        enc.updated_at = datetime.utcnow() - timedelta(days=10)
        session.add(enc)
        session.commit()

    # Any encounter/workspace access triggers TTL pruning.
    lst = client.get("/api/encounters/", headers=h)
    assert lst.status_code == 200
    assert enc_id not in {e["id"] for e in lst.json()["encounters"]}

    with Session(db.engine) as session:
        segment = session.exec(
            select(AsrRecordingSegment).where(AsrRecordingSegment.id == uuid.UUID(seg_id))
        ).one_or_none()
        assert segment is None, "segment row must be deleted when its encounter TTL-expires"
    assert not file_path.exists(), "segment audio file must be deleted when its encounter TTL-expires"
