# scripts/fetch_site_pdfs.py
#!/usr/bin/env python3
"""
Fetch all clinical guideline PDFs from a website into ./local_guidelines.

Default target: https://thrombosiscanada.ca/hcp/practice/clinical_guides

Behavior
- Crawls within the base path and domain (depth-limited BFS) and collects all links ending in .pdf
- Downloads PDFs with a reasonable timeout and User-Agent
- Skips duplicates and preserves original filenames when possible

Usage
  python scripts/fetch_site_pdfs.py \
      --base https://thrombosiscanada.ca/hcp/practice/clinical_guides \
      --out ./local_guidelines
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import json
from pathlib import Path
from typing import List, Set, Tuple
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "RAG-PDF-Fetcher/1.0 (+contact@example.local)",
    "Accept": "text/html,application/xhtml+xml,application/pdf",
}


def is_same_scope(url: str, base_netloc: str, base_path: str) -> bool:
    u = urlparse(url)
    if u.netloc and u.netloc != base_netloc:
        return False
    # Only allow within the base path prefix
    return u.path.startswith(base_path)


def fetch_html(session: requests.Session, url: str, timeout: int = 20) -> str:
    r = session.get(url, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    if "text/html" not in r.headers.get("Content-Type", "") and r.text.strip() == "":
        return ""
    return r.text


def discover_links(session: requests.Session, page_url: str) -> Tuple[List[str], List[str]]:
    """Return (pdf_links, page_links) discovered on page_url."""
    html = fetch_html(session, page_url)
    if not html:
        return [], []
    soup = BeautifulSoup(html, "html.parser")
    pdfs: List[str] = []
    pages: List[str] = []
    for a in soup.find_all("a"):
        href = a.get("href") or ""
        if not href or href.startswith("#"):
            continue
        absu = urljoin(page_url, href)
        if absu.lower().endswith(".pdf"):
            pdfs.append(absu)
        else:
            pages.append(absu)
    # dedupe
    pdfs = list(dict.fromkeys(pdfs))
    pages = list(dict.fromkeys(pages))
    return pdfs, pages


def sanitize_name(name: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name)
    return name.strip("._-") or "file"


def choose_filename(url: str, out_dir: Path) -> Path:
    base = sanitize_name(Path(urlparse(url).path).name or "file.pdf")
    if not base.lower().endswith(".pdf"):
        base += ".pdf"
    p = out_dir / base
    if not p.exists():
        return p
    # Avoid overwrite: add numeric suffix
    stem = p.stem
    suffix = p.suffix
    for i in range(2, 1000):
        cand = out_dir / f"{stem}_{i}{suffix}"
        if not cand.exists():
            return cand
    return p


def download_pdf(session: requests.Session, url: str, out_dir: Path, timeout: int = 60) -> Path | None:
    try:
        r = session.get(url, headers=HEADERS, timeout=timeout, stream=True)
        r.raise_for_status()
        ctype = r.headers.get("Content-Type", "").lower()
        if "pdf" not in ctype and not url.lower().endswith(".pdf"):
            return None
        out_dir.mkdir(parents=True, exist_ok=True)
        fp = choose_filename(url, out_dir)
        with open(fp, "wb") as f:
            for chunk in r.iter_content(1024 * 64):
                if not chunk:
                    break
                f.write(chunk)
        return fp
    except Exception as e:
        sys.stderr.write(f"Failed to download {url}: {e}\n")
        return None


def crawl_and_download(base_url: str, out_dir: Path, max_pages: int = 800, max_depth: int = 3) -> Tuple[int, int]:
    sess = requests.Session()
    sess.headers.update(HEADERS)

    parsed = urlparse(base_url)
    base_netloc = parsed.netloc
    base_path = parsed.path.rstrip("/") or "/"

    queue: List[Tuple[str, int]] = [(base_url, 0)]
    visited: Set[str] = set()
    found_pdfs: Set[str] = set()
    pages_seen = 0
    downloaded = 0

    while queue and pages_seen < max_pages:
        url, depth = queue.pop(0)
        norm = url.split("#")[0]
        if norm in visited:
            continue
        visited.add(norm)
        pages_seen += 1
        try:
            pdfs, pages = discover_links(sess, norm)
        except Exception as e:
            sys.stderr.write(f"Skip {norm}: {e}\n")
            continue

        for p in pdfs:
            if is_same_scope(p, base_netloc, base_path) and p not in found_pdfs:
                found_pdfs.add(p)

        if depth < max_depth:
            for p in pages:
                if is_same_scope(p, base_netloc, base_path):
                    queue.append((p, depth + 1))

    for p in sorted(found_pdfs):
        fp = download_pdf(sess, p, out_dir)
        if fp is not None:
            downloaded += 1

    return len(found_pdfs), downloaded


def main():
    ap = argparse.ArgumentParser(description="Fetch guideline PDFs from a website")
    ap.add_argument("--base", default="https://thrombosiscanada.ca/hcp/practice/clinical_guides", help="Base URL to crawl")
    ap.add_argument("--out", default="./local_guidelines", help="Output directory for PDFs")
    ap.add_argument("--depth", type=int, default=3, help="Crawl depth")
    ap.add_argument("--max-pages", type=int, default=800, help="Max pages to visit")
    args = ap.parse_args()

    out_dir = Path(args.out)
    total, downloaded = crawl_and_download(args.base, out_dir, max_pages=args.max_pages, max_depth=args.depth)
    print(json.dumps({"base": args.base, "candidates": total, "downloaded": downloaded, "out": str(out_dir.resolve())}, indent=2))


if __name__ == "__main__":
    main()
