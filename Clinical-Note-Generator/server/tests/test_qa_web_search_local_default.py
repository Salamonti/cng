"""P3-2 regression: searx_search()'s default candidate list must never
include an external domain. Before this fix, SEARXNG_URL defaulted to
https://ieissa.com:3443/searxng/search -- and nothing in production
actually set SEARXNG_URL, so that external default was live: if the local
SearXNG instance was ever unreachable, the consult clinical focus text
(the search query) got sent to a server outside the local network as a GET
query string, directly undermining the "PHI never leaves your building"
positioning this app is built on.
"""
from __future__ import annotations

from urllib.parse import urlparse

from server.services.qa_web_search import _candidate_search_bases


def test_default_candidates_are_all_local_when_searxng_url_unset(monkeypatch):
    monkeypatch.delenv("SEARXNG_URL", raising=False)
    bases = _candidate_search_bases()
    assert bases  # sanity: still produces candidates
    for base in bases:
        host = urlparse(base).hostname
        assert host in ("127.0.0.1", "localhost"), f"non-local candidate: {base}"


def test_operator_can_still_opt_into_a_custom_endpoint(monkeypatch):
    # SEARXNG_URL remains a deliberate, explicit operator choice -- this
    # fix only changed the silent DEFAULT, not the ability to configure one.
    monkeypatch.setenv("SEARXNG_URL", "https://searx.example.internal/search")
    bases = _candidate_search_bases()
    assert "https://searx.example.internal/search" in bases
