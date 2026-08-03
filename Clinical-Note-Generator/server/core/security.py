# server/core/security.py
import time
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

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
    return jwt.encode(payload, secret, algorithm="HS256", headers={"typ": "JWT"})


class InvalidTokenType(jwt.InvalidTokenError):
    """Raised when a token decodes fine but isn't the type the caller expects."""


def create_access_token(subject: str, claims: Optional[Dict[str, Any]] = None) -> str:
    payload: Dict[str, Any] = {"typ": "access"}
    if claims:
        payload.update(claims)
    return _create_token(
        subject,
        settings.jwt_secret,
        timedelta(minutes=settings.access_token_exp_minutes),
        payload,
    )


def create_refresh_token(subject: str) -> str:
    return _create_token(
        subject,
        settings.jwt_refresh_secret,
        timedelta(days=settings.refresh_token_exp_days),
        {"typ": "refresh"},
    )


def _decode_jwt(token: str, secret: str) -> Dict[str, Any]:
    return jwt.decode(token, secret, algorithms=["HS256"])


def decode_access_token(token: str) -> Dict[str, Any]:
    # The access and refresh secrets differ, so a refresh token already fails
    # signature verification if presented here -- this typ check is
    # defense-in-depth against the secrets ever being misconfigured equal,
    # and it's the same field a token's own JWT header "typ" doesn't cover
    # (that header only marks "this is a JWT", not "this is an access JWT").
    data = _decode_jwt(token, settings.jwt_secret)
    if data.get("typ") != "access":
        raise InvalidTokenType("expected an access token")
    return data


def decode_refresh_token(token: str) -> Dict[str, Any]:
    data = _decode_jwt(token, settings.jwt_refresh_secret)
    if data.get("typ") != "refresh":
        raise InvalidTokenType("expected a refresh token")
    return data


class AttemptLimiter:
    """In-memory rate limit / lockout keyed by an arbitrary string (email, IP,
    or a combination). Safe as plain process state because this service runs
    as a single uvicorn worker (--workers 1); a restart clears counters,
    which is an acceptable trade-off for lockout state -- unlike password
    reset tokens, which are persisted (see PasswordResetToken) because
    losing an in-flight one has real user impact.
    """

    def __init__(self, max_attempts: int, window_sec: float, lockout_sec: float) -> None:
        self.max_attempts = max_attempts
        self.window_sec = window_sec
        self.lockout_sec = lockout_sec
        self._failures: Dict[str, List[float]] = defaultdict(list)
        self._locked_until: Dict[str, float] = {}

    def is_locked(self, key: str) -> bool:
        until = self._locked_until.get(key)
        return bool(until and until > time.time())

    def seconds_remaining(self, key: str) -> int:
        until = self._locked_until.get(key, 0.0)
        return max(0, int(until - time.time()))

    def record_failure(self, key: str) -> None:
        now = time.time()
        recent = [t for t in self._failures[key] if now - t < self.window_sec]
        recent.append(now)
        if len(recent) >= self.max_attempts:
            self._locked_until[key] = now + self.lockout_sec
            self._failures.pop(key, None)
        else:
            self._failures[key] = recent

    def record_success(self, key: str) -> None:
        self._failures.pop(key, None)
        self._locked_until.pop(key, None)


# Login: 5 failed attempts per email within 15 minutes locks that email out
# for 15 minutes. Keyed by email (not IP) so a distributed brute force
# against one account is still caught even from many source IPs.
login_attempts = AttemptLimiter(max_attempts=5, window_sec=900, lockout_sec=900)

# Password-reset requests: cap per email so an attacker can't mail-bomb a
# victim's inbox with reset links.
reset_request_attempts = AttemptLimiter(max_attempts=5, window_sec=900, lockout_sec=900)
