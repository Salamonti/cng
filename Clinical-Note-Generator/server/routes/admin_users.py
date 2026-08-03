# server/routes/admin_users.py
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select

from server.core.config import get_settings
from server.core.db import get_session
from server.core.dependencies import get_current_admin
from server.models.asr_recording_segment import AsrRecordingSegment
from server.models.queued_job import QueuedJob
from server.models.refresh_token import RefreshToken
from server.models.user import User
from server.models.user_encounter import UserEncounter
from server.models.user_preferences import UserPreferences
from server.models.workspace import UserWorkspace
from server.routes.asr_segments import delete_asr_segment_file
from server.routes.queue import delete_queued_file
from server.schemas.auth import UserProfile
from server.services.email_service import send_approval_email

router = APIRouter(prefix="/api/admin/users", tags=["admin-users"])
settings = get_settings()


@router.get("", response_model=list[UserProfile])
@router.get("/", response_model=list[UserProfile])
def list_users(
    session: Session = Depends(get_session),
    _: User = Depends(get_current_admin),
):
    users = session.exec(select(User)).all()
    return [
        UserProfile(
            id=str(user.id),
            email=user.email,
            is_admin=user.is_admin,
            is_approved=user.is_approved,
            created_at=user.created_at,
        )
        for user in users
    ]


def _get_user_or_404(user_id: uuid.UUID, session: Session) -> User:
    user = session.exec(select(User).where(User.id == user_id)).one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.patch("/{user_id}/approve", response_model=UserProfile)
@router.patch("/{user_id}/approve/", response_model=UserProfile)
def approve_user(
    user_id: uuid.UUID,
    session: Session = Depends(get_session),
    _: User = Depends(get_current_admin),
):
    user = _get_user_or_404(user_id, session)
    user.is_approved = True
    session.add(user)
    session.commit()
    session.refresh(user)

    # Send approval email to user (fire-and-forget, don't block response)
    send_approval_email(to=user.email)

    return UserProfile(
        id=str(user.id),
        email=user.email,
        is_admin=user.is_admin,
        is_approved=user.is_approved,
        created_at=user.created_at,
    )


@router.patch("/{user_id}/reject", response_model=UserProfile)
@router.patch("/{user_id}/reject/", response_model=UserProfile)
def reject_user(
    user_id: uuid.UUID,
    session: Session = Depends(get_session),
    _: User = Depends(get_current_admin),
):
    user = _get_user_or_404(user_id, session)
    user.is_approved = False
    session.add(user)
    session.commit()
    session.refresh(user)
    return UserProfile(
        id=str(user.id),
        email=user.email,
        is_admin=user.is_admin,
        is_approved=user.is_approved,
        created_at=user.created_at,
    )


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
@router.delete("/{user_id}/", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(
    user_id: uuid.UUID,
    session: Session = Depends(get_session),
    _: User = Depends(get_current_admin),
):
    user = _get_user_or_404(user_id, session)
    if user.is_admin:
        raise HTTPException(status_code=400, detail="Cannot delete admin users")

    tokens = session.exec(select(RefreshToken).where(RefreshToken.user_id == user.id)).all()
    for token in tokens:
        session.delete(token)

    # Cascade the user's clinical data too -- previously only RefreshToken and
    # the legacy UserWorkspace row were removed, leaving every UserEncounter
    # (transcriptions, generated notes), QueuedJob, and AsrRecordingSegment
    # (plus their on-disk files) permanently orphaned in the database with no
    # route able to reach them again. Mirrors the per-encounter cleanup
    # already done correctly in routes/encounters.py::delete_encounter.
    jobs = session.exec(select(QueuedJob).where(QueuedJob.user_id == user.id)).all()
    for job in jobs:
        delete_queued_file(job.server_file_key)
        session.delete(job)

    segments = session.exec(
        select(AsrRecordingSegment).where(AsrRecordingSegment.user_id == user.id)
    ).all()
    for segment in segments:
        delete_asr_segment_file(segment.server_file_key)
        session.delete(segment)

    encounters = session.exec(
        select(UserEncounter).where(UserEncounter.user_id == user.id)
    ).all()
    for encounter in encounters:
        session.delete(encounter)

    prefs = session.exec(
        select(UserPreferences).where(UserPreferences.user_id == user.id)
    ).one_or_none()
    if prefs:
        session.delete(prefs)

    workspace = session.exec(
        select(UserWorkspace).where(UserWorkspace.user_id == user.id)
    ).one_or_none()
    if workspace:
        session.delete(workspace)

    session.delete(user)
    session.commit()
    return
