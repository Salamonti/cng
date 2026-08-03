"""P2-8: client-side error telemetry route.

Reuses the existing (tested) ASR incident store rather than a second
mechanism -- these tests check the route's own responsibilities: batching,
sanitizing/clipping untrusted browser-supplied strings, and the per-IP
rate limit that exists because this route is intentionally open (no
require_api_bearer -- an expired session is exactly the kind of thing
worth reporting).
"""
import json

from fastapi.testclient import TestClient

from server.app import app
from server.routes import client_errors as client_errors_module

client = TestClient(app)


def _reset_rate_limiter():
    client_errors_module._rate_limit_hits.clear()


def test_single_event_payload_is_recorded(tmp_path, monkeypatch):
    _reset_rate_limiter()
    monkeypatch.setenv("ASR_INCIDENT_DIR", str(tmp_path))
    r = client.post("/api/client_errors", json={"message": "boom", "stack": "at x()", "url": "/workspace"})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "recorded": 1}
    incidents = (tmp_path / "incidents.jsonl").read_text(encoding="utf-8").strip().splitlines()
    rec = json.loads(incidents[-1])
    assert rec["stage"] == "client_error"
    assert rec["payload"]["message"] == "boom"


def test_batched_events_payload_is_recorded(tmp_path, monkeypatch):
    _reset_rate_limiter()
    monkeypatch.setenv("ASR_INCIDENT_DIR", str(tmp_path))
    r = client.post(
        "/api/client_errors",
        json={"events": [{"message": "first"}, {"message": "second", "kind": "unhandledrejection"}]},
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True, "recorded": 2}


def test_oversized_fields_are_clipped_not_rejected(tmp_path, monkeypatch):
    _reset_rate_limiter()
    monkeypatch.setenv("ASR_INCIDENT_DIR", str(tmp_path))
    huge_message = "x" * 50_000
    r = client.post("/api/client_errors", json={"message": huge_message})
    assert r.status_code == 200
    incidents = (tmp_path / "incidents.jsonl").read_text(encoding="utf-8").strip().splitlines()
    rec = json.loads(incidents[-1])
    assert len(rec["payload"]["message"]) == client_errors_module.MAX_MESSAGE_CHARS


def test_more_than_max_events_in_one_request_is_capped(tmp_path, monkeypatch):
    _reset_rate_limiter()
    monkeypatch.setenv("ASR_INCIDENT_DIR", str(tmp_path))
    events = [{"message": f"err {i}"} for i in range(50)]
    r = client.post("/api/client_errors", json={"events": events})
    assert r.status_code == 200
    assert r.json()["recorded"] == client_errors_module.MAX_EVENTS_PER_REQUEST


def test_empty_or_invalid_payload_is_rejected(tmp_path, monkeypatch):
    _reset_rate_limiter()
    monkeypatch.setenv("ASR_INCIDENT_DIR", str(tmp_path))
    r = client.post("/api/client_errors", json={"events": [{"message": ""}, {"no_message": "x"}]})
    assert r.status_code == 400


def test_no_auth_header_is_accepted_not_rejected(tmp_path, monkeypatch):
    # The whole point: an expired/missing token must not stop the error
    # report itself from getting through.
    _reset_rate_limiter()
    monkeypatch.setenv("ASR_INCIDENT_DIR", str(tmp_path))
    r = client.post("/api/client_errors", json={"message": "session already expired"})
    assert r.status_code == 200


def test_per_ip_rate_limit_stops_writing_past_the_cap(tmp_path, monkeypatch):
    _reset_rate_limiter()
    monkeypatch.setenv("ASR_INCIDENT_DIR", str(tmp_path))
    monkeypatch.setattr(client_errors_module, "_RATE_LIMIT_MAX_EVENTS", 3)
    for _ in range(3):
        r = client.post("/api/client_errors", json={"message": "spam"})
        assert r.json()["recorded"] == 1
    # 4th call from the same (test client) IP within the window is dropped.
    r = client.post("/api/client_errors", json={"message": "spam"})
    assert r.status_code == 200
    assert r.json()["recorded"] == 0
