"""Deterministic guards for runaway and unsupported clinical output."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Iterable, List


# ~8192 tokens * 4 chars/token plus margin. A single constant so the streaming
# guard, final validation, and the source-proportionality check can never drift
# apart again (they previously allowed 24000/20000/20000 respectively, which let
# a note in that band stream to completion and then be rejected).
MAX_OUTPUT_CHARS = 36_000

_WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9'/-]*")
_SENTENCE_BREAK_RE = re.compile(r"(?:[.!?](?:\s+|$)|\n+)")
_MEASUREMENT_RE = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:%|mg|mcg|g|kg|ml|l|mmol/l|mg/dl|g/l|"
    r"mmhg|bpm|cm|mm|units?|iu|meq/l|l/min|°c|°f)(?![A-Za-z])",
    re.IGNORECASE,
)
_AGE_RE = re.compile(r"\b(\d{1,3})[ -]year[ -]old\b", re.IGNORECASE)

# Spoken ages reach the transcript either as digits ("45") or as words
# ("forty-five"), depending on how Whisper rendered them. The age grounding
# check below compares the note's digits against number tokens found in the
# source, so without word->digit expansion a perfectly correct note is
# rejected whenever the clinician's speech was transcribed as words.
# Expanding the SOURCE side only makes the check more permissive, never less
# safe: a genuinely fabricated age still matches neither representation.
_UNIT_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_TENS_WORDS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}
_TENS_UNIT_RE = re.compile(
    r"\b(" + "|".join(_TENS_WORDS) + r")[\s-]+(" + "|".join(_UNIT_WORDS) + r")\b",
    re.IGNORECASE,
)
_NUMBER_WORD_RE = re.compile(
    r"\b(" + "|".join(list(_TENS_WORDS) + list(_UNIT_WORDS)) + r")\b",
    re.IGNORECASE,
)
_PLACEHOLDER_RE = re.compile(
    r"(?:\[(?:name|age|sex|gender|dob|date of birth|patient)[^\]]*\]|"
    r"\bxx[ -]year[ -]old\b|\b(?:mr|mrs|ms)\.?\s+(?:patient|unknown|unspecified)\b)",
    re.IGNORECASE,
)
_REASONING_RE = re.compile(
    r"</?(?:think|analysis|redacted_thinking)>|\b(?:chain of thought|my reasoning)\b",
    re.IGNORECASE,
)
_META_FAILURE_RE = re.compile(
    r"\b(?:I (?:will|shall) (?:now )?(?:write|generate|create)|"
    r"here is (?:the|your) (?:note|document)|token (?:budget|limit)|"
    r"maximum (?:output|token)|as an ai language model)\b",
    re.IGNORECASE,
)
_GENDER_TERMS_RE = re.compile(
    r"\b(?:male|female|man|woman|boy|girl|he|she|him|her|his|hers)\b",
    re.IGNORECASE,
)
_IDENTITY_FIELD_RE = re.compile(
    r"(?im)^\s*(?:patient\s+)?(?:name|age|sex|gender|dob|date of birth)\s*:\s*([^\n]+)"
)
_HONORIFIC_NAME_RE = re.compile(
    r"\b(?:Mr|Mrs|Ms|Miss)\.?\s+([A-Z][A-Za-z'-]+)\b"
)
_ABSENCE_PLACEHOLDER_RE = re.compile(
    r"\b(?:not documented|no documented [a-z -]+|no [a-z -]+ documented|not specified|"
    r"none performed(?: today)?|no data (?:available|provided))\b",
    re.IGNORECASE,
)
_INTERPRETATION_RE = re.compile(
    r"\b(?:differential|likely|suggestive of|consistent with|secondary to|due to|"
    r"etiolog(?:y|ies)|possible causes?|other causes?)\b",
    re.IGNORECASE,
)
_ASSESSMENT_SECTION_RE = re.compile(
    r"(?ims)^\s*(?:#+\s*)?(?:assessment|impression|diagnos(?:is|es))\s*:?\s*"
    r"(.*?)(?=^\s*(?:#+\s*)?(?:plan|recommendations?|follow[ -]?up|disposition)\s*:?|\Z)"
)
_ASSESSMENT_ALLOWED = {
    "assessment",
    "current",
    "currently",
    "patient",
    "reports",
    "reported",
    "symptom",
    "symptoms",
    "resolved",
    "ongoing",
    "persistent",
    "exertion",
    "exertional",
    "dyspnea",
    "shortness",
    "breath",
    "history",
    "today",
}
_CONTENT_STOPWORDS = {
    "about", "after", "again", "also", "been", "before", "being", "between",
    "both", "could", "from", "have", "into", "more", "most", "only", "other",
    "over", "same", "such", "than", "that", "their", "there", "these", "they",
    "this", "those", "through", "under", "very", "what", "when", "where", "which",
    "while", "with", "would",
}
_OXYGEN_QUALIFIER_RE = re.compile(
    r"(\boxygen saturation(?:\s+(?:is|was))?\s*:?\s*\d+(?:\.\d+)?\s*%)"
    r"(\s+(?:on|at|while)\s+[^.;,\n]+)",
    re.IGNORECASE,
)


class ClinicalOutputRejected(RuntimeError):
    """Raised when a draft fails deterministic safety validation.

    Carries the rejected draft text (when available) so the caller can offer
    it to the clinician as an unverified draft (WO-3 C-3) instead of
    discarding a full encounter's dictation because two automated attempts
    were rejected. Never returned or streamed as if it were a valid note --
    display of `draft` must always be explicitly labeled unverified.
    """

    def __init__(self, message: str, *, draft: str = "") -> None:
        super().__init__(message)
        self.draft = draft


@dataclass(frozen=True)
class OutputGuardResult:
    accepted: bool
    reasons: tuple[str, ...]


def _normalized_words(text: str) -> List[str]:
    return [match.group(0).lower() for match in _WORD_RE.finditer(text or "")]


def _spelled_number_tokens(text: str) -> set:
    """Return digit strings for spelled-out numbers 0-99 present in text.

    "forty-five" and "forty five" both yield "45" (and, harmlessly, "40" and
    "5" from the standalone pass — extra tokens only widen the match).
    """
    found = set()
    lowered = str(text or "").lower()
    for tens, unit in _TENS_UNIT_RE.findall(lowered):
        found.add(str(_TENS_WORDS[tens] + _UNIT_WORDS[unit]))
    for word in _NUMBER_WORD_RE.findall(lowered):
        value = _TENS_WORDS.get(word, _UNIT_WORDS.get(word))
        if value is not None:
            found.add(str(value))
    return found


def _has_repeated_ngram(words: List[str]) -> bool:
    if len(words) < 15:
        return False
    # (n-gram size, repeat count that signals a runaway loop). Short grams recur
    # naturally in clinical prose ("no acute distress", "the patient is") so they
    # need many repeats; long verbatim spans repeating even a few times indicate
    # genuine degeneration. Trigram/4-gram triggers were removed — at >=3 they
    # rejected essentially every real note.
    #
    # 2026-08-07: long-gram counts raised from (12,3)/(10,3)/(8,4) to (12,6)/
    # (10,6)/(8,6). Legitimate structured outputs (e.g. the 28-option patient
    # meal plan, a ~10k-char doc) repeat their markdown table column-header once
    # per section (~4x), which the old thresholds misclassified as a "runaway
    # loop" and threw away the good output. Genuine degeneration repeats the
    # same span 15-30+ times and is still caught by these thresholds (verified:
    # the old list-style 28x macro-phrase loop is still rejected).
    for size, min_count in ((12, 6), (10, 6), (8, 6), (6, 9), (5, 12)):
        if len(words) < size:
            continue
        counts: dict[tuple[str, ...], int] = {}
        for index in range(0, len(words) - size + 1):
            gram = tuple(words[index : index + size])
            count = counts.get(gram, 0) + 1
            if count >= min_count:
                return True
            counts[gram] = count
    return False


def detect_degenerate_output(text: str, *, max_chars: int = MAX_OUTPUT_CHARS) -> List[str]:
    """Return generic, task-independent degeneration signals."""
    value = str(text or "").strip()
    if not value:
        return ["empty output"]

    reasons: List[str] = []
    words = _normalized_words(value)
    if len(value) > max_chars:
        reasons.append("output exceeded the hard character limit")
    if _has_repeated_ngram(words):
        reasons.append("repeated n-gram loop detected")

    if len(words) >= 600:
        punctuation_count = sum(value.count(mark) for mark in ".!?;:\n")
        unique_ratio = len(set(words)) / max(1, len(words))
        if punctuation_count / max(1, len(words)) < 0.012 and unique_ratio > 0.68:
            reasons.append("low-punctuation lexical runaway detected")

    longest_segment = max(
        (len(_normalized_words(segment)) for segment in _SENTENCE_BREAK_RE.split(value)),
        default=0,
    )
    if len(words) >= 300 and longest_segment > 240:
        reasons.append("abnormally long unbroken sentence detected")
    return reasons


def extract_patient_data(prompt: str) -> str:
    """Extract only the patient-data portion from a DreamCision prompt."""
    value = str(prompt or "")
    marker = "PATIENT DATA:\n"
    if marker not in value:
        return value
    data = value.split(marker, 1)[1]
    for end_marker in (
        "\n\nWhen finished, output END_OF_NOTE",
        "\n\nASSISTANT:",
    ):
        if end_marker in data:
            data = data.split(end_marker, 1)[0]
    return data.strip()


def _looks_like_heading(line: str) -> bool:
    value = line.strip()
    if not value:
        return False
    if re.match(r"^#{1,6}\s+\S", value):
        return True
    if re.match(r"^\*\*[^*]{1,80}\*\*:?$", value):
        return True
    return bool(re.match(r"^[A-Z][A-Za-z /&-]{1,60}:$", value))


def sanitize_clinical_note(prompt: str, output: str) -> str:
    """Remove only deterministic unsupported boilerplate before validation."""
    source_lower = extract_patient_data(prompt).lower()
    kept: List[str] = []
    for line in str(output or "").splitlines():
        lowered = line.lower()
        absence = _ABSENCE_PLACEHOLDER_RE.search(line)
        if absence and absence.group(0).lower() not in source_lower:
            continue
        if (
            re.search(r"\bpatient (?:presents|is seen|seen) today for evaluation\b", lowered)
            and not re.search(r"\b(?:present(?:s|ed)?|evaluation|seen for)\b", source_lower)
        ):
            continue
        if (
            re.search(r"\bpatient (?:was )?(?:educated|counselled|counseled|instructed)\b", lowered)
            and not re.search(r"\b(?:educat|counsel|instruct|discuss)\w*\b", source_lower)
        ):
            continue
        qualifier_match = _OXYGEN_QUALIFIER_RE.search(line)
        if qualifier_match:
            qualifier = qualifier_match.group(2).strip().lower()
            if qualifier not in source_lower:
                line = (
                    line[: qualifier_match.start()]
                    + qualifier_match.group(1)
                    + line[qualifier_match.end() :]
                )
        kept.append(line.rstrip())

    changed = True
    while changed:
        changed = False
        compacted: List[str] = []
        for index, line in enumerate(kept):
            if not _looks_like_heading(line):
                compacted.append(line)
                continue
            next_index = index + 1
            while next_index < len(kept) and not kept[next_index].strip():
                next_index += 1
            if next_index >= len(kept) or _looks_like_heading(kept[next_index]):
                changed = True
                continue
            compacted.append(line)
        kept = compacted

    value = "\n".join(kept).strip()
    return re.sub(r"\n{3,}", "\n\n", value)


def _unsupported_measurements(source: str, output: str) -> Iterable[str]:
    source_values = {
        re.sub(r"\s+", "", match.group(0).lower())
        for match in _MEASUREMENT_RE.finditer(source)
    }
    for match in _MEASUREMENT_RE.finditer(output):
        normalized = re.sub(r"\s+", "", match.group(0).lower())
        if normalized not in source_values:
            yield match.group(0)


def validate_clinical_note(prompt: str, output: str) -> OutputGuardResult:
    """Validate a completed draft before any text is returned to the client."""
    value = str(output or "").strip()
    source = extract_patient_data(prompt)
    reasons = detect_degenerate_output(value, max_chars=MAX_OUTPUT_CHARS)
    # FATAL vs NON-FATAL contract (two production outages taught this):
    #   2026-08-01 the guard rejected every note; 2026-08-06 it rejected a
    #   correct consult because a faxed chart wrote "71year old" and the age
    #   token regex could not see it. Every grounding heuristic has now been
    #   demoted to non-fatal. Fatal is reserved for model FAILURE modes that
    #   are self-evident from the output alone and never depend on comparing
    #   it to the source: empty output, degeneration/runaway n-grams, leaked
    #   reasoning, meta-commentary, placeholder demographics, and grossly
    #   disproportionate length. Anything that asks "is this fact IN the
    #   source?" is a heuristic on messy OCR/ASR text and must only be
    #   logged -- clinicians review and sign every note.
    # Grounding heuristics that assume the note is lexically contained in the
    # structured "PATIENT DATA:" block are NON-FATAL: DreamCision is a dictation
    # scribe, so a note legitimately synthesizes spoken content (diagnoses,
    # interpretation phrases like "consistent with", pronouns, spoken vitals)
    # that never appears verbatim in that block. These are logged for
    # observability but never block display; clinicians review every note before
    # signing. Fatal reasons stay reserved for model FAILURE modes (degeneration,
    # reasoning leakage, meta-commentary, placeholder/fabricated identity).
    soft: List[str] = []

    if _REASONING_RE.search(value):
        reasons.append("reasoning content leaked into the draft")
    if _META_FAILURE_RE.search(value):
        reasons.append("model meta-commentary detected")
    if _PLACEHOLDER_RE.search(value):
        reasons.append("placeholder demographic content detected")
    absence_match = _ABSENCE_PLACEHOLDER_RE.search(value)
    if absence_match and absence_match.group(0).lower() not in source.lower():
        soft.append("unsupported absence placeholder detected")
    if _INTERPRETATION_RE.search(value) and not _INTERPRETATION_RE.search(source):
        soft.append("unsupported diagnosis or interpretation detected")
    qualifier_match = _OXYGEN_QUALIFIER_RE.search(value)
    if qualifier_match and qualifier_match.group(2).strip().lower() not in source.lower():
        soft.append("unsupported measurement qualifier detected")
    output_lower = value.lower()
    source_lower = source.lower()
    if (
        re.search(r"\bpatient (?:presents|is seen|seen) today for evaluation\b", output_lower)
        and not re.search(r"\b(?:present(?:s|ed)?|evaluation|seen for)\b", source_lower)
    ):
        soft.append("unsupported encounter boilerplate detected")
    if (
        re.search(r"\bpatient (?:was )?(?:educated|counselled|counseled|instructed)\b", output_lower)
        and not re.search(r"\b(?:educat|counsel|instruct|discuss)\w*\b", source_lower)
    ):
        soft.append("unsupported counseling action detected")

    source_words = _normalized_words(source)
    output_words = _normalized_words(value)
    allowed_chars = max(6000, min(MAX_OUTPUT_CHARS, len(source) * 6))
    if len(value) > allowed_chars:
        reasons.append("draft is disproportionate to the supplied clinical source")

    # (?<!\d)...(?!\d) instead of \b...\b: faxed/OCR'd charts routinely lose
    # the space in "71 year old" -> "71year old", and \b requires a non-word
    # char after the digits, so the age was invisible in the source and a
    # correct note was rejected. Digit-adjacency is the real constraint here.
    source_number_tokens = set(re.findall(r"(?<!\d)\d{1,3}(?!\d)", source))
    source_number_tokens |= _spelled_number_tokens(source)
    _source_ages = {t.lstrip("0") for t in source_number_tokens}
    for age in _AGE_RE.findall(value):
        if age.lstrip("0") not in _source_ages:
            soft.append(f"unsupported patient age detected: {age}")
            break

    if _GENDER_TERMS_RE.search(value) and not _GENDER_TERMS_RE.search(source):
        soft.append("unsupported sex, gender, or pronoun detected")

    for field_value in _IDENTITY_FIELD_RE.findall(value):
        normalized = field_value.strip().strip("*_").lower()
        if normalized and normalized not in source_lower:
            soft.append("unsupported patient identity field detected")
            break
    for surname in _HONORIFIC_NAME_RE.findall(value):
        if surname.lower() not in source_lower:
            soft.append("unsupported patient identity detected")
            break

    assessment_match = _ASSESSMENT_SECTION_RE.search(value)
    if assessment_match:
        source_vocabulary = set(_normalized_words(source))
        unsupported_terms = {
            word
            for word in _normalized_words(assessment_match.group(1))
            if len(word) >= 5
            and word not in source_vocabulary
            and word not in _ASSESSMENT_ALLOWED
            and word not in _CONTENT_STOPWORDS
        }
        if len(unsupported_terms) >= 2:
            preview = ", ".join(sorted(unsupported_terms)[:4])
            soft.append(f"unsupported assessment terminology detected: {preview}")

    # Spoken vitals (e.g. "sats are 95" -> "95%") legitimately are not byte-exact
    # in the structured block, so measurement grounding is non-fatal too.
    unsupported_measurements = list(_unsupported_measurements(source, value))
    if unsupported_measurements:
        soft.append(
            "unsupported measured value detected: "
            + ", ".join(unsupported_measurements[:3])
        )

    if len(output_words) >= 350 and len(source_words) >= 20:
        source_vocabulary = set(source_words)
        grounded_ratio = sum(word in source_vocabulary for word in output_words) / len(output_words)
        if grounded_ratio < 0.18:
            soft.append("draft vocabulary is weakly grounded in the supplied source")

    if soft:
        logging.getLogger(__name__).info(
            "output guard: %d non-fatal grounding signal(s) (note still accepted): %s",
            len(soft),
            "; ".join(dict.fromkeys(soft)),
        )

    unique_reasons = tuple(dict.fromkeys(reasons))
    return OutputGuardResult(accepted=not unique_reasons, reasons=unique_reasons)


def build_guard_retry_prompt(prompt: str, reasons: Iterable[str]) -> str:
    """Add a concise retry directive to the system role of an existing prompt."""
    reason_text = "; ".join(str(reason) for reason in reasons if str(reason).strip())
    directive = (
        "SAFETY RETRY: The previous draft was rejected before display because it was not "
        "sufficiently grounded or concise. Regenerate from the source only. Omit every "
        "unsupported field and stop promptly. Rejection signals: "
        + (reason_text or "unsafe output")
        + "."
    )
    marker = "\n\nUSER:\n"
    if marker in prompt:
        return prompt.replace(marker, "\n\n" + directive + marker, 1)
    return directive + "\n\n" + prompt


class IncrementalDegenerationGuard:
    """Bound a streamed response and stop it when generic degeneration appears."""

    def __init__(self, *, max_chars: int = MAX_OUTPUT_CHARS) -> None:
        self._parts: List[str] = []
        self._chars = 0
        self._last_check = 0
        self._max_chars = max_chars

    def add(self, chunk: str) -> None:
        value = str(chunk or "")
        if not value:
            return
        self._parts.append(value)
        self._chars += len(value)
        if self._chars - self._last_check < 256 and self._chars <= self._max_chars:
            return
        self._last_check = self._chars
        reasons = detect_degenerate_output("".join(self._parts), max_chars=self._max_chars)
        if reasons:
            raise ClinicalOutputRejected("; ".join(reasons))
