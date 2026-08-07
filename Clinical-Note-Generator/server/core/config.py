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
    access_exp = int(os.environ.get("JWT_ACCESS_TOKEN_EXP_MINUTES") or cfg.get("auth_access_token_exp_minutes") or 600)
    refresh_exp = int(os.environ.get("JWT_REFRESH_TOKEN_EXP_DAYS") or cfg.get("auth_refresh_token_exp_days") or 30)
    resend_from = os.environ.get("RESEND_FROM_EMAIL") or "DreamCision <noreply@support.dreamcision.com>"
    admin_email = os.environ.get("ADMIN_NOTIFICATION_EMAIL", "")

    # Secrets MUST come from the environment (systemd EnvironmentFile/.env) --
    # NEVER from config.json, which ships public "SET_IN_ENV_*" placeholders.
    # Reading them from env ensures we fail closed instead of silently signing
    # tokens with a known key if a deploy forgets to set them.
    jwt_secret = os.environ.get("JWT_SECRET")
    jwt_refresh = os.environ.get("JWT_REFRESH_SECRET")
    resend_key = os.environ.get("RESEND_API_KEY", "")

    def _require_secret(name: str, val: "str | None") -> str:
        if not val:
            raise RuntimeError(
                "Missing required JWT secrets in environment: "
                + f"{name}. Set it via the systemd EnvironmentFile/.env. "
                "Refusing to boot with a placeholder or missing secret."
            )
        if len(val.encode("utf-8")) < 32:
            raise RuntimeError(
                f"{name} is too short ({len(val.encode('utf-8'))} bytes); "
                "need at least 32 random bytes for HMAC-SHA256."
            )
        return val

    jwt_secret = _require_secret("JWT_SECRET", jwt_secret)
    jwt_refresh = _require_secret("JWT_REFRESH_SECRET", jwt_refresh)


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
