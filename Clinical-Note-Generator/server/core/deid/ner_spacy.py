"""Optional spaCy-based NER layer for de-identification.

Design goals:
- Keep existing regex de-id as the baseline (fast/deterministic).
- Add a *best-effort* PERSON redaction pass on top when enabled.
- Make it optional: if spaCy/model isn't installed, code should safely no-op.

Enable via env var:
  CNG_DEID_NER=1

Model override (optional):
  CNG_DEID_SPACY_MODEL=en_core_web_sm
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any, Dict, Tuple


def ner_enabled() -> bool:
    return os.environ.get("CNG_DEID_NER", "0").strip().lower() in {"1", "true", "yes", "on"}


@lru_cache(maxsize=1)
def _load_nlp():
    # Import lazily so environments without spaCy don't break startup.
    import spacy  # type: ignore

    model = os.environ.get("CNG_DEID_SPACY_MODEL", "en_core_web_sm").strip() or "en_core_web_sm"
    return spacy.load(model)


def redact_person_entities(text: str) -> Tuple[str, Dict[str, Any]]:
    """Redact names using spaCy.

    Primary method: NER PERSON entities.
    Fallback (when NER misses): redact 2+ consecutive Proper Nouns (PROPN), which
    catches many "First Last" strings while avoiding most medical terms.

    Returns (redacted_text, meta).
    """

    raw = text or ""

    if not ner_enabled():
        return raw, {"ner_ran": False, "ner_person_redactions": 0}

    try:
        nlp = _load_nlp()
    except Exception:
        # spaCy not installed, model missing, etc.
        return raw, {"ner_ran": False, "ner_person_redactions": 0, "ner_error": "spacy_unavailable"}

    doc = nlp(raw)

    spans = [ent for ent in doc.ents if ent.label_ == "PERSON"]

    # If the model fails to detect obvious names (this happens), fall back to
    # sequences of Proper Nouns (e.g., "Gregory Leblanc").
    used_fallback = False
    if not spans:
        used_fallback = True
        i = 0
        while i < len(doc):
            tok = doc[i]
            if tok.pos_ == "PROPN" and tok.is_alpha and tok.text[:1].isupper():
                start = i
                i += 1
                while i < len(doc):
                    t2 = doc[i]
                    if t2.pos_ == "PROPN" and t2.is_alpha and t2.text[:1].isupper():
                        i += 1
                        continue
                    break
                if (i - start) >= 2:
                    spans.append(doc[start:i])
                continue
            i += 1

    if not spans:
        return raw, {"ner_ran": True, "ner_person_redactions": 0, "ner_used_fallback": used_fallback}

    # Replace from end to start to keep indices valid.
    out = raw
    redactions = 0
    for ent in sorted(spans, key=lambda e: e.start_char, reverse=True):
        # Basic guard: avoid redacting very short tokens (often false positives)
        if (ent.end_char - ent.start_char) < 3:
            continue
        out = out[: ent.start_char] + "[NAME_REDACTED]" + out[ent.end_char :]
        redactions += 1

    return out, {
        "ner_ran": True,
        "ner_person_redactions": redactions,
        "ner_used_fallback": used_fallback,
    }
