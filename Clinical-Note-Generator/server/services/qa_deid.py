import re
from typing import Dict, Tuple

_PATTERNS = {
    "email": re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I),
    "phone": re.compile(r"\b(?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)\d{3}[-.\s]?\d{4}\b"),
    "mrn": re.compile(r"\b(?:MRN|HCN|PHN|Patient\s*ID)\s*[:#-]?\s*[A-Z0-9-]{4,}\b", re.I),
    "dob": re.compile(r"\b(?:DOB|Date of Birth)\s*[:#-]?\s*\d{4}-\d{2}-\d{2}\b", re.I),
}

_NAME_HINT_RE = re.compile(r"\b(?:patient|doctor|dr\.?|hospital)\s*[:\-]\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})")


def deidentify_text(text: str) -> Tuple[str, Dict[str, int]]:
    t = text or ""
    counts: Dict[str, int] = {}
    for key, rx in _PATTERNS.items():
        t, n = rx.subn(f"[{key.upper()}_REDACTED]", t)
        counts[key] = n

    # Simple name hint masking for labeled fields
    def _mask_name(m: re.Match) -> str:
        return m.group(0).replace(m.group(1), "[NAME_REDACTED]")

    t, n_name = _NAME_HINT_RE.subn(_mask_name, t)
    counts["name_hint"] = n_name
    return t, counts
