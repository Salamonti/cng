# server/core/security.py
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

import jwt
from passlib.context import CryptContext

from .config import get_settings

# PBKDF2 avoids bcrypt's 72-byte limit and doesn't rely on native backends.
pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")
settings = get_settings()


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    return pwd_context.verify(password, hashed)


def _create_token(
    subject: str,
    secret: str,
    expires_delta: timedelta,
    extra_claims: Optional[Dict[str, Any]] = None,
) -> str:
    payload: Dict[str, Any] = {"sub": subject, "exp": datetime.utcnow() + expires_delta}
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, secret, algorithm="HS256")


def create_access_token(subject: str, claims: Optional[Dict[str, Any]] = None) -> str:
    return _create_token(
        subject,
        settings.jwt_secret,
        timedelta(minutes=settings.access_token_exp_minutes),
        claims,
    )


def create_refresh_token(subject: str) -> str:
    return _create_token(
        subject,
        settings.jwt_refresh_secret,
        timedelta(days=settings.refresh_token_exp_days),
    )


def decode_access_token(token: str) -> Dict[str, Any]:
    return jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])


def decode_refresh_token(token: str) -> Dict[str, Any]:
    return jwt.decode(token, settings.jwt_refresh_secret, algorithms=["HS256"])
