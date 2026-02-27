import os
from typing import Any, Dict, List

import aiohttp

_ALLOWED_DOMAINS = [
    "pubmed.ncbi.nlm.nih.gov",
    "ncbi.nlm.nih.gov",
    "nejm.org",
    "jamanetwork.com",
    "thelancet.com",
    "bmj.com",
    "acpjournals.org",
    "thoracic.org",
    "ersnet.org",
    "chestnet.org",
    "who.int",
    "cdc.gov",
    "canada.ca",
    "nice.org.uk",
    "fda.gov",
    "ema.europa.eu",
    "diabetesjournals.org",
    "aace.com",
    "accessdata.fda.gov",
    "dailymed.nlm.nih.gov",
    "ozempic.com",
    "wegovy.com",
    "novonordisk",
]


def _allowed(url: str) -> bool:
    u = (url or "").lower()
    return any(d in u for d in _ALLOWED_DOMAINS)


async def searx_search(query: str, *, limit: int = 8) -> List[Dict[str, Any]]:
    preferred = os.environ.get("SEARXNG_URL", "https://ieissa.com:3443/searxng/search").rstrip("/")
    # Prefer local SearXNG path on workstation first (fast + no remote ACL issues).
    bases = []
    for b in ["http://127.0.0.1:8083/search", preferred, "http://127.0.0.1:8083/searxng/search", "http://127.0.0.1:3443/searxng/search"]:
        if b and b not in bases:
            bases.append(b.rstrip('/'))

    api_key = os.environ.get("SEARXNG_API_KEY", "")
    params = {"q": query, "format": "json"}

    data = None
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=12)) as s:
        for base in bases:
            for with_key in ([True, False] if api_key else [False]):
                headers = {"accept": "application/json"}
                if with_key and api_key:
                    headers["X-API-Key"] = api_key
                try:
                    async with s.get(base, params=params, headers=headers) as r:
                        if r.status != 200:
                            continue
                        data = await r.json()
                        break
                except Exception:
                    continue
            if data is not None:
                break

    if not isinstance(data, dict):
        return []

    raw_results = (data.get("results") or [])[: max(1, limit * 5)]
    out: List[Dict[str, Any]] = []
    for it in raw_results:
        url = it.get("url") or ""
        if not _allowed(url):
            continue
        out.append(
            {
                "title": it.get("title") or "",
                "url": url,
                "snippet": (it.get("content") or "")[:500],
                "source": "web",
            }
        )
        if len(out) >= limit:
            break

    if out:
        return out

    # If strict allowlist yields zero, allow high-signal medical/regulatory domains.
    def _semi_allowed(u: str) -> bool:
        uu = (u or "").lower()
        return any(k in uu for k in [".gov", ".edu", "nih", "pubmed", "fda", "ema", "nejm", "jama", "bmj", "lancet", "diabetes", "novonordisk", "ozempic", "wegovy"])

    for it in raw_results:
        url = it.get("url") or ""
        if not _semi_allowed(url):
            continue
        out.append(
            {
                "title": it.get("title") or "",
                "url": url,
                "snippet": (it.get("content") or "")[:500],
                "source": "web",
            }
        )
        if len(out) >= limit:
            break

    return out
