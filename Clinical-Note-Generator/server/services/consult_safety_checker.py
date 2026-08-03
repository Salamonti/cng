"""Consult safety checker — medication safety, red flags, guideline verification.

Analyzes notes for clinical safety issues and injects them into the structured
consult comment pipeline.
"""
import re
from typing import Any, Dict, List

# ---------------------------------------------------------------------------
# Medication extraction
# ---------------------------------------------------------------------------

# Common medication names for pattern matching
_COMMON_MEDICATIONS = [
    "insulin", "metformin", "atorvastatin", "simvastatin", "rosuvastatin",
    "aspirin", "clopidogrel", "warfarin", "apixaban", "rivaroxaban",
    "lisinopril", "enalapril", "ramipril", "losartan", "valsartan",
    "metoprolol", "bisoprolol", "carvedilol", "atenolol",
    "amlodipine", "nifedipine", "diltiazem",
    "furosemide", "hydrochlorothiazide", "spironolactone",
    "levothyroxine", "omeprazole", "pantoprazole",
    "gabapentin", "pregabalin", "sertraline", "citalopram", "escitalopram",
    "albuterol", "fluticasone", "prednisone", "hydrocortisone",
    "insulin glargine", "insulin aspart", "insulin lispro", "insulin detemir",
    "liraglutide", "semaglutide", "empagliflozin", "dapagliflozin",
    "sitagliptin", "liraglutide", "ozempic",
]


def extract_medications_from_note(note_text: str) -> List[str]:
    """Extract medication names from note text using dose-pattern + known-name matching."""
    if not note_text:
        return []

    medications = set()

    # Strategy 1: Dose-pattern regex (catches "DrugName Xmg" patterns)
    # Using \t and literal space instead of \s to avoid matching newlines
    dose_re = re.compile(
        r"([A-Za-z][A-Za-z0-9 \t\-'.]{2,40})\s+(\d+\.?\d*)\s+(mg|mcg|g|mL|ml|L|units?)",
        re.IGNORECASE,
    )
    for m in dose_re.finditer(note_text):
        name = re.sub(r"\s+", " ", m.group(1).strip())
        # Filter out non-medication noise (e.g., "twice daily", line markers)
        if len(name) >= 3 and not any(c in name for c in ["-", "•", "*"]):
            medications.add(name)

    # Strategy 2: Known medication names anywhere in text
    note_lower = note_text.lower()
    for med in _COMMON_MEDICATIONS:
        if med.lower() in note_lower:
            medications.add(med)

    # Strategy 3: Lines starting with dash/bullet/common med list markers
    for line in note_text.splitlines():
        stripped = line.strip().lstrip("-•* ")
        # Look for "Medication: notes" patterns
        if re.match(r"^[A-Za-z][a-z]+(?:\s+[A-Za-z][a-z]+){0,3}\s+\d+", stripped):
            name = re.sub(r"\s+\d+.*$", "", stripped)
            if 3 <= len(name) <= 50:
                medications.add(name.strip())

    # Deduplicate case-insensitively, keeping the first (usually title-cased) variant
    seen = set()
    result = []
    for m in medications:
        key = m.lower().strip()
        if key and key not in seen:
            seen.add(key)
            result.append(m)
    return sorted(result, key=lambda x: x.lower())


# ---------------------------------------------------------------------------
# Red-flag detection patterns (symptom → missing test → severity)
# ---------------------------------------------------------------------------

RED_FLAG_PATTERNS: Dict[str, Dict[str, Any]] = {
    "chest_pain_no_ecg": {
        "symptoms": ["chest pain", "chest tightness", "chest pressure"],
        "missing_test": ["ECG", "troponin", "EKG", "electrocardiogram"],
        "severity": "critical",
        "message": "Chest pain documented without ECG/troponin — risk of missed MI/ACS",
    },
    "fever_no_cultures": {
        "symptoms": ["fever", "febrile", "temperature"],
        "missing_test": ["blood cultures", "cultures", "sepsis workup"],
        "severity": "critical",
        "message": "Fever without documented cultures — risk of missed sepsis/bacteremia",
    },
    "headache_no_imaging": {
        "symptoms": ["headache", "new headache", "worst headache"],
        "missing_test": ["CT head", "MRI", "imaging", "neuro exam"],
        "severity": "critical",
        "message": "New/worst headache without neuroimaging — risk of missed hemorrhage",
    },
    "abdominal_pain_no_ct": {
        "symptoms": ["abdominal pain", "acute abdomen", "severe abdominal pain"],
        "missing_test": ["CT abdomen", "CT scan", "imaging", "ultrasound"],
        "severity": "high",
        "message": "Severe abdominal pain without imaging — risk of missed surgical emergency",
    },
    "leg_swelling_no_dvt": {
        "symptoms": ["leg swelling", "unilateral leg swelling", "calf pain"],
        "missing_test": ["D-dimer", "DVT ultrasound", "venous Doppler", "venous duplex"],
        "severity": "critical",
        "message": "Unilateral leg swelling without DVT exclusion — risk of missed PE",
    },
    "hypoglycemia_no_checks": {
        "symptoms": ["hypoglycemia", "low blood sugar", "nocturnal hypoglycemia"],
        "missing_test": ["CGM", "continuous glucose", "glucose monitoring"],
        "severity": "high",
        "message": "Reported hypoglycemia without CGM/glucose monitoring — risk of undetected lows",
    },
    "weight_loss_no_workup": {
        "symptoms": ["weight loss", "unintentional weight loss", "unexplained weight loss"],
        "missing_test": ["cancer screening", "CT", "endoscopy", "colonoscopy"],
        "severity": "high",
        "message": "Unintentional weight loss without age-appropriate cancer screening",
    },
    "fall_no_bone": {
        "symptoms": ["fall", "falls", "fall risk"],
        "missing_test": ["DEXA", "bone density", "osteoporosis"],
        "severity": "moderate",
        "message": "Fall risk without bone density assessment — missed osteoporosis screening",
    },
}


def _is_negated(note_text: str, keyword: str) -> bool:
    """Check if a keyword is negated in the text (e.g., 'no chest pain', 'denies chest pain').

    Looks for common negation words within ±3 words of the keyword.
    """
    negation_words = {"no", "not", "denies", "negative", "absent", "none", "without", "normal", "unremarkable"}
    text_lower = note_text.lower()
    kw_lower = keyword.lower()
    idx = text_lower.find(kw_lower)
    if idx < 0:
        return False

    # O(n) word position build — single pass with re.finditer
    word_positions = [(m.start(), m.end()) for m in re.finditer(r'\S+', text_lower)]
    if not word_positions:
        return False

    # Find which word position contains the keyword start
    kw_word_idx = None
    for i, (ws, we) in enumerate(word_positions):
        if ws <= idx < we:
            kw_word_idx = i
            break
    if kw_word_idx is None:
        return False

    # Check a narrow window of ±3 words around the keyword
    start = max(0, kw_word_idx - 3)
    end = min(len(word_positions), kw_word_idx + 4)
    # Extract actual word text for window
    nearby_words = [text_lower[ws:we] for ws, we in word_positions[start:end]]
    for neg in negation_words:
        for w in nearby_words:
            if re.search(rf"\b{re.escape(neg)}\b", w):
                return True
    return False


def check_red_flags(note_text: str) -> List[Dict[str, Any]]:
    """Detect clinical red flags in note text — symptoms present but key tests missing."""
    flags_found: List[Dict[str, Any]] = []
    note_lower = note_text.lower()

    for flag_name, pattern in RED_FLAG_PATTERNS.items():
        # Check if any symptom is present AND not negated
        symptom_found = False
        for sym in pattern["symptoms"]:
            if sym.lower() in note_lower and not _is_negated(note_text, sym):
                symptom_found = True
                break
        if not symptom_found:
            continue
        tests_present = any(test.lower() in note_lower for test in pattern["missing_test"])
        if not tests_present:
            flags_found.append({
                "flag": flag_name,
                "severity": pattern["severity"],
                "message": pattern["message"],
            })

    return flags_found


# ---------------------------------------------------------------------------
# Medication safety query builder
# ---------------------------------------------------------------------------

MED_SAFETY_QUERY = """MEDICATION SAFETY REVIEW — check ONLY the medications listed above. Do NOT evaluate medications not listed.

Review for:
1. Drug-drug interactions between the specific medications this patient is taking
2. Age-appropriate dosing (Beers Criteria if 65+)
3. Renal dosing adjustments (check eGFR if available)
4. Hepatic dosing adjustments (check LFTs if available)
5. Contraindications based on the patient's documented conditions
6. Duplicate therapy (same drug class)
7. Adverse effects of the specific medications this patient is currently taking

CRITICAL: Do NOT alert about medications the patient is not taking. Do NOT speculate about hypothetical treatments (e.g., do not warn about SGLT2 side effects if the patient is not on SGLT2). Only report issues for medications explicitly listed above.

For each issue found, provide:
- The specific interaction/contraindication
- The evidence source or guideline
- Recommended action (monitor, adjust dose, switch, stop)

If no significant safety issues found, return an empty safety_alerts array.
"""

GUIDELINE_QUERY = """GUIDELINE COMPLIANCE CHECK — verify the patient's CURRENT management against relevant guidelines:
1. Identify applicable clinical practice guidelines for the patient's documented conditions
2. Compare the patient's current management to guideline recommendations
3. Note any deviations from standard of care in what is currently being done
4. If current management deviates from guidelines, note the gap — do NOT suggest starting new treatments that are not already in the plan
5. Cite specific guideline sources (e.g., ADA 2025, AHA/ACC, IDSA, etc.)

CRITICAL: Do not suggest treatments the patient is not currently receiving. Focus on whether current management aligns with guidelines, not on hypothetical additions.

If current plan appears guideline-concordant, state so with supporting evidence.
"""


def build_safety_context(
    note_text: str,
    medications: List[str],
    red_flags: List[Dict[str, Any]],
) -> str:
    """Build a safety context block to append to the structured consult prompt.

    Includes medication list, detected red flags, and instructions for the LLM
    to perform a safety review.
    """
    parts = ["--- ADDITIONAL SAFETY ANALYSIS REQUIRED ---"]

    # Red flags
    if red_flags:
        parts.append("\nDETECTED RED FLAGS (symptoms present, key tests missing):")
        for flag in red_flags:
            icon = "CRITICAL" if flag["severity"] == "critical" else "HIGH" if flag["severity"] == "high" else "MODERATE"
            parts.append(f"  [{icon}] {flag['message']}")
        parts.append("ACTION: Verify these are addressed. If tests are truly not indicated, explain why.\n")

    # Medications
    if medications:
        parts.append(f"CURRENT MEDICATIONS: {', '.join(medications)}")
        parts.append(MED_SAFETY_QUERY)
    else:
        parts.append("No medications identified in note.")

    # Guideline check
    parts.append(f"\n{GUIDELINE_QUERY}")

    return "\n".join(parts)
