"""STEP 2e -- reclassified silent handlers in routes/qa_chat.py + queue.py.

- qa_chat.py L288 _parse_evidence_year_value -- safe parse guard (malformed
  year -> None so one bad RAG metadata value can't crash max-year). Comment only.
- qa_chat.py L406 _load_cfg -- corrupt *present* config now logs a warning while
  still falling back to {} (defaults); missing config stays silent.
- queue.py L87 delete_queued_file -- cleanup-guard (best-effort unlink, never
  raises). Comment only.
- queue.py L218 best-effort response-header set -- logged at debug (comment).
"""
import logging
import uuid

import server.routes.qa_chat as qa_chat
import server.routes.queue as queue

from server.routes.qa_chat import _parse_evidence_year_value, _load_cfg
from server.routes.queue import delete_queued_file


def test_parse_evidence_year_value_malformed_returns_none():
    """Malformed year strings must yield None (safe parse guard), never raise,
    and valid in-range years parse correctly."""
    for bad in ("garbage", "not-a-year", "1e99999", "12,34,56", ""):
        assert _parse_evidence_year_value(bad) is None
    assert _parse_evidence_year_value("2021") == 2021
    assert _parse_evidence_year_value("2019,") == 2019


def test_load_cfg_returns_defaults_and_logs_on_corrupt(monkeypatch, caplog):
    """A corrupt-but-present config must fall back to {} for the QA flow, AND
    the corruption is surfaced with a warning (no longer a silent swallow)."""
    class _FakePath:
        def __init__(self, *_a, **_k):
            self.parents = [self, self, self]

        def resolve(self):
            return self

        def __truediv__(self, _other):
            return self

        def exists(self):
            return True

        def read_text(self, **_k):
            return "this is {{{ not valid json"

    monkeypatch.setattr(qa_chat, "Path", _FakePath)
    caplog.set_level(logging.WARNING, logger=qa_chat.logger.name)

    assert _load_cfg() == {}
    assert any("Failed to load QA config/config.json" in r.getMessage()
               for r in caplog.records)


def test_load_cfg_missing_config_stays_silent(monkeypatch, caplog):
    """A missing config file (normal case) still returns defaults with NO
    warning -- only a corrupt PRESENT file is logged."""
    class _MissingPath:
        def __init__(self, *_a, **_k):
            self.parents = [self, self, self]

        def resolve(self):
            return self

        def __truediv__(self, _other):
            return self

        def exists(self):
            return False

    monkeypatch.setattr(qa_chat, "Path", _MissingPath)
    caplog.set_level(logging.WARNING, logger=qa_chat.logger.name)

    assert _load_cfg() == {}
    assert not any("Failed to load QA config" in r.getMessage()
                   for r in caplog.records)


def test_delete_queued_file_never_raises_on_unlink_failure(monkeypatch):
    """Cleanup-guard class (queue): a failed unlink must not propagate."""
    class _NoUnlink:
        def __truediv__(self, _key):
            return self

        def exists(self):
            return True

        def unlink(self, *_a, **_k):
            raise OSError("file in use")

    monkeypatch.setattr(queue, "get_queue_storage_root", lambda: _NoUnlink())
    delete_queued_file(f"key-{uuid.uuid4().hex}")  # must not raise
    delete_queued_file("")  # empty key short-circuits
