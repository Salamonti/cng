import os
import re
import threading
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_UNIT_MAP: Dict[str, str] = {
    "milligram": "mg",
    "milligrams": "mg",
    "mg": "mg",
    "microgram": "mcg",
    "micrograms": "mcg",
    "mcg": "mcg",
    "gram": "g",
    "grams": "g",
    "g": "g",
    "kilogram": "kg",
    "kilograms": "kg",
    "kg": "kg",
    "milliliter": "mL",
    "milliliters": "mL",
    "millilitre": "mL",
    "millilitres": "mL",
    "ml": "mL",
    "mL": "mL",
    "liter": "L",
    "liters": "L",
    "litre": "L",
    "litres": "L",
    "l": "L",
    "unit": "units",
    "units": "units",
}

_NUM_SMALL = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
}

_NUM_TENS = {
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
}

_WORDS = "|".join(sorted(set(list(_NUM_SMALL.keys()) + list(_NUM_TENS.keys()) + ["hundred", "thousand", "and"]), key=len, reverse=True))
_UNIT_WORDS = "|".join(sorted(_UNIT_MAP.keys(), key=len, reverse=True))
_DOSE_WORDS_RE = re.compile(rf"\b((?:{_WORDS}|[\s-])+?)\s+({_UNIT_WORDS})\b", re.IGNORECASE)
_NUMERIC_SPACE_UNIT_RE = re.compile(r"\b(\d+(?:\.\d+)?)\s*(mg|mcg|g|kg|ml|mL|l|L|units?)\b", re.IGNORECASE)
_MED_LINE_RE = re.compile(r"^(?P<prefix>\s*(?:[-*•]|\d+[.)])?\s*)(?P<name>[A-Za-z][A-Za-z0-9\-/ ]{1,80}?)(?=\s+\d+(?:\.\d+)?\s*(?:mg|mcg|g|kg|ml|mL|units?)\b)", re.IGNORECASE)



def _parse_number_words(words: str) -> Optional[int]:
    tokens = [t for t in re.split(r"[\s-]+", words.lower().strip()) if t and t != "and"]
    if not tokens:
        return None

    total = 0
    current = 0
    consumed = False

    for t in tokens:
        if t in _NUM_SMALL:
            current += _NUM_SMALL[t]
            consumed = True
        elif t in _NUM_TENS:
            current += _NUM_TENS[t]
            consumed = True
        elif t == "hundred":
            if current == 0:
                current = 1
            current *= 100
            consumed = True
        elif t == "thousand":
            if current == 0:
                current = 1
            total += current * 1000
            current = 0
            consumed = True
        else:
            return None

    if not consumed:
        return None
    return total + current


class RxNormIndex:
    def __init__(self) -> None:
        self._loaded = False
        self._lock = threading.Lock()
        self._terms: List[str] = []

    def _discover_file(self) -> Optional[Path]:
        explicit = os.environ.get("RXNORM_TERMS_FILE", "").strip()
        if explicit:
            p = Path(explicit)
            return p if p.exists() else None

        rx_dir = os.environ.get("RXNORM_DIR", "").strip()
        if rx_dir:
            p = Path(rx_dir) / "RXNCONSO.RRF"
            if p.exists():
                return p
        return None

    def _load(self) -> None:
        f = self._discover_file()
        if not f:
            self._loaded = True
            self._terms = []
            return

        terms: List[str] = []
        try:
            with f.open("r", encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    parts = line.rstrip("\n").split("|")
                    if len(parts) < 15:
                        continue
                    sab = parts[11].strip().upper() if len(parts) > 11 else ""
                    tty = parts[12].strip().upper() if len(parts) > 12 else ""
                    string = parts[14].strip()
                    if sab != "RXNORM":
                        continue
                    if tty not in {"IN", "PIN", "BN", "SCD", "SBD"}:
                        continue
                    if not string:
                        continue
                    terms.append(string)
        except Exception:
            terms = []

        # Deduplicate and limit memory footprint
        seen = set()
        out: List[str] = []
        for t in terms:
            k = t.lower()
            if k in seen:
                continue
            seen.add(k)
            out.append(t)
        self._terms = out
        self._loaded = True

    def terms(self) -> List[str]:
        if not self._loaded:
            with self._lock:
                if not self._loaded:
                    self._load()
        return self._terms

    def best_match(self, query: str, min_confidence: float = 0.93) -> Tuple[Optional[str], float]:
        q = (query or "").strip()
        if not q:
            return None, 0.0
        terms = self.terms()
        if not terms:
            return None, 0.0

        ql = q.lower()
        best_term = None
        best_score = 0.0
        for term in terms:
            score = SequenceMatcher(None, ql, term.lower()).ratio()
            if score > best_score:
                best_score = score
                best_term = term

        if best_term and best_score >= min_confidence:
            return best_term, best_score
        return None, best_score


_RXNORM = RxNormIndex()


@dataclass
class NormalizationResult:
    text: str
    unit_conversions: int
    rxnorm_replacements: int


def _replace_dose_words(match: re.Match) -> str:
    number_words = match.group(1)
    unit_word = match.group(2)
    parsed = _parse_number_words(number_words)
    if parsed is None:
        return match.group(0)
    unit = _UNIT_MAP.get(unit_word.lower(), unit_word)
    return f"{parsed} {unit}"


def normalize_numeric_units(text: str) -> Tuple[str, int]:
    if not text:
        return "", 0

    count = 0

    def repl(m: re.Match) -> str:
        nonlocal count
        replaced = _replace_dose_words(m)
        if replaced != m.group(0):
            count += 1
        return replaced

    normalized = _DOSE_WORDS_RE.sub(repl, text)

    def fix_spacing(m: re.Match) -> str:
        nonlocal count
        unit_raw = m.group(2)
        unit = _UNIT_MAP.get(unit_raw.lower(), unit_raw)
        out = f"{m.group(1)} {unit}"
        if out != m.group(0):
            count += 1
        return out

    normalized = _NUMERIC_SPACE_UNIT_RE.sub(fix_spacing, normalized)
    return normalized, count


def canonicalize_medication_lines(text: str, min_confidence: float = 0.93) -> Tuple[str, int]:
    if not text:
        return "", 0

    replacements = 0
    lines = text.splitlines()
    out_lines: List[str] = []

    for line in lines:
        m = _MED_LINE_RE.search(line)
        if not m:
            out_lines.append(line)
            continue
        name = (m.group("name") or "").strip()
        if len(name.split()) == 1 and len(name) < 5:
            out_lines.append(line)
            continue

        best, _score = _RXNORM.best_match(name, min_confidence=min_confidence)
        if not best:
            out_lines.append(line)
            continue

        start, end = m.start("name"), m.end("name")
        new_line = line[:start] + best + line[end:]
        if new_line != line:
            replacements += 1
        out_lines.append(new_line)

    return "\n".join(out_lines), replacements


def normalize_clinical_note_output(text: str) -> NormalizationResult:
    normalized, unit_conversions = normalize_numeric_units(text)
    normalized, rxnorm_replacements = canonicalize_medication_lines(normalized)
    return NormalizationResult(
        text=normalized,
        unit_conversions=unit_conversions,
        rxnorm_replacements=rxnorm_replacements,
    )
