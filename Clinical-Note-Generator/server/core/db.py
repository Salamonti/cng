# server/core/db.py
import logging

from sqlalchemy import event, inspect, text
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


def configure_sqlite_pragmas(target_engine, database_url: str) -> None:
    """G2: WAL + busy_timeout for concurrent workspace writes.

    journal_mode is persisted in the database file itself, but busy_timeout
    and synchronous are per-connection -- a one-shot PRAGMA at import time
    only ever reached whichever single connection happened to run it,
    leaving every other connection the pool opens (QueuePool defaults to 5)
    on SQLite's own defaults (busy_timeout=0, synchronous=FULL). Registering
    this on the "connect" event guarantees every physical connection gets
    configured, not just the first one. Skipped for :memory: (each
    connection is already its own isolated, non-contending database).
    """
    if getattr(target_engine.dialect, "name", "") != "sqlite" or ":memory:" in database_url:
        return

    @event.listens_for(target_engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, connection_record) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.execute("PRAGMA synchronous=NORMAL")
        finally:
            cursor.close()


configure_sqlite_pragmas(engine, settings.database_url)

# P2-5: Postgres migration trigger point. WAL mode means readers never block
# writers, but SQLite still serializes writers one at a time -- busy_timeout
# just controls how long a second writer queues (5s) before giving up. At
# current scale (~50 doctors, workspace saves are occasional, not
# continuous) that queue essentially never fills. The concrete signal to
# watch, not a guessed doctor-count: workspace PUT 409s (see
# record_sync_incident() outcomes in routes/workspace.py) or literal
# "database is locked" exceptions in `journalctl -u dreamcision-fastapi`
# trending up over time. Either one showing up with any regularity means
# writers are actually queuing behind each other in practice, not just in
# theory -- that's the point to plan the Postgres migration, not before.


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
    except Exception as exc:
        logger.warning("SQLite queued_jobs column migration failed: %s", exc)


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
