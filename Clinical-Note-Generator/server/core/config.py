# server/core/config.py
import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict

from pydantic import BaseModel, Field
from server.core.env import load_env_file


class Settings(BaseModel):
    database_url: str = Field(alias="auth_database_url")
    jwt_secret: str
    jwt_refresh_secret: str
    access_token_exp_minutes: int = 600
    refresh_token_exp_days: int = 30
    # Email / password recovery
    resend_api_key: str = ""
    resend_from_email: str = "DreamCision <noreply@support.dreamcision.com>"
    admin_notification_email: str = ""


def _config_path() -> Path:
    return Path(__file__).resolve().parents[2] / "config" / "config.json"


def _default_db_url() -> str:
    data_dir = Path(__file__).resolve().parents[2] / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "user_data.sqlite"
    return f"sqlite:///{db_path.as_posix()}"


def _load_config() -> Dict[str, Any]:
    path = _config_path()
    if path.exists():
        try:
            with path.open("r", encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            return {}
    return {}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    load_env_file()
    cfg = _load_config()
    db_url = os.environ.get("DATABASE_URL") or cfg.get("auth_database_url") or _default_db_url()
    jwt_secret = os.environ.get("JWT_SECRET") or cfg.get("jwt_secret")
    jwt_refresh = os.environ.get("JWT_REFRESH_SECRET") or cfg.get("jwt_refresh_secret")
    access_exp = int(os.environ.get("JWT_ACCESS_TOKEN_EXP_MINUTES") or cfg.get("auth_access_token_exp_minutes") or 600)
    refresh_exp = int(os.environ.get("JWT_REFRESH_TOKEN_EXP_DAYS") or cfg.get("auth_refresh_token_exp_days") or 30)
    resend_key = os.environ.get("RESEND_API_KEY") or cfg.get("resend_api_key") or ""
    resend_from = os.environ.get("RESEND_FROM_EMAIL") or cfg.get("resend_from_email") or "DreamCision <noreply@support.dreamcision.com>"
    # No hardcoded personal-email fallback: set ADMIN_NOTIFICATION_EMAIL or
    # config.json's admin_notification_email (gitignored, unlike this file).
    # Admin registration notifications are simply not sent if unconfigured
    # (send_email already degrades gracefully -- see email_service.py).
    admin_email = os.environ.get("ADMIN_NOTIFICATION_EMAIL") or cfg.get("admin_notification_email") or ""

    if not jwt_secret or not jwt_refresh:
        raise RuntimeError("JWT secrets are not configured. Set JWT_SECRET and JWT_REFRESH_SECRET or update config.json.")

    return Settings(
        auth_database_url=db_url,
        jwt_secret=jwt_secret,
        jwt_refresh_secret=jwt_refresh,
        access_token_exp_minutes=access_exp,
        refresh_token_exp_days=refresh_exp,
        resend_api_key=resend_key,
        resend_from_email=resend_from,
        admin_notification_email=admin_email,
    )
