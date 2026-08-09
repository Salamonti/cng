"""STEP 8a -- incident store signal-to-noise for workspace_sync.

Root cause: record_sync_incident() only ever recorded `put_ok` SUCCESSES
(a routine save = happy path, not an incident), which at steady state were
~91% of all incident-store records (9,671/10,579) and drowned out the genuine
incidents. Meanwhile the whole point of the workspace_sync telemetry (per the
ops contract in core/db.py) is to watch for PUT 409 WRITE-CONTENTION incidents
-- and those were exactly the records that were NEVER written.

Fix: stop recording put_ok successes; record `put_conflict` on the 409 paths
(version_mismatch pre-check and CAS rowcount-zero race).

These tests pin, through the real route (TestClient with a fresh user), that:
  1. a successful workspace save writes NO incident record, and
  2. a version-mismatch PUT that returns 409 writes a put_conflict record.
"""
import json
import uuid

from auth_utils import register_approve_login


def _workspace_payload(version: int, draft: str) -> dict:
    return {
        "state": {
            "settings": {"theme": "light", "language": "en"},
            "documents": [],
            "draft": draft,
            "extras": {},
        },
        "version": version,
    }


def _read_incidents(tmp_path):
    p = tmp_path / "incidents.jsonl"
    if not p.exists():
        return []
    out = []
    for ln in p.read_text(encoding="utf-8").strip().splitlines():
        if ln:
            out.append(json.loads(ln))
    return out


def _ws_sync_records(tmp_path):
    return [r for r in _read_incidents(tmp_path) if r.get("stage") == "workspace_sync"]


def test_successful_save_writes_no_incident(client, tmp_path, monkeypatch):
    monkeypatch.setenv("ASR_INCIDENT_DIR", str(tmp_path))
    email = f"ws-snr-{uuid.uuid4().hex[:8]}@example.com"
    password = "Passw0rd!1234"
    token = register_approve_login(client, email, password)
    h = {"Authorization": f"Bearer {token}"}

    client.get("/api/workspace/", headers=h)
    version = client.get("/api/workspace/version", headers=h).json()["version"]

    r = client.put("/api/workspace/", headers=h, json=_workspace_payload(version, "draft one"))
    assert r.status_code == 200

    # The happy-path save must NOT appear in the incident store.
    assert _ws_sync_records(tmp_path) == [], (
        "a successful workspace save must not write an incident record"
    )


def test_version_mismatch_409_records_put_conflict(client, tmp_path, monkeypatch):
    monkeypatch.setenv("ASR_INCIDENT_DIR", str(tmp_path))
    email = f"ws-cf-{uuid.uuid4().hex[:8]}@example.com"
    password = "Passw0rd!1234"
    token = register_approve_login(client, email, password)
    h = {"Authorization": f"Bearer {token}"}

    client.get("/api/workspace/", headers=h)
    version = client.get("/api/workspace/version", headers=h).json()["version"]

    # Send a PUT with a STALE version (client believes version-1) -> 409.
    r = client.put("/api/workspace/", headers=h, json=_workspace_payload(version - 1, "stale"))
    assert r.status_code == 409

    recs = _ws_sync_records(tmp_path)
    assert len(recs) == 1, f"expected exactly one workspace_sync record, got {recs}"
    assert recs[0]["outcome"] == "put_conflict"
    assert recs[0]["payload"]["reason"] == "version_mismatch"
    assert recs[0]["payload"]["client_version"] == version - 1
    assert recs[0]["payload"]["server_version"] == version
