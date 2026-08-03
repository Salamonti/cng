"""Admin user deletion must cascade-delete the user's clinical data.

Regression test for: delete_user only removed RefreshToken + the legacy
UserWorkspace row, leaving every UserEncounter (transcriptions, generated
notes), QueuedJob, and AsrRecordingSegment permanently orphaned in the
database with no route able to reach them again.
"""
import uuid

from sqlmodel import Session, select

import server.core.db as db
from server.models.asr_recording_segment import AsrRecordingSegment
from server.models.queued_job import QueuedJob
from server.models.user import User
from server.models.user_encounter import UserEncounter
from server.models.user_preferences import UserPreferences
from server.models.workspace import UserWorkspace
from auth_utils import register_approve_login

_PW = "Passw0rd!1234"


def _make_admin_token(client) -> str:
    email = f"admin-{uuid.uuid4().hex[:8]}@example.com"
    register_approve_login(client, email, _PW)
    with Session(db.engine) as session:
        user = session.exec(select(User).where(User.email == email)).one()
        user.is_admin = True
        session.add(user)
        session.commit()
    login = client.post("/api/auth/login", json={"email": email, "password": _PW})
    assert login.status_code == 200
    return login.json()["access_token"]


def test_delete_user_cascades_clinical_data(client):
    # Regular user: register + touch the workspace so a UserEncounter/UserWorkspace
    # row gets created, then plant a QueuedJob and an AsrRecordingSegment row
    # directly (avoids needing real file uploads) to prove those cascade too.
    target_email = f"deleteme-{uuid.uuid4().hex[:8]}@example.com"
    target_token = register_approve_login(client, target_email, _PW)
    h = {"Authorization": f"Bearer {target_token}"}
    client.get("/api/workspace/", headers=h)

    with Session(db.engine) as session:
        target_user = session.exec(select(User).where(User.email == target_email)).one()
        target_user_id = target_user.id

        encounters_before = session.exec(
            select(UserEncounter).where(UserEncounter.user_id == target_user_id)
        ).all()
        assert encounters_before, "expected at least one UserEncounter to exist before delete"
        encounter_id = encounters_before[0].id

        session.add(UserPreferences(user_id=target_user_id, preferences_json={"note_type": "soap"}))
        session.add(
            QueuedJob(
                user_id=target_user_id,
                encounter_id=encounter_id,
                type="ocr",
                status="done",
                file_name="scan.png",
                mime_type="image/png",
                file_size=123,
                server_file_key=f"nonexistent/{uuid.uuid4().hex}.png",
            )
        )
        session.add(
            AsrRecordingSegment(
                client_recording_id=str(uuid.uuid4()),
                user_id=target_user_id,
                encounter_id=encounter_id,
                file_name="rec.webm",
                mime_type="audio/webm",
                file_size=456,
                sha256="0" * 64,
                server_file_key=f"nonexistent/{uuid.uuid4().hex}.webm",
            )
        )
        session.commit()

    admin_token = _make_admin_token(client)
    resp = client.delete(
        f"/api/admin/users/{target_user_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 204

    with Session(db.engine) as session:
        assert session.exec(select(User).where(User.id == target_user_id)).one_or_none() is None
        assert not session.exec(
            select(UserEncounter).where(UserEncounter.user_id == target_user_id)
        ).all()
        assert not session.exec(
            select(UserWorkspace).where(UserWorkspace.user_id == target_user_id)
        ).all()
        assert not session.exec(
            select(QueuedJob).where(QueuedJob.user_id == target_user_id)
        ).all()
        assert not session.exec(
            select(AsrRecordingSegment).where(AsrRecordingSegment.user_id == target_user_id)
        ).all()
        assert not session.exec(
            select(UserPreferences).where(UserPreferences.user_id == target_user_id)
        ).all()


def test_delete_user_requires_admin(client):
    other_email = f"target-{uuid.uuid4().hex[:8]}@example.com"
    register_approve_login(client, other_email, _PW)
    with Session(db.engine) as session:
        other_user_id = session.exec(select(User).where(User.email == other_email)).one().id

    non_admin_email = f"nonadmin-{uuid.uuid4().hex[:8]}@example.com"
    non_admin_token = register_approve_login(client, non_admin_email, _PW)
    resp = client.delete(
        f"/api/admin/users/{other_user_id}",
        headers={"Authorization": f"Bearer {non_admin_token}"},
    )
    assert resp.status_code == 403
