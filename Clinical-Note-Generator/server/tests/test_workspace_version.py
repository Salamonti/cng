"""G5: lightweight workspace version endpoint + rename bumps workspace.version."""
import threading
import uuid

from auth_utils import register_approve_login


def test_workspace_version_endpoint(client):
    email = f"wsver-{uuid.uuid4().hex[:8]}@example.com"
    password = "Passw0rd!1234"
    token = register_approve_login(client, email, password)
    h = {"Authorization": f"Bearer {token}"}

    client.get("/api/workspace/", headers=h)

    r = client.get("/api/workspace/version", headers=h)
    assert r.status_code == 200
    data = r.json()
    assert "version" in data
    assert "updated_at" in data
    assert "has_encounter_recording" in data


def test_rename_encounter_bumps_workspace_version(client):
    email = f"wsren-{uuid.uuid4().hex[:8]}@example.com"
    password = "Passw0rd!1234"
    token = register_approve_login(client, email, password)
    h = {"Authorization": f"Bearer {token}"}

    client.get("/api/workspace/", headers=h)
    listed = client.get("/api/encounters/", headers=h).json()
    enc_id = listed["encounters"][0]["id"]
    before = client.get("/api/workspace/version", headers=h).json()["version"]
    r = client.patch(
        f"/api/encounters/{enc_id}",
        headers=h,
        json={"label": "Renamed encounter test"},
    )
    assert r.status_code == 200
    after = client.get("/api/workspace/version", headers=h).json()["version"]
    assert after > before


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


def test_concurrent_puts_at_same_version_one_wins_one_gets_409(client):
    """Regression test: two PUTs that both read the same starting version must
    not both succeed. Before the atomic-CAS fix, the version check happened in
    Python (read-then-compare) with a plain commit after -- two requests that
    both passed the check before either committed could both return 200,
    silently discarding one edit. The UPDATE ... WHERE version = :expected
    guard means the loser now gets rowcount=0 and a real 409."""
    email = f"wsrace-{uuid.uuid4().hex[:8]}@example.com"
    password = "Passw0rd!1234"
    token = register_approve_login(client, email, password)
    h = {"Authorization": f"Bearer {token}"}

    client.get("/api/workspace/", headers=h)
    start_version = client.get("/api/workspace/version", headers=h).json()["version"]

    barrier = threading.Barrier(2)
    results = {}

    def _put(name: str, draft: str):
        barrier.wait(timeout=5)
        resp = client.put(
            "/api/workspace/",
            headers=h,
            json=_workspace_payload(start_version, draft),
        )
        results[name] = resp.status_code

    t1 = threading.Thread(target=_put, args=("a", "edit from tab A"))
    t2 = threading.Thread(target=_put, args=("b", "edit from tab B"))
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    statuses = sorted(results.values())
    assert statuses == [200, 409], (
        f"expected exactly one 200 and one 409 for two racing PUTs at the same "
        f"starting version, got {results}"
    )
