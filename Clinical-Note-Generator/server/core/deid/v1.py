import re
from typing import Any, Dict


_DATE_PATTERN = re.compile(
    r"\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|"
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s+\d{4})\b",
    re.IGNORECASE,
)

_PATTERNS = {
    "name": re.compile(
        r"\b(?:patient|pt|name|doctor|dr\.?|provider)\s*[:\-]\s*"
        r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})",
        re.IGNORECASE,
    ),
    "date": _DATE_PATTERN,
    "mrn": re.compile(
        r"\b(?:MRN|HCN|PHN|Patient\s*ID|Chart\s*ID)\s*[:#-]?\s*[A-Z0-9-]{4,}\b",
        re.IGNORECASE,
    ),
    "phone": re.compile(r"\b(?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)\d{3}[-.\s]?\d{4}\b"),
    "email": re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE),
}

_REPLACEMENTS = {
    "name": "[NAME_REDACTED]",
    "date": "[DATE_REDACTED]",
    "mrn": "[MRN_REDACTED]",
    "phone": "[PHONE_REDACTED]",
    "email": "[EMAIL_REDACTED]",
}


def deidentify_text(text: str) -> Dict[str, Any]:
    raw = text or ""
    redacted = raw
    counts: Dict[str, int] = {}
    leak_flags: Dict[str, bool] = {}

    for key, pattern in _PATTERNS.items():
        leak_flags[f"raw_has_{key}"] = bool(pattern.search(raw))
        redacted, n = pattern.subn(_REPLACEMENTS[key], redacted)
        counts[key] = int(n)
        leak_flags[f"residual_{key}"] = bool(pattern.search(redacted))

    leak_flags["raw_has_any"] = any(v for k, v in leak_flags.items() if k.startswith("raw_has_"))
    leak_flags["residual_any"] = any(v for k, v in leak_flags.items() if k.startswith("residual_"))

    return {
        "text": redacted,
        "redaction_counts": counts,
        "leak_flags": leak_flags,
    }

