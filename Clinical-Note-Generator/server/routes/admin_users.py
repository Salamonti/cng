# C:\Clinical-Note-Generator\server\routes\admin_users.py
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select

from server.core.db import get_session
from server.core.dependencies import get_current_admin
from server.models.refresh_token import RefreshToken
from server.models.user import User
from server.models.workspace import UserWorkspace
from server.schemas.auth import UserProfile

router = APIRouter(prefix="/api/admin/users", tags=["admin-users"])


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

    workspace = session.exec(
        select(UserWorkspace).where(UserWorkspace.user_id == user.id)
    ).one_or_none()
    if workspace:
        session.delete(workspace)

    session.delete(user)
    session.commit()
    return
