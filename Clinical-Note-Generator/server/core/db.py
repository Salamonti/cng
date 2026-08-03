# server/core/db.py
import logging

from sqlalchemy import inspect, text
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine

from .config import get_settings

settings = get_settings()

connect_args = {}
engine_kwargs = {}
if settings.database_url.startswith("sqlite"):
    connect_args["check_same_thread"] = False
    if ":memory:" in settings.database_url:
        engine_kwargs["poolclass"] = StaticPool

engine = create_engine(
    settings.database_url,
    echo=False,
    connect_args=connect_args,
    **engine_kwargs,
)

logger = logging.getLogger(__name__)


def _configure_sqlite_wal() -> None:
    """G2: WAL + busy_timeout for concurrent workspace writes (skip :memory: tests)."""
    try:
        if getattr(engine.dialect, "name", "") != "sqlite":
            return
        if ":memory:" in settings.database_url:
            return
        with engine.connect() as conn:
            conn.execute(text("PRAGMA journal_mode=WAL"))
            conn.execute(text("PRAGMA busy_timeout=5000"))
            conn.execute(text("PRAGMA synchronous=NORMAL"))
            conn.commit()
    except Exception:
        pass


_configure_sqlite_wal()


def _migrate_sqlite_user_columns() -> None:
    # Use the active engine (tests swap db.engine for an isolated SQLite file).
    try:
        if getattr(engine.dialect, "name", "") != "sqlite":
            return
    except Exception:
        return
    try:
        insp = inspect(engine)
        cols = {c["name"] for c in insp.get_columns("user")}
    except Exception:
        return
    alters: list[str] = []
    if "display_name" not in cols:
        alters.append("ALTER TABLE user ADD COLUMN display_name VARCHAR")
    if "default_specialty" not in cols:
        alters.append("ALTER TABLE user ADD COLUMN default_specialty VARCHAR")
    if "default_location" not in cols:
        alters.append("ALTER TABLE user ADD COLUMN default_location VARCHAR")
    if "profile_updated_at" not in cols:
        alters.append("ALTER TABLE user ADD COLUMN profile_updated_at VARCHAR")
    if not alters:
        return
    with engine.begin() as conn:
        for stmt in alters:
            conn.execute(text(stmt))


def _migrate_sqlite_queued_jobs_columns() -> None:
    try:
        if getattr(engine.dialect, "name", "") != "sqlite":
            return
    except Exception:
        return
    try:
        insp = inspect(engine)
        cols = {c["name"] for c in insp.get_columns("queued_jobs")}
    except Exception:
        return
    alters: list[str] = []
    if "encounter_id" not in cols:
        alters.append("ALTER TABLE queued_jobs ADD COLUMN encounter_id VARCHAR")
    if not alters:
        return
    try:
        with engine.begin() as conn:
            for stmt in alters:
                conn.execute(text(stmt))
    except Exception:
        pass


def _migrate_sqlite_asr_segment_columns() -> None:
    try:
        if getattr(engine.dialect, "name", "") != "sqlite":
            return
    except Exception:
        return
    try:
        insp = inspect(engine)
        cols = {c["name"] for c in insp.get_columns("asr_recording_segments")}
    except Exception:
        return
    alters: list[str] = []
    if "recording_session_id" not in cols:
        alters.append("ALTER TABLE asr_recording_segments ADD COLUMN recording_session_id VARCHAR(128)")
    if "segment_role" not in cols:
        alters.append("ALTER TABLE asr_recording_segments ADD COLUMN segment_role VARCHAR(32) DEFAULT 'full_backup'")
    if "chunk_index" not in cols:
        alters.append("ALTER TABLE asr_recording_segments ADD COLUMN chunk_index INTEGER")
    if "window_start_sec" not in cols:
        alters.append("ALTER TABLE asr_recording_segments ADD COLUMN window_start_sec REAL")
    if "committed_text" not in cols:
        alters.append("ALTER TABLE asr_recording_segments ADD COLUMN committed_text TEXT")
    if "transcript_json" not in cols:
        alters.append("ALTER TABLE asr_recording_segments ADD COLUMN transcript_json TEXT")
    if "merge_method" not in cols:
        alters.append("ALTER TABLE asr_recording_segments ADD COLUMN merge_method VARCHAR(32)")
    if "refined_committed_text" not in cols:
        alters.append("ALTER TABLE asr_recording_segments ADD COLUMN refined_committed_text TEXT")
    if not alters:
        return
    try:
        with engine.begin() as conn:
            for stmt in alters:
                conn.execute(text(stmt))
    except Exception as exc:
        logger.warning("SQLite ASR segment column migration failed: %s", exc)


def init_db() -> None:
    # Import models to ensure metadata is registered
    from ..models import asr_recording_segment  # noqa: F401
    from ..models import refresh_token  # noqa: F401
    from ..models import password_reset_token  # noqa: F401
    from ..models import phi_access_log  # noqa: F401
    from ..models import user  # noqa: F401
    from ..models import workspace  # noqa: F401
    from ..models import queued_job  # noqa: F401
    from ..models import user_preferences  # noqa: F401
    from ..models import user_encounter  # noqa: F401

    SQLModel.metadata.create_all(engine)
    _migrate_sqlite_queued_jobs_columns()
    _migrate_sqlite_user_columns()
    _migrate_sqlite_asr_segment_columns()


def get_session():
    with Session(engine) as session:
        yield session
