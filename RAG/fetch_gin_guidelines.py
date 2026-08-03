#!/usr/bin/env python3
"""
Fetch clinical guidelines from the GIN (Guidelines International Network) library.

Scrapes https://guidelines.ebmportal.com/ for guidelines, extracts metadata,
follows "View Publication" links to fetch full text, and saves to JSONL.

Usage:
  python3 fetch_gin_guidelines.py                    # Fetch all English guidelines
  python3 fetch_gin_guidelines.py --query "asthma"   # Search for specific topic
  python3 fetch_gin_guidelines.py --since 2024       # Only guidelines from 2024+
  python3 fetch_gin_guidelines.py --update           # Update existing guidelines

Output: raw_docs/guidelines_gin_YYYYMMDD.jsonl
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import time
from typing import Set

import requests
from bs4 import BeautifulSoup
from urllib.parse import quote, urljoin

BASE_URL = "https://guidelines.ebmportal.com"
SEARCH_URL = f"{BASE_URL}/guidelines-international-network"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

REQUEST_DELAY = 2.0
MAX_PAGES = 500  # GIN has ~388 pages total
OUTPUT_DIR = "./raw_docs"


def search_guidelines(session, query="", language="English", status="Published",
                      year_from=None):
    """Search GIN library for guidelines matching criteria."""
    all_guidelines = []
    seen_urls = set()

    url = SEARCH_URL
    if query:
        url = f"{SEARCH_URL}?q={quote(query)}"

    page = 0
    while page < MAX_PAGES:
        print(f"  Fetching page {page + 1}...")

        # Build URL with page parameter
        if page == 0:
            fetch_url = url
        else:
            # Add page parameter
            separator = "&" if "?" in url else "?"
            fetch_url = f"{url}{separator}page={page}"
        
        try:
            response = session.get(fetch_url, headers=HEADERS, timeout=30)
            response.raise_for_status()
        except Exception as e:
            print(f"  Error fetching page {page + 1}: {e}")
            break

        soup = BeautifulSoup(response.text, "html.parser")
        articles = soup.find_all("article")

        if not articles:
            print("  No more results found.")
            break

        page_count = 0
        for article in articles:
            guideline = extract_guideline(article)
            if not guideline:
                continue

            lang = guideline.get("Languages")
            if language and lang and language not in lang:
                continue

            st = guideline.get("Guideline Publication Status")
            if status and st and status not in st:
                continue

            if year_from and guideline.get("Publication Year"):
                try:
                    if int(guideline["Publication Year"]) < year_from:
                        continue
                except (ValueError, TypeError):
                    pass

            gin_url = guideline.get("gin_url", "")
            if gin_url in seen_urls:
                continue
            seen_urls.add(gin_url)

            all_guidelines.append(guideline)
            page_count += 1
        print(f"  Found {page_count} guidelines on this page (total: {len(all_guidelines)})")

        if page_count == 0:
            print("  No more results.")
            break

        page += 1
        time.sleep(REQUEST_DELAY)

    return all_guidelines


def extract_guideline(article):
    """Extract metadata from a guideline article element."""
    try:
        h2 = article.find("h2")
        if not h2:
            return None
        title_link = h2.find("a")
        if not title_link:
            return None

        title = title_link.get_text(strip=True)
        gin_url = urljoin(BASE_URL, title_link.get("href", ""))

        metadata = {}
        dl = article.find("dl")
        if dl:
            for term in dl.find_all("dt"):
                term_text = term.get_text(strip=True).rstrip(":")
                dd = term.find_next_sibling("dd")
                if dd:
                    definitions = []
                    for def_elem in dd.find_all(["p", "li"]):
                        definitions.append(def_elem.get_text(strip=True))
                    if not definitions:
                        definitions = [dd.get_text(strip=True)]
                    metadata[term_text] = ", ".join(definitions)
        # Find "View Publication" link
        publication_url = None
        for link in article.find_all("a", href=True):
            text = link.get_text(strip=True).lower()
            if "publication" in text:
                publication_url = urljoin(BASE_URL, link["href"])
                break

        result = {
            "title": title,
            "gin_url": gin_url,
            "publication_url": publication_url,
        }
        result.update(metadata)
        return result

    except Exception as e:
        print(f"  Error extracting guideline: {e}")
        return None


def fetch_guideline_text(session, pub_url):
    """Fetch the full text of a guideline from its publication URL."""
    if not pub_url:
        return None
    try:
        response = session.get(pub_url, headers=HEADERS, timeout=30)
        response.raise_for_status()
        content_type = response.headers.get("Content-Type", "")
        if "pdf" in content_type:
            return f"[PDF: {pub_url}]"
        soup = BeautifulSoup(response.text, "html.parser")
        for elem in soup.find_all(["script", "style", "nav", "footer", "header"]):
            elem.decompose()
        text = soup.get_text(separator="\n", strip=True)
        return text[:50000]
    except Exception as e:
        print(f"  Error fetching guideline text: {e}")
        return None


def load_existing(output_path):
    """Load existing guidelines from JSONL file, keyed by gin_url."""
    existing = {}
    if os.path.exists(output_path):
        with open(output_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        g = json.loads(line)
                        gin_url = g.get("gin_url", "")
                        if gin_url:
                            existing[gin_url] = g
                    except json.JSONDecodeError:
                        continue
    return existing


def main():
    parser = argparse.ArgumentParser(description="Fetch guidelines from GIN library")
    parser.add_argument("--query", default="", help="Search query (empty = all)")
    parser.add_argument("--language", default="English", help="Filter by language")
    parser.add_argument("--status", default="Published", help="Filter by status")
    parser.add_argument("--since", type=int, default=None, help="Only from this year onwards")
    parser.add_argument("--update", action="store_true", help="Only fetch new guidelines")
    parser.add_argument("--fetch-text", action="store_true", help="Fetch full text from publication URLs")
    parser.add_argument("--output", default=None, help="Output JSONL file path")
    args = parser.parse_args()

    if not args.output:
        timestamp = dt.datetime.now().strftime("%Y%m%d")
        args.output = f"{OUTPUT_DIR}/guidelines_gin_{timestamp}.jsonl"

    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)

    print(f"GIN Guidelines Fetcher")
    print(f"Query: '{args.query or '(all)'}'")
    print(f"Language: {args.language}")
    print(f"Status: {args.status}")
    if args.since:
        print(f"Since year: {args.since}")
    print(f"Output: {args.output}")
    print()

    existing = {}
    if args.update:
        existing = load_existing(args.output)
        print(f"Loaded {len(existing)} existing guidelines")

    session = requests.Session()
    guidelines = search_guidelines(
        session, query=args.query, language=args.language,
        status=args.status, year_from=args.since,
    )

    print(f"\nTotal guidelines found: {len(guidelines)}")

    if args.update:
        new_guidelines = [g for g in guidelines if g["gin_url"] not in existing]
        print(f"New guidelines: {len(new_guidelines)}")
        guidelines = new_guidelines

    if args.fetch_text:
        print("\nFetching full text...")
        for i, g in enumerate(guidelines):
            if g.get("publication_url"):
                print(f"  [{i+1}/{len(guidelines)}] {g['title'][:80]}...")
                g["text"] = fetch_guideline_text(session, g["publication_url"])
                time.sleep(REQUEST_DELAY)
            else:
                g["text"] = None

    with open(args.output, "a" if args.update else "w", encoding="utf-8") as f:
        for g in guidelines:
            f.write(json.dumps(g, ensure_ascii=False) + "\n")

    print(f"\nSaved {len(guidelines)} guidelines to {args.output}")


if __name__ == "__main__":
    main()
