# server/tests/test_encounters_p5.py — P5 encounters + workspace shell + queue encounter_id
import io
import uuid
from datetime import datetime, timedelta, UTC

from auth_utils import register_approve_login


def test_workspace_get_creates_default_encounter(client):
    email = f"p5ws-{uuid.uuid4().hex[:8]}@example.com"
    password = "Passw0rd!1234"
    token = register_approve_login(client, email, password)
    h = {"Authorization": f"Bearer {token}"}

    r = client.get("/api/workspace/", headers=h)
    assert r.status_code == 200
    data = r.json()
    extras = data["state"]["extras"]
    assert "activeEncounterId" in extras
    uuid.UUID(str(extras["activeEncounterId"]))

    lst = client.get("/api/encounters/", headers=h)
    assert lst.status_code == 200
    encs = lst.json()["encounters"]
    assert len(encs) == 1
    assert encs[0]["is_active"] is True
    assert encs[0]["label"] == "Default"


def test_encounter_create_activate_delete(client):
    email = f"p5cr-{uuid.uuid4().hex[:8]}@example.com"
    password = "Passw0rd!1234"
    token = register_approve_login(client, email, password)
    h = {"Authorization": f"Bearer {token}"}

    client.get("/api/workspace/", headers=h)

    cr = client.post("/api/encounters/", headers=h, json={"label": "Clinic B"})
    assert cr.status_code == 201
    eid_b = cr.json()["id"]

    act = client.post(f"/api/encounters/{eid_b}/activate", headers=h)
    assert act.status_code == 200
    assert act.json()["is_active"] is True

    ws = client.get("/api/workspace/", headers=h)
    assert ws.json()["state"]["extras"]["activeEncounterId"] == eid_b

    bad_del = client.request(
        "DELETE",
        f"/api/encounters/{eid_b}",
        headers=h,
        json={"confirm": False},
    )
    assert bad_del.status_code == 400

    ok_del = client.request(
        "DELETE",
        f"/api/encounters/{eid_b}",
        headers=h,
        json={"confirm": True},
    )
    assert ok_del.status_code == 200
    remaining = ok_del.json()["encounters"]
    assert len(remaining) >= 1
    assert all(x["id"] != eid_b for x in remaining)


def test_workspace_put_409_stale_version(client):
    email = f"p5v-{uuid.uuid4().hex[:8]}@example.com"
    password = "Passw0rd!1234"
    token = register_approve_login(client, email, password)
    h = {"Authorization": f"Bearer {token}"}

    g = client.get("/api/workspace/", headers=h)
    assert g.status_code == 200
    v = g.json()["version"]
    st = g.json()["state"]

    st["extras"]["currentEncounter"] = "hello"
    bad = client.put(
        "/api/workspace/",
        headers=h,
        json={"version": v - 1, "state": st},
    )
    assert bad.status_code == 409


def test_queue_job_has_encounter_id(client):
    email = f"p5q-{uuid.uuid4().hex[:8]}@example.com"
    password = "Passw0rd!1234"
    token = register_approve_login(client, email, password)
    h = {"Authorization": f"Bearer {token}"}

    client.get("/api/workspace/", headers=h)
    ws = client.get("/api/workspace/", headers=h)
    eid = ws.json()["state"]["extras"]["activeEncounterId"]

    files = {"file": ("t.txt", io.BytesIO(b"x"), "text/plain")}
    data = {"type": "ocr"}
    r = client.post("/api/queue", headers=h, files=files, data=data)
    assert r.status_code == 200
    body = r.json()
    assert body.get("encounter_id") == eid


def test_rename_encounter(client):
    email = f"p5rn-{uuid.uuid4().hex[:8]}@example.com"
    password = "Passw0rd!1234"
    token = register_approve_login(client, email, password)
    h = {"Authorization": f"Bearer {token}"}

    lst = client.get("/api/encounters/", headers=h)
    eid = lst.json()["encounters"][0]["id"]
    p = client.patch(f"/api/encounters/{eid}", headers=h, json={"label": "Renamed"})
    assert p.status_code == 200
    assert p.json()["label"] == "Renamed"


def test_create_encounter_seeds_user_speciality_from_profile(client):
    email = f"p5sp-{uuid.uuid4().hex[:8]}@example.com"
    password = "Passw0rd!1234"
    token = register_approve_login(client, email, password)
    h = {"Authorization": f"Bearer {token}"}

    client.put(
        "/api/profile",
        headers=h,
        json={"default_specialty": "Internal Medicine"},
    )
    client.get("/api/workspace/", headers=h)

    cr = client.post("/api/encounters/", headers=h, json={"label": "Clinic B"})
    assert cr.status_code == 201
    extras = cr.json()["state"].get("extras") or {}
    assert extras.get("userSpeciality") == "Internal Medicine"


def test_encounter_limit_max_24(client):
    email = f"p5lim-{uuid.uuid4().hex[:8]}@example.com"
    password = "Passw0rd!1234"
    token = register_approve_login(client, email, password)
    h = {"Authorization": f"Bearer {token}"}

    # Ensure default encounter exists (count=1)
    client.get("/api/workspace/", headers=h)

    # Create up to total=24 encounters (1 default + 23 new)
    for i in range(23):
        r = client.post("/api/encounters/", headers=h, json={"label": f"Enc {i+1}"})
        assert r.status_code == 201

    lst = client.get("/api/encounters/", headers=h)
    assert lst.status_code == 200
    assert len(lst.json()["encounters"]) == 24

    # Next create should fail with 409
    r2 = client.post("/api/encounters/", headers=h, json={"label": "Overflow"})
    assert r2.status_code == 409


def test_auto_delete_encounters_after_9_days(client):
    email = f"p5ttl-{uuid.uuid4().hex[:8]}@example.com"
    password = "Passw0rd!1234"
    token = register_approve_login(client, email, password)
    h = {"Authorization": f"Bearer {token}"}

    # Ensure baseline/default exists.
    client.get("/api/workspace/", headers=h)

    # Create a second encounter so TTL deletion doesn't have to delete the only one.
    cr = client.post("/api/encounters/", headers=h, json={"label": "Old One"})
    assert cr.status_code == 201
    old_id = cr.json()["id"]

    # Force it to be older than TTL by manipulating DB timestamps directly.
    import server.core.db as db
    from sqlmodel import Session, select
    from server.models.user_encounter import UserEncounter

    cutoff_old = datetime.utcnow() - timedelta(days=10)
    with Session(db.engine) as s:
        enc = s.exec(select(UserEncounter).where(UserEncounter.id == uuid.UUID(old_id))).one()
        enc.updated_at = cutoff_old
        s.add(enc)
        s.commit()

    # Any encounter/workspace access triggers TTL pruning.
    lst = client.get("/api/encounters/", headers=h)
    assert lst.status_code == 200
    ids = {e["id"] for e in lst.json()["encounters"]}
    assert old_id not in ids
