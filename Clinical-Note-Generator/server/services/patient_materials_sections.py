"""
Section parser for clinical notes.

Parses raw markdown notes into structured sections by ## headings,
handling common section name variations (e.g., Assessment/Impression,
A&I, Physical Exam, etc.).
"""

import re
from typing import Dict, List, Optional


# Canonical section names → list of possible heading patterns
SECTION_VARIANTS: Dict[str, List[str]] = {
    "patient_identification": ["Patient Identification", "Patient Identity", "Patient"],
    "chief_complaint": ["Chief Complaint", "CC", "Presenting Problem", "Presenting Complaint"],
    "hpi": ["History of Present Illness", "HPI", "History"],
    "past_medical_history": [
        "Past Medical History", "PMH", "Medical History", "Past History",
        "Past Medical & Surgical History", "PM&SH",
    ],
    "medications": [
        "Medications", "Current Medications", "Medication List",
        "Medications (Current)", "Home Medications",
    ],
    "allergies": ["Allergies", "Allergies/Adverse Reactions", "A/R", "Known Allergies"],
    "social_history": ["Social History", "SH", "Social Hx"],
    "family_history": ["Family History", "FH", "FHx"],
    "review_of_systems": ["Review of Systems", "ROS", "Review Of Systems"],
    "physical_exam": [
        "Physical Examination", "Physical Exam", "Exam",
        "PE", "Examination", "Objective",
    ],
    "assessments": [
        "Assessment", "Assessment/Impression", "Assessment & Impression",
        "A&I", "Impression", "Assessments", "Assessment and Impression",
        "Diagnosis", "Assessment (Problems)",
    ],
    "plan": [
        "Plan", "Plan of Care", "Treatment Plan", "Management Plan",
        "Treatment & Plan", "Plan of Action",
    ],
    "follow_up": ["Follow-up", "Follow Up", "Follow Up Plan", "Follow-Up Plan"],
    "conflicts": ["Conflicts", "Conflicts Section", "Conflict"],
}

# Build reverse lookup: heading text → canonical name
_HEADING_TO_CANONICAL: Dict[str, str] = {}
for canonical, variants in SECTION_VARIANTS.items():
    for variant in variants:
        _HEADING_TO_CANONICAL[variant.lower()] = canonical

# Compile regex for section headings
# Require at least one '#' so that lines like "Weight: 85kg" are not mistaken
# for heading candidates. Only lines starting with #, ##, or ### are headings.
_HEADING_RE = re.compile(r"^\s*#{1,3}\s+(.+?)\s*:?\s*$", re.MULTILINE)


def _match_heading(heading_text: str) -> Optional[str]:
    """Match a heading text to a canonical section name."""
    text = heading_text.strip().lower()
    # Exact match first
    if text in _HEADING_TO_CANONICAL:
        return _HEADING_TO_CANONICAL[text]
    # Match variant with parenthetical suffix: "Assessment (Problems)"
    m = re.match(r"^(.+?)\s*\(.+\)$", text)
    if m and m.group(1) in _HEADING_TO_CANONICAL:
        return _HEADING_TO_CANONICAL[m.group(1)]
    # Match slash-separated compound: "Assessment/Impression", "A&I"
    for sep in ["/", "&"]:
        if sep in text:
            parts = text.split(sep, 1)
            for part in parts:
                part = part.strip()
                if part in _HEADING_TO_CANONICAL:
                    return _HEADING_TO_CANONICAL[part]
    return None


def parse_note_sections(note_text: str) -> Dict[str, str]:
    """Parse a markdown clinical note into sections by ## headings.

    Args:
        note_text: Raw markdown note text.

    Returns:
        Dict mapping canonical section names to their content.
        Sections that don't match known patterns are kept under
        their original heading text.
    """
    if not note_text or not note_text.strip():
        return {}

    lines = note_text.split("\n")
    sections: Dict[str, str] = {}
    current_heading: Optional[str] = None
    current_content: List[str] = []

    for line in lines:
        match = _HEADING_RE.match(line)
        if match:
            heading_text = match.group(1).strip()
            canonical = _match_heading(heading_text)
            if canonical:
                # Save previous section
                if current_heading is not None:
                    sections[current_heading] = "\n".join(current_content).strip()
                    current_content = []
                current_heading = canonical
            else:
                # Not a recognized heading — treat as content
                current_content.append(line)
        else:
            current_content.append(line)

    # Save last section
    if current_heading is not None:
        sections[current_heading] = "\n".join(current_content).strip()

    return sections


def extract_section(
    sections: Dict[str, str],
    canonical_name: str,
) -> Optional[str]:
    """Extract a section by canonical name, handling variations.

    Args:
        sections: Parsed sections dict.
        canonical_name: Canonical section name to find.

    Returns:
        Section content or None if not found.
    """
    if canonical_name in sections:
        return sections[canonical_name]
    # Fuzzy match
    for key, value in sections.items():
        if key.lower() == canonical_name.lower():
            return value
    return None


def get_medication_text(sections: Dict[str, str]) -> Optional[str]:
    """Extract medication information from relevant sections.

    Looks in Plan, Assessments, and Medications sections.
    """
    plan = extract_section(sections, "plan")
    medications = extract_section(sections, "medications")
    assessment = extract_section(sections, "assessments")

    parts = []
    if medications:
        parts.append(medications)
    if plan:
        parts.append(plan)
    if assessment:
        parts.append(assessment)

    return "\n\n".join(parts) if parts else None


def get_diagnosis_text(sections: Dict[str, str]) -> Optional[str]:
    """Extract diagnosis/assessment text."""
    return extract_section(sections, "assessments")


def get_patient_data_from_note(sections: Dict[str, str]) -> Dict[str, Optional[str]]:
    """Extract patient demographic data from note sections.

    Looks for weight, height, BMI, age, sex in Physical Exam
    and Patient Identification sections.
    """
    result: Dict[str, Optional[str]] = {
        "weight_kg": None,
        "height_cm": None,
        "bmi": None,
        "age": None,
        "sex": None,
    }

    pe = extract_section(sections, "physical_exam")
    pid = extract_section(sections, "patient_identification")
    hpi = extract_section(sections, "hpi")

    search_text = "\n".join(filter(None, [pe, pid, hpi]))
    if not search_text:
        return result

    # Weight patterns
    weight_patterns = [
        r"weight\s*[:=]\s*(\d+\.?\d*)\s*(?:kg|kgs?|kilograms?)",
        r"(\d+\.?\d*)\s*(?:kg|kgs?|kilograms?)\s*(?:weight)?",
        r"wt\.?\s*[:=]\s*(\d+\.?\d*)\s*(?:kg|kgs?)?",
    ]
    for pattern in weight_patterns:
        match = re.search(pattern, search_text, re.IGNORECASE)
        if match:
            result["weight_kg"] = match.group(1)
            break

    # Height patterns
    height_patterns = [
        r"height\s*[:=]\s*(\d+\.?\d*)\s*(?:cm|cms?)",
        r"(\d+\.?\d*)\s*(?:cm|cms?)\s*(?:height)?",
        r"ht\.?\s*[:=]\s*(\d+\.?\d*)\s*(?:cm|cms?)?",
        r"height\s*[:=]\s*(\d+)\s*(?:in|inches?)",
    ]
    for pattern in height_patterns:
        match = re.search(pattern, search_text, re.IGNORECASE)
        if match:
            val = match.group(1)
            # Convert inches to cm if needed
            if re.search(r"in|inches?", search_text[match.start():match.end()], re.IGNORECASE):
                val = str(round(int(val) * 2.54, 1))
            result["height_cm"] = val
            break

    # BMI pattern
    bmi_patterns = [
        r"bmi\s*[:=]\s*(\d+\.?\d*)",
        r"(\d+\.?\d*)\s*(?:bmi)",
    ]
    for pattern in bmi_patterns:
        match = re.search(pattern, search_text, re.IGNORECASE)
        if match:
            result["bmi"] = match.group(1)
            break

    # Age pattern
    age_patterns = [
        r"(\d+)\s*(?:year|yr)-old",
        r"age\s*[:=]\s*(\d+)",
    ]
    for pattern in age_patterns:
        match = re.search(pattern, search_text, re.IGNORECASE)
        if match:
            result["age"] = match.group(1)
            break

    # Sex pattern
    sex_patterns = [
        r"(?:man|male)",
        r"(?:woman|female)",
    ]
    if re.search(sex_patterns[0], search_text, re.IGNORECASE):
        result["sex"] = "male"
    elif re.search(sex_patterns[1], search_text, re.IGNORECASE):
        result["sex"] = "female"

    return result
