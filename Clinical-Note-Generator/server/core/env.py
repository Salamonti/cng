from functools import lru_cache
from pathlib import Path

try:
    from dotenv import load_dotenv  # type: ignore
except Exception:
    load_dotenv = None


@lru_cache(maxsize=1)
def load_env_file() -> Path:
    """
    Load environment variables from repo-root .env once.
    Does not override pre-existing process environment values.
    """
    env_path = Path(__file__).resolve().parents[2] / ".env"
    if load_dotenv is not None:
        load_dotenv(dotenv_path=env_path, override=False)
        return env_path

    # Lightweight fallback parser when python-dotenv is unavailable.
    if env_path.exists():
        try:
            for raw in env_path.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                if not key:
                    continue
                value = value.strip()
                if (value.startswith('"') and value.endswith('"')) or (
                    value.startswith("'") and value.endswith("'")
                ):
                    value = value[1:-1]
                import os
                os.environ.setdefault(key, value)
        except Exception:
            pass
    return env_path
