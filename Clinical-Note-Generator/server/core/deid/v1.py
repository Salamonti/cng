import re
from typing import Any, Dict

from server.core.deid.ner_spacy import redact_person_entities


_DATE_PATTERN = re.compile(
    r"\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|"
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s+\d{4})\b",
    re.IGNORECASE,
)

_PATTERNS = {
    # Name patterns (v1.1): keep these conservative but cover common real-world forms.
    # 1) Labeled names: "Patient: John Smith", "Dr: Jane Doe"
    "name_labeled": re.compile(
        r"\b(?:patient|pt|name|doctor|dr\.?|provider)\s*[:\-]\s*"
        r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})",
        re.IGNORECASE,
    ),
    # 2) "Lastname, 52 year-old ..." or "Lastname, 52-year-old ..."
    "name_comma_age": re.compile(
        r"\b([A-Z][a-z]{2,}),\s*(\d{1,3}\s*[-]?\s*(?:y/?o|yo|years?|year)[-\s]*(?:old)?)",
        re.IGNORECASE,
    ),
    # 3) Sentence-style: "Gregory reports ...", "Sarah denies ..."
    # Only triggers for common patient-reporting verbs to avoid redacting meds/tests.
    "name_sentence_verb": re.compile(
        r"(^|[\.\n]\s*)([A-Z][a-z]{2,})\s+(reports|states|presents|presented|complains|denies|endorses|describes|notes)\b",
        re.IGNORECASE | re.MULTILINE,
    ),
    # 4) "Dr. Smith", "Dr Smith", "Dr. Jane Doe" (standalone, not just after label)
    "name_doctor": re.compile(
        r"\bDr\.?\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)",
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

    # --- Names (grouped) ---
    name_keys = ["name_labeled", "name_comma_age", "name_sentence_verb", "name_doctor"]
    raw_has_name = any(_PATTERNS[k].search(raw) for k in name_keys)

    name_total = 0

    # 1) Lastname, 52-year-old
    redacted, n = _PATTERNS["name_comma_age"].subn(r"[NAME_REDACTED], \2", redacted)
    name_total += int(n)

    # 2) Gregory reports ...
    redacted, n = _PATTERNS["name_sentence_verb"].subn(r"\1[NAME_REDACTED] \3", redacted)
    name_total += int(n)

    # 3) Dr. Smith / Dr Jane Doe
    redacted, n = _PATTERNS["name_doctor"].subn(r"Dr. [NAME_REDACTED]", redacted)
    name_total += int(n)

    # 4) Patient: John Smith
    redacted, n = _PATTERNS["name_labeled"].subn(_REPLACEMENTS["name"], redacted)
    name_total += int(n)

    residual_name = any(_PATTERNS[k].search(redacted) for k in name_keys)

    counts["name"] = name_total
    leak_flags["raw_has_name"] = bool(raw_has_name)
    leak_flags["residual_name"] = bool(residual_name)

    # --- Other PHI types ---
    for key in ["date", "mrn", "phone", "email"]:
        pattern = _PATTERNS[key]
        leak_flags[f"raw_has_{key}"] = bool(pattern.search(raw))
        redacted, n = pattern.subn(_REPLACEMENTS[key], redacted)
        counts[key] = int(n)
        leak_flags[f"residual_{key}"] = bool(pattern.search(redacted))

    # --- Optional NER layer (spaCy PERSON entities) ---
    redacted, ner_meta = redact_person_entities(redacted)
    leak_flags["ner_ran"] = bool(ner_meta.get("ner_ran", False))
    if ner_meta.get("ner_error"):
        leak_flags["ner_error"] = True
    counts["name_ner"] = int(ner_meta.get("ner_person_redactions", 0))
    counts["name"] = int(counts.get("name", 0)) + counts["name_ner"]

    leak_flags["raw_has_any"] = any(v for k, v in leak_flags.items() if k.startswith("raw_has_"))
    leak_flags["residual_any"] = any(v for k, v in leak_flags.items() if k.startswith("residual_"))

    return {
        "text": redacted,
        "redaction_counts": counts,
        "leak_flags": leak_flags,
    }

