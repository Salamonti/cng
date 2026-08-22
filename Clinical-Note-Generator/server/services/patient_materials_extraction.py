"""Note-based parameter extraction for diet/exercise plans.

Interactive flow: instead of hard-blocking when vitals are absent, the
system first reads the note (deterministic regex + one small LLM call) and
surfaces what it found so the clinician can confirm/fill only the gaps.

Merge rules (authoritative, in order):
  1. User-entered value ALWAYS wins.
  2. Numeric (age/weight/height): regex and LLM must agree (within 0.5) or
     the value is treated as missing (safer to ask than to guess a number
     that feeds calorie math). Regex is preferred when they agree.
  3. Qualitative (goal/activity/allergies/restrictions/joint_issues):
     LLM value used (regex is unreliable for these); None if absent.

Nothing here is fatal: any failure (LLM offline, unparseable JSON) degrades
to regex-only extraction, and the worst case is the pre-interactive
behavior — the plan is generated with the values that are known.
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Blocking fields per plan type. Activity level is deliberately NON-blocking:
# a plan can be generated without it (the prompt then states its assumption).
REQUIRED_FIELDS = {
    "diet": ["weight_kg", "height_cm", "goal"],
    "exercise": ["weight_kg", "height_cm", "goal"],
}

# Fields the prompt consumes but never blocks on.
EXTRACTABLE_FIELDS = [
    "age", "sex", "weight_kg", "height_cm", "goal",
    "activity_level", "allergies", "restrictions", "joint_issues",
]

_NUM_RANGES = {
    "age": (1, 120),
    "weight_kg": (20, 400),
    "height_cm": (50, 260),
}

_ACTIVITY_CHOICES = {"sedentary", "lightly_active", "moderately_active", "very_active"}
_GOAL_CHOICES = {"maintain", "increase", "decrease"}

# ---------------------------------------------------------------- regex tier


def _in_range(field: str, value: float) -> bool:
    lo, hi = _NUM_RANGES[field]
    return lo <= value <= hi


def extract_from_note_regex(note_text: str) -> Dict[str, Optional[Any]]:
    """High-precision numeric/demographic extraction from note text."""
    out: Dict[str, Optional[Any]] = {"age": None, "weight_kg": None,
                                     "height_cm": None, "sex": None}
    if not note_text:
        return out
    text = note_text

    # Age: "68-year-old", "68 year old", "age 68", "68 yo"
    for m in re.finditer(r"(\d{1,3})\s*-?\s*year[\s-]*old", text, re.I):
        v = int(m.group(1))
        if _in_range("age", v):
            out["age"] = v
            break
    if out["age"] is None:
        for m in re.finditer(r"\bag[e]?\s*[:=]?\s*(\d{1,3})\b", text, re.I):
            v = int(m.group(1))
            if _in_range("age", v):
                out["age"] = v
                break
    if out["age"] is None:
        for m in re.finditer(r"\b(\d{1,3})\s*(?:year|yr)?\s*yo\b", text, re.I):
            v = int(m.group(1))
            if _in_range("age", v):
                out["age"] = v
                break

    # Weight: "92 kg", "weight: 92", "weight of 85.5 kg"
    for m in re.finditer(r"(\d{2,3}(?:\.\d)?)\s*(?:kg|kilograms?|kilo)\b", text, re.I):
        v = float(m.group(1))
        if _in_range("weight_kg", v):
            out["weight_kg"] = v
            break

    # Height: "165 cm", "height 5'9\"" is intentionally NOT handled (units
    # differ too much to convert confidently — treat as missing, ask).
    for m in re.finditer(r"(\d{2,3})\s*cm\b", text, re.I):
        v = float(m.group(1))
        if _in_range("height_cm", v):
            out["height_cm"] = v
            break

    # Sex: prefer patient-referencing mentions.
    for m in re.finditer(r"\b(patient|he|she|man|woman|male|female)\b", text, re.I):
        word = m.group(1).lower()
        if word in ("man", "male"):
            out["sex"] = "male"
            break
        if word in ("woman", "female"):
            out["sex"] = "female"
            break
    return out


# ----------------------------------------------------------------- LLM tier

_LLM_PROMPT = """You are extracting structured patient parameters from a clinical
note for use in diet and exercise planning.

Return STRICT JSON only (no prose, no markdown fences) with EXACTLY these keys:
{{
  "age": <integer or null>,
  "weight_kg": <number or null>,
  "height_cm": <number or null>,
  "sex": "male" | "female" | null,
  "goal": "maintain" | "increase" | "decrease" | null,
  "activity_level": "sedentary" | "lightly_active" | "moderately_active" | "very_active" | null,
  "allergies": <short string or null>,
  "restrictions": <short string or null>,
  "joint_issues": <short string or null>
}}

Rules:
- Use ONLY information stated in the note. If a value is not stated, use null.
- age: patient's age in years if stated anywhere.
- weight_kg / height_cm: numeric, in kg / cm. Convert lb to kg (divide by 2.2)
  and inches to cm only when the note states those units; round to 1 decimal.
- sex: "male" or "female" based on the patient described.
- goal: only when a weight goal is stated (weight loss -> "decrease",
  weight gain -> "increase", maintaining weight -> "maintain").
- activity_level: from the patient's described exercise habits (little or no
  exercise -> sedentary; light 1-3 days/week -> lightly_active; moderate
  3-5 days/week -> moderately_active; hard 6-7 days/week -> very_active).
- allergies / restrictions / joint_issues: short phrases, or null.

CLINICAL NOTE:
<NOTE>
{note}
</NOTE>"""


async def extract_from_note_llm(
    note_text: str,
    note_gen,
    timeout_sec: float = 60.0,
) -> Dict[str, Optional[Any]]:
    """One small LLM call returning the EXTRACTABLE_FIELDS dict.

    Returns {} on any failure (network, parse, timeout) — extraction is
    best-effort and must never block plan generation.
    """
    if not note_text:
        return {}
    try:
        raw = await note_gen.collect_completion(
            _LLM_PROMPT.format(note=note_text[:6000]),
            temperature=0.0,
            max_tokens=400,
            stop=["__STOP__"],
            timeout_sec=timeout_sec,
        )
    except Exception as exc:  # noqa: BLE001 - best-effort by design
        logger.info("[PM-EXTRACT] LLM extraction failed (%s); regex only", exc)
        return {}

    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            logger.info("[PM-EXTRACT] LLM returned no JSON; regex only")
            return {}
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return {}
    if not isinstance(data, dict):
        return {}

    out: Dict[str, Optional[Any]] = {}
    for k in ("age", "weight_kg", "height_cm"):
        try:
            v = data.get(k)
            v = int(float(v)) if k == "age" else round(float(v), 1)
        except (TypeError, ValueError):
            v = None
        if v is not None and _in_range(k, v):
            out[k] = v
        else:
            out[k] = None
    sex = str(data.get("sex") or "").lower().strip()
    out["sex"] = sex if sex in ("male", "female") else None
    goal = str(data.get("goal") or "").lower().strip()
    out["goal"] = goal if goal in _GOAL_CHOICES else None
    act = str(data.get("activity_level") or "").lower().strip()
    out["activity_level"] = act if act in _ACTIVITY_CHOICES else None
    for k in ("allergies", "restrictions", "joint_issues"):
        v = data.get(k)
        out[k] = str(v).strip()[:200] if v else None
    return out


def merge_extraction(
    user_data: Optional[Dict[str, Any]],
    regex_vals: Dict[str, Optional[Any]],
    llm_vals: Dict[str, Optional[Any]],
) -> Dict[str, Any]:
    """Merge user input + regex + LLM into a final patient_data dict.

    User values always win. For numerics, regex and LLM must agree
    (within 0.5) or the field is dropped (None). Qualitative fields
    prefer the LLM value over regex (regex has no qualitative fields,
    but the rule is documented here for future callers).
    """
    user_data = user_data or {}
    merged: Dict[str, Any] = {}

    for k in EXTRACTABLE_FIELDS:
        user_v = user_data.get(k)
        if user_v not in (None, ""):
            merged[k] = user_v
            continue
        rv, lv = regex_vals.get(k), llm_vals.get(k)
        if k in _NUM_RANGES:
            if rv is not None and lv is not None:
                rv_f: float = float(rv)
                lv_f: float = float(lv)
                if abs(rv_f - lv_f) > 0.5:
                    merged[k] = None  # disagreement -> ask
                else:
                    merged[k] = rv
            else:
                merged[k] = rv if rv is not None else lv
        else:
            merged[k] = lv if lv is not None else rv
    return merged


def missing_blocking(
    material_type: str,
    patient_data: Optional[Dict[str, Any]],
) -> List[str]:
    """Blocking fields still absent after merging. Activity level excluded
    by design (non-blocking)."""
    if material_type not in REQUIRED_FIELDS:
        return []
    data = patient_data or {}
    return [f for f in REQUIRED_FIELDS[material_type] if not data.get(f)]
