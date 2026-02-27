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
]


def _allowed(url: str) -> bool:
    u = (url or "").lower()
    return any(d in u for d in _ALLOWED_DOMAINS)


async def searx_search(query: str, *, limit: int = 8) -> List[Dict[str, Any]]:
    base = os.environ.get("SEARXNG_URL", "https://ieissa.com:3443/searxng/search").rstrip("/")
    api_key = os.environ.get("SEARXNG_API_KEY", "")
    params = {"q": query, "format": "json"}
    headers = {"accept": "application/json"}
    if api_key:
        headers["X-API-Key"] = api_key

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=12)) as s:
        async with s.get(base, params=params, headers=headers) as r:
            if r.status != 200:
                return []
            data = await r.json()

    out: List[Dict[str, Any]] = []
    for it in (data.get("results") or [])[: max(1, limit * 3)]:
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
    return out
