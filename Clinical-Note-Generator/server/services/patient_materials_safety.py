"""
Safety and validation for patient materials.

Handles disclaimer templates, contraindication checks,
and safety validation for AI-generated patient-facing content.
"""

import re
from typing import Dict, List, Optional

_CONDITION_ABBREVIATIONS = [
    ("chf", "heart failure"),
    ("congestive heart failure", "heart failure"),
    ("htn", "hypertension"),
    ("t2dm", "diabetes"),
    ("dm2", "diabetes"),
    ("dm1", "diabetes"),
    ("type 2 diabetes", "diabetes"),
    ("type 1 diabetes", "diabetes"),
    ("t1dm", "diabetes"),
    ("dm", "diabetes"),
    ("oa", "arthritis"),
    ("osteoarthritis", "arthritis"),
    ("ckd", "kidney disease"),
    ("chronic kidney disease", "kidney disease"),
    ("copd", "asthma"),
    ("cad", "cardiac"),
    ("coronary artery disease", "cardiac"),
    ("mi", "cardiac"),
    ("m.i.", "cardiac"),
    ("mi/cad", "cardiac"),
]


def _expand_condition_abbreviations(text_lower: str) -> str:
    """Expand common medical abbreviations to full condition names for contraindication matching."""
    result = text_lower
    # Sort by length descending so longer abbreviations match first (e.g. "congestive heart failure" before "chf")
    for abbr, full in sorted(_CONDITION_ABBREVIATIONS, key=lambda x: len(x[0]), reverse=True):
        # Use word boundary to avoid matching inside other words
        result = re.sub(rf"\b{re.escape(abbr)}\b", f" {full} ", result)
    return result


# Safety disclaimers per material type
DISCLAIMERS: Dict[str, str] = {
    "medications": (
        "_Educational summary, reviewed by your clinician. "
        "Do not start, stop, or change medications without medical advice._"
    ),
    "diagnosis": "_Educational information, reviewed by your clinician._",
    "issues_plan": "_Educational summary of your visit, reviewed by your clinician._",
    "diet": "_General guidance, reviewed by your clinician; adjust to your needs and labs._",
    "exercise": (
        "_General guidance, reviewed by your clinician. Stop and seek help for chest pain, "
        "severe breathlessness, dizziness, or fainting._"
    ),
    "full_report": "_Educational health summary, reviewed by your clinician._",
}

# Medical condition → exercise contraindications
EXERCISE_CONTRAINDICATIONS: Dict[str, List[str]] = {
    "heart failure": [
        "Avoid high-intensity interval training",
        "Avoid isometric exercises (heavy lifting, push-ups against wall)",
        "Seek medical clearance before starting any new exercise",
    ],
    "cardiac": [
        "Seek medical clearance before starting any new exercise",
        "Avoid exercises that cause chest pain or severe shortness of breath",
    ],
    "asthma": [
        "Warm up gradually, avoid sudden intense exercise",
        "Keep inhaler accessible during exercise",
        "Avoid exercise in cold air if it triggers symptoms",
    ],
    "diabetes": [
        "Monitor blood sugar before, during, and after exercise",
        "Avoid exercise if blood sugar is above 250 mg/dL or below 100 mg/dL",
        "Always carry fast-acting carbohydrates",
    ],
    "hypertension": [
        "Avoid heavy weightlifting and breath-holding exercises",
        "Focus on moderate-intensity aerobic exercise",
    ],
    "arthritis": [
        "Avoid high-impact activities (running, jumping)",
        "Focus on low-impact exercises (swimming, cycling, walking)",
        "Stop if joint pain worsens",
    ],
    "osteoporosis": [
        "Avoid forward bending and twisting motions",
        "Avoid high-impact activities",
        "Focus on weight-bearing exercises as tolerated",
    ],
    "pregnancy": [
        "Avoid contact sports and activities with fall risk",
        "Avoid exercises lying flat on back after first trimester",
        "Seek obstetrician clearance before starting new exercise",
    ],
}

# Real drug names (generic + common brand) → the medication-class keys used
# in MEDICATION_EXERCISE_WARNINGS below. Patient medication lists contain
# actual drug names ("metoprolol 50mg"), never the class label itself
# ("beta blocker"), so matching class labels directly against the med list
# text never fires except by coincidence (e.g. a med literally named
# "insulin ..."). This maps the names that actually appear in practice to
# the class each one belongs to.
_DRUG_NAME_TO_CLASS: Dict[str, str] = {
    # Insulin
    "insulin": "insulin",
    "lantus": "insulin",
    "levemir": "insulin",
    "humalog": "insulin",
    "novolog": "insulin",
    "novorapid": "insulin",
    "tresiba": "insulin",
    "toujeo": "insulin",
    "basaglar": "insulin",
    "fiasp": "insulin",
    # Beta blockers
    "metoprolol": "beta blocker",
    "atenolol": "beta blocker",
    "bisoprolol": "beta blocker",
    "carvedilol": "beta blocker",
    "propranolol": "beta blocker",
    "nebivolol": "beta blocker",
    "labetalol": "beta blocker",
    "toprol": "beta blocker",
    "lopressor": "beta blocker",
    "coreg": "beta blocker",
    "beta blocker": "beta blocker",
    # Blood thinners (anticoagulants + antiplatelets)
    "warfarin": "blood thinner",
    "coumadin": "blood thinner",
    "apixaban": "blood thinner",
    "eliquis": "blood thinner",
    "rivaroxaban": "blood thinner",
    "xarelto": "blood thinner",
    "dabigatran": "blood thinner",
    "pradaxa": "blood thinner",
    "clopidogrel": "blood thinner",
    "plavix": "blood thinner",
    "aspirin": "blood thinner",
    "heparin": "blood thinner",
    "enoxaparin": "blood thinner",
    "lovenox": "blood thinner",
    "blood thinner": "blood thinner",
    "anticoagulant": "blood thinner",
    # Antihypertensives (ACE inhibitors, ARBs, CCBs, diuretics)
    "lisinopril": "antihypertensive",
    "enalapril": "antihypertensive",
    "ramipril": "antihypertensive",
    "losartan": "antihypertensive",
    "valsartan": "antihypertensive",
    "irbesartan": "antihypertensive",
    "amlodipine": "antihypertensive",
    "nifedipine": "antihypertensive",
    "diltiazem": "antihypertensive",
    "hydrochlorothiazide": "antihypertensive",
    "furosemide": "antihypertensive",
    "spironolactone": "antihypertensive",
    "hydralazine": "antihypertensive",
    "clonidine": "antihypertensive",
    "antihypertensive": "antihypertensive",
    # Steroids (systemic corticosteroids)
    "prednisone": "steroid",
    "prednisolone": "steroid",
    "methylprednisolone": "steroid",
    "medrol": "steroid",
    "dexamethasone": "steroid",
    "hydrocortisone": "steroid",
    "cortisone": "steroid",
    "triamcinolone": "steroid",
    "steroid": "steroid",
}


def _match_medication_classes(meds_lower: str) -> set:
    """Return the set of MEDICATION_EXERCISE_WARNINGS class keys present in a medication list."""
    matched = set()
    # Sort by length descending so e.g. "insulin glargine" isn't shadowed weirdly by "insulin".
    for drug_name, med_class in sorted(_DRUG_NAME_TO_CLASS.items(), key=lambda x: len(x[0]), reverse=True):
        if re.search(rf"\b{re.escape(drug_name)}\b", meds_lower):
            matched.add(med_class)
    return matched


# Medication → exercise contraindications
MEDICATION_EXERCISE_WARNINGS: Dict[str, List[str]] = {
    "insulin": [
        "Monitor blood sugar closely during and after exercise",
        "Exercise may cause hypoglycemia — carry fast-acting carbohydrates",
    ],
    "beta blocker": [
        "Heart rate may not accurately reflect exercise intensity",
        "Use perceived exertion instead of heart rate to gauge intensity",
    ],
    "blood thinner": [
        "Avoid contact sports or high-injury-risk activities",
        "Inform healthcare provider of exercise plan",
    ],
    "antihypertensive": [
        "Avoid sudden position changes (dizziness risk)",
        "Stand up slowly after exercises",
    ],
    "steroid": [
        "May increase risk of muscle weakness — avoid overexertion",
        "Monitor for joint pain",
    ],
}

# Dietary condition contraindications
DIET_CONTRAINDICATIONS: Dict[str, List[str]] = {
    "diabetes": [
        "Focus on low-glycemic index foods",
        "Limit refined sugars and white carbohydrates",
        "Work with dietitian for carbohydrate counting",
    ],
    "hypertension": [
        "Limit sodium intake (< 2,300mg/day, ideally < 1,500mg/day)",
        "Follow DASH diet principles",
    ],
    "kidney disease": [
        "May need restrictions on potassium, phosphorus, protein",
        "Consult nephrologist or renal dietitian for personalized plan",
    ],
    "heart failure": [
        "Limit sodium intake (< 2,000mg/day)",
        "Monitor fluid intake as directed by doctor",
    ],
    "celiac": ["Strictly avoid all gluten-containing foods"],
    "lactose intolerance": ["Limit or avoid dairy products"],
    "gout": [
        "Limit purine-rich foods (red meat, organ meats, some seafood)",
        "Limit alcohol, especially beer",
    ],
}


def get_disclaimer(material_type: str) -> str:
    """Get the safety disclaimer for a material type."""
    return DISCLAIMERS.get(material_type, DISCLAIMERS["full_report"])


def check_exercise_contraindications(
    note_text: str,
    patient_data: Optional[Dict] = None,
) -> List[str]:
    """Check for exercise contraindications based on conditions in the note.

    Args:
        note_text: The clinical note text.
        patient_data: Optional patient data dict.

    Returns:
        List of contraindication warnings.
    """
    warnings = []
    note_lower = _expand_condition_abbreviations(note_text.lower())

    # Check conditions
    for condition, contras in EXERCISE_CONTRAINDICATIONS.items():
        if condition in note_lower:
            warnings.extend(contras)

    # Check medications — match real drug names against their class, since
    # patient medication lists contain drug names, not class labels.
    patient_meds = patient_data.get("medications", "") if patient_data else ""
    meds_lower = patient_meds.lower() if patient_meds else ""
    matched_classes = _match_medication_classes(meds_lower)
    for med_class, warnings_list in MEDICATION_EXERCISE_WARNINGS.items():
        if med_class in matched_classes:
            warnings.extend(warnings_list)

    return list(dict.fromkeys(warnings))  # Deduplicate, preserve order


def check_diet_contraindications(
    note_text: str,
    patient_data: Optional[Dict] = None,
) -> List[str]:
    """Check for dietary contraindications based on conditions in the note."""
    warnings = []
    note_lower = _expand_condition_abbreviations(note_text.lower())

    for condition, contras in DIET_CONTRAINDICATIONS.items():
        if condition in note_lower:
            warnings.extend(contras)

    # Check allergies from patient data
    allergies = patient_data.get("allergies", "") if patient_data else ""
    if allergies:
        warnings.append(f"Allergies noted: {allergies} — avoid these foods")

    return list(dict.fromkeys(warnings))


def validate_patient_data(patient_data: Dict) -> List[str]:
    """Validate patient data for diet/exercise plans.

    Args:
        patient_data: Dict with weight_kg, height_cm, goal, etc.

    Returns:
        List of validation warnings.
    """
    warnings = []

    weight = patient_data.get("weight_kg")
    height = patient_data.get("height_cm")

    if weight:
        try:
            w = float(weight)
            if w < 30 or w > 300:
                warnings.append(f"Weight ({weight}kg) seems unusual — please verify")
        except ValueError:
            warnings.append(f"Invalid weight value: {weight}")

    if height:
        try:
            h = float(height)
            if h < 100 or h > 250:
                warnings.append(f"Height ({height}cm) seems unusual — please verify")
        except ValueError:
            warnings.append(f"Invalid height value: {height}")

    # BMI calculation
    if weight and height:
        try:
            w = float(weight)
            h = float(height) / 100  # cm to m
            bmi = w / (h ** 2)
            if bmi < 15 or bmi > 50:
                warnings.append(f"Calculated BMI ({bmi:.1f}) seems unusual — please verify data")
        except (ValueError, ZeroDivisionError):
            warnings.append("Could not calculate BMI from provided data")

    goal = patient_data.get("goal", "").lower()
    if goal and goal not in ("maintain", "increase", "decrease", "lose", "gain"):
        warnings.append("Weight goal unclear — please specify: Maintain, Increase, or Decrease")

    return warnings


def check_missing_data(
    material_type: str,
    patient_data: Optional[Dict] = None,
) -> List[str]:
    """Check for missing data that affects material quality.

    Args:
        material_type: Type of material being generated.
        patient_data: Patient data dict (for diet/exercise).

    Returns:
        List of missing data warnings.
    """
    warnings = []

    if material_type in ("diet", "exercise"):
        if not patient_data:
            return ["No patient data provided — recommendations will be general"]

        if not patient_data.get("weight_kg"):
            warnings.append("Weight not provided — recommendations will be general")
        if not patient_data.get("height_cm"):
            warnings.append("Height not provided — BMI cannot be calculated")
        if not patient_data.get("goal"):
            warnings.append("Weight goal not specified — general guidance provided")

    return warnings
