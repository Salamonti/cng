"""STEP 3a -- silent-handler reclassification in core/asr_incident_store.py.

The incident store's WHOLE JOB is recording failures (ASR incidents,
client errors, workspace sync). If its own writes fail silently, then
"nothing records that nothing recorded". This module never raises (best-effort
by contract) but on any write failure it must:

  1. surface a structured error log (was: bare `pass` at lines 33/79/89/91),
  2. bump a thread-safe `degraded_count` counter,
  3. expose that counter on /health via `incident_store_degraded_count()`.

These tests pin: normal success unchanged, primary (JSONL) write failure
surfaces + increments + does NOT raise, /health mirrors the counter, and the
incident_store_degraded_count() read is thread-safe.

Failure injection is scoped to the store module (monkeypatching the module's
`open`) or done via real-filesystem shape (making last_incident.json a
directory) rather than monkeypatching the global builtin, which collides with
the app's logging/background I/O.
"""
import logging

import server.core.asr_incident_store as ais
from server.routes import perf  # _health endpoint uses incident_store_degraded_count


def _reset_degraded():
    ais._degraded_count = 0


def _jsonl_failing_open(real_open, *, filename="incidents.jsonl"):
    """Wrap open for the incident-store module so writes to a given file fail."""

    def _wrapped(filepath, *args, **kwargs):
        if filename in str(filepath):
            raise OSError("simulated incident store disk failure")
        return real_open(filepath, *args, **kwargs)

    return _wrapped


def test_normal_record_writes_and_no_degradation(tmp_path, monkeypatch):
    _reset_degraded()
    monkeypatch.setenv("ASR_INCIDENT_DIR", str(tmp_path))
    ais.record_asr_incident(trace_id="t1", stage="asr", outcome="fail", payload={"k": "v"})
    lines = (tmp_path / "incidents.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert ais.incident_store_degraded_count() == 0
    # last_incident.json is also written
    assert (tmp_path / "last_incident.json").exists()


def test_jsonl_write_failure_surfaces_increments_and_does_not_raise(tmp_path, monkeypatch, caplog):
    _reset_degraded()
    monkeypatch.setenv("ASR_INCIDENT_DIR", str(tmp_path))
    # Patch the module's `open` (not the global builtin) so only the store's
    # JSONL write fails; app logging/background I/O is untouched.
    monkeypatch.setattr(ais, "open", _jsonl_failing_open(__import__("builtins").open), raising=False)
    caplog.set_level(logging.ERROR, logger=ais._log.name)
    # Must NOT raise even though the primary write is failing.
    ais.record_asr_incident(trace_id="t2", stage="asr", outcome="fail", payload={})
    assert ais.incident_store_degraded_count() == 1
    assert "incident store DEGRADED" in caplog.text
    assert "incidents.jsonl append failed" in caplog.text


def test_last_incident_write_failure_surfaces(tmp_path, monkeypatch, caplog):
    """JSONL append succeeds; the secondary last_incident.json write fails
    because last_incident.json is a DIRECTORY (so both the atomic tmp+replace
    and the direct fallback raise). Must surface, not silently pass, and must
    not raise."""
    _reset_degraded()
    monkeypatch.setenv("ASR_INCIDENT_DIR", str(tmp_path))
    (tmp_path / "last_incident.json").mkdir()  # make the target a directory
    caplog.set_level(logging.ERROR, logger=ais._log.name)
    ais.record_asr_incident(trace_id="t3", stage="asr", outcome="fail", payload={})
    assert ais.incident_store_degraded_count() == 1
    assert "incident store DEGRADED" in caplog.text
    assert "last_incident.json write failed" in caplog.text


def test_health_exposes_degraded_counter(tmp_path, monkeypatch):
    _reset_degraded()
    monkeypatch.setenv("ASR_INCIDENT_DIR", str(tmp_path))
    healthy = perf.health()
    assert healthy["status"] == "ok"
    assert healthy["asr_incident_store_degraded"] == 0

    monkeypatch.setattr(ais, "open", _jsonl_failing_open(__import__("builtins").open), raising=False)
    ais.record_asr_incident(trace_id="t4", stage="asr", outcome="fail", payload={})
    degraded = perf.health()
    assert degraded["asr_incident_store_degraded"] == 1


def test_degraded_counter_is_thread_safe():
    _reset_degraded()
    import threading

    def writer(n):
        for _ in range(50):
            ais._note_store_degraded(f"boom-{n}")

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert ais.incident_store_degraded_count() == 400
