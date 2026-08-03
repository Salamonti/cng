"""G2 regression: WAL/busy_timeout/synchronous must apply to every pooled
SQLite connection, not just whichever one happens to run first.

Before this fix, configure_sqlite_pragmas() ran PRAGMA statements once
against a single connection.connect() call. journal_mode is persisted in
the database file so that part looked fine, but busy_timeout and
synchronous are per-connection: with the default QueuePool (size 5),
every connection beyond the first one silently fell back to SQLite's
defaults (busy_timeout=0, synchronous=FULL) -- exactly the concurrent
writer scenario (~50 doctors, shared workspace-save path) this pragma
tuning exists for.
"""
from pathlib import Path

from sqlmodel import create_engine

from server.core.db import configure_sqlite_pragmas


def test_pragmas_apply_to_every_pooled_connection(tmp_path: Path):
    db_file = tmp_path / "pragma_check.sqlite"
    engine = create_engine(
        f"sqlite:///{db_file.as_posix()}",
        connect_args={"check_same_thread": False},
    )
    configure_sqlite_pragmas(engine, str(engine.url))

    # Hold several connections open simultaneously to force the pool to
    # actually open multiple distinct physical connections (a single
    # connect()/close() cycle would just reuse the same one and mask the bug).
    conns = [engine.connect() for _ in range(5)]
    try:
        for i, conn in enumerate(conns):
            busy_timeout = conn.exec_driver_sql("PRAGMA busy_timeout").scalar()
            synchronous = conn.exec_driver_sql("PRAGMA synchronous").scalar()
            journal_mode = conn.exec_driver_sql("PRAGMA journal_mode").scalar()
            assert busy_timeout == 5000, f"connection {i} missing busy_timeout"
            assert synchronous == 1, f"connection {i} missing synchronous=NORMAL (got {synchronous})"
            assert journal_mode == "wal", f"connection {i} missing journal_mode=WAL"
    finally:
        for conn in conns:
            conn.close()


def test_pragmas_skipped_for_memory_db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    # Must not raise, and must not attach a "connect" pragma listener --
    # nothing to assert on directly beyond "this doesn't blow up".
    configure_sqlite_pragmas(engine, "sqlite:///:memory:")
