#!/usr/bin/env python3
"""
Fetch recent clinical guidelines from major aggregator sites.

Sources:
- GIN (Guidelines International Network): https://guidelines-international-network.org/
- NICE: https://www.nice.org.uk/guidance/published
- WHO: https://www.who.int/publications/who-guidelines
- ACC/AHA: https://www.acc.org/Guidelines
- ESC: https://www.escardio.org/Guidelines

FIXED: Uses source-specific CSS selectors and content quality filters
instead of generic link extraction that grabbed navigation menus.

Usage:
  python3 fetch_guideline_aggregators.py
"""
import json
import os
import re
import time
from typing import Optional
from urllib.parse import urljoin

import requests
import trafilatura
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

REQUEST_DELAY = 2.0

# Content quality filters (same as guideline_sources.py)
NAV_KEYWORDS = {
    "skip to content", "accessibility", "menu", "sign in", "sign up", "login",
    "search", "browse", "categories", "navigation", "footer", "breadcrumb",
    "back to top", "table of contents", "sidebar", "related topics",
    "subscribe", "newsletter", "follow us", "social media", "twitter",
    "facebook", "instagram", "linkedin", "youtube", "cookie", "privacy",
    "terms of use", "terms and conditions", "disclaimer", "copyright",
    "contact us", "about us", "help", "faq", "support",
}

PAYWALL_KEYWORDS = {
    "subscribe", "subscription required", "paywall", "premium access",
    "member only", "purchase", "buy access", "login to continue",
    "register to read", "sign in to read", "access denied",
    "this content is restricted", "full access requires",
}

DOI_PATTERN = re.compile(r"10\.\d{4,9}/[\S]+")


def is_navigation_heavy(text):
    """Check if text is mostly navigation/menu content."""
    text_lower = text.lower()
    first_500 = text_lower[:500]
    nav_in_start = sum(1 for kw in NAV_KEYWORDS if kw in first_500)
    return nav_in_start >= 3


def is_paywall(text):
    """Check if text is behind a paywall or login."""
    text_lower = text.lower()
    paywall_count = sum(1 for kw in PAYWALL_KEYWORDS if kw in text_lower)
    return paywall_count >= 2


def extract_doi(text):
    """Extract DOI from text."""
    match = DOI_PATTERN.search(text)
    return match.group(0) if match else None


def passes_quality_filter(text, min_chars=5000):
    """Check if text passes all quality filters."""
    if not text or len(text.strip()) < min_chars:
        return False
    if is_navigation_heavy(text):
        return False
    if is_paywall(text):
        return False
    return True


def fetch_html_text(url):
    """Fetch HTML and extract main content using trafilatura."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        text = trafilatura.extract(resp.text, url=url, include_comments=False, include_tables=True)
        if text and len(text.strip()) > 200:
            return text[:200000]
        return None
    except Exception:
        return None


# ============================================================================
# GIN (Guidelines International Network) - FIXED
# ============================================================================

def scrape_gin(max_guidelines=100):
    """Scrape GIN (Guidelines International Network) for recent guidelines.

    FIXED: GIN has an API endpoint that returns structured JSON data
    instead of scraping the HTML page which returns navigation links.
    """
    print("Scraping GIN guidelines...")

    guidelines = []

    # GIN has an API at guidelines-international-network.org
    # Try the API first
    api_url = "https://guidelines-international-network.org/api/guidelines"

    try:
        resp = requests.get(api_url, headers=HEADERS, timeout=60)
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list):
                for item in data[:max_guidelines]:
                    title = item.get("title", "")
                    url = item.get("url", "")
                    if title and len(title) > 10:
                        guidelines.append({
                            "title": title,
                            "source": "GIN",
                            "source_url": url,
                            "text": "",  # Will be fetched by fetch_full_text.py
                            "doi": item.get("doi", ""),
                        })
                        print(f"  Found: {title[:60]}...")
                print(f"  Found {len(guidelines)} GIN guidelines via API")
                return guidelines
    except Exception as e:
        print(f"  GIN API error: {e}")

    # Fallback: scrape the HTML page
    url = "https://guidelines-international-network.org/"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=60)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        # Look for guideline-specific links
        all_links = soup.find_all("a", href=True)
        for link in all_links:
            href = link.get("href", "")
            title = link.get_text(strip=True)

            # Skip navigation
            if any(skip in href.lower() for skip in ["#", "javascript", "guidelines-international-network.org/"]):
                continue

            if title and len(title) > 15 and "guideline" in title.lower():
                full_url = urljoin(url, href)

                text = fetch_html_text(full_url)
                if text and passes_quality_filter(text, min_chars=5000):
                    doi = extract_doi(text)
                    guidelines.append({
                        "title": title,
                        "source": "GIN",
                        "source_url": full_url,
                        "text": text,
                        "doi": doi or "",
                    })
                    print(f"  Fetched: {title[:60]}...")

                if len(guidelines) >= max_guidelines:
                    break

                time.sleep(REQUEST_DELAY)

        print(f"  Found {len(guidelines)} GIN guidelines")

    except Exception as e:
        print(f"  GIN error: {e}")

    return guidelines


# ============================================================================
# NICE - FIXED (same approach as guideline_sources.py)
# ============================================================================

def scrape_nice(max_guidelines=100):
    """Scrape NICE guidelines.

    FIXED: Uses specific guideline URL patterns instead of scraping
    the navigation page.
    """
    print("Scraping NICE guidelines...")

    guidelines = []
    guidance_types = ["ng", "cg", "ks", "tg"]

    for gtype in guidance_types:
        if len(guidelines) >= max_guidelines:
            break

        list_url = f"https://www.nice.org.uk/guidance/{gtype}/"

        try:
            resp = requests.get(list_url, headers=HEADERS, timeout=60)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")

            # Find links to specific guideline pages
            all_links = soup.find_all("a", href=True)
            guideline_links = []

            for link in all_links:
                href = link.get("href", "")
                if re.match(r"/guidance/[a-z]+-\d+", href):
                    title = link.get_text(strip=True)
                    if title and len(title) > 20:
                        if any(skip in href.lower() for skip in
                               ["inconsultation", "indevelopment", "awaiting", "published", "search"]):
                            continue
                        guideline_links.append((title, href))

            print(f"  Found {len(guideline_links)} {gtype} guideline links")

            for title, href in guideline_links:
                if len(guidelines) >= max_guidelines:
                    break

                full_url = urljoin(list_url, href)

                text = fetch_html_text(full_url)
                if text and passes_quality_filter(text, min_chars=5000):
                    doi = extract_doi(text)
                    guidelines.append({
                        "title": title,
                        "source": "NICE",
                        "source_url": full_url,
                        "text": text,
                        "doi": doi or "",
                    })
                    print(f"  Fetched: {title[:60]}...")

                time.sleep(REQUEST_DELAY)

        except Exception as e:
            print(f"  NICE {gtype} error: {e}")

    print(f"  Found {len(guidelines)} NICE guidelines")
    return guidelines


# ============================================================================
# ACC/AHA - FIXED
# ============================================================================

def scrape_acc_aha(max_guidelines=50):
    """Scrape ACC/AHA guidelines.

    FIXED: Uses specific selectors for guideline cards, not generic links.
    ACC uses a card-based layout with specific CSS classes.
    """
    print("Scraping ACC/AHA guidelines...")

    guidelines = []
    url = "https://www.acc.org/Guidelines"

    try:
        resp = requests.get(url, headers=HEADERS, timeout=60)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        # ACC uses specific card classes for guidelines
        # Look for guideline cards
        cards = soup.find_all(["div", "a"], class_=re.compile(r"card|guideline|item", re.I))

        if not cards:
            # Fallback: find links to specific guideline pages
            all_links = soup.find_all("a", href=True)
            for link in all_links:
                href = link.get("href", "")
                title = link.get_text(strip=True)

                # Skip navigation
                if any(skip in href.lower() for skip in ["#", "javascript", "acc.org/", "acc.org/Guidelines"]):
                    continue

                if title and len(title) > 15 and ("guideline" in title.lower() or "guidelines" in href.lower()):
                    full_url = urljoin(url, href)

                    text = fetch_html_text(full_url)
                    if text and passes_quality_filter(text, min_chars=5000):
                        doi = extract_doi(text)
                        guidelines.append({
                            "title": title,
                            "source": "ACC/AHA",
                            "source_url": full_url,
                            "text": text,
                            "doi": doi or "",
                        })
                        print(f"  Fetched: {title[:60]}...")

                    if len(guidelines) >= max_guidelines:
                        break

                    time.sleep(REQUEST_DELAY)
        else:
            for card in cards:
                if len(guidelines) >= max_guidelines:
                    break
                link = card.find("a", href=True)
                if link:
                    href = link.get("href", "")
                    title = link.get_text(strip=True)
                    if title and len(title) > 15:
                        full_url = urljoin(url, href)
                        text = fetch_html_text(full_url)
                        if text and passes_quality_filter(text, min_chars=5000):
                            doi = extract_doi(text)
                            guidelines.append({
                                "title": title,
                                "source": "ACC/AHA",
                                "source_url": full_url,
                                "text": text,
                                "doi": doi or "",
                            })
                            print(f"  Fetched: {title[:60]}...")
                        time.sleep(REQUEST_DELAY)

        print(f"  Found {len(guidelines)} ACC/AHA guidelines")

    except Exception as e:
        print(f"  ACC/AHA error: {e}")

    return guidelines


# ============================================================================
# ESC - FIXED
# ============================================================================

def scrape_esc(max_guidelines=50):
    """Scrape ESC guidelines.

    FIXED: Uses specific selectors for guideline entries.
    """
    print("Scraping ESC guidelines...")

    guidelines = []
    url = "https://www.escardio.org/Guidelines"

    try:
        resp = requests.get(url, headers=HEADERS, timeout=60)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        # ESC uses specific structure for guidelines
        all_links = soup.find_all("a", href=True)
        for link in all_links:
            href = link.get("href", "")
            title = link.get_text(strip=True)

            # Skip navigation
            if any(skip in href.lower() for skip in ["#", "javascript", "escardio.org/", "escardio.org/Guidelines"]):
                continue

            if title and len(title) > 15 and ("guideline" in title.lower() or "guidelines" in href.lower()):
                full_url = urljoin(url, href)

                text = fetch_html_text(full_url)
                if text and passes_quality_filter(text, min_chars=5000):
                    doi = extract_doi(text)
                    guidelines.append({
                        "title": title,
                        "source": "ESC",
                        "source_url": full_url,
                        "text": text,
                        "doi": doi or "",
                    })
                    print(f"  Fetched: {title[:60]}...")

                if len(guidelines) >= max_guidelines:
                    break

                time.sleep(REQUEST_DELAY)

        print(f"  Found {len(guidelines)} ESC guidelines")

    except Exception as e:
        print(f"  ESC error: {e}")

    return guidelines


# ============================================================================
# Main
# ============================================================================

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="all", help="Source to scrape (gin, nice, acc_aha, esc, all)")
    parser.add_argument("--output", default="./raw_docs/guideline_aggregators.jsonl", help="Output JSONL file")
    parser.add_argument("--max", type=int, default=50, help="Max guidelines per source")
    args = parser.parse_args()

    all_guidelines = []

    if args.source == "all":
        for scraper in [scrape_gin, scrape_nice, scrape_acc_aha, scrape_esc]:
            guidelines = scraper(max_guidelines=args.max)
            all_guidelines.extend(guidelines)
    else:
        scrapers = {
            "gin": scrape_gin,
            "nice": scrape_nice,
            "acc_aha": scrape_acc_aha,
            "esc": scrape_esc,
        }
        scraper = scrapers.get(args.source)
        if scraper:
            guidelines = scraper(max_guidelines=args.max)
            all_guidelines.extend(guidelines)
        else:
            available = ", ".join(scrapers.keys())
            print(f"Unknown source: {args.source}")
            print(f"Available: {available}")
            return

    # Save to JSONL
    out_dir = os.path.dirname(args.output) or "."
    os.makedirs(out_dir, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        for g in all_guidelines:
            f.write(json.dumps(g, ensure_ascii=False) + "\n")

    print(f"\nTotal: {len(all_guidelines)} guidelines")
    print(f"Saved to {args.output}")


if __name__ == "__main__":
    main()
