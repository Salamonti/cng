"""Regression tests (P1-5): url_safety.py must block SSRF targets --
internal/private addresses reached via a URL that ultimately comes from
search-engine results, not a curated source list -- both on the initial
URL and on every redirect hop (requests' default allow_redirects=True
would otherwise follow a redirect to an internal target without any
re-validation).
"""
import ipaddress

import pytest

import url_safety


def test_blocks_loopback():
    assert url_safety.is_safe_public_url("http://127.0.0.1/") is False
    assert url_safety.is_safe_public_url("http://localhost/") is False


def test_blocks_private_ranges():
    assert url_safety.is_safe_public_url("http://192.168.0.9:8095/") is False
    assert url_safety.is_safe_public_url("http://10.0.0.5/") is False
    assert url_safety.is_safe_public_url("http://172.16.0.5/") is False


def test_blocks_link_local():
    # e.g. cloud metadata endpoints live in this range on some providers
    assert url_safety.is_safe_public_url("http://169.254.169.254/") is False


def test_blocks_bad_scheme():
    assert url_safety.is_safe_public_url("file:///etc/passwd") is False
    assert url_safety.is_safe_public_url("ftp://example.com/") is False


def test_blocks_url_with_no_hostname():
    assert url_safety.is_safe_public_url("http://") is False
    assert url_safety.is_safe_public_url("not a url") is False


def test_allows_public_ip_literal():
    # No DNS involved -- deterministic regardless of network state.
    assert url_safety.is_safe_public_url("https://8.8.8.8/") is True


def test_allows_hostname_resolving_to_public_ip(monkeypatch):
    def fake_getaddrinfo(host, port):
        assert host == "guidelines.example"
        return [(None, None, None, None, ("93.184.216.34", 0))]

    monkeypatch.setattr(url_safety.socket, "getaddrinfo", fake_getaddrinfo)
    assert url_safety.is_safe_public_url("https://guidelines.example/doc") is True


def test_blocks_hostname_resolving_to_private_ip_dns_rebinding(monkeypatch):
    """A hostname can look public but resolve to an internal address --
    is_safe_public_url must check the resolved IP, not just the string."""

    def fake_getaddrinfo(host, port):
        return [(None, None, None, None, ("127.0.0.1", 0))]

    monkeypatch.setattr(url_safety.socket, "getaddrinfo", fake_getaddrinfo)
    assert url_safety.is_safe_public_url("https://looks-public.example/") is False


def test_safe_get_rejects_unsafe_initial_url():
    with pytest.raises(ValueError):
        url_safety.safe_get("http://127.0.0.1:7860/api/admin/config/save")


def test_safe_get_follows_safe_redirect_and_blocks_unsafe_one(monkeypatch):
    calls = []

    class _Resp:
        def __init__(self, status, location=None):
            self.status_code = status
            self.headers = {"Location": location} if location else {}
            self.is_redirect = status in (301, 302, 303, 307, 308)
            self.is_permanent_redirect = status in (301, 308)

    # IP literals so is_safe_public_url doesn't need a real DNS lookup.
    start = "https://93.184.216.34/start"
    next_ = "https://93.184.216.34/next"

    def fake_get(url, headers=None, timeout=None, allow_redirects=None):
        calls.append(url)
        assert allow_redirects is False
        if url == start:
            return _Resp(302, next_)
        if url == next_:
            return _Resp(200)
        raise AssertionError(f"unexpected fetch: {url}")

    monkeypatch.setattr(url_safety.requests, "get", fake_get)
    resp = url_safety.safe_get(start)
    assert resp.status_code == 200
    assert calls == [start, next_]


def test_safe_get_blocks_redirect_to_internal_target(monkeypatch):
    class _Resp:
        def __init__(self, status, location=None):
            self.status_code = status
            self.headers = {"Location": location} if location else {}
            self.is_redirect = status in (301, 302, 303, 307, 308)
            self.is_permanent_redirect = status in (301, 308)

    start = "https://93.184.216.34/start"

    def fake_get(url, headers=None, timeout=None, allow_redirects=None):
        if url == start:
            return _Resp(302, "http://127.0.0.1:7860/api/admin/config/save")
        raise AssertionError(f"should never fetch the redirect target: {url}")

    monkeypatch.setattr(url_safety.requests, "get", fake_get)
    with pytest.raises(ValueError):
        url_safety.safe_get(start)
