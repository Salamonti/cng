#!/usr/bin/env python3
"""
Fetch full text from guideline publication URLs.

Reads guidelines from a JSONL file (output of fetch_gin_guidelines.py),
follows publication URLs, extracts full text (PDF or HTML), and saves
to a new JSONL file with the text field populated.

Usage:
  python3 fetch_full_text.py --input ./raw_docs/guidelines_gin.jsonl --output ./raw_docs/guidelines_gin_full.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import time
from typing import Optional

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

REQUEST_DELAY = 3.0


def fetch_guideline_text(session, pub_url):
    """Fetch full text from a publication URL (PDF or HTML)."""
    if not pub_url:
        return None
    try:
        response = session.get(pub_url, headers=HEADERS, timeout=60)
        response.raise_for_status()
        content_type = response.headers.get("Content-Type", "")

        # PDF - check content type or URL pattern
        is_pdf = ("pdf" in content_type or 
                  pub_url.endswith(".pdf") or 
                  "bitstream" in pub_url)

        if is_pdf:
            import io
            with pdfplumber.open(io.BytesIO(response.content)) as pdf:
                pages = []
                for page in pdf.pages:
                    page_text = page.extract_text()
                    if page_text:
                        pages.append(page_text)
                return "\n\n".join(pages)[:200000]

        # HTML - use trafilatura for best content extraction
        text = trafilatura.extract(response.text, include_comments=False, include_tables=True)
        if text:
            return text[:200000]

        # Fallback: BeautifulSoup
        soup = BeautifulSoup(response.text, "html.parser")
        for elem in soup.find_all(["script", "style", "nav", "footer", "header"]):
            elem.decompose()
        text = soup.get_text(separator="\n", strip=True)
        return text[:200000] if text else None

    except Exception as e:
        print(f"    Error: {e}")
        return None


def main():
    parser = argparse.ArgumentParser(description="Fetch full text from guideline publication URLs")
    parser.add_argument("--input", required=True, help="Input JSONL file (from fetch_gin_guidelines.py)")
    parser.add_argument("--output", required=True, help="Output JSONL file with full text")
    args = parser.parse_args()

    # Read input
    guidelines = []
    with open(args.input, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                guidelines.append(json.loads(line))

    print(f"Loaded {len(guidelines)} guidelines")
    print(f"Output: {args.output}")
    print()

    # Fetch full text
    session = requests.Session()
    success = 0
    failed = 0

    for i, g in enumerate(guidelines):
        title = g.get("title", "Unknown")
        pub_url = g.get("publication_url")

        print(f"[{i+1}/{len(guidelines)}] {title[:80]}")

        if not pub_url:
            print("    No publication URL")
            g["text"] = None
            failed += 1
            continue

        # Skip if already has text
        if g.get("text") and len(g["text"]) > 100:
            print("    Already has text")
            success += 1
            continue

        text = fetch_guideline_text(session, pub_url)

        if text and len(text.strip()) > 100:
            g["text"] = text
            print(f"    OK: {len(text)} chars")
            success += 1
        else:
            g["text"] = None
            print("    Failed to extract text")
            failed += 1

        time.sleep(REQUEST_DELAY)

    # Save output
    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        for g in guidelines:
            f.write(json.dumps(g, ensure_ascii=False) + "\n")

    print(f"\nDone! Success: {success}, Failed: {failed}")
    print(f"Saved to {args.output}")


if __name__ == "__main__":
    main()
