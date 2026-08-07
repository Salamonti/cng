# server/routes/auth_users.py
import hashlib
import os
import secrets
import uuid
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlmodel import Session, select

from server.core.config import get_settings
from server.core.db import get_session
from server.core.dependencies import get_current_user
from server.core.security import (
    create_access_token,
    create_refresh_token,
    decode_refresh_token,
    hash_password,
    login_attempts,
    reset_request_attempts,
    verify_password,
)
from server.models.password_reset_token import PasswordResetToken
from server.models.refresh_token import RefreshToken
from server.models.user import User
from server.schemas.auth import (
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
    UserProfile,
)
from server.services.email_service import (
    send_admin_registration_notification,
    send_password_reset_email,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])
settings = get_settings()

_RESET_TTL = 900  # 15 minutes


def _hash_reset_token(token: str) -> str:
    # Unlike password/refresh-token hashing, this needs an exact-match DB
    # lookup by token alone (no user_id is known yet at request time), so it
    # can't use the salted, non-deterministic pwd_context hash RefreshToken
    # uses. A fast deterministic hash is fine here because the input already
    # carries 256 bits of entropy from secrets.token_urlsafe(32) -- there's
    # no offline-guessing risk a slow hash would mitigate, only a lookup-key
    # requirement a slow hash can't satisfy.
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


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

    # Notify admin about new registration (fire-and-forget)
    send_admin_registration_notification(
        admin_email=settings.admin_notification_email,
        user_email=user.email,
    )

    return UserProfile(
        id=str(user.id),
        email=user.email,
        is_admin=user.is_admin,
        is_approved=user.is_approved,
        created_at=user.created_at,
    )


def _cookie_secure() -> bool:
    """Refresh-token cookie: Secure by default. Override with COOKIE_SECURE=0
    only for local/LAN plaintext dev where there is no TLS. Production is
    served behind a TLS edge (cloudflared) so the cookie must be Secure."""
    raw = os.environ.get("COOKIE_SECURE", "").strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return False
    if raw in {"1", "true", "yes", "on"}:
        return True
    return True


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
        secure=_cookie_secure(),
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
    lockout_key = payload.email.strip().lower()
    if login_attempts.is_locked(lockout_key):
        raise HTTPException(
            status_code=429,
            detail=(
                "Too many failed login attempts. "
                f"Try again in {login_attempts.seconds_remaining(lockout_key)} seconds."
            ),
        )

    user = session.exec(select(User).where(User.email == payload.email)).first()
    if not user or not verify_password(payload.password, user.hashed_password):
        login_attempts.record_failure(lockout_key)
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not user.is_approved:
        raise HTTPException(status_code=403, detail="Awaiting approval")
    login_attempts.record_success(lockout_key)
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


# ---------------------------------------------------------------------------
# Password recovery
# ---------------------------------------------------------------------------

@router.post("/forgot-password")
def forgot_password(
    payload: dict,  # {"email": "user@example.com"}
    session: Session = Depends(get_session),
):
    """Request a password reset email. Always returns 200 to prevent email enumeration."""
    email = payload.get("email", "")
    if not email:
        raise HTTPException(status_code=400, detail="Email is required")

    lockout_key = email.strip().lower()
    if reset_request_attempts.is_locked(lockout_key):
        # Same generic message as the "no such user" branch below -- a
        # distinct rate-limit response here would itself leak whether the
        # email exists (an account gets 429s, a non-account never will).
        return {"message": "If an account exists with that email, a reset link has been sent."}
    reset_request_attempts.record_failure(lockout_key)

    user = session.exec(select(User).where(User.email == email)).first()
    if not user:
        # Return 200 even if user doesn't exist (prevents email enumeration)
        return {"message": "If an account exists with that email, a reset link has been sent."}

    # Generate a secure reset token and persist it (WO P1-1: survives a
    # restart -- an in-memory dict here previously dropped every pending
    # reset token on every deploy or crash).
    token = secrets.token_urlsafe(32)
    session.add(
        PasswordResetToken(
            user_id=user.id,
            token_hash=_hash_reset_token(token),
            expires_at=datetime.utcnow() + timedelta(seconds=_RESET_TTL),
        )
    )
    session.commit()

    # Build reset link — frontend will handle this route
    # The base URL depends on the deployment (localhost or production)
    # We use a generic path that the frontend can intercept
    reset_link = f"https://notes.ieissa.com/reset-password/{token}"

    # Try to send email — don't fail if email service is down
    send_password_reset_email(to=user.email, reset_link=reset_link)

    return {"message": "If an account exists with that email, a reset link has been sent."}


@router.post("/reset-password/{token}")
def reset_password(
    token: str,
    payload: dict,  # {"new_password": "NewP@ss123!"}
    session: Session = Depends(get_session),
):
    """Reset password using a valid token."""
    new_password = payload.get("new_password", "")

    # Validate password strength
    if not new_password or len(new_password) < 12:
        raise HTTPException(status_code=400, detail="Password must be at least 12 characters")
    rules = [
        any(c.islower() for c in new_password),
        any(c.isupper() for c in new_password),
        any(c.isdigit() for c in new_password),
        any(not c.isalnum() for c in new_password),
    ]
    if not all(rules):
        raise HTTPException(
            status_code=400,
            detail="Password must include uppercase, lowercase, digit, and symbol.",
        )

    # Look up the token
    token_hash = _hash_reset_token(token)
    entry = session.exec(
        select(PasswordResetToken).where(PasswordResetToken.token_hash == token_hash)
    ).first()
    if not entry or entry.used:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")

    # Check expiry
    if datetime.utcnow() > entry.expires_at:
        raise HTTPException(status_code=400, detail="Reset token has expired")

    # Find user and update password
    user = session.exec(select(User).where(User.id == entry.user_id)).first()
    if not user:
        raise HTTPException(status_code=400, detail="User not found")

    user.hashed_password = hash_password(new_password)
    session.add(user)

    # Invalidate the token (one-time use)
    entry.used = True
    session.add(entry)
    session.commit()

    # Invalidate all refresh tokens (force re-login)
    tokens = session.exec(select(RefreshToken).where(RefreshToken.user_id == user.id)).all()
    for t in tokens:
        t.revoked = True
        session.add(t)
    session.commit()

    return {"message": "Password has been reset. Please log in with your new password."}
