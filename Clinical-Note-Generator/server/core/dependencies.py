# C:\Clinical-Note-Generator\server\core\dependencies.py
import uuid
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials, OAuth2PasswordBearer
from sqlmodel import Session, select

from .db import get_session
from .security import decode_access_token
from server.models.user import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")
_http_bearer = HTTPBearer(auto_error=False)


def require_api_bearer(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(_http_bearer),
    session: Session = Depends(get_session),
):
    """Require a valid user JWT bearer token."""
    token = creds.credentials if creds else None
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")

    try:
        payload = decode_access_token(token)
        user_id = payload.get("sub")
        user_uuid = uuid.UUID(str(user_id))
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    user = session.exec(select(User).where(User.id == user_uuid)).one_or_none()
    if not user or not user.is_active or not user.is_approved:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User is not authorized")
    return True


def get_current_user(
    token: str = Depends(oauth2_scheme),
    session: Session = Depends(get_session),
) -> User:
    try:
        payload = decode_access_token(token)
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token payload")

    try:
        user_uuid = uuid.UUID(str(user_id))
    except (ValueError, TypeError):
        raise HTTPException(status_code=401, detail="Invalid token payload")

    user = session.exec(select(User).where(User.id == user_uuid)).one_or_none()
    if not user or not user.is_active or not user.is_approved:
        raise HTTPException(status_code=403, detail="User is not authorized")
    return user


def get_current_admin(current_user: User = Depends(get_current_user)) -> User:
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin privileges required")
    return current_user
