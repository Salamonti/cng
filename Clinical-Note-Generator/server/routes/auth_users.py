# server/routes/auth_users.py
import uuid
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlmodel import Session, select

from server.core.config import get_settings
from server.core.db import get_session
from server.core.dependencies import get_current_user
from server.core.security import (
    create_access_token,
    create_refresh_token,
    decode_refresh_token,
    hash_password,
    verify_password,
)
from server.models.refresh_token import RefreshToken
from server.models.user import User
from server.schemas.auth import (
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
    UserProfile,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])
settings = get_settings()


@router.post("/register", response_model=UserProfile)
def register_user(payload: RegisterRequest, session: Session = Depends(get_session)):
    if session.exec(select(User).where(User.email == payload.email)).first():
        raise HTTPException(status_code=400, detail="Email already registered")
    user = User(
        email=payload.email,
        hashed_password=hash_password(payload.password),
        is_active=True,
        is_admin=False,
        is_approved=False,
    )
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


def _issue_tokens(user: User, session: Session, response: Response) -> TokenResponse:
    access = create_access_token(str(user.id))
    refresh = create_refresh_token(str(user.id))
    token_entry = RefreshToken(
        user_id=user.id,
        token_hash=hash_password(refresh),
        expires_at=datetime.utcnow() + timedelta(days=settings.refresh_token_exp_days),
    )
    session.add(token_entry)
    session.commit()
    response.set_cookie(
        "refresh_token",
        refresh,
        httponly=True,
        secure=False,
        samesite="lax",
        max_age=settings.refresh_token_exp_days * 24 * 3600,
    )
    return TokenResponse(
        access_token=access,
        expires_in=settings.access_token_exp_minutes * 60,
        refresh_token=refresh,
    )


@router.post("/login", response_model=TokenResponse)
def login_user(
    payload: LoginRequest,
    response: Response,
    session: Session = Depends(get_session),
):
    user = session.exec(select(User).where(User.email == payload.email)).first()
    if not user or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not user.is_approved:
        raise HTTPException(status_code=403, detail="Awaiting approval")
    return _issue_tokens(user, session, response)


@router.get("/me", response_model=UserProfile)
def get_me(current_user: User = Depends(get_current_user)):
    return UserProfile(
        id=str(current_user.id),
        email=current_user.email,
        is_admin=current_user.is_admin,
        is_approved=current_user.is_approved,
        created_at=current_user.created_at,
    )


def _extract_refresh_token(payload: RefreshRequest, request: Request) -> Optional[str]:
    if payload.refresh_token:
        return payload.refresh_token
    return request.cookies.get("refresh_token")


@router.post("/refresh", response_model=TokenResponse)
def refresh_token(
    payload: RefreshRequest,
    request: Request,
    response: Response,
    session: Session = Depends(get_session),
):
    token = _extract_refresh_token(payload, request)
    if not token:
        raise HTTPException(status_code=401, detail="Missing refresh token")
    try:
        data = decode_refresh_token(token)
        user_uuid = uuid.UUID(str(data.get("sub", "")))
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid refresh token")
    user = session.exec(select(User).where(User.id == user_uuid)).one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status_code=403, detail="User unavailable")
    token_entries = session.exec(
        select(RefreshToken).where(
            RefreshToken.user_id == user.id,
            RefreshToken.revoked.is_(False),
            RefreshToken.expires_at > datetime.utcnow(),
        )
    ).all()
    matching = next((t for t in token_entries if verify_password(token, t.token_hash)), None)
    if not matching:
        raise HTTPException(status_code=401, detail="Refresh token revoked")
    matching.revoked = True
    session.add(matching)
    session.commit()
    return _issue_tokens(user, session, response)


@router.post("/logout", status_code=204)
def logout(
    payload: RefreshRequest,
    request: Request,
    response: Response,
    session: Session = Depends(get_session),
):
    token = _extract_refresh_token(payload, request)
    if token:
        try:
            data = decode_refresh_token(token)
            user_uuid = uuid.UUID(str(data.get("sub", "")))
        except Exception:
            user_uuid = None
        if user_uuid:
            token_entries = session.exec(
                select(RefreshToken).where(
                    RefreshToken.user_id == user_uuid,
                    RefreshToken.revoked.is_(False),
                )
            ).all()
            for entry in token_entries:
                if verify_password(token, entry.token_hash):
                    entry.revoked = True
                    session.add(entry)
                    break
            session.commit()
    response.delete_cookie("refresh_token")
    return


@router.post("/logout_all", status_code=204)
def logout_all(
    response: Response,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    tokens = session.exec(select(RefreshToken).where(RefreshToken.user_id == current_user.id)).all()
    for entry in tokens:
        entry.revoked = True
        session.add(entry)
    session.commit()
    response.delete_cookie("refresh_token")
    return
