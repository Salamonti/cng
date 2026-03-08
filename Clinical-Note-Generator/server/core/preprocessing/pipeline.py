from __future__ import annotations

import re
from collections import deque
from typing import Dict, Optional

from .constants import (
    BOILERPLATE_LINE_PATTERNS,
    DATE_STAMP_ONLY,
    HEADER_CANDIDATE_PATTERNS,
    JUNK_LINE_PATTERNS,
    MULTINEWLINE_RE,
)


class PreprocessingPipeline:
    def __init__(self, cfg: Optional[Dict] = None):
        preprocessing = (cfg or {}).get("preprocessing") or {}
        self.enabled = bool(preprocessing.get("enabled", False))
        steps = preprocessing.get("steps") or {}
        self.steps = {
            "remove_boilerplate": bool(steps.get("remove_boilerplate", True)),
            "collapse_repeated_headers": bool(steps.get("collapse_repeated_headers", True)),
            "remove_junk_artifacts": bool(steps.get("remove_junk_artifacts", True)),
            "deduplicate_blocks": bool(steps.get("deduplicate_blocks", True)),
            "normalize_whitespace": bool(steps.get("normalize_whitespace", True)),
        }

    def process(self, text: str) -> str:
        if not text:
            return ""

        out = text
        if self.steps["remove_boilerplate"]:
            out = self.remove_boilerplate(out)
        if self.steps["collapse_repeated_headers"]:
            out = self.collapse_repeated_headers(out)
        if self.steps["remove_junk_artifacts"]:
            out = self.remove_junk_artifacts(out)
        if self.steps["deduplicate_blocks"]:
            out = self.deduplicate_near_identical_blocks(out)
        if self.steps["normalize_whitespace"]:
            out = self.normalize_whitespace(out)
        return out.strip()

    def normalize_whitespace(self, text: str) -> str:
        if not text:
            return ""
        normalized_lines = []
        for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
            normalized_lines.append(re.sub(r"[ \t]+", " ", line).strip())
        out = "\n".join(normalized_lines)
        out = MULTINEWLINE_RE.sub("\n\n", out)
        return out.strip()

    def remove_boilerplate(self, text: str) -> str:
        kept = []
        for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
            stripped = line.strip()
            if not stripped:
                kept.append("")
                continue
            if DATE_STAMP_ONLY.match(stripped):
                continue
            if any(rx.search(stripped) for rx in BOILERPLATE_LINE_PATTERNS):
                continue
            if re.fullmatch(r"[-=*#_]{3,}", stripped):
                continue
            kept.append(line)
        return "\n".join(kept)

    def collapse_repeated_headers(self, text: str, window: int = 10) -> str:
        lines = text.splitlines()
        recent = deque(maxlen=window)
        out = []

        for line in lines:
            stripped = line.strip()
            if not stripped:
                out.append("")
                continue

            normalized = re.sub(r"\s+", " ", stripped.lower())
            is_headerish = any(rx.search(stripped) for rx in HEADER_CANDIDATE_PATTERNS)

            if is_headerish and normalized in recent:
                continue

            recent.append(normalized)
            out.append(line)

        return "\n".join(out)

    def remove_junk_artifacts(self, text: str) -> str:
        out = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                out.append("")
                continue
            if any(rx.match(stripped) for rx in JUNK_LINE_PATTERNS):
                continue
            if not any(ch.isalnum() for ch in stripped):
                continue
            out.append(line)
        return "\n".join(out)

    def deduplicate_near_identical_blocks(self, text: str, window: int = 5) -> str:
        blocks = [b for b in re.split(r"\n\s*\n", text) if b.strip()]
        if not blocks:
            return ""

        recent_hashes = deque(maxlen=window)
        kept = []
        for block in blocks:
            norm = re.sub(r"\s+", " ", block).strip().lower()
            key = norm[:80]
            if key in recent_hashes:
                continue
            recent_hashes.append(key)
            kept.append(block.strip())

        return "\n\n".join(kept)
