"""Consult focus builder — extracts richer clinical context from generated notes.

Replaces the simple Impression+Plan regex extraction with multi-section awareness.
"""
import re
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Section extraction
# ---------------------------------------------------------------------------

SECTION_ALIASES: Dict[str, List[str]] = {
    "chief_complaint": ["Chief Complaint", "CC", "Reason for Visit", "Presenting Complaint"],
    "history_of_present_illness": ["History of Present Illness", "HPI", "History", "Present Illness"],
    "past_medical_history": ["Past Medical History", "PMH", "Medical History", "Past History"],
    "medications": ["Medications", "Meds", "Current Medications", "Drug List"],
    "allergies": ["Allergies", "Drug Allergies", "Allergy List"],
    "social_history": ["Social History", "SH", "Lifestyle"],
    "family_history": ["Family History", "FH", "Familial"],
    "physical_exam": ["Physical Exam", "Physical Examination", "PE", "Objective", "Examination"],
    "investigations": ["Investigations", "Labs", "Laboratory", "Imaging", "Studies", "Pathology"],
    "impression": ["Impression", "Assessment", "Diagnosis", "Diagnoses", "Impressions"],
    "plan": ["Plan", "Management", "Recommendations", "Plan of Care", "Treatment Plan"],
}

# Boundary pattern used to find where a section ENDS: any recognized heading
# from any section, not just the section currently being extracted. Sorted
# longest-first so e.g. "Plan of Care" is tried before the shorter "Plan".
_ALL_HEADING_NAMES: List[str] = sorted(
    {name for names in SECTION_ALIASES.values() for name in names},
    key=len,
    reverse=True,
)
_ANY_HEADING_RE = re.compile(
    rf"(?im)^(?:#{{1,3}}\s*)?({'|'.join(re.escape(n) for n in _ALL_HEADING_NAMES)})\s*(?::|-)?\s*$"
)


def extract_section_by_heading(
    note_text: str,
    heading: str,
    *,
    aliases: Optional[List[str]] = None,
) -> str:
    """Extract a section body by matching its markdown heading.

    The section body runs until the next occurrence of ANY recognized
    section heading (not just aliases of this same section) -- otherwise a
    section with no repeated heading of its own (e.g. Impression, which
    never recurs later in the note) has no way to detect where it ends and
    swallows every section after it, including Plan/Follow-up.

    Args:
        note_text: Full note text.
        heading: Primary heading (e.g. "History of Present Illness").
        aliases: Alternative names.

    Returns:
        Section body or empty string.
    """
    if not note_text:
        return ""

    all_names = [heading] + (aliases or [])
    name_pattern = "|".join(re.escape(n) for n in all_names)
    heading_re = re.compile(rf"(?im)^(?:#{{1,3}}\s*)?({name_pattern})\s*(?::|-)?\s*$")

    m = heading_re.search(note_text)
    if not m:
        return ""

    start = m.end()
    rest = note_text[start:]
    next_m = _ANY_HEADING_RE.search(rest)
    end = next_m.start() if next_m else len(rest)
    return rest[:end].strip()


def extract_clinical_data_sections(note_text: str) -> Dict[str, str]:
    """Extract all known clinical sections from a generated note.

    Returns:
        Dict mapping standardised keys to section body text.
    """
    sections: Dict[str, str] = {}
    for key, names in SECTION_ALIASES.items():
        body = extract_section_by_heading(note_text, names[0], aliases=names[1:])
        if body:
            sections[key] = body
    return sections


# ---------------------------------------------------------------------------
# Combined focus
# ---------------------------------------------------------------------------

def build_consult_focus(
    note_text: str,
    *,
    strategy: str = "sections",
    extract_sections_fn: Optional[Any] = None,
    head_text: str = "",
    tail_text: str = "",
    fallback_text: str = "",
) -> Tuple[str, List[str]]:
    """Build a clinical focus string for RAG/evidence queries.

    Args:
        note_text: Full generated note.
        strategy: "sections", "full_note", or "llm_query" (query handled in pipeline).
        extract_sections_fn: Optional function to extract sections.
        head_text: Pre-computed head of note.
        tail_text: Pre-computed tail of note.
        fallback_text: Heuristic fallback from pipeline.

    Returns:
        (focus_string, list_of_section_names_used)
    """
    sections = extract_clinical_data_sections(note_text)

    # Collect relevant sections for clinical decision support
    priority_keys = [
        "impression",
        "plan",
        "history_of_present_illness",
        "chief_complaint",
        "investigations",
        "past_medical_history",
        "medications",
        "physical_exam",
    ]
    used: List[str] = []

    if strategy == "full_note":
        parts: List[str] = []
        # Always include Impression + Plan if present
        for k in ["impression", "plan"]:
            if sections.get(k):
                label = SECTION_ALIASES[k][0]
                parts.append(f"{label}:\n{sections[k]}")
                used.append(k)
        # HPI
        if sections.get("history_of_present_illness"):
            parts.append(f"History of Present Illness:\n{sections['history_of_present_illness'][:2000]}")
            used.append("history_of_present_illness")
        # Investigations
        if sections.get("investigations"):
            parts.append(f"Investigations:\n{sections['investigations'][:1500]}")
            used.append("investigations")
        # Medications
        if sections.get("medications"):
            parts.append(f"Medications:\n{sections['medications'][:800]}")
            used.append("medications")
        # Head + tail
        if head_text.strip():
            parts.append(head_text)
            used.append("note_head")
        if tail_text.strip():
            parts.append(tail_text)
            used.append("note_tail")
        focus = "\n\n".join(p for p in parts if p.strip())
    else:
        # Default "sections" strategy — key sections only
        parts: List[str] = []
        for k in priority_keys:
            if sections.get(k):
                label = SECTION_ALIASES[k][0]
                body = sections[k]
                cap = 3000 if k in ("history_of_present_illness", "impression", "plan") else 1500
                if len(body) > cap:
                    body = body[:cap] + "…"
                parts.append(f"{label}:\n{body}")
                used.append(k)
        focus = "\n\n".join(parts)

    # Fallbacks
    if not focus.strip() and fallback_text.strip():
        focus = fallback_text.strip()
        used = ["heuristic_fallback"]

    if not focus.strip():
        focus = " ".join((note_text or "").split()[:200])
        used = ["truncated_note_fallback"]

    return focus, used


# ---------------------------------------------------------------------------
# Build a structured clinical summary for the prompt
# ---------------------------------------------------------------------------

def build_note_excerpt(
    note_text: str,
    *,
    include_sections: Optional[List[str]] = None,
) -> str:
    """Build a focused excerpt of the note for prompt injection."""
    if include_sections is None:
        include_sections = ["impression", "plan", "history_of_present_illness"]

    sections = extract_clinical_data_sections(note_text)
    parts: List[str] = []
    for k in include_sections:
        if sections.get(k):
            label = SECTION_ALIASES[k][0]
            parts.append(f"{label}:\n{sections[k]}")

    if not parts:
        # Fall back to Impression+Plan via regex
        for label, pat in [("Impression", r"(?im)^\s*Impression\s*:\s*(.+?)(?:\n\S|\Z)"),
                            ("Plan", r"(?im)^\s*Plan\s*:\s*(.+?)(?:\n\S|\Z)")]:
            m = re.search(pat, note_text, flags=re.DOTALL)
            if m:
                parts.append(f"{label}:\n{m.group(1).strip()}")

    return "\n\n".join(parts) if parts else " ".join((note_text or "").split()[:250])


# ---------------------------------------------------------------------------
# Marker sentence extraction (unchanged from current pipeline helpers)
# ---------------------------------------------------------------------------
# Kept here for self-contained focus building; the current extract_marker_sentences
# is still used in pipeline.py for confirmed/ruled out extraction.
