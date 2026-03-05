# C:\Clinical-Note-Generator\server\auth.py
import json
import os
import uuid
from functools import lru_cache
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlmodel import Session, select

from server.core.db import get_session
try:
    from server.core.env import load_env_file
except Exception:
    def load_env_file():
        try:
            from pathlib import Path
            from dotenv import load_dotenv
            env_path = Path(__file__).resolve().parents[1] / ".env"
            load_dotenv(dotenv_path=env_path, override=False)
            return env_path
        except Exception:
            return None
from server.core.security import decode_access_token
from server.models.user import User


security = HTTPBearer(auto_error=False)


@lru_cache(maxsize=1)
def _load_config() -> dict:
    cfg_path = os.path.join(os.path.dirname(__file__), '..', 'config', 'config.json')
    if os.path.exists(cfg_path):
        try:
            with open(cfg_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _get_token_from_env_or_cfg(env_name: str, cfg_key: str, default: Optional[str] = None) -> Optional[str]:
    load_env_file()
    val = os.environ.get(env_name)
    if val:
        return val
    cfg = _load_config()
    return cfg.get(cfg_key, default)


def require_api_bearer(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(security),
    session: Session = Depends(get_session),
):
    token = creds.credentials if creds else None
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Missing bearer token')

    try:
        payload = decode_access_token(token)
        user_id = payload.get("sub")
        user_uuid = uuid.UUID(str(user_id))
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Invalid token')

    user = session.exec(select(User).where(User.id == user_uuid)).one_or_none()
    if not user or not user.is_active or not user.is_approved:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='User is not authorized')
    return True


def require_admin_bearer(creds: Optional[HTTPAuthorizationCredentials] = Depends(security)):
    # Admin token separate from API key; default to 'notegenadmin' if unset
    admin_key = _get_token_from_env_or_cfg('ADMIN_API_KEY', 'admin_api_key', 'notegenadmin')
    if not creds or creds.scheme.lower() != 'bearer':
        raise HTTPException(status_code=401, detail='Missing admin bearer token')
    if creds.credentials != admin_key:
        raise HTTPException(status_code=401, detail='Invalid admin token')
    return True


def require_any_bearer(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(security),
    session: Session = Depends(get_session),
):
    """Allow either a valid user bearer token or the admin key."""
    if not creds or creds.scheme.lower() != 'bearer':
        raise HTTPException(status_code=401, detail='Missing bearer token')
    token = creds.credentials or ''
    admin_key = _get_token_from_env_or_cfg('ADMIN_API_KEY', 'admin_api_key', 'notegenadmin') or ''
    if token == admin_key:
        return True

    # Fallback: treat like standard user bearer auth
    token = creds.credentials
    if not token:
        raise HTTPException(status_code=401, detail='Missing bearer token')
    try:
        payload = decode_access_token(token)
        user_id = payload.get("sub")
        user_uuid = uuid.UUID(str(user_id))
    except Exception:
        raise HTTPException(status_code=401, detail='Invalid token')

    user = session.exec(select(User).where(User.id == user_uuid)).one_or_none()
    if not user or not user.is_active or not user.is_approved:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='User is not authorized')
    return True
