#!/usr/bin/env python3
import json
import os
import sys
import urllib.parse
import urllib.request

BASE_URL = os.getenv("SEARXNG_PROXY_URL", "https://ieissa.com:3443/searxng/search")
API_KEY = os.getenv("SEARXNG_API_KEY", "")


def main():
    json_mode = False
    if len(sys.argv) < 2:
        print("Usage: searx_search.py [--json] <query> [limit]", file=sys.stderr)
        sys.exit(1)

    args = sys.argv[1:]
    if args and args[0] == "--json":
        json_mode = True
        args = args[1:]

    if len(args) < 1:
        print("Usage: searx_search.py [--json] <query> [limit]", file=sys.stderr)
        sys.exit(1)

    query = args[0]
    limit = args[1] if len(args) > 1 else "5"

    if not API_KEY:
        print("Error: SEARXNG_API_KEY is not set", file=sys.stderr)
        sys.exit(2)

    params = urllib.parse.urlencode({"q": query})
    url = f"{BASE_URL}?{params}"

    req = urllib.request.Request(url)
    req.add_header("X-API-Key", API_KEY)
    req.add_header("Accept", "application/json")

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception as e:
        print(f"Request failed: {e}", file=sys.stderr)
        sys.exit(3)

    if json_mode:
        print(json.dumps(data, ensure_ascii=False))
        return

    results = data.get("results", [])[: int(limit)]
    if not results:
        print("No results")
        return

    for i, r in enumerate(results, start=1):
        title = (r.get("title") or "(no title)").strip()
        link = r.get("url") or ""
        engine = r.get("engine") or ""
        print(f"{i}. {title}")
        if link:
            print(f"   {link}")
        if engine:
            print(f"   [{engine}]")


if __name__ == "__main__":
    main()
