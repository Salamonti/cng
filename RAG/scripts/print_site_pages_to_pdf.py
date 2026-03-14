# scripts/print_site_pages_to_pdf.py
#!/usr/bin/env python3
"""
Print guideline web pages to PDF via headless Chromium (Playwright).

Use when a site is a JavaScript SPA or doesn't expose direct PDF links.

It crawls within the same domain and under the base path, discovers candidate
guideline pages, and prints each page to a PDF saved in ./local_guidelines (or
the --out directory). Supports optional interactive login once and reusing the
saved storage state for subsequent runs.

Prereqs
  pip install playwright
  playwright install chromium

Usage
  python scripts/print_site_pages_to_pdf.py \
    --base https://thrombosiscanada.ca/hcp/practice/clinical_guides \
    --out ./local_guidelines --depth 2 --max-pages 200 --interactive

Notes
  - If the site requires authentication, run with --interactive the first time,
    complete login manually, then the script saves storage state to --storage.
    Next runs can be headless using the saved storage state.
"""
from __future__ import annotations

import argparse
import asyncio
import re
from pathlib import Path
from typing import List, Set, Tuple
from urllib.parse import urlparse, urljoin

from playwright.async_api import async_playwright


def sanitize_name(s: str) -> str:
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", s)
    return s.strip("._-") or "page"


def in_scope(url: str, base_netloc: str, base_path: str) -> bool:
    u = urlparse(url)
    if u.netloc and u.netloc != base_netloc:
        return False
    return u.path.startswith(base_path)


async def extract_links(page, url: str) -> List[str]:
    # Return absolute hrefs found on the current page
    # Use DOM evaluation to capture in-SPA links as well
    anchors = await page.eval_on_selector_all(
        "a",
        "els => els.map(a => a.getAttribute('href')).filter(Boolean)",
    )
    out: List[str] = []
    for href in anchors or []:
        if href.startswith("javascript:") or href.startswith("#"):
            continue
        out.append(urljoin(url, href))
    # de-duplicate while preserving order
    seen = set()
    uniq: List[str] = []
    for u in out:
        if u not in seen:
            seen.add(u)
            uniq.append(u)
    return uniq


def is_candidate_detail(url: str, base_path: str) -> bool:
    # Heuristic: include pages under base_path that are not the exact listing base
    p = urlparse(url).path.rstrip("/")
    bp = base_path.rstrip("/")
    if not p.startswith(bp):
        return False
    if p == bp:
        return False
    # Exclude clear non-guide sections
    if any(seg in p for seg in ["sign_in", "account", "search", "admin", "preferences"]):
        return False
    return True


async def dismiss_disclaimer(page) -> None:
    """Best-effort: click common consent/OK buttons and accept dialogs."""
    try:
        # Try common role-based buttons
        import re as _re
        for pat in [
            r"^i\s*agree$", r"^i\s*understand$", r"^accept$", r"^ok$", r"^continue$", r"^proceed$",
        ]:
            try:
                btn = page.get_by_role("button", name=_re.compile(pat, _re.I)).first
                if await btn.is_visible(timeout=500):
                    await btn.click(timeout=800)
                    await page.wait_for_timeout(200)
            except Exception:
                pass
        # Generic locator fallbacks
        for sel in [
            "button:has-text('I Agree')",
            "button:has-text('I understand')",
            "button:has-text('OK')",
            "text=I Agree",
            "text=I understand",
            "text=OK",
        ]:
            try:
                loc = page.locator(sel).first
                if await loc.is_visible(timeout=500):
                    await loc.click(timeout=800)
                    await page.wait_for_timeout(200)
            except Exception:
                pass
    except Exception:
        pass


async def crawl_and_print(base_url: str, out_dir: Path, depth: int, max_pages: int, storage: Path | None, interactive: bool):
    out_dir.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=not interactive)
        context_kwargs = {}
        if storage and storage.exists():
            context_kwargs["storage_state"] = str(storage)
        context = await browser.new_context(**context_kwargs)
        page = await context.new_page()

        # Global dialog handler: auto-accept JS alerts/confirms
        page.on("dialog", lambda d: asyncio.create_task(d.accept()))

        # If interactive and no storage yet, allow user to login once
        if interactive and (not storage or not storage.exists()):
            await page.goto(base_url, wait_until="networkidle")
            print("[INFO] Please complete any required login in the opened window.")
            print("       Press Enter here to continue after login is complete...")
            input()
            if storage:
                await context.storage_state(path=str(storage))

        parsed = urlparse(base_url)
        base_netloc = parsed.netloc
        base_path = parsed.path or "/"

        queue: List[Tuple[str, int]] = [(base_url, 0)]
        visited: Set[str] = set()
        pages_seen = 0
        printed = 0

        while queue and pages_seen < max_pages:
            url, d = queue.pop(0)
            norm = url.split("#")[0]
            if norm in visited:
                continue
            visited.add(norm)
            pages_seen += 1
            try:
                await page.goto(norm, wait_until="domcontentloaded")
                # Give SPA some time to render initial content
                await page.wait_for_timeout(800)
                # Attempt to dismiss any disclaimer/consent modals
                await dismiss_disclaimer(page)
            except Exception as e:
                print(f"[WARN] Skip {norm}: {e}")
                continue

            # Collect links
            try:
                links = await extract_links(page, norm)
            except Exception:
                links = []

            # Queue next-level pages
            if d < depth:
                for href in links:
                    if in_scope(href, base_netloc, base_path):
                        queue.append((href, d + 1))

            # Decide if this is a candidate detail page and print
            if is_candidate_detail(norm, base_path):
                fname = sanitize_name(Path(urlparse(norm).path).name or "page") + ".pdf"
                out_path = out_dir / fname
                try:
                    await page.emulate_media(media="print")
                    # One more attempt to clear modals before print
                    await dismiss_disclaimer(page)
                    await page.pdf(path=str(out_path), print_background=True, format="A4", margin={"top": "12mm", "bottom": "12mm", "left": "12mm", "right": "12mm"})
                    printed += 1
                    print(f"[OK] Printed {norm} -> {out_path}")
                except Exception as e:
                    print(f"[WARN] Failed to print {norm}: {e}")

        await context.close()
        await browser.close()
        print(f"Done. Visited: {pages_seen}, Printed: {printed}, Saved to: {out_dir}")


def main():
    ap = argparse.ArgumentParser(description="Print guideline web pages to PDF using Playwright")
    ap.add_argument("--base", required=True, help="Base listing URL (within which to crawl)")
    ap.add_argument("--out", default="./local_guidelines", help="Output directory for PDFs")
    ap.add_argument("--depth", type=int, default=2, help="Crawl depth (beyond base page)")
    ap.add_argument("--max-pages", type=int, default=200, help="Max pages to visit")
    ap.add_argument("--storage", default="./.playwright_storage.json", help="Storage state file for login reuse")
    ap.add_argument("--interactive", action="store_true", help="Open headed browser to allow manual login")
    args = ap.parse_args()

    out_dir = Path(args.out)
    storage = Path(args.storage) if args.storage else None
    asyncio.run(crawl_and_print(args.base, out_dir, args.depth, args.max_pages, storage, args.interactive))


if __name__ == "__main__":
    main()
