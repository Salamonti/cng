import os
from typing import Any, Dict, List
from urllib.parse import urlparse

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
    # ACC/AHA cardiovascular guidelines
    "theacc.com",
    "acc.org",
    "aha.org",
    "heart.org",
    # Additional major guideline sources
    "guidelinecentral.com",
    "mdguidelines.com",
    "upToDate.com",
    "merckmanuals.com",
    "mayoclinic.org",
    "clevelandclinic.org",
    "acs.org",
    "asco.org",
    "nccn.org",
]


def _hostname(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def _host_matches_entry(hostname: str, entry: str) -> bool:
    """A domain-shaped entry ("who.int") must match the hostname exactly or
    as a proper subdomain. A bare keyword entry ("novonordisk", no dot)
    matches as a substring of the hostname only -- never of the full URL,
    which an attacker fully controls (path/query), unlike the hostname."""
    e = entry.lower()
    if e.startswith("."):
        return hostname.endswith(e)
    if "." in e:
        return hostname == e or hostname.endswith("." + e)
    return e in hostname


def _matches_allowlist(url: str, allowlist) -> bool:
    hostname = _hostname(url)
    if not hostname:
        return False
    return any(_host_matches_entry(hostname, entry) for entry in allowlist)


def _allowed(url: str) -> bool:
    # Was a substring check against the full (lowercased) URL -- e.g.
    # "https://evil.example/who.int" or "https://who.int.evil.example"
    # would have passed even though neither is actually who.int. Now
    # matches against the parsed hostname only, with proper subdomain
    # semantics for domain-shaped entries.
    return _matches_allowlist(url, _ALLOWED_DOMAINS)


def _candidate_search_bases() -> List[str]:
    """Ordered list of SearXNG base URLs to try, local-first.

    P3-2: this used to default SEARXNG_URL to an EXTERNAL domain
    (https://ieissa.com:3443/...) as a silent fallback -- if the local
    SearXNG instance was ever unreachable, the consult clinical focus text
    got sent as a GET query string to a server outside the local network,
    undermining the "PHI never leaves your building" positioning this whole
    app is built on. Nothing in production actually sets SEARXNG_URL, so
    that external default was live, not theoretical. SEARXNG_URL remains
    operator-configurable for anyone who explicitly wants a non-default
    endpoint; only the silent default changed, to another local candidate
    that's a harmless no-op against the list below.
    """
    preferred = os.environ.get("SEARXNG_URL", "http://127.0.0.1:8083/searxng/search").rstrip("/")
    bases: List[str] = []
    for b in ["http://127.0.0.1:8083/search", preferred, "http://127.0.0.1:8083/searxng/search", "http://127.0.0.1:3443/searxng/search"]:
        if b and b not in bases:
            bases.append(b.rstrip('/'))
    return bases


async def searx_search(query: str, *, limit: int = 8) -> List[Dict[str, Any]]:
    # Prefer local SearXNG path on workstation first (fast + no remote ACL issues).
    bases = _candidate_search_bases()

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
                    # Legitimate failover: SearXNG is multi-endpoint; a failure on
                    # one base simply tries the next (and ultimately returns []).
                    continue
            if data is not None:
                break

    if not isinstance(data, dict):
        return []

    raw_results = (data.get("results") or [])[: max(1, limit * 5)]
    
    # Separate PubMed results from others — PubMed abstracts have CAPTCHA,
    # so we fetch their full text via NCBI EFetch API instead of relying on snippets
    pubmed_ids = []
    other_results = []
    
    import re
    pubmed_id_pattern = re.compile(r'pubmed\.ncbi\.nlm\.nih\.gov/(\d+)')
    
    for it in raw_results:
        url = it.get("url") or ""
        pmid_match = pubmed_id_pattern.search(url)
        if pmid_match:
            pubmed_ids.append({
                "pmid": pmid_match.group(1),
                "title": it.get("title") or "",
                "url": url,
            })
        else:
            other_results.append(it)
    
    out: List[Dict[str, Any]] = []
    
    # First, fetch PubMed abstracts via EFetch API (no CAPTCHA)
    if pubmed_ids:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as s:
            # Batch fetch up to 20 PMIDs at once
            for i in range(0, len(pubmed_ids), 20):
                batch = pubmed_ids[i:i+20]
                ids = ",".join([pm["pmid"] for pm in batch])
                try:
                    efetch_url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=pubmed&id={ids}&retmode=xml"
                    async with s.get(efetch_url) as r:
                        if r.status == 200:
                            xml_text = await r.text()
                            # Extract abstracts from XML
                            import xml.etree.ElementTree as ET
                            try:
                                root = ET.fromstring(xml_text)
                                for article in root.findall('.//PubmedArticle'):
                                    title_el = article.find('.//ArticleTitle')
                                    abstract_el = article.find('.//AbstractText')
                                    pmid_el = article.find('.//PMID')
                                    
                                    title = title_el.text if title_el is not None else ""
                                    abstract = abstract_el.text if abstract_el is not None else ""
                                    pmid = pmid_el.text if pmid_el is not None else ""
                                    
                                    # Find matching original result for URL
                                    matching = next((pm for pm in batch if pm["pmid"] == pmid), None)
                                    url = matching["url"] if matching else f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
                                    
                                    out.append({
                                        "title": title,
                                        "url": url,
                                        "snippet": abstract[:500] if abstract else "",
                                        "source": "pubmed",
                                    })
                            except ET.ParseError:
                                # Legitimately safe: a malformed PubMed abstract XML
                                # for one batch is skipped; other batches still parse.
                                pass
                except Exception:
                    # Legitimate failover: efetch for one PMID batch failing just
                    # omits those abstracts; non-PubMed results still surface.
                    pass
    
    # Then add non-PubMed results that pass allowlist
    for it in other_results:
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
    
    # If still not enough, use semi-allowlist
    if len(out) < limit:
        _SEMI_ALLOWED_KEYWORDS = [".gov", ".edu", "nih", "pubmed", "fda", "ema", "nejm", "jama", "bmj", "lancet", "diabetes", "novonordisk", "ozempic", "wegovy", "theacc.com", "acc.org", "aha.org", "heart.org"]

        def _semi_allowed(u: str) -> bool:
            return _matches_allowlist(u, _SEMI_ALLOWED_KEYWORDS)
        
        for it in other_results:
            if len(out) >= limit:
                break
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
    
    return out[:limit]
