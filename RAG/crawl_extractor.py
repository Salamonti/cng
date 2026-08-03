#!/usr/bin/env python3
"""
crawl_extractor.py - Full-text extraction using Crawl4AI API.

Uses Crawl4AI Docker container to extract clean, LLM-ready markdown
from web pages. Handles JavaScript-rendered content, PDFs, and complex layouts.

Usage:
    from crawl_extractor import extract_text
    text = extract_text("https://www.ncbi.nlm.nih.gov/pmc/articles/PMC123456/")
"""
from __future__ import annotations

import requests
from typing import Any, Dict, List, Optional

from url_safety import is_safe_public_url

# Crawl4AI configuration
CRAWL4AI_URL = "http://127.0.0.1:11235"
CRAWL4AI_TOKEN = None  # Will be loaded from file
REQUEST_TIMEOUT = 60  # seconds - crawling can take time


def _get_token() -> str:
    """Load Crawl4AI token from file."""
    global CRAWL4AI_TOKEN
    if CRAWL4AI_TOKEN:
        return CRAWL4AI_TOKEN
    
    try:
        with open('/tmp/crawl4ai_token.txt', 'r') as f:
            CRAWL4AI_TOKEN = f.read().strip()
        return CRAWL4AI_TOKEN
    except Exception:
        raise ValueError("Crawl4AI token not found. Run: docker exec crawl4ai env | grep CRAWL4AI_API_TOKEN | cut -d= -f2 > /tmp/crawl4ai_token.txt")


def extract_text(
    url: str,
    timeout: int = REQUEST_TIMEOUT,
) -> Optional[str]:
    """
    Extract clean markdown text from a URL using Crawl4AI.
    
    Args:
        url: URL to crawl
        timeout: Request timeout in seconds
    
    Returns:
        Extracted markdown text, or None if extraction failed
    """
    # url comes from search-engine results, not a curated source list.
    # Crawl4AI does the actual fetch inside its own container, so this
    # process can't re-validate its redirects the way web_search.py does --
    # but it can and does refuse to hand Crawl4AI an obviously internal
    # target (private IP, loopback, etc.) as the initial URL.
    if not is_safe_public_url(url):
        print(f"  Crawl4AI extraction blocked: unsafe URL {url}")
        return None

    token = _get_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    try:
        # Use the /md endpoint for markdown extraction
        response = requests.post(
            CRAWL4AI_URL + "/md",
            headers=headers,
            json={"url": url},
            timeout=timeout,
        )
        response.raise_for_status()
        data = response.json()
        
        # Check if successful
        if data.get("success"):
            return data.get("markdown", "")
        
        return None
        
    except Exception as e:
        print(f"  Crawl4AI extraction error: {e}")
        return None


def extract_batch(
    urls: List[str],
    timeout: int = REQUEST_TIMEOUT,
) -> List[Dict[str, Any]]:
    """
    Extract text from multiple URLs.
    
    Args:
        urls: List of URLs to crawl
        timeout: Request timeout in seconds
    
    Returns:
        List of dicts with url, markdown, success
    """
    token = _get_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    
    results = []

    for url in urls:
        if not is_safe_public_url(url):
            print(f"  Crawl4AI extraction blocked: unsafe URL {url}")
            results.append({
                "url": url,
                "markdown": None,
                "success": False,
                "error": "blocked: unsafe URL",
            })
            continue
        try:
            response = requests.post(
                CRAWL4AI_URL + "/md",
                headers=headers,
                json={"url": url},
                timeout=timeout,
            )
            response.raise_for_status()
            data = response.json()
            
            results.append({
                "url": url,
                "markdown": data.get("markdown", "") if data.get("success") else None,
                "success": data.get("success", False),
            })
            
        except Exception as e:
            print(f"  Crawl4AI extraction error for {url}: {e}")
            results.append({
                "url": url,
                "markdown": None,
                "success": False,
                "error": str(e),
            })
    
    return results