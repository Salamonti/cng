"""url_safety.py - SSRF guard shared by web_search.py and crawl_extractor.py.

Both modules fetch URLs that ultimately come from search-engine results
(SearXNG, PubMed) -- content this process doesn't control. A malicious or
compromised result could point at an internal-only address (the FastAPI
admin API, the ASR/LLM backends, cloud metadata endpoints) rather than a
real public document. requests' default allow_redirects=True also means a
URL that passes an initial check can still redirect to an internal target
after the fact, so redirects must be re-validated hop by hop, not just the
original URL.
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urljoin, urlparse

import requests

ALLOWED_SCHEMES = {"http", "https"}
MAX_REDIRECTS = 5


def _ip_is_blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def is_safe_public_url(url: str) -> bool:
    """True if url is http(s) and every address its host resolves to is a
    public, routable address (not private/loopback/link-local/reserved)."""
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    if parsed.scheme not in ALLOWED_SCHEMES:
        return False
    hostname = parsed.hostname
    if not hostname:
        return False
    try:
        infos = socket.getaddrinfo(hostname, None)
    except Exception:
        return False
    if not infos:
        return False
    for info in infos:
        ip_str = info[4][0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            return False
        if _ip_is_blocked(ip):
            return False
    return True


def safe_get(url: str, *, headers=None, timeout=30, max_redirects: int = MAX_REDIRECTS):
    """requests.get() that re-validates every redirect hop against
    is_safe_public_url() before following it, instead of trusting
    requests' default allow_redirects=True to follow blindly."""
    if not is_safe_public_url(url):
        raise ValueError(f"Blocked unsafe URL: {url}")
    current = url
    for _ in range(max_redirects + 1):
        resp = requests.get(current, headers=headers, timeout=timeout, allow_redirects=False)
        if resp.is_redirect or resp.is_permanent_redirect:
            location = resp.headers.get("Location")
            if not location:
                return resp
            next_url = urljoin(current, location)
            if not is_safe_public_url(next_url):
                raise ValueError(f"Blocked unsafe redirect target: {next_url}")
            current = next_url
            continue
        return resp
    raise ValueError(f"Too many redirects fetching {url}")
