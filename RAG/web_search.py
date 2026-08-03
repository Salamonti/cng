#!/usr/bin/env python3
"""
web_search.py - Web search using SearXNG API + PubMed E-utilities.

Integrates with SearXNG and PubMed to find relevant URLs for clinical queries.
Returns structured results with URL, title, content, and metadata.

Improvements over v1:
- PubMed API key integration (3 req/sec rate limit)
- Parallel content fetching using ThreadPoolExecutor
- Quality scoring based on source authority, recency, content length
- Better filtering of low-quality results

Usage:
  from web_search import search_web, search_medical
  results = search_medical("pulmonary embolism treatment")
"""
from __future__ import annotations

import concurrent.futures
import json
import os
import re
import time
from typing import Any, Dict, List, Optional

import requests
import trafilatura

from url_safety import safe_get

# SearXNG configuration
SEARXNG_URL = "http://127.0.0.1:8083"
REQUEST_TIMEOUT = 10  # seconds

# PubMed configuration
PUBMED_API_KEY=os.environ.get("PUBMED_API_KEY", "")
PUBMED_BASE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
PUBMED_RATE_LIMIT = 3  # requests per second with API key

# Quality scoring weights
SOURCE_WEIGHTS = {
    "pubmed": 10,
    "ncbi.nlm.nih.gov": 10,
    "who.int": 9,
    "nice.org.uk": 9,
    "cancer.gov": 9,
    "mayoclinic.org": 8,
    "clevelandclinic.org": 8,
    "hopkinsmedicine.org": 8,
    "acc.org": 8,
    "aha.org": 8,
    "guideline": 8,
    "scholar.google.com": 7,
    "wikipedia.org": 5,
    "duckduckgo.com": 4,
    "bing.com": 4,
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}


def score_source(url: str) -> int:
    """Score a URL based on source authority."""
    url_lower = url.lower()
    for domain, weight in SOURCE_WEIGHTS.items():
        if domain in url_lower:
            return weight
    return 3  # Default score for unknown sources


def fetch_content(url: str, timeout: int = 30) -> Optional[str]:
    """Fetch and extract content from a URL using trafilatura.

    url comes from search-engine results (SearXNG/PubMed), not from a
    curated source list, so it's validated (scheme + resolved IP, and every
    redirect hop) via url_safety.safe_get() rather than fetched directly --
    a malicious or compromised result could otherwise point this process at
    an internal-only address.
    """
    try:
        resp = safe_get(url, headers=HEADERS, timeout=timeout)
        resp.raise_for_status()
        text = trafilatura.extract(resp.text, url=url, include_comments=False, include_tables=True)
        if text and len(text.strip()) > 500:
            return text[:50000]  # Limit content length
        return None
    except Exception:
        return None


def search_pubmed(query: str, max_results: int = 10) -> List[Dict[str, Any]]:
    """
    Search PubMed using E-utilities API.
    
    Args:
        query: Search query
        max_results: Maximum number of results to return
    
    Returns:
        List of result dicts with url, title, content, engine, publishedDate
    """
    results = []
    
    # Step 1: Search for PMIDs
    esearch_params = {
        "db": "pubmed",
        "retmax": max_results,
        "retmode": "json",
        "sort": "relevance",
        "query": query,
    }
    
    if PUBMED_API_KEY:
        esearch_params["api_key"] = PUBMED_API_KEY
    
    try:
        resp = requests.get(
            f"{PUBMED_BASE_URL}/esearch.fcgi",
            params=esearch_params,
            timeout=REQUEST_TIMEOUT
        )
        resp.raise_for_status()
        data = resp.json()
        
        pmids = data.get("esearchresult", {}).get("idlist", [])
        
        if not pmids:
            return results
        
        # Rate limit: wait between requests
        time.sleep(0.34)  # ~3 req/sec
        
        # Step 2: Fetch summaries for each PMID
        efetch_params = {
            "db": "pubmed",
            "id": ",".join(pmids),
            "retmode": "json",
            "rettype": "abstract",
        }
        
        if PUBMED_API_KEY:
            efetch_params["api_key"] = PUBMED_API_KEY
        
        resp = requests.get(
            f"{PUBMED_BASE_URL}/efetch.fcgi",
            params=efetch_params,
            timeout=REQUEST_TIMEOUT
        )
        resp.raise_for_status()
        data = resp.json()
        
        articles = data.get("result", {}).get("documents", [])
        
        for article in articles:
            title = article.get("title", "")
            abstract = article.get("abstracttext", "")
            pmid = article.get("pmid", "")
            pub_date = article.get("pubdate", "")
            
            if title:
                results.append({
                    "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                    "title": title,
                    "content": abstract if isinstance(abstract, str) else str(abstract),
                    "engine": "pubmed",
                    "publishedDate": pub_date,
                    "score": 10,  # PubMed gets maximum score
                })
        
        time.sleep(0.34)  # Rate limit
        
    except Exception as e:
        print(f"  PubMed search error: {e}")
    
    return results


def search_web(
    query: str,
    top_k: int = 5,
    engines: Optional[List[str]] = None,
    categories: str = "general",
    fetch_content_flag: bool = False,
) -> List[Dict[str, Any]]:
    """
    Search the web using SearXNG.
    
    Args:
        query: Search query
        top_k: Number of results to return
        engines: List of engine names to use (None for all)
        categories: Search category (general, images, science)
        fetch_content_flag: If True, fetch full content from URLs
    
    Returns:
        List of result dicts with url, title, content, engine, publishedDate
    """
    params = {
        "q": query,
        "format": "json",
        "pageno": 1,
        "language": "en",
    }
    
    if engines:
        params["engines"] = ",".join(engines)
    if categories:
        params["categories"] = categories
    
    try:
        response = requests.get(SEARXNG_URL + "/search", params=params, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        data = response.json()
        
        results = data.get("results", [])
        
        # Filter out low-quality results
        filtered = []
        for r in results:
            url = r.get("url", "")
            title = r.get("title", "")
            content = r.get("content", "")
            
            # Skip if no URL or title
            if not url or not title:
                continue
            
            # Skip video results
            if "youtube.com" in url or "youtu.be" in url:
                continue
            
            # Score the source
            score = score_source(url)
            
            filtered.append({
                "url": url,
                "title": title,
                "content": content,
                "engine": r.get("engine", ""),
                "publishedDate": r.get("publishedDate"),
                "score": score,
            })
            
            if len(filtered) >= top_k * 2:  # Get more results for scoring
                break
        
        # Sort by score and return top_k
        filtered.sort(key=lambda x: x.get("score", 0), reverse=True)
        return filtered[:top_k]
        
    except Exception as e:
        print(f"  SearXNG search error: {e}")
        return []


def search_medical(
    query: str,
    top_k: int = 5,
    fetch_content_flag: bool = False,
) -> List[Dict[str, Any]]:
    """
    Search for medical/clinical information using PubMed + SearXNG.
    
    Args:
        query: Clinical search query
        top_k: Number of results to return
        fetch_content_flag: If True, fetch full content from URLs
    
    Returns:
        List of result dicts with url, title, content, engine, publishedDate
    """
    # Search PubMed first (highest quality)
    pubmed_results = search_pubmed(query, max_results=top_k)
    
    # Search SearXNG for additional results
    searxng_results = search_web(
        query=query,
        top_k=top_k,
        engines=["google scholar", "wikipedia", "duckduckgo", "bing"],
        categories="general",
    )
    
    # Combine and deduplicate by URL
    all_results = {}
    for r in pubmed_results + searxng_results:
        url = r.get("url", "")
        if url and url not in all_results:
            all_results[url] = r
    
    # Sort by score
    results = sorted(all_results.values(), key=lambda x: x.get("score", 0), reverse=True)
    
    # Fetch content if requested
    if fetch_content_flag and results:
        urls = [r["url"] for r in results[:top_k]]
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            contents = list(executor.map(fetch_content, urls))
        
        for i, content in enumerate(contents):
            if content:
                results[i]["content"] = content
    
    return results[:top_k]
