"""Filter prompt templates: drop rows whose *title* has an accented vowel or non-Latin script."""
from __future__ import annotations

import re
import unicodedata
from typing import Tuple

_VOWELS = frozenset("aeiouyAEIOUY")

# Blocks that indicate clearly non-Latin scripts (title only).
_NON_LATIN_RE = re.compile(
    r"[\u3040-\u30ff\u3400-\u9fff"
    r"\uac00-\ud7af"
    r"\u0400-\u04ff"
    r"\u0600-\u06ff"
    r"\u0590-\u05ff"
    r"\u0e00-\u0e7f"
    r"\u0900-\u097f"
    r"]"
)


def has_non_latin_script(text: str) -> bool:
    return bool(_NON_LATIN_RE.search(text or ""))


def title_has_accented_vowel(title: str) -> bool:
    """True if the title contains a vowel with a diacritic (Unicode NFD), or a precomposed accented vowel letter."""
    if not (title or "").strip():
        return False
    # Decomposed: vowel (ASCII or not) followed by combining mark => accented vowel
    nfd = unicodedata.normalize("NFD", title)
    for i, ch in enumerate(nfd):
        if unicodedata.category(ch) != "Mn":
            continue
        if i == 0:
            continue
        prev = nfd[i - 1]
        if prev in _VOWELS:
            return True
    # Precomposed letters like U+00E9 that stay one codepoint in some NFC paths:
    for ch in title:
        if ord(ch) <= 127:
            continue
        if not ch.isalpha():
            continue
        parts = unicodedata.normalize("NFD", ch)
        if len(parts) >= 2 and parts[0].lower() in "aeiouy":
            return True
    return False


def is_english_template(title: str, prompt: str) -> bool:
    """Keep template only if title passes accent and script rules (prompt is not inspected)."""
    t = title or ""
    if not t.strip() and not (prompt or "").strip():
        return False
    if has_non_latin_script(t):
        return False
    if title_has_accented_vowel(t):
        return False
    return True


def filter_stats(rows: list[Tuple[str, str]]) -> tuple[list[Tuple[str, str]], int]:
    kept: list[Tuple[str, str]] = []
    dropped = 0
    for t, p in rows:
        if is_english_template(t, p):
            kept.append((t, p))
        else:
            dropped += 1
    return kept, dropped
