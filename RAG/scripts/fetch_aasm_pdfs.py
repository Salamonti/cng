# scripts/fetch_aasm_pdfs.py
#!/usr/bin/env python3
"""
Fetch guideline PDFs from AASM practice guidelines page into ./local_guidelines.

URL: https://aasm.org/clinical-resources/practice-standards/practice-guidelines/

Behavior
- Parses the page HTML for any href ending in .pdf
- Also handles 'members-only-resource/?url=...' links by extracting the underlying PDF URL
- Downloads PDFs to the output directory with sanitized filenames

Usage
  python scripts/fetch_aasm_pdfs.py --out ./local_guidelines
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlparse, parse_qs, urljoin, unquote

import requests

BASE = "https://aasm.org/clinical-resources/practice-standards/practice-guidelines/"
HEADERS = {
    "User-Agent": "RAG-PDF-Fetcher/1.0 (+contact@example.local)",
    "Accept": "text/html,application/xhtml+xml,application/pdf",
}


def sanitize_name(name: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name)
    return name.strip("._-") or "file"


def extract_pdf_links(html: str, base: str) -> list[str]:
    # regex href="...pdf"
    links = re.findall(r"href=\"([^\"]+\.pdf)\"", html, flags=re.IGNORECASE)
    # normalize and unique
    norm: list[str] = []
    seen = set()
    for href in links:
        href = href.strip()
        # unwrap members-only-resource param
        if "members-only-resource" in href:
            try:
                u = urlparse(href)
                qs = parse_qs(u.query)
                if "url" in qs and qs["url"]:
                    href = unquote(qs["url"][0])
            except Exception:
                pass
        if not href.lower().startswith("http"):
            href = urljoin(base, href)
        if href not in seen:
            seen.add(href)
            norm.append(href)
    return norm


def download_pdf(session: requests.Session, url: str, out_dir: Path) -> Path | None:
    try:
        r = session.get(url, headers=HEADERS, timeout=60, stream=True)
        r.raise_for_status()
        ctype = r.headers.get("Content-Type", "").lower()
        if "pdf" not in ctype and not url.lower().endswith(".pdf"):
            return None
        name = sanitize_name(Path(urlparse(url).path).name or "file.pdf")
        if not name.lower().endswith(".pdf"):
            name += ".pdf"
        out_dir.mkdir(parents=True, exist_ok=True)
        fp = out_dir / name
        with open(fp, "wb") as f:
            for chunk in r.iter_content(1024 * 64):
                if not chunk:
                    break
                f.write(chunk)
        return fp
    except Exception as e:
        sys.stderr.write(f"Failed {url}: {e}\n")
        return None


def main():
    ap = argparse.ArgumentParser(description="Fetch AASM guideline PDFs")
    ap.add_argument("--out", default="./local_guidelines", help="Output directory")
    args = ap.parse_args()

    sess = requests.Session()
    sess.headers.update(HEADERS)
    r = sess.get(BASE, timeout=60)
    r.raise_for_status()
    html = r.text
    links = extract_pdf_links(html, base=BASE)
    print(f"Found {len(links)} PDF href candidates")
    out_dir = Path(args.out)
    downloaded = 0
    for url in links:
        fp = download_pdf(sess, url, out_dir)
        if fp is not None:
            downloaded += 1
            print(f"[OK] {url} -> {fp}")
        else:
            print(f"[SKIP] {url}")
    print(f"Done. Downloaded {downloaded}/{len(links)} into {out_dir.resolve()}")


if __name__ == "__main__":
    main()

