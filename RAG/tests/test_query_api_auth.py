"""Step 4 regression tests (H-5/H-6): RAG shared-key auth, PHI egress gate,
off-by-default web search, open /health."""

import os

import pytest
from fastapi.testclient import TestClient

# Configure the shared key BEFORE importing the app so require_api_key sees it.
os.environ.setdefault("RAG_API_KEY", "test-rag-api-key-0123456789abcdef")

import query_api  # noqa: E402
from query_api import _phi_in_query, app  # noqa: E402


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


def test_query_requires_key(client):
    r = client.post("/query", json={"query": "COPD management", "top_k": 3})
    assert r.status_code == 401


def test_query_wrong_key_rejected(client):
    r = client.post("/query", json={"query": "COPD", "top_k": 3}, headers={"X-API-Key": "wrong"})
    assert r.status_code == 401


def test_web_query_requires_key(client):
    r = client.post("/web_query", json={"query": "asthma guideline"})
    assert r.status_code == 401


def test_health_stays_open(client):
    assert client.get("/health").status_code == 200


def test_web_search_off_by_default():
    req = query_api.WebQueryRequest(query="sepsis management")
    assert req.web_search is False
    assert req.include_local is True


def test_phi_detector_detects():
    assert _phi_in_query("patient jdoe@example.com") == "email"
    assert _phi_in_query("DOB 1985-04-12 management") == "date-of-birth"
    assert _phi_in_query("SIN 123-456-789 in chart") == "SIN/SSN"
    assert _phi_in_query("postal B3H 2T1 asthma") == "postal-code"
    assert _phi_in_query("their PHN needs checking") == "patient-identifier-keyword"


def test_phi_detector_benign_queries():
    assert _phi_in_query("COPD exacerbation steroid dosing") is None
    assert _phi_in_query("warfarin INR target pregnancy") is None
    assert _phi_in_query("monitoring labs") is None


def test_web_query_blocks_phi(client):
    r = client.post(
        "/web_query",
        json={"query": "treatment for jdoe@example.com", "web_search": True},
        headers={"X-API-Key": os.environ["RAG_API_KEY"]},
    )
    assert r.status_code == 400
    assert "blocked" in r.json()["detail"].lower()
