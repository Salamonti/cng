"""Regression test (P1-7): PHI access must be recorded in an append-only
audit trail. Before this, there was no record anywhere of who generated,
downloaded, or deleted a PHI-bearing artifact.
"""
import hashlib
import uuid

from sqlmodel import Session, select

import server.core.db as db
from server.models.phi_access_log import PhiAccessLog


def _webm_payload(label=b"audio", size=1600):
    head = bytes([0x1A, 0x45, 0xDF, 0xA3]) + b"webm-segment-" + label
    return head + (b"\x00" * max(0, size - len(head)))


def _log_rows(user_id, action):
    with Session(db.engine) as session:
        return session.exec(
            select(PhiAccessLog).where(
                PhiAccessLog.user_id == user_id,
                PhiAccessLog.action == action,
            )
        ).all()


def test_note_generation_is_logged(client):
    from auth_utils import register_approve_login
    from server.app import app
    from server.core.dependencies import get_current_user

    email = f"audit-gen-{uuid.uuid4().hex[:8]}@example.com"
    token = register_approve_login(client, email, "Passw0rd!1234")

    with Session(db.engine) as session:
        from server.models.user import User as UserModel

        user = session.exec(select(UserModel).where(UserModel.email == email)).one()
        user_id = user.id

    resp = client.post(
        "/api/generate_v8_stream",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "note_type": "consult",
            "transcription_text": "hello",
            "old_visits_text": "",
            "mixed_other_text": "",
        },
    )
    assert resp.status_code == 200

    rows = _log_rows(user_id, "generate_note")
    assert len(rows) == 1
    assert rows[0].resource_type == "encounter"
    assert rows[0].detail == "consult"


def test_segment_download_is_logged(client):
    from auth_utils import register_approve_login
    from server.routes.asr_segments import get_asr_segment_storage_root

    email = f"audit-dl-{uuid.uuid4().hex[:8]}@example.com"
    token = register_approve_login(client, email, "Passw0rd!1234")
    h = {"Authorization": f"Bearer {token}"}

    ws = client.get("/api/workspace/", headers=h)
    enc_id = ws.json()["state"]["extras"]["activeEncounterId"]

    payload = _webm_payload(b"dl-seg")
    up = client.post(
        "/api/asr/segments",
        headers=h,
        data={
            "encounter_id": enc_id,
            "client_recording_id": "dl-seg-1",
            "expected_file_name": "dl-seg.webm",
            "expected_size_bytes": str(len(payload)),
            "expected_sha256": hashlib.sha256(payload).hexdigest(),
            "duration_sec": "3.5",
        },
        files={"file": ("dl-seg.webm", payload, "audio/webm")},
    )
    assert up.status_code == 200, up.text
    seg_id = up.json()["id"]

    dl = client.get(f"/api/asr/segments/{seg_id}/download", headers=h)
    assert dl.status_code == 200

    with Session(db.engine) as session:
        from server.models.user import User as UserModel

        user = session.exec(select(UserModel).where(UserModel.email == email)).one()

    rows = _log_rows(user.id, "download_segment")
    assert len(rows) == 1
    assert rows[0].resource_type == "asr_segment"
    assert rows[0].resource_id == seg_id


def test_encounter_deletion_is_logged(client):
    from auth_utils import register_approve_login

    email = f"audit-del-{uuid.uuid4().hex[:8]}@example.com"
    token = register_approve_login(client, email, "Passw0rd!1234")
    h = {"Authorization": f"Bearer {token}"}

    client.get("/api/workspace/", headers=h)
    cr = client.post("/api/encounters/", headers=h, json={"label": "To Delete"})
    assert cr.status_code == 201
    enc_id = cr.json()["id"]

    deleted = client.request("DELETE", f"/api/encounters/{enc_id}", headers=h, json={"confirm": True})
    assert deleted.status_code == 200, deleted.text

    with Session(db.engine) as session:
        from server.models.user import User as UserModel

        user = session.exec(select(UserModel).where(UserModel.email == email)).one()

    rows = _log_rows(user.id, "delete_encounter")
    assert len(rows) == 1
    assert rows[0].resource_id == enc_id


def test_audit_log_has_no_update_or_delete_helpers_exposed():
    """The whole point is append-only: confirm log_phi_access() only ever
    calls session.add(), never session.delete() or an update on an
    existing PhiAccessLog row."""
    import inspect

    from server.core.phi_audit import log_phi_access

    src = inspect.getsource(log_phi_access)
    assert "session.add(" in src
    assert "session.delete(" not in src
    assert ".query(PhiAccessLog)" not in src
