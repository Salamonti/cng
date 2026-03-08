import uuid


def test_queue_create_list_delete(client):
    from server.app import app
    from server.core.dependencies import get_current_user
    from server.models.user import User

    fake_user = User(
        id=uuid.uuid4(),
        email="queue-smoke@example.com",
        hashed_password="x",
        is_active=True,
        is_approved=True,
    )
    app.dependency_overrides[get_current_user] = lambda: fake_user

    create_resp = client.post(
        "/api/queue",
        data={"type": "ocr"},
        files={"file": ("smoke.txt", b"queued content", "text/plain")},
    )
    assert create_resp.status_code == 200
    job_id = create_resp.json()["id"]

    list_resp = client.get("/api/queue")
    assert list_resp.status_code == 200
    ids = [item["id"] for item in list_resp.json()]
    assert job_id in ids

    delete_resp = client.delete(f"/api/queue/{job_id}")
    assert delete_resp.status_code == 204

    list_after = client.get("/api/queue")
    assert list_after.status_code == 200
    ids_after = [item["id"] for item in list_after.json()]
    assert job_id not in ids_after
