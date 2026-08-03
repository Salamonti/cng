"""P4-4: pilot instrumentation telemetry route.

Mirrors test_client_errors.py's shape (batching, rate limiting, open
registration) but adds the piece that's different about this route: unlike
client_errors' free-text message field, "kind" and "meta" here are closed
vocabularies specifically because this route has no auth requirement --
free text would make it a way to smuggle arbitrary strings through an
unauthenticated endpoint. These tests check that closed-vocabulary
enforcement as much as the batching/rate-limit mechanics themselves.
"""
import glob
import json
import os

from fastapi.testclient import TestClient

from server.app import app
from server.routes import client_usage as client_usage_module

client = TestClient(app)


def _reset_rate_limiter():
    client_usage_module._rate_limit_hits.clear()


def _read_events(tmp_path):
    files = glob.glob(os.path.join(str(tmp_path), "case_events_*.jsonl"))
    assert files, "expected a case_events_*.jsonl file to have been written"
    with open(files[0], "r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def test_known_kind_is_recorded(tmp_path, monkeypatch):
    _reset_rate_limiter()
    monkeypatch.setenv("CNG_DATASET_DIR", str(tmp_path))
    r = client.post("/api/client_usage", json={"kind": "nav_tools"})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "recorded": 1}
    events = _read_events(tmp_path)
    assert events[-1]["event_type"] == "usage.nav_tools"


def test_unknown_kind_is_rejected_not_recorded(tmp_path, monkeypatch):
    _reset_rate_limiter()
    monkeypatch.setenv("CNG_DATASET_DIR", str(tmp_path))
    r = client.post("/api/client_usage", json={"kind": "arbitrary_free_text_event"})
    assert r.status_code == 400


def test_meta_outside_allowed_set_is_dropped_but_event_still_recorded(tmp_path, monkeypatch):
    _reset_rate_limiter()
    monkeypatch.setenv("CNG_DATASET_DIR", str(tmp_path))
    r = client.post(
        "/api/client_usage",
        json={"kind": "patient_materials_generate", "meta": "<script>not a real category</script>"},
    )
    assert r.status_code == 200
    events = _read_events(tmp_path)
    assert events[-1]["meta"] is None


def test_allowed_meta_is_recorded(tmp_path, monkeypatch):
    _reset_rate_limiter()
    monkeypatch.setenv("CNG_DATASET_DIR", str(tmp_path))
    r = client.post(
        "/api/client_usage",
        json={"kind": "patient_materials_generate", "meta": "medications"},
    )
    assert r.status_code == 200
    events = _read_events(tmp_path)
    assert events[-1]["meta"] == "medications"


def test_edit_distance_value_is_clamped_to_zero_one(tmp_path, monkeypatch):
    _reset_rate_limiter()
    monkeypatch.setenv("CNG_DATASET_DIR", str(tmp_path))
    r = client.post(
        "/api/client_usage",
        json={"kind": "note_edit_distance", "value": 7.5, "case_id": "gen-123"},
    )
    assert r.status_code == 200
    events = _read_events(tmp_path)
    assert events[-1]["value"] == 1.0
    assert events[-1]["case_id"] == "gen-123"


def test_batched_events_payload_is_recorded(tmp_path, monkeypatch):
    _reset_rate_limiter()
    monkeypatch.setenv("CNG_DATASET_DIR", str(tmp_path))
    r = client.post(
        "/api/client_usage",
        json={"events": [{"kind": "nav_qa"}, {"kind": "tools_camera"}]},
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True, "recorded": 2}


def test_more_than_max_events_in_one_request_is_capped(tmp_path, monkeypatch):
    _reset_rate_limiter()
    monkeypatch.setenv("CNG_DATASET_DIR", str(tmp_path))
    events = [{"kind": "nav_chart"} for _ in range(50)]
    r = client.post("/api/client_usage", json={"events": events})
    assert r.status_code == 200
    assert r.json()["recorded"] == client_usage_module.MAX_EVENTS_PER_REQUEST


def test_empty_or_all_invalid_payload_is_rejected(tmp_path, monkeypatch):
    _reset_rate_limiter()
    monkeypatch.setenv("CNG_DATASET_DIR", str(tmp_path))
    r = client.post("/api/client_usage", json={"events": [{"kind": "not_a_real_kind"}, {"no_kind": "x"}]})
    assert r.status_code == 400


def test_no_auth_header_is_accepted_not_rejected(tmp_path, monkeypatch):
    _reset_rate_limiter()
    monkeypatch.setenv("CNG_DATASET_DIR", str(tmp_path))
    r = client.post("/api/client_usage", json={"kind": "nav_note"})
    assert r.status_code == 200


def test_per_ip_rate_limit_stops_writing_past_the_cap(tmp_path, monkeypatch):
    _reset_rate_limiter()
    monkeypatch.setenv("CNG_DATASET_DIR", str(tmp_path))
    monkeypatch.setattr(client_usage_module, "_RATE_LIMIT_MAX_EVENTS", 3)
    for _ in range(3):
        r = client.post("/api/client_usage", json={"kind": "nav_chart"})
        assert r.json()["recorded"] == 1
    r = client.post("/api/client_usage", json={"kind": "nav_chart"})
    assert r.status_code == 200
    assert r.json()["recorded"] == 0
