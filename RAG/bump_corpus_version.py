#!/usr/bin/env python3
"""Bump corpus_version in settings.yaml after a successful weekly rebuild.

retriever.py's and query_api.py's result caches key on corpus_version to
know when previously-cached answers are stale -- but nothing ever wrote
this value, so it sat at whatever it was initialized to forever, and
those caches were effectively never invalidated by real corpus updates
(only by their own LRU eviction or a process restart). This is the
missing write side of that mechanism, run as the last step of
scripts/weekly_run.sh once the index rebuild has actually happened.

Does a targeted line replacement rather than a full yaml.safe_load +
yaml.dump round-trip so settings.yaml's comments and formatting survive
untouched -- PyYAML's dumper does not preserve either.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

SETTINGS_PATH = Path(__file__).resolve().parent / "settings.yaml"
PATTERN = re.compile(r"^(corpus_version:\s*)(\d+)(\s*.*)$", re.MULTILINE)


def main() -> int:
    text = SETTINGS_PATH.read_text(encoding="utf-8")
    match = PATTERN.search(text)
    if not match:
        print(f"ERROR: no 'corpus_version: N' line found in {SETTINGS_PATH}", file=sys.stderr)
        return 1

    old_version = int(match.group(2))
    new_version = old_version + 1
    new_text = PATTERN.sub(lambda m: f"{m.group(1)}{new_version}{m.group(3)}", text, count=1)
    SETTINGS_PATH.write_text(new_text, encoding="utf-8")
    print(f"corpus_version: {old_version} -> {new_version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
