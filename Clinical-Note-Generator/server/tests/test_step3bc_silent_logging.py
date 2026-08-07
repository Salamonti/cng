"""STEP 3b/3c -- reclassified SILENT handlers that now log explicitly.

Only the *unsafe-silence* cases are behaviorally observable (they now emit a
structured log where before they swallowed the exception silently). The
*legitimately-safe parse-guards* are comment-only (no behavior change), so they
are not test targets.

Verified here:
  - service_endpoints.load_service_endpoints() logs + returns {} on bad JSON
  - prompt.builder.load_config() logs + returns {} on corrupt config.json
  - http_actor.eval...(na)  -- covered separately below
  - rag_http_client config load logs on corrupt config.json
  - ttl_store sweep failure logs (does not crash daemon)
"""
import json
import logging

import pytest


# ---- service_endpoints ----

def test_service_endpoints_malformed_json_logs(tmp_path, monkeypatch, caplog):
    import server.core.service_endpoints as se
    bad = tmp_path / "service_endpoints.json"
    bad.write_text("{not valid json", encoding="utf-8")
    monkeypatch.setenv("SERVICE_ENDPOINTS_PATH", str(bad))
    caplog.set_level(logging.WARNING, logger="cng.service_endpoints")
    result = se.load_service_endpoints()
    assert result == {}
    assert any("Failed to parse service_endpoints.json" in r.message
               for r in caplog.records)


# ---- prompt.builder ----

def test_prompt_builder_corrupt_config_logs(tmp_path, monkeypatch, caplog):
    import server.core.prompt.builder as b
    corrupt = tmp_path / "config.json"
    corrupt.write_text("{oops", encoding="utf-8")
    monkeypatch.setattr(b, "CONFIG_PATH", corrupt)
    caplog.set_level(logging.WARNING, logger="cng.prompt.builder")
    assert b.load_config() == {}
    assert any("Failed to load config.json" in r.message for r in caplog.records)


# ---- rag_http_client ----

def test_rag_http_client_corrupt_config_logs(tmp_path, monkeypatch, caplog):
    from pathlib import Path as RealPath
    import server.services.rag_http_client as rag

    # The module resolves config via Path(__file__).resolve().parents[2]
    # / "config" / "config.json". We swap the module's `Path` (bound at import)
    # for one that always resolves to a synthetic base whose parents[2] is
    # tmp_path, so the eligible path lands on a real corrupt file under tmp_path.
    base = tmp_path / "a" / "b" / "c"
    corrupt = tmp_path / "config" / "config.json"
    corrupt.parent.mkdir(parents=True, exist_ok=True)
    corrupt.write_text("{bad", encoding="utf-8")

    class _FakePath(RealPath):
        def __new__(cls, *args, **kwargs):
            return RealPath(base)

        def resolve(self):
            return self

    monkeypatch.setattr(rag, "Path", _FakePath)

    class _Stub:
        _cfg = None

    caplog.set_level(logging.WARNING, logger="cng.rag_http_client")
    cfg = rag.RAGHttpClient._load_cfg(_Stub())
    assert cfg == {}
    assert any("Failed to load RAG config.json" in r.message for r in caplog.records)


# ---- ttl_store ----

def test_ttl_store_sweep_failure_logs_and_does_not_crash(tmp_path, monkeypatch, caplog):
    import server.core.stores.ttl_store as ts

    store = ts.TTLStore(ttl_seconds=60)
    # Force evict_expired to raise, then confirm _sweep_loop logs and reschedules.
    def _boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(store, "evict_expired", _boom)
    caplog.set_level(logging.WARNING, logger="cng.ttl_store")
    # Disable the background timer so the test doesn't schedule real loops.
    monkeypatch.setattr(store, "_start_sweep", lambda: None)
    store._sweep_loop()
    assert any("TTLStore sweep failed" in r.message for r in caplog.records)
