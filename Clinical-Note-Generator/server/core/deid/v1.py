import re
from typing import Any, Dict

from server.core.deid.ner_spacy import redact_person_entities


#: P3-2: the old pattern only covered ISO, numeric, and month-first
#: ("January 1, 2024") dates -- day-first month-name dates ("25 January
#: 2024") and ordinal suffixes ("1st"/"25th") matched nothing at all and
#: leaked through un-redacted. Day-first with a month NAME is the standard
#: Canadian/British form; ordinals show up constantly in spoken dictation
#: ("the twenty-fifth" transcribes as "25th").
_MONTH_NAME = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*"
_ORDINAL_SUFFIX = r"(?:st|nd|rd|th)?"
_DATE_PATTERN = re.compile(
    r"\b(?:"
    r"\d{4}-\d{2}-\d{2}"
    r"|\d{1,2}[/-]\d{1,2}[/-]\d{2,4}"
    rf"|{_MONTH_NAME}\s+\d{{1,2}}{_ORDINAL_SUFFIX},?\s+\d{{4}}"
    rf"|\d{{1,2}}{_ORDINAL_SUFFIX}\s+(?:of\s+)?{_MONTH_NAME}\s*,?\s*\d{{4}}"
    r")\b",
    re.IGNORECASE,
)

#: De-id incident (2026-08): every one of these name_* patterns except
#: name_labeled captured only a SINGLE Title-Case word, on the assumption
#: that names in these contexts are one word. In practice multi-word real
#: names ("Roger Blithroy Nickerson", "Mary Marguerite Amero") only had
#: their LAST word caught -- the leading word(s) leaked through completely
#: unredacted. This was found repeatedly in production data via manual
#: audit; extending each pattern to swallow up to two additional
#: Title-Case words (matching name_labeled's existing {0,2} behaviour)
#: closes that gap. name_doctor additionally needed to tolerate a bare
#: middle initial ("Dr. Islam R Eissa"), which no [A-Z][a-z]+ token can
#: ever match since it requires a lowercase letter after the capital.
_EXTRA_NAME_WORDS = r"(?:\s+[A-Z][a-z]{1,}){0,2}"
_MIDDLE_INITIAL = r"(?:\s+[A-Z]\.?)?"

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
        rf"\b([A-Z][a-z]{{2,}}{_EXTRA_NAME_WORDS}),\s*(\d{{1,3}}\s*[-]?\s*(?:y/?o|yo|years?|year)[-\s]*(?:old)?)",
        re.IGNORECASE,
    ),
    # 3) Sentence-style: "Gregory reports ...", "Sarah denies ..."
    # Only triggers for common patient-reporting verbs to avoid redacting meds/tests.
    "name_sentence_verb": re.compile(
        rf"(^|[\.\n]\s*)([A-Z][a-z]{{2,}}{_EXTRA_NAME_WORDS})\s+(reports|states|presents|presented|complains|denies|endorses|describes|notes)\b",
        re.IGNORECASE | re.MULTILINE,
    ),
    # 4) "Dr. Smith", "Dr Smith", "Dr. Jane Doe" (standalone, not just after label)
    # P3-2: this was the one name pattern missing re.IGNORECASE -- ASR
    # transcripts of spoken dictation routinely produce all-lowercase text
    # ("dr smith ordered..."), and every other name_* pattern here already
    # handles that via IGNORECASE; this one silently didn't.
    "name_doctor": re.compile(
        rf"\bDr\.?\s+([A-Z][a-z]+{_MIDDLE_INITIAL}(?:\s+[A-Z][a-z]+)?)",
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


def _regex_name_variants(text: str) -> set:
    """Fast, deterministic: original name surface forms matched by the regex
    patterns (no NER). Reuses _PATTERNS so it never diverges from
    deidentify_text."""
    raw = text or ""
    names: set = set()
    for key in ("name_labeled", "name_comma_age", "name_sentence_verb", "name_doctor"):
        for m in _PATTERNS[key].finditer(raw):
            # name_labeled/name_comma_age/name_doctor -> group(1);
            # name_sentence_verb -> group(2) (group(1) is the leading boundary).
            g = m.group(2) if key == "name_sentence_verb" else m.group(1)
            if g:
                names.add(g.strip())
    return {n for n in names if len(n) >= 3}


def extract_name_variants(text: str) -> set:
    """Return the set of original name surface forms that deidentify_text would
    redact in this text (regex patterns + spaCy PERSON entities when NER is
    available).

    Used for cross-field consistency (see redact_known_names): the same patient
    name appears across multiple source blocks (transcription, prior visits,
    labs/imaging). Per-field de-id only catches names in specific syntactic
    forms, so a name redacted in one block can leak in another. We collect the
    surface forms here and redact them everywhere.
    """
    names = _regex_name_variants(text)
    try:
        from server.core.deid.ner_spacy import _load_nlp, ner_enabled

        if ner_enabled():
            doc = _load_nlp()(text or "")
            for ent in doc.ents:
                if ent.label_ == "PERSON" and (ent.end_char - ent.start_char) >= 3:
                    names.add(ent.text.strip())
    except Exception:
        pass
    return {n for n in names if len(n) >= 3}


def redact_known_names(text: str, names: set) -> tuple:
    """Replace any occurrence of the given name surface forms with
    [NAME_REDACTED]. Case-insensitive, word-bounded. Returns (text, count).

    Only propagates names that are 2+ words (contain a space) or were captured
    by a high-confidence labeled pattern -- single common words are too risky
    to redact globally. Longer names are replaced first so a full name is not
    partially consumed by a shorter one.
    """
    if not names:
        return text, 0
    safe = {n for n in names if n and (" " in n or len(n) >= 4)}
    if not safe:
        return text, 0
    count = 0
    for name in sorted(safe, key=len, reverse=True):
        pattern = re.compile(r"\b" + re.escape(name) + r"\b", re.IGNORECASE)
        text, n = pattern.subn("[NAME_REDACTED]", text)
        count += n
    return text, count


def deidentify_fields(fields: Dict[str, str]) -> Dict[str, Dict[str, Any]]:
    """De-identify a set of related text fields with cross-field name
    consistency.

    Each field is de-identified independently (regex + NER), then every name
    surface form detected in ANY field is redacted in ALL fields. This closes
    the partial-redaction defect where the same patient name was redacted in
    one block (e.g. "Patient: John Smith" in the transcription) but leaked in
    another (e.g. "John Smith reports..." in a prior-visit note that the
    per-field patterns/NER didn't catch).

    Returns {field_key: deidentify_text-shaped result} with the propagated
    redactions folded into each field's text and redaction_counts["name"].
    """
    # Pass 1: independent de-id + collect name surface forms (regex + NER,
    # so a name caught only by NER in one field still propagates to others).
    results: Dict[str, Dict[str, Any]] = {}
    known_names: set = set()
    for key, raw in fields.items():
        res = deidentify_text(raw or "")
        results[key] = res
        known_names |= extract_name_variants(raw or "")

    if not known_names:
        return results

    # Pass 2: propagate every detected name into every field.
    for key, res in results.items():
        new_text, n = redact_known_names(res["text"], known_names)
        if n:
            res["text"] = new_text
            counts = res.setdefault("redaction_counts", {})
            counts["name"] = int(counts.get("name", 0)) + n
            counts["name_cross_field"] = int(counts.get("name_cross_field", 0)) + n
    return results


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

    counts["name"] = name_total
    leak_flags["raw_has_name"] = bool(raw_has_name)

    # --- Other PHI types ---
    for key in ["date", "mrn", "phone", "email"]:
        pattern = _PATTERNS[key]
        leak_flags[f"raw_has_{key}"] = bool(pattern.search(raw))
        redacted, n = pattern.subn(_REPLACEMENTS[key], redacted)
        counts[key] = int(n)
        leak_flags[f"residual_{key}"] = bool(pattern.search(redacted))

    # --- Optional NER layer (spaCy PERSON entities) ---
    # De-id incident (2026-08): residual_name used to be computed here,
    # BEFORE this NER pass ran -- meaning it could never reflect anything
    # NER found (or failed to find), regardless of whether NER succeeded,
    # errored, or left names behind. Moved below so it checks the text
    # NER actually produced.
    redacted, ner_meta = redact_person_entities(redacted)
    leak_flags["ner_ran"] = bool(ner_meta.get("ner_ran", False))
    if ner_meta.get("ner_error"):
        leak_flags["ner_error"] = True
    counts["name_ner"] = int(ner_meta.get("ner_person_redactions", 0))
    counts["name"] = int(counts.get("name", 0)) + counts["name_ner"]

    # residual_name now reflects the FINAL text (post-regex AND post-NER),
    # which is what "did we actually miss something" needs to mean.
    residual_name = any(_PATTERNS[k].search(redacted) for k in name_keys)
    leak_flags["residual_name"] = bool(residual_name)

    leak_flags["raw_has_any"] = any(v for k, v in leak_flags.items() if k.startswith("raw_has_"))
    leak_flags["residual_any"] = any(v for k, v in leak_flags.items() if k.startswith("residual_"))

    return {
        "text": redacted,
        "redaction_counts": counts,
        "leak_flags": leak_flags,
    }
