"""
Patient materials generator service.

Generates patient-facing educational materials from clinical notes:
- Medication information sheets
- Diagnosis information sheets
- Issues & Current Plan summaries
- Diet plans (with interactive data)
- Exercise plans (with interactive data)
- Detailed patient reports
"""

import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from server.core.clinical_output_guard import ClinicalOutputRejected, build_guard_retry_prompt
from server.services.note_generator_clean import SimpleNoteGenerator
from server.services.patient_materials_extraction import missing_blocking
from server.services.patient_materials_sections import (
    extract_section,
    get_diagnosis_text,
    get_medication_text,
    parse_note_sections,
)
from server.services.patient_materials_safety import (
    check_diet_contraindications,
    check_exercise_contraindications,
    check_missing_data,
    get_disclaimer,
    validate_patient_data,
)

logger = logging.getLogger("patient_materials")


class PatientMaterialsError(Exception):
    """Raised when LLM generation fails for a patient material."""
    pass


class PatientMaterialsNeedsInput(Exception):
    """A blocking patient-data field is absent after note-based extraction.

    Carries the material type, the missing blocking field names, and the
    currently known (merged) patient data so the client can pre-fill its
    form and ask the clinician only for the gaps.
    """

    def __init__(self, material_type: str, missing: List[str], known: Dict[str, Any]):
        super().__init__(
            f"Patient data needed for {material_type}: {', '.join(missing)}"
        )
        self.material_type = material_type
        self.missing = list(missing)
        self.known = dict(known or {})



MATERIAL_TYPES = {
    "medications": "New Medication Information",
    "diagnosis": "New Diagnosis Information",
    "issues_plan": "Current Visit Summary",
    "diet": "Diet Plan",
    "exercise": "Exercise Plan",
    "full_report": "Comprehensive Health Report",
}

TEMPERATURE_MAP = {
    "medications": 0.1,
    "diagnosis": 0.1,
    "issues_plan": 0.15,
    "diet": 0.15,
    "exercise": 0.15,
    "full_report": 0.15,
}

# Detailed materials (diet meal options, full report) need far more room than 4096.
MAX_TOKENS_MAP = {
    "medications": 6000,
    "diagnosis": 6000,
    "issues_plan": 4000,
    "diet": 12000,
    "exercise": 8000,
    "full_report": 10000,
}

# Long generations exceed the note-gen default 90s timeout; allow more here only.
PM_TIMEOUT_SEC = 300.0


class PatientMaterialsGenerator:
    """Generate patient-facing materials from clinical notes."""

    def __init__(self, note_gen: SimpleNoteGenerator) -> None:
        self.note_gen = note_gen

    async def generate_one(
        self,
        material_type: str,
        note_text: str,
        sections: Optional[Dict[str, str]] = None,
        patient_data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if sections is None:
            sections = parse_note_sections(note_text)

        generator = getattr(self, f"_generate_{material_type}", None)
        if not generator:
            return {
                "error": f"Unknown material type: {material_type}",
                "content": "",
                "disclaimer": get_disclaimer(material_type),
                "safety_flags": [],
                "source_attribution": [],
            }

        start = time.time()
        try:
            content, attributions, safety_flags = await generator(
                note_text, sections, patient_data
            )
        except PatientMaterialsNeedsInput as e:
            elapsed = time.time() - start
            logger.info(
                "Needs-input for %s: missing=%s", material_type, e.missing
            )
            return {
                "status": "needs_input",
                "material_type": material_type,
                "missing_fields": e.missing,
                "known_data": e.known,
                "content": "",
                "source_attribution": [],
                "disclaimer": "",
                "safety_flags": [],
                "generated_at": "",
                "generation_time_sec": round(elapsed, 1),
                "error": None,
            }
        except PatientMaterialsError as e:
            elapsed = time.time() - start
            logger.error("Generation failed for %s: %s", material_type, e)
            return {
                "content": "",
                "source_attribution": [],
                "disclaimer": "",
                "safety_flags": [],
                "generated_at": "",
                "generation_time_sec": round(elapsed, 1),
                "error": str(e),
            }
        elapsed = time.time() - start

        logger.info(
            "Generated %s in %.1fs",
            MATERIAL_TYPES.get(material_type, material_type),
            elapsed,
        )

        return {
            "content": content,
            "source_attribution": attributions,
            "disclaimer": get_disclaimer(material_type),
            "safety_flags": safety_flags,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "generation_time_sec": round(elapsed, 1),
        }

    async def generate_all(
        self,
        note_text: str,
        sections: Optional[Dict[str, str]] = None,
        patient_data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Dict[str, Any]]:
        if sections is None:
            sections = parse_note_sections(note_text)

        results = {}
        for mat_type in MATERIAL_TYPES:
            results[mat_type] = await self.generate_one(
                mat_type, note_text, sections, patient_data
            )
        return results

    # --- Individual generators ---

    async def _generate_medications(
        self,
        note_text: str,
        sections: Dict[str, str],
        patient_data: Optional[Dict[str, Any]],
    ) -> Tuple[str, List[Dict], List[str]]:
        med_text = get_medication_text(sections) or ""
        plan_text = extract_section(sections, "plan") or ""

        if not med_text and not plan_text:
            return (
                "No medication information was found in the clinical note. "
                "If you have new prescriptions, please ask your doctor.",
                [],
                [],
            )

        prompt = self._build_system_prompt("medications")
        user_content = (
            "Relevant clinical note information:\n\n"
            "<PLAN_SECTION>\n" + plan_text + "\n</PLAN_SECTION>\n\n"
            "<MEDICATION_SECTION>\n" + med_text + "\n</MEDICATION_SECTION>\n\n"
            "Please generate a medication information sheet for the patient."
        )
        content = await self._call_llm(prompt, user_content, "medications")
        attributions = self._extract_attributions(plan_text, "Plan")
        return content, attributions, []

    async def _generate_diagnosis(
        self,
        note_text: str,
        sections: Dict[str, str],
        patient_data: Optional[Dict[str, Any]],
    ) -> Tuple[str, List[Dict], List[str]]:
        diag_text = get_diagnosis_text(sections)
        hpi_text = extract_section(sections, "hpi") or ""

        if not diag_text:
            return (
                "No diagnosis information was found in the clinical note.",
                [],
                [],
            )

        prompt = self._build_system_prompt("diagnosis")
        user_content = (
            "Relevant clinical note information:\n\n"
            "<DIAGNOSIS_SECTION>\n" + diag_text + "\n</DIAGNOSIS_SECTION>\n\n"
            "<HISTORY_SECTION>\n" + hpi_text + "\n</HISTORY_SECTION>\n\n"
            "Please generate a diagnosis information sheet explaining the diagnosis in plain language."
        )
        content = await self._call_llm(prompt, user_content, "diagnosis")
        attributions = self._extract_attributions(diag_text, "Assessment/Impression")
        return content, attributions, []

    async def _generate_issues_plan(
        self,
        note_text: str,
        sections: Dict[str, str],
        patient_data: Optional[Dict[str, Any]],
    ) -> Tuple[str, List[Dict], List[str]]:
        assessment = extract_section(sections, "assessments") or ""
        plan = extract_section(sections, "plan") or ""

        if not assessment and not plan:
            return (
                "No issues or plan information was found in the clinical note.",
                [],
                [],
            )

        prompt = self._build_system_prompt("issues_plan")
        user_content = (
            "Full clinical note:\n\n<CLINICAL_NOTE>\n"
            + note_text + "\n</CLINICAL_NOTE>\n\n"
            "Please generate an organized summary of each medical issue paired with its management plan."
        )
        content = await self._call_llm(prompt, user_content, "issues_plan")
        attributions = []
        if assessment:
            attributions.append({"section": "Assessment/Impression", "excerpt": assessment[:200]})
        if plan:
            attributions.append({"section": "Plan", "excerpt": plan[:200]})
        return content, attributions, []

    async def _generate_diet(
        self,
        note_text: str,
        sections: Dict[str, str],
        patient_data: Optional[Dict[str, Any]],
    ) -> Tuple[str, List[Dict], List[str]]:
        safety_flags = []

        if not patient_data:
            patient_data = {}

        # Blocking fields: weight/height/goal. Activity level is NON-blocking
        # (the prompt states its assumption when absent). Note-derived values
        # are merged into patient_data by the route BEFORE this call; the
        # model never invents them here.
        missing = missing_blocking("diet", patient_data)
        if missing:
            raise PatientMaterialsNeedsInput("diet", missing, patient_data)

        data_warnings = validate_patient_data(patient_data)
        safety_flags.extend(data_warnings)
        contra_warnings = check_diet_contraindications(note_text, patient_data)
        safety_flags.extend(contra_warnings)
        diet_missing = check_missing_data("diet", patient_data)
        safety_flags.extend(diet_missing)

        assessment = extract_section(sections, "assessments") or ""
        medications = extract_section(sections, "medications") or ""
        plan = extract_section(sections, "plan") or ""

        prompt = self._build_system_prompt("diet")
        patient_info = (
            "Age: " + str(patient_data.get("age", "Not provided")) + "\n"
            "Sex: " + str(patient_data.get("sex", "Not provided")) + "\n"
            "Weight: " + str(patient_data.get("weight_kg", "Not provided")) + " kg\n"
            "Height: " + str(patient_data.get("height_cm", "Not provided")) + " cm\n"
            "Goal: " + str(patient_data.get("goal", "Not specified")) + "\n"
            "Allergies: " + str(patient_data.get("allergies", "Not provided")) + "\n"
            "Activity level: " + str(patient_data.get("activity_level", "Not specified")) + "\n"
            "Dietary restrictions: " + str(patient_data.get("restrictions", "None specified"))
        )
        safety_block = ""
        if safety_flags:
            safety_block = "\n\nSAFETY FLAGS:\n" + "\n".join("- " + f for f in safety_flags)

        user_content = (
            "Patient data:\n\n<PATIENT_DATA>\n" + patient_info + "\n</PATIENT_DATA>\n\n"
            "<DIAGNOSIS_SECTION>\n" + assessment + "\n</DIAGNOSIS_SECTION>\n\n"
            "<MEDICATIONS_SECTION>\n" + medications + "\n</MEDICATIONS_SECTION>\n\n"
            "<PLAN_SECTION>\n" + plan + "\n</PLAN_SECTION>"
            + safety_block + "\n\n"
            "Please generate a personalized diet plan for this patient."
        )
        content = await self._call_llm(prompt, user_content, "diet")
        attributions = []
        if assessment:
            attributions.append({"section": "Assessment/Impression", "excerpt": assessment[:200]})
        if medications:
            attributions.append({"section": "Medications", "excerpt": medications[:200]})
        return content, attributions, safety_flags

    async def _generate_exercise(
        self,
        note_text: str,
        sections: Dict[str, str],
        patient_data: Optional[Dict[str, Any]],
    ) -> Tuple[str, List[Dict], List[str]]:
        safety_flags = []

        if not patient_data:
            patient_data = {}

        # Blocking fields: weight/height/goal (activity level is NON-blocking).
        missing = missing_blocking("exercise", patient_data)
        if missing:
            raise PatientMaterialsNeedsInput("exercise", missing, patient_data)

        data_warnings = validate_patient_data(patient_data)
        safety_flags.extend(data_warnings)
        contra_warnings = check_exercise_contraindications(note_text, patient_data)
        safety_flags.extend(contra_warnings)
        ex_missing = check_missing_data("exercise", patient_data)
        safety_flags.extend(ex_missing)

        assessment = extract_section(sections, "assessments") or ""
        medications = extract_section(sections, "medications") or ""
        pe = extract_section(sections, "physical_exam") or ""

        prompt = self._build_system_prompt("exercise")
        patient_info = (
            "Age: " + str(patient_data.get("age", "Not provided")) + "\n"
            "Sex: " + str(patient_data.get("sex", "Not provided")) + "\n"
            "Weight: " + str(patient_data.get("weight_kg", "Not provided")) + " kg\n"
            "Height: " + str(patient_data.get("height_cm", "Not provided")) + " cm\n"
            "Activity level: " + str(patient_data.get("activity_level", "Not specified")) + "\n"
            "Joint/mobility issues: " + str(patient_data.get("joint_issues", "None specified")) + "\n"
            "Goal: " + str(patient_data.get("goal", "Not specified"))
        )
        safety_block = ""
        if safety_flags:
            safety_block = "\n\nSAFETY FLAGS:\n" + "\n".join("- " + f for f in safety_flags)

        user_content = (
            "Patient data:\n\n<PATIENT_DATA>\n" + patient_info + "\n</PATIENT_DATA>\n\n"
            "<DIAGNOSIS_SECTION>\n" + assessment + "\n</DIAGNOSIS_SECTION>\n\n"
            "<MEDICATIONS_SECTION>\n" + medications + "\n</MEDICATIONS_SECTION>\n\n"
            "<PHYSICAL_EXAM_SECTION>\n" + pe + "\n</PHYSICAL_EXAM_SECTION>"
            + safety_block + "\n\n"
            "Please generate a personalized exercise plan for this patient."
        )
        content = await self._call_llm(prompt, user_content, "exercise")
        attributions = []
        if assessment:
            attributions.append({"section": "Assessment/Impression", "excerpt": assessment[:200]})
        if pe:
            attributions.append({"section": "Physical Exam", "excerpt": pe[:200]})
        return content, attributions, safety_flags

    async def _generate_full_report(
        self,
        note_text: str,
        sections: Dict[str, str],
        patient_data: Optional[Dict[str, Any]],
    ) -> Tuple[str, List[Dict], List[str]]:
        prompt = self._build_system_prompt("full_report")
        user_content = (
            "Clinical note (use ONLY to identify the patient's conditions, medications, and history):\n\n<CLINICAL_NOTE>\n"
            + note_text + "\n</CLINICAL_NOTE>\n\n"
            "Write the comprehensive, general Patient Health Summary as specified. "
            "Do NOT frame it around today's visit."
        )
        content = await self._call_llm(prompt, user_content, "full_report")
        attributions = []
        for section_name in ["patient_identification", "hpi", "assessments", "plan", "medications"]:
            section_text = extract_section(sections, section_name)
            if section_text:
                display_name = section_name.replace("_", " ").title()
                attributions.append({"section": display_name, "excerpt": section_text[:200]})
        return content, attributions, []

    # --- LLM helpers ---

    def _build_system_prompt(self, material_type: str) -> str:
        base = """You create clear, reliable, patient-facing health education documents. A clinician reviews every document before it reaches the patient, so be thorough and genuinely useful.

GROUNDING RULES:
- Identify the patient's conditions, medications, allergies, and care plan ONLY from the clinical note and any provided patient data. Do NOT invent diagnoses, medications, findings, lab values, or history the note does not contain.
- To EXPLAIN those identified conditions and medications, use accurate, evidence-based, established medical knowledge so the patient gets reliable, detailed information and does not need to search online for it.
- Treat synonymous conditions or drugs as ONE item; never list the same thing twice under different names (for example COPD and obstructive airway disease are the same; hypertension and high blood pressure are the same).

STYLE:
- Plain, respectful language. Explain any medical term in parentheses the first time it appears.
- Detailed but scannable: markdown headings (##), short paragraphs, bullet points, and tables where helpful.
- Print-friendly. Do NOT repeat long disclaimers or generic boilerplate in every section; a short footer is added automatically."""

        type_specific = {
            "diagnosis": """

DOCUMENT: New Diagnosis Information

Identify any condition NEWLY diagnosed, newly confirmed, or first addressed at this encounter.
- If one or more conditions are newly diagnosed, focus on them. For EACH, under its own heading, cover: what it is; common causes and risk factors; typical symptoms; how it is diagnosed and monitored; how it is managed (lifestyle and medical, in general terms); warning signs that need urgent care; and the realistic outlook with good control. Be detailed and reliable.
- Keep each section focused and reasonably concise (3-6 short bullets per sub-point). Do NOT pad the document with repeated, near-identical, or boilerplate sentences — a clean, scannable document is more useful than a long one, and a clinician will add any further detail needed.
- If NO condition is newly diagnosed, state clearly "No new diagnosis was made at this visit," then give a brief, helpful paragraph about each of the patient's current or ongoing conditions.
- Never duplicate synonymous conditions.""",
            "medications": """

DOCUMENT: New Medication Information

Identify medications NEWLY prescribed or started at this encounter (new drugs, or a change that amounts to new therapy).
- For EACH newly prescribed medication give complete, reliable information so the patient need not call or search online: generic name (and common brand) and drug class; what it treats (tie to this patient's diagnosis); exactly how to take it (dose, route, frequency, timing, with or without food); what to do about a missed dose; common side effects and which usually settle; serious side effects or red flags needing urgent care; important interactions and what to avoid (foods, alcohol, other drugs); and practical tips (storage, what to expect, how long until it works).
- If NOTHING was newly prescribed, say so, then briefly list the patient's current medications in a simple format: name -- what it is for -- how it is taken.
- Include one clear line telling the patient never to start, stop, or change a medication without first talking to their doctor or pharmacist.""",
            "issues_plan": """

DOCUMENT: Current Visit Summary

Summarize this visit for the patient.
- List each medical issue addressed. For each: current status, the plan or management, and the follow-up timeline.
- Note which issues are new versus ongoing, and group related issues. Keep it clear and organized.""",
            "diet": """

DOCUMENT: Diet Plan (detailed; a clinician will review it)

Use the patient's data (age, sex, weight, height, goal, activity level, allergies, restrictions) and their conditions and medications from the note. Produce the plan in EXACTLY this order:

## Overview & Goals
The patient's situation in plain language and the dietary goal.

## General Guidelines
What to do and what to avoid for THIS patient's conditions (for example low sodium for hypertension or heart failure; low glycemic index and carbohydrate control for diabetes; potassium/phosphorus/protein limits for kidney disease). Foods to emphasize and foods to limit.

## Daily Targets
- Total daily calories: estimate with the Mifflin-St Jeor equation from the patient's weight, height, and the age/sex given in the patient data (use exactly those values — do not estimate a different age), times an activity factor, then adjust for the goal (about a 500 kcal/day deficit for weight loss, a surplus for gain, maintenance otherwise). Give a range and state your assumptions.
- Macronutrients in grams and % (protein, carbohydrate, fat) appropriate to the conditions.
- Fluid target (mL/day), adjusted for conditions (for example restriction in heart failure or kidney disease when indicated).
- Sodium target (mg/day) when a condition warrants it; otherwise general guidance.
- Any other relevant targets (fiber, potassium or phosphorus limits, etc.).

## Precautions
Diet cautions specific to the patient's conditions and medications (for example warfarin and consistent vitamin K intake, grapefruit interactions, hypoglycemia risk).

## Meal Plan
Provide Breakfast, Lunch, Dinner, and a Snack section.
- For EACH of the three meals give AT LEAST 7 distinct options; for the Snack give AT LEAST 7 distinct options (about 28 options in total).
- Present each meal's options as a single compact markdown table with these columns and ONE header row: `Option | Foods & portions | Calories | Protein (g) | Carbs (g) | Fat (g)`. Put each option on its own row. Write the column header only once at the top of each meal's table — do NOT repeat the header or the words "calories protein carbs fat" for every option.
- For the Snack use the same table format (portions and macros for one typical serving).
- Options should fit the per-meal share of the daily calorie target and respect the patient's allergies and restrictions; make most options fit the patient's relevant dietary theme (e.g. heart-healthy, diabetes-appropriate, low-sodium).
- Keep tables clean so they print well.

If weight, height, or goal is missing, state the assumption you used.""",
            "exercise": """

DOCUMENT: Exercise Plan (detailed; a clinician will review it). Safety first.

Use the patient's data (age, sex, weight, height, goal, activity level, joint or mobility issues) and their conditions and medications from the note. Produce the plan in EXACTLY this order:

## Overview & Goals

## General Guidelines
What to do and avoid for this patient's situation.

## Precautions & Safety
Condition- and medication-specific cautions (for example cardiac limits; using perceived exertion or the talk test rather than heart rate on beta-blockers; hypoglycemia with diabetes; joint protection in arthritis; fall and impact precautions in osteoporosis). List red-flag symptoms that mean STOP and seek help (chest pain, severe breathlessness, dizziness, fainting). Note when medical clearance is advised.

## Weekly Plan
Total recommended weekly volume (aerobic minutes, resistance sessions, flexibility and balance work), following ACSM and AHA guidance, scaled to the patient's current level.

## Daily Schedule
A 7-day schedule. For EACH day give AT LEAST 2 options (Option A and Option B) with activity type, duration, and intensity (talk test or RPE). Include warm-up, cool-down, and rest days. Start conservative and progress gradually.

If weight, height, goal, or activity level is missing, state the assumption you used (a missing activity level means assume a lightly active baseline).""",
            "full_report": """

DOCUMENT: Comprehensive Health Report

IMPORTANT: This is a GENERAL, comprehensive overview of the patient's overall health and care -- NOT a summary of today's visit. Do not frame it around "this encounter" or "today." Write it as a standing reference the patient can keep.

Cover, as applicable, with clear headed sections:
1. Patient overview (general terms)
2. Active medical conditions -- each briefly explained with current status
3. Current medications -- what each is for and how it is taken
4. Monitoring and follow-up -- what to track, relevant labs or tests, target values, and a sensible follow-up cadence
5. Lifestyle -- diet, exercise, weight, and smoking or alcohol guidance relevant to the conditions
6. Preventive care -- screenings and vaccinations relevant to the patient's conditions and demographics (general, evidence-based recommendations)
7. Warning signs -- when to seek care, for each major condition
8. Questions to ask the care team

Do NOT include ICD or CPT codes or any billing or encounter framing.""",
        }

        return base + type_specific.get(material_type, "")

    async def _call_llm(
        self,
        system_prompt: str,
        user_content: str,
        material_type: str,
    ) -> str:
        """Generate material content with a guard-retry so a single stochastic
        degeneration (repeated n-gram loop / truncation) doesn't hard-fail the
        user. On a rejected draft, retry once at a moderate temperature (0.5)
        with build_guard_retry_prompt to draw a genuinely new sample that can
        escape a content-driven n-gram loop (temp 0.0 would deterministically
        re-roll the same degenerate path). If both drafts are rejected we
        surface the guard reason (never fabricate patient material)."""
        temperature = TEMPERATURE_MAP.get(material_type, 0.15)

        # SimpleNoteGenerator has no .generate(); its chat payload is a single user
        # message (no system role), so fold the system prompt into the prompt string.
        prompt = (system_prompt or "").strip() + chr(10) + chr(10) + (user_content or "").strip()
        attempt_prompt = prompt.strip()
        last_reasons: Tuple[str, ...] = ()
        for attempt in range(2):
            try:
                response = await self.note_gen.collect_completion(
                    attempt_prompt,
                    # Retry at a moderate temperature so a rejected (degenerate)
                    # draft gets a genuinely NEW sample that can escape an
                    # n-gram loop. Temperature 0.0 would deterministically
                    # re-roll the same degenerate path for a content-driven loop.
                    temperature=0.5 if attempt else temperature,
                    max_tokens=MAX_TOKENS_MAP.get(material_type, 4096),
                    timeout_sec=PM_TIMEOUT_SEC,
                )
                return response or ""
            except ClinicalOutputRejected as exc:
                last_reasons = (str(exc),)
                logger.warning(
                    "patient-materials %s rejected on attempt %d of 2: %s",
                    material_type, attempt + 1, exc,
                )
                if attempt == 0:
                    attempt_prompt = build_guard_retry_prompt(prompt.strip(), last_reasons)
            except PatientMaterialsError:
                raise
            except Exception as e:
                logger.error("LLM call failed for %s: %s", material_type, e)
                raise PatientMaterialsError(str(e)) from e
        raise PatientMaterialsError("; ".join(last_reasons) or "unsafe model output")

    def _extract_attributions(
        self, text: str, section_name: str
    ) -> List[Dict[str, str]]:
        if not text.strip():
            return []
        return [{"section": section_name, "excerpt": text[:300]}]
