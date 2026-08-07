"""STEP 2f -- reclassified silent handlers across routes/{rag_updates,perf,client_usage}.py.

- rag_updates.py _load_config -- corrupt *present* config now logs a warning
  (was bare swallow) while still falling back to {}; missing config stays silent.
  (Also documented L353 stat + recent-cache + timestamp parse guards.)
- perf.py _load_cfg -- same corrupt-config warning policy as above.
- client_usage.py -- L101 value clamp + L125 actor extraction are both graceful
  recovery (documented); pin that malformed values and actor failures degrade
  safely rather than crashing the usage report.

Notes on rag_updates._load_config: cached in a module global; tests force a
re-read by clearing _CONFIG_CACHE.
"""
import logging

import server.routes.perf as perf
import server.routes.rag_updates as rag_updates
import server.routes.client_usage as client_usage


def _corrupt_config_path():
    class _FakePath:
        def __init__(self, *_a, **_k):
            self.parents = [self, self, self]

        def resolve(self):
            return self

        def __truediv__(self, _other):
            return self

        def open(self, *_a, **_k):
            return _CorruptReader()

        def exists(self):
            return True

        def read_text(self, **_k):
            return "corrupt {{{ json"

    class _CorruptReader:
        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        def read(self, *_a, **_k):
            return "corrupt {{{ json"

    return _FakePath


def test_rag_updates_load_config_corrupt_logs_warning(monkeypatch, caplog):
    rag_updates._CONFIG_CACHE = None  # force re-read
    monkeypatch.setattr(rag_updates, "Path", _corrupt_config_path())
    caplog.set_level(logging.WARNING, logger=rag_updates.logger.name)
    assert rag_updates._load_config() == {}
    assert any("Failed to load RAG config/config.json" in r.getMessage()
               for r in caplog.records)


def test_perf_load_cfg_corrupt_logs_warning(monkeypatch, caplog):
    monkeypatch.setattr(perf, "Path", _corrupt_config_path())
    caplog.set_level(logging.WARNING, logger=perf.logger.name)
    assert perf._load_cfg() == {}
    assert any("Failed to load perf config/config.json" in r.getMessage()
               for r in caplog.records)


def test_client_usage_sanitize_event_malformed_value():
    """Malformed client 'value' -> event without a value, not a crash."""
    ev = client_usage._sanitize_event({"kind": "nav_chart", "value": "not-a-number"})
    assert isinstance(ev, dict)
    assert ev["kind"] == "nav_chart"
    assert "value" not in ev
    # Valid value still clamps into [0,1].
    ev2 = client_usage._sanitize_event({"kind": "nav_chart", "value": "1.7"})
    assert ev2["value"] == 1.0


def test_client_usage_actor_failure_falls_back_to_anonymous(client, monkeypatch):
    """If extract_request_actor raises, the report still records under
    'anonymous' instead of crashing (graceful recovery)."""
    captured = []

    def _spy_log(payload):
        captured.append(payload)

    def _boom(*_a, **_k):
        raise RuntimeError("actor extraction failed")

    monkeypatch.setattr(client_usage, "extract_request_actor", _boom)
    monkeypatch.setattr(client_usage, "log_case_event", _spy_log)

    resp = client.post("/api/client_usage", json={
        "events": [{"kind": "nav_chart", "value": "0.5"}],
    })
    assert resp.status_code == 200, resp.text
    assert resp.json()["ok"] is True
    assert captured and captured[0]["user_id"] == "anonymous"
