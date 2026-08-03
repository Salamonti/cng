# server/core/bootstrap_admin.py
"""Optional one-time bootstrap of an admin user from environment variables (operator-only).

Set ADMIN_BOOTSTRAP_EMAIL and ADMIN_BOOTSTRAP_PASSWORD on the server process.
If a user with that email already exists, no change is made (see logs if not admin).

Never commit real credentials. Use deployment env or a local .env excluded from git.
"""
from __future__ import annotations

import logging
import os

from sqlmodel import Session, select

from server.core.db import engine
from server.core.security import hash_password
from server.models.user import User

logger = logging.getLogger(__name__)


def ensure_bootstrap_admin() -> None:
    email = (os.environ.get("ADMIN_BOOTSTRAP_EMAIL") or "").strip()
    password = os.environ.get("ADMIN_BOOTSTRAP_PASSWORD") or ""
    if not email or not password:
        logger.debug("ADMIN_BOOTSTRAP_EMAIL / ADMIN_BOOTSTRAP_PASSWORD not set; skip bootstrap admin")
        return

    with Session(engine) as session:
        existing = session.exec(select(User).where(User.email == email)).first()
        if existing:
            if not existing.is_admin:
                logger.warning(
                    "Bootstrap skipped: user %s already exists but is not admin (fix in DB if needed)",
                    email,
                )
            return

        user = User(
            email=email,
            hashed_password=hash_password(password),
            is_active=True,
            is_admin=True,
            is_approved=True,
        )
        session.add(user)
        session.commit()
        logger.info("Bootstrap admin user created for %s", email)
