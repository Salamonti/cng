import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import create_engine


# Ensure imports like "server.app" work during pytest.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Test-safe auth defaults.
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-0123456789abcdef0123456789abcdef")
os.environ.setdefault("JWT_REFRESH_SECRET", "test-jwt-refresh-secret-0123456789abcdef0123456789abc")


@pytest.fixture(autouse=True)
def _never_send_real_email(monkeypatch):
    """Tests must never place a real call to the Resend API, regardless of
    what RESEND_API_KEY resolves to in this environment. Every test that
    registers a user (directly or via a helper) triggers a real admin
    notification email through send_admin_registration_notification, and
    the password-reset tests trigger send_password_reset_email -- with the
    real (if leaked) production key live in .env, repeated full-suite runs
    were quietly burning through the account's real Resend send quota.
    """
    import resend

    monkeypatch.setattr(resend.Emails, "send", lambda *a, **k: {"id": "test-noop"})


@pytest.fixture(autouse=True)
def _reset_attempt_limiters():
    """Isolate in-memory rate-limiters between tests. The register/login/
    reset AttemptLimiter instances are module-global singletons; without a
    reset, one test's attempts bleed into the next (e.g. every register call
    now counts toward the per-IP registration cap, which would exhaust the
    window across the whole suite from the shared TestClient IP)."""
    from server.core import security

    for limiter in (
        security.login_attempts,
        security.reset_request_attempts,
        security.register_attempts,
    ):
        limiter.reset()
    yield
    for limiter in (
        security.login_attempts,
        security.reset_request_attempts,
        security.register_attempts,
    ):
        limiter.reset()


@pytest.fixture
def client(tmp_path):
    import server.core.db as db
    from server.app import app

    db_file = tmp_path / "smoke.sqlite"
    eng = create_engine(
        f"sqlite:///{db_file.as_posix()}",
        connect_args={"check_same_thread": False},
    )
    db.engine = eng
    # Routes under server/routes import `server.core.db`; keep one engine.
    # (No longer needed after unifying imports — left as no-op for safety.)
    pass
    db.init_db()

    app.dependency_overrides.clear()
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()

