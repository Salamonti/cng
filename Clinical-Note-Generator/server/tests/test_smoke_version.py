def test_version_endpoint_returns_stamp_and_no_store(client):
    response = client.get("/api/version")
    assert response.status_code == 200
    assert "no-store" in response.headers.get("cache-control", "")

    data = response.json()
    assert "commit_hash" in data
    assert "build_timestamp_utc" in data
    assert "versions" in data
    assert "python" in data["versions"]
    assert "fastapi" in data["versions"]
