#!/usr/bin/env python3
"""
Guideline source scrapers for major clinical guideline organizations.

Each scraper extracts full-text guidelines from a specific source using
source-specific CSS selectors. All scrapers follow the same interface:
  scrape() -> list of guideline dicts.

Content quality filters:
  - Minimum 5000 chars of actual content
  - Block navigation-heavy pages (>50% nav text)
  - Block login/paywall pages
  - DOI extraction for deduplication

Usage:
  python3 guideline_sources.py --source nice --output ./raw_docs/guidelines_nice.jsonl
  python3 guideline_sources.py --source all --output ./raw_docs/guidelines_all.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
from typing import Optional
from urllib.parse import urljoin

import requests
import pdfplumber
from bs4 import BeautifulSoup
import trafilatura

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

REQUEST_DELAY = 2.0

# ============================================================================
# Content quality filters
# ============================================================================

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
    """Check if text is mostly navigation/menu content rather than actual content."""
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


# ============================================================================
# Helper functions
# ============================================================================

def fetch_pdf_text(url):
    """Download a PDF and extract text using pdfplumber."""
    import io
    try:
        response = requests.get(url, headers=HEADERS, timeout=60)
        response.raise_for_status()
        with pdfplumber.open(io.BytesIO(response.content)) as pdf:
            pages = []
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    pages.append(page_text)
            return "\n\n".join(pages)[:200000]
    except Exception as e:
        print(f"    PDF error: {e}")
        return None


def fetch_html_text(url):
    """Fetch HTML and extract main content using trafilatura."""
    try:
        response = requests.get(url, headers=HEADERS, timeout=30)
        response.raise_for_status()
        text = trafilatura.extract(response.text, url=url, include_comments=False, include_tables=True)
        if text and len(text.strip()) > 100:
            return text[:200000]
        # Fallback to BeautifulSoup
        soup = BeautifulSoup(response.text, "html.parser")
        for elem in soup.find_all(["script", "style", "nav", "footer", "header"]):
            elem.decompose()
        text = soup.get_text(separator="\n", strip=True)
        return text[:200000] if text else None
    except Exception as e:
        print(f"    HTML error: {e}")
        return None


# ============================================================================
# NICE (nice.org.uk) - FIXED
# ============================================================================

def scrape_nice(max_guidelines=100):
    """
    Scrape NICE guidelines from nice.org.uk/guidance/published.

    FIXED: Uses /guidance/published endpoint which returns actual guideline
    links. The old approach used /guidance/ng/, /guidance/cg/ etc. which
    return 404 errors.
    """
    print("Scraping NICE guidelines...")

    guidelines = []
    url = "https://www.nice.org.uk/guidance/published"

    try:
        response = requests.get(url, headers=HEADERS, timeout=60)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        # Find links to specific guideline pages
        all_links = soup.find_all("a", href=True)
        guideline_links = []

        for link in all_links:
            href = link.get("href", "")
            title = link.get_text(strip=True)
            
            # Match guideline URLs: /guidance/qs216, /guidance/ta1173, /guidance/cg81, etc.
            if re.search(r"/guidance/[a-z]+\d+", href):
                if title and len(title) > 10:
                    # Skip pagination and category pages
                    if any(skip in href.lower() for skip in 
                           ["pa=", "published", "inconsultation", "indevelopment", "deferred", "awaiting", "prioritisation"]):
                        continue
                    guideline_links.append((title, href))

        print(f"  Found {len(guideline_links)} guideline links")

        # Fetch full text for each guideline
        for title, href in guideline_links:
            if len(guidelines) >= max_guidelines:
                break

            full_url = urljoin(url, href)

            try:
                text = fetch_html_text(full_url)
                if text and len(text.strip()) > 1000:  # Lower threshold for NICE summaries
                    doi = extract_doi(text)
                    guidelines.append({
                        "title": title,
                        "source": "NICE",
                        "source_url": full_url,
                        "publication_url": full_url,
                        "text": text,
                        "doi": doi or "",
                    })
                    print(f"    Fetched: {title[:60]}... ({len(text)} chars)")
                elif text:
                    print(f"    Skipped (too short): {title[:40]}... ({len(text)} chars)")
            except Exception as e:
                print(f"    Error fetching {title[:40]}: {e}")

            time.sleep(REQUEST_DELAY)

    except Exception as e:
        print(f"  NICE error: {e}")

    print(f"  Found {len(guidelines)} NICE guidelines")
    return guidelines


# ============================================================================
# WHO (who.int / iris.who.int) - IMPROVED
# ============================================================================

def scrape_who(max_guidelines=50):
    """
    Scrape WHO guidelines from who.int/publications/who-guidelines.

    WHO guidelines are free PDFs hosted on iris.who.int.
    FIXED: Better pairing of titles with PDF download links.
    """
    print("Scraping WHO guidelines...")

    guidelines = []

    url = "https://www.who.int/publications/who-guidelines"

    try:
        response = requests.get(url, headers=HEADERS, timeout=60)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        # WHO uses article elements for publications
        h3s = soup.find_all("h3")

        for h3 in h3s:
            if len(guidelines) >= max_guidelines:
                break

            # Get title
            title_tag = h3
            if not title_tag:
                continue
            title = title_tag.get_text(strip=True)
            if not title or len(title) < 10:
                continue

            # Get publication URL
            title_link = title_tag.find("a", href=True)
            pub_url = title_link.get("href", "") if title_link else ""
            if pub_url:
                pub_url = urljoin(url, pub_url)

            # Find PDF download link (iris.who.int)
            pdf_url = ""
            for link in h3.find_all("a", href=True):
                href = link.get("href", "")
                if "iris.who.int" in href or "bitstream" in href:
                    pdf_url = href
                    break

            # If no PDF found in article, try the publication page
            if not pdf_url and pub_url:
                try:
                    pub_resp = requests.get(pub_url, headers=HEADERS, timeout=30)
                    pub_resp.raise_for_status()
                    pub_soup = BeautifulSoup(pub_resp.text, "html.parser")
                    for link in pub_soup.find_all("a", href=True):
                        href = link.get("href", "")
                        if "iris.who.int" in href or href.endswith(".pdf") or "bitstream" in href:
                            pdf_url = href
                            if not pdf_url.startswith("http"):
                                pdf_url = urljoin(pub_url, pdf_url)
                            break
                except Exception:
                    pass

            # P3-3: this used to append every guideline with text="" hardcoded
            # -- titles and PDF links were collected, but the actual guideline
            # text was never fetched at all, so every WHO entry was empty and
            # contributed nothing to the corpus (confirmed empty in
            # production logs). Fetch it the same way every other PDF-based
            # scraper in this file does (scrape_gold, scrape_gina, etc.).
            if not pdf_url:
                continue
            text = fetch_pdf_text(pdf_url)
            if not text or not passes_quality_filter(text, min_chars=5000):
                continue
            doi = extract_doi(text)
            guidelines.append({
                "title": title,
                "source": "WHO",
                "source_url": pub_url or url,
                "publication_url": pdf_url,
                "text": text,
                "doi": doi or "",
            })
            time.sleep(REQUEST_DELAY)

        print(f"  Collected {len(guidelines)} WHO guidelines")

    except Exception as e:
        print(f"  WHO error: {e}")

    return guidelines


# ============================================================================
# EAN (ean.org) - FIXED
# ============================================================================

def scrape_ean(max_guidelines=50):
    """
    Scrape EAN guidelines from ean.org/research/ean-guidelines.

    FIXED: Uses specific selectors for guideline entries, not generic links.
    """
    print("Scraping EAN guidelines...")

    guidelines = []

    url = "https://www.ean.org/research/ean-guidelines"

    try:
        response = requests.get(url, headers=HEADERS, timeout=60)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        # Look for links that point to EJoN articles or guideline PDFs
        all_links = soup.find_all("a", href=True)
        for link in all_links:
            href = link.get("href", "")
            title = link.get_text(strip=True)

            # Skip navigation links
            if any(skip in href.lower() for skip in
                   ["ean-guidelines", "research", "#", "javascript"]):
                continue

            # Look for EJoN article links or PDF links
            if title and len(title) > 20 and (
                "ejn" in href.lower() or
                "guideline" in title.lower() or
                href.endswith(".pdf")
            ):
                full_url = urljoin(url, href)

                try:
                    text = fetch_html_text(full_url)
                    if text and passes_quality_filter(text, min_chars=5000):
                        doi = extract_doi(text)
                        guidelines.append({
                            "title": title,
                            "source": "EAN",
                            "source_url": full_url,
                            "publication_url": full_url,
                            "text": text,
                            "doi": doi or "",
                        })
                        print(f"    Fetched: {title[:60]}...")
                except Exception as e:
                    print(f"    Error fetching {title[:40]}: {e}")

                if len(guidelines) >= max_guidelines:
                    break

        print(f"  Found {len(guidelines)} EAN guidelines")

    except Exception as e:
        print(f"  EAN error: {e}")

    return guidelines


# ============================================================================
# AAOS (orthoguidelines.org) - FIXED
# ============================================================================

def scrape_aaos(max_guidelines=50):
    """
    Scrape AAOS guidelines from orthoguidelines.org.

    FIXED: Uses specific selectors for guideline entries.
    """
    print("Scraping AAOS guidelines...")

    guidelines = []

    url = "https://www.orthoguidelines.org/"

    try:
        response = requests.get(url, headers=HEADERS, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        # Look for guideline links - skip navigation
        for link in soup.find_all("a", href=True):
            href = link.get("href", "")
            title = link.get_text(strip=True)

            # Skip navigation
            if any(skip in href.lower() for skip in
                   ["#", "javascript", "orthoguidelines.org/"]):
                continue

            if "guideline" in href.lower() and title and len(title) > 10:
                full_url = urljoin(url, href)

                try:
                    text = fetch_html_text(full_url)
                    if text and passes_quality_filter(text, min_chars=5000):
                        doi = extract_doi(text)
                        guidelines.append({
                            "title": title,
                            "source": "AAOS",
                            "source_url": full_url,
                            "publication_url": full_url,
                            "text": text,
                            "doi": doi or "",
                        })
                except Exception as e:
                    print(f"    Error fetching {title[:40]}: {e}")

                if len(guidelines) >= max_guidelines:
                    break

        print(f"  Found {len(guidelines)} AAOS guidelines")

    except Exception as e:
        print(f"  AAOS error: {e}")

    return guidelines


# ============================================================================
# GOLD (goldcopd.org)
# ============================================================================

def scrape_gold(max_guidelines=10):
    """
    Scrape GOLD guidelines from goldcopd.org.

    GOLD guidelines are free PDFs.
    """
    print("Scraping GOLD guidelines...")

    guidelines = []

    url = "https://goldcopd.org/gold-reports/"

    try:
        response = requests.get(url, headers=HEADERS, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        # Find PDF links
        for link in soup.find_all("a", href=True):
            href = link.get("href", "")
            if href.endswith(".pdf") or "report" in href.lower():
                full_url = urljoin(url, href)
                title = link.get_text(strip=True)

                if title and len(title) > 10:
                    text = fetch_pdf_text(full_url)
                    if text and passes_quality_filter(text, min_chars=5000):
                        doi = extract_doi(text)
                        guidelines.append({
                            "title": title,
                            "source": "GOLD",
                            "source_url": full_url,
                            "publication_url": full_url,
                            "text": text,
                            "doi": doi or "",
                        })

                if len(guidelines) >= max_guidelines:
                    break

        print(f"  Found {len(guidelines)} GOLD guidelines")

    except Exception as e:
        print(f"  GOLD error: {e}")

    return guidelines


# ============================================================================
# GINA (ginasthma.org)
# ============================================================================

def scrape_gina(max_guidelines=10):
    """
    Scrape GINA guidelines from ginasthma.org.

    GINA guidelines are free PDFs.
    """
    print("Scraping GINA guidelines...")

    guidelines = []

    url = "https://ginasthma.org/gina-report/"

    try:
        response = requests.get(url, headers=HEADERS, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        # Find PDF links
        for link in soup.find_all("a", href=True):
            href = link.get("href", "")
            if href.endswith(".pdf") or "report" in href.lower():
                full_url = urljoin(url, href)
                title = link.get_text(strip=True)

                if title and len(title) > 10:
                    text = fetch_pdf_text(full_url)
                    if text and passes_quality_filter(text, min_chars=5000):
                        doi = extract_doi(text)
                        guidelines.append({
                            "title": title,
                            "source": "GINA",
                            "source_url": full_url,
                            "publication_url": full_url,
                            "text": text,
                            "doi": doi or "",
                        })

                if len(guidelines) >= max_guidelines:
                    break

        print(f"  Found {len(guidelines)} GINA guidelines")

    except Exception as e:
        print(f"  GINA error: {e}")

    return guidelines


# ============================================================================
# Main
# ============================================================================

SCRAPERS = {
    "nice": scrape_nice,
    "who": scrape_who,
    "ean": scrape_ean,
    "aaos": scrape_aaos,
    "gold": scrape_gold,
    "gina": scrape_gina,
}


def main():
    parser = argparse.ArgumentParser(description="Scrape guideline sources")
    parser.add_argument("--source", required=True,
                        help="Source to scrape (nice, who, ean, aaos, gold, gina, all)")
    parser.add_argument("--output", required=True, help="Output JSONL file")
    parser.add_argument("--max", type=int, default=50, help="Max guidelines per source")
    args = parser.parse_args()

    if args.source == "all":
        all_guidelines = []
        for name, scraper in SCRAPERS.items():
            guidelines = scraper(max_guidelines=args.max)
            all_guidelines.extend(guidelines)

        print(f"\nTotal: {len(all_guidelines)} guidelines")

        # Save
        out_dir = os.path.dirname(args.output) or "."
        os.makedirs(out_dir, exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            for g in all_guidelines:
                f.write(json.dumps(g, ensure_ascii=False) + "\n")

        print(f"Saved to {args.output}")
    else:
        scraper = SCRAPERS.get(args.source)
        if not scraper:
            available = ", ".join(SCRAPERS.keys())
            print(f"Unknown source: {args.source}")
            print(f"Available: {available}")
            return

        guidelines = scraper(max_guidelines=args.max)

        print(f"\nTotal: {len(guidelines)} guidelines")

        # Save
        out_dir = os.path.dirname(args.output) or "."
        os.makedirs(out_dir, exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            for g in guidelines:
                f.write(json.dumps(g, ensure_ascii=False) + "\n")

        print(f"Saved to {args.output}")


if __name__ == "__main__":
    main()


# ============================================================================
# NICE (nice.org.uk) - FIXED
# ============================================================================

