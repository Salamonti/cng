import re
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional
import json
from server.core.preprocessing import PreprocessingPipeline, TokenBudgetTruncator
from server.core.preprocessing.constants import effective_preprocessing_config
from server.core.prompt.policy_v2 import canonical_system_prompt

CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "config.json"

_CONTROL_CHARS_RE = re.compile(r"[\x00-\x09\x0b-\x1f\x7f]")
_FORMAT_SYMBOLS_RE = re.compile(r"[#*=_+\-]{3,}")


def load_config() -> Dict:
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _has_minimum_signal(text: str, *, min_alnum: int) -> bool:
    if not text:
        return False
    return sum(1 for ch in text if ch.isalnum()) >= min_alnum


def _sanitize_chart_text(text: str) -> str:
    if not text:
        return ""
    cleaned = _CONTROL_CHARS_RE.sub(" ", text)
    cleaned = _FORMAT_SYMBOLS_RE.sub(" ", cleaned)
    cleaned = re.sub(r"\s{3,}", "  ", cleaned)
    cleaned = cleaned.strip()
    return cleaned if _has_minimum_signal(cleaned, min_alnum=10) else text.strip()


def _sanitize_transcription_text(text: str) -> str:
    if not text:
        return ""
    cleaned = _CONTROL_CHARS_RE.sub(" ", text)
    cleaned = re.sub(r"\s{3,}", "  ", cleaned)
    cleaned = cleaned.strip()
    return cleaned if _has_minimum_signal(cleaned, min_alnum=6) else text.strip()


def _fill_template(tpl: str, values: dict) -> str:
    out = tpl
    for k, v in values.items():
        out = out.replace("{" + k + "}", str(v))
    return out


def _apply_region_substitution(text: str, user_location: Optional[str]) -> str:
    """Replace legacy Nova Scotia wording when profile/encounter location is set (P4)."""
    loc = (user_location or "").strip()
    if not loc or not text:
        return text
    t = text.replace("Nova Scotia, Canada", loc)
    t = t.replace("Nova Scotia", loc)
    return t


PROFILE_LOCATION_MARKER = "[Profile location]"
PROFILE_AUTHOR_MARKER = "[Profile author]"


def _author_primary_label(display_name: Optional[str], email: Optional[str]) -> str:
    """Prefer profile display name; else account email; else placeholder for templates."""
    dn = (display_name or "").strip()
    if dn:
        return dn
    em = (email or "").strip()
    if em:
        return em
    return "Not specified"


def _ensure_profile_author_in_system(system_filled: str, author_label: Optional[str]) -> str:
    """Ensure SYSTEM names the authoring clinician when the account is known."""
    label = (author_label or "").strip()
    if not label or label == "Not specified":
        return system_filled
    base = (system_filled or "").strip()
    if PROFILE_AUTHOR_MARKER in base:
        return base
    if label in base:
        return base
    line = (
        f"{PROFILE_AUTHOR_MARKER} The clinician authoring this note is: {label}. "
        "Write the note entirely in the first person as this clinician — use 'I' or 'we', "
        "never 'the clinician', 'the physician', 'the provider', or any other third-person label for the author."
    )
    return f"{base}\n\n{line}".strip() if base else line


def _ensure_profile_location_in_system(system_filled: str, user_location: Optional[str]) -> str:
    """
    When `User.default_location` is set, ensure the SYSTEM block explicitly orients the model
    to that practice/region even if operator `config.json` omits `{USER_LOCATION}`.
    Skips if the location text already appears in the system string or the marker was added.
    """
    loc = (user_location or "").strip()
    if not loc:
        return system_filled
    base = (system_filled or "").strip()
    if PROFILE_LOCATION_MARKER in base:
        return base
    if loc in base:
        return base
    line = (
        f"{PROFILE_LOCATION_MARKER} Practice or region: {loc}. "
        "Tailor the note to this setting: use appropriate local conventions, documentation norms, "
        "and jurisdiction where relevant."
    )
    return f"{base}\n\n{line}".strip() if base else line


def _compose_user_section(user_instructions: str, encounter_addendum: Optional[str]) -> str:
    """Single USER block: note-type instructions plus optional encounter addendum (P4)."""
    base = (user_instructions or "").strip()
    add = (encounter_addendum or "").strip()
    if not add:
        return base
    if base:
        return base + "\n\nCLINICIAN ENCOUNTER-SPECIFIC INSTRUCTION:\n" + add
    return "CLINICIAN ENCOUNTER-SPECIFIC INSTRUCTION:\n" + add


def _prompt_values(
    *,
    today: str,
    note_type: str,
    user_speciality: Optional[str],
    user_location: Optional[str],
    raw_data: str,
    infer: str = "the current encounter data",
    user_display_name: Optional[str] = None,
    user_email: Optional[str] = None,
) -> Dict[str, str]:
    speciality = (user_speciality or "").strip() or "internal medicine"
    loc = (user_location or "").strip() or "Not specified"
    unkn = "Not documented"
    primary = _author_primary_label(user_display_name, user_email)
    em = (user_email or "").strip() or "Not specified"
    return {
        "CURRENT_DATE": today,
        "NOTE_TYPE": note_type,
        "USER_SPECIALITY": speciality,
        "USER_LOCATION": loc,
        "USER_DISPLAY_NAME": primary,
        "USER_EMAIL": em,
        "REASON_FOR_VISIT": unkn,
        "ADMISSION_DX": unkn,
        "DISCHARGE_DX": unkn,
        "RAW_DATA": raw_data,
    }


def _cfg_text(val: Any) -> str:
    if val is None:
        return ""
    if isinstance(val, str):
        return val.strip()
    if isinstance(val, list):
        parts: List[str] = []
        for x in val:
            if x is None:
                continue
            parts.append(x if isinstance(x, str) else str(x))
        return "\n\n".join(part.strip() for part in parts if part.strip()).strip()
    return str(val).strip()


#: Note types whose CURRENT_ENCOUNTER block is not a dictated encounter at
#: all -- the clinician pastes material to be reviewed (an incoming referral
#: letter, an outside report, a chart extract). Framing that content as
#: "the transcription from today's clinical encounter" made the model write
#: in the treating clinician's voice about a visit that never happened
#: ("I evaluated Lucy Dugas...") and impose encounter-note structure on a
#: request for a summary.
DOCUMENT_ORIENTED_NOTE_TYPES = {"summarize", "pre_encounter_prep", "custom"}


def is_document_oriented_note_type(note_type: str) -> bool:
    return (note_type or "").strip().lower() in DOCUMENT_ORIENTED_NOTE_TYPES


def _source_block_framing(note_type: str) -> tuple[str, str]:
    """Return (opening line, currency line) for the primary source block."""
    if is_document_oriented_note_type(note_type):
        return (
            "This is source material supplied for review. It may be a document "
            "written by another clinician or service.\n",
            "Do not assume it describes an encounter you conducted. Do not write "
            "in the first person as the treating clinician unless the source "
            "documents your own actions.\n",
        )
    return (
        "This is the transcription from today's clinical encounter.\n",
        "Treat all information in this section as CURRENT.\n",
    )


def _current_encounter_data_preamble(note_type: str) -> str:
    nt = (note_type or "").strip().lower()
    if nt in DOCUMENT_ORIENTED_NOTE_TYPES:
        # No note sections to file findings into -- these produce prose or a
        # review list, so any section-routing instruction is noise at best.
        return ""
    if nt == "multi_issue_soap":
        return (
            "Subjective: document the patient's statements in third person only (Patient reports…; "
            "never I/my/me/we as the patient).\n"
            "Objective: examination findings, vitals, labs, and imaging from this block belong under "
            "## Objective under the numbered issue they relate to—not in Subjective.\n"
        )
    # Deliberately does NOT name "Physical Exam": under prompt policy v2 no
    # note type emits that heading. It is "Objective Findings" (progress),
    # "Relevant Examination and Results" (followup), "Examination"
    # (admission), "Current Findings" (referral), "Findings" (procedure), and
    # absent entirely from discharge/transfer/consult. Naming a heading the
    # selected template does not define either invents a stray section or
    # gets ignored, so defer to the note-type instruction instead.
    return (
        "Physical examination: any vitals, examiner observations, or system-specific "
        "objective findings in this block belong in whichever examination or objective "
        "section the selected note type defines. If it defines none, omit them rather "
        "than inventing a section. List only systems with explicit findings and omit "
        "unmentioned systems.\n"
    )


def _labs_imaging_data_preamble(note_type: str) -> str:
    nt = (note_type or "").strip().lower()
    if nt in DOCUMENT_ORIENTED_NOTE_TYPES:
        return ""
    if nt == "multi_issue_soap":
        return (
            "Laboratory and imaging results here are objective data. Place each under the matching "
            "numbered issue in ## Objective (test, date if known, key findings)—do not omit CT/MRI/X-ray "
            "results that apply to an active problem.\n"
        )
    return (
        "Laboratory and imaging results in this section belong in whichever objective, "
        "investigations, or results section the selected note type defines—not only in "
        "Subjective or HPI.\n"
    )


def _apply_preprocessing(
    cfg: Dict,
    transcription_text: str,
    old_visits_text: str,
    mixed_other_text: str,
) -> tuple[str, str, str]:
    trans_clean = _sanitize_transcription_text(transcription_text).strip()
    old_clean = _sanitize_chart_text(old_visits_text).strip()
    mixed_clean = _sanitize_chart_text(mixed_other_text).strip()

    effective_cfg = dict(cfg or {})
    effective_cfg["preprocessing"] = effective_preprocessing_config(cfg)
    pipeline = PreprocessingPipeline(effective_cfg)

    trans_clean = pipeline.normalize_whitespace(trans_clean).strip()
    old_clean = pipeline.process(old_clean).strip()
    mixed_clean = pipeline.process(mixed_clean).strip()

    truncator = TokenBudgetTruncator(effective_cfg)
    if trans_clean:
        trans_clean = truncator.truncate_section(trans_clean, section="current_encounter").strip()
    if old_clean:
        old_clean = truncator.truncate_section(old_clean, section="prior_visits").strip()
    if mixed_clean:
        mixed_clean = truncator.truncate_section(mixed_clean, section="labs_imaging_other").strip()

    # P3-2: each section above was independently kept within its OWN budget,
    # but nothing checked the sum of all three against the model's real
    # context window -- a complex patient with all three sections near their
    # individual maximums could still exceed it. current_encounter (today's
    # live dictation) keeps its full allotment; the two historical-context
    # sections give up space if the aggregate is over.
    trans_clean, old_clean, mixed_clean = truncator.enforce_aggregate_budget(
        current_encounter=trans_clean, prior_visits=old_clean, labs_imaging_other=mixed_clean
    )

    return trans_clean, old_clean, mixed_clean


def _build_patient_data(
    cfg: Dict,
    *,
    transcription_text: str,
    old_visits_text: str,
    mixed_other_text: str,
    note_type: str,
    today: str,
) -> str:
    """Build consistently tagged, chronologically explicit patient-data blocks."""
    trans_clean, old_clean, mixed_clean = _apply_preprocessing(
        cfg, transcription_text, old_visits_text, mixed_other_text
    )
    sections: List[str] = []
    if trans_clean:
        sections.append(
            "<CURRENT_ENCOUNTER>\n"
            "DATE: " + today + "\n"
            + _source_block_framing(note_type)[0]
            + _source_block_framing(note_type)[1]
            + _current_encounter_data_preamble(note_type)
            + trans_clean
            + "\n</CURRENT_ENCOUNTER>"
        )
    if old_clean:
        sections.append(
            "<PRIOR_VISITS>\n"
            "These are records from previous encounters. Treat them as HISTORICAL unless "
            "a dated item is explicitly relevant to the current encounter.\n"
            + old_clean
            + "\n</PRIOR_VISITS>"
        )
    if mixed_clean:
        sections.append(
            "<LABS_IMAGING_OTHER>\n"
            "These are laboratory results, imaging reports, and other clinical records. "
            "Use each item's documented date and status.\n"
            + _labs_imaging_data_preamble(note_type)
            + mixed_clean
            + "\n</LABS_IMAGING_OTHER>"
        )
    return "\n\n".join(sections) if sections else "[No patient data provided]"


def build_prompt_v8(
    transcription_text: str,
    old_visits_text: str,
    mixed_other_text: str,
    note_type: str,
    custom_prompt: Optional[str] = None,
    user_speciality: Optional[str] = None,
    merged_user_prompts: Optional[Dict[str, Any]] = None,
    user_location: Optional[str] = None,
    user_display_name: Optional[str] = None,
    user_email: Optional[str] = None,
) -> str:
    cfg = load_config()
    today = date.today().strftime("%Y-%m-%d")

    system_prompt = canonical_system_prompt(cfg.get("default_note_system_prompt"))

    user_templates = (
        merged_user_prompts
        if merged_user_prompts is not None
        else (cfg.get("default_note_user_prompts", {}) or {})
    )
    raw_user_tpl = ""
    if isinstance(user_templates, dict):
        raw_user_tpl = user_templates.get(note_type) or ""
    user_tpl = _cfg_text(raw_user_tpl)

    if not user_tpl:
        user_tpl = (
            "Note type: {NOTE_TYPE}\n"
            "Current date: {CURRENT_DATE}\n"
            "Generate a clinical note based on the provided patient data.\n"
        )

    raw_data = _build_patient_data(
        cfg,
        transcription_text=transcription_text,
        old_visits_text=old_visits_text,
        mixed_other_text=mixed_other_text,
        note_type=note_type,
        today=today,
    )

    values = _prompt_values(
        today=today,
        note_type=note_type,
        user_speciality=user_speciality,
        user_location=user_location,
        raw_data=raw_data,
        user_display_name=user_display_name,
        user_email=user_email,
    )

    system_prompt_filled = _fill_template(system_prompt, values).strip() if system_prompt else ""
    system_prompt_filled = _apply_region_substitution(system_prompt_filled, user_location)
    system_prompt_filled = _ensure_profile_location_in_system(system_prompt_filled, user_location)
    author_label = _author_primary_label(user_display_name, user_email)
    system_prompt_filled = _ensure_profile_author_in_system(system_prompt_filled, author_label)
    user_instructions = _fill_template(user_tpl, values).strip()
    user_block = _compose_user_section(user_instructions, custom_prompt)

    prompt_body = ""
    if system_prompt_filled:
        prompt_body += "SYSTEM:\n" + system_prompt_filled + "\n\n"

    prompt_body += "USER:\n" + user_block + "\n\n"
    prompt_body += "PATIENT DATA:\n" + raw_data + "\n\n"

    prompt_body += "When finished, output END_OF_NOTE on its own line and stop.\n\n"
    prompt_body += "ASSISTANT:\n"
    return prompt_body


def build_prompt_other(
    transcription_text: str,
    old_visits_text: str,
    mixed_other_text: str,
    note_type: str,
    custom_prompt: Optional[str] = None,
    user_speciality: Optional[str] = None,
    merged_user_prompts_other: Optional[Dict[str, Any]] = None,
    user_location: Optional[str] = None,
    user_display_name: Optional[str] = None,
    user_email: Optional[str] = None,
) -> str:
    cfg = load_config()
    today = date.today().strftime("%Y-%m-%d")

    system_prompt = canonical_system_prompt(cfg.get("default_note_system_prompt"))

    user_templates_other = (
        merged_user_prompts_other
        if merged_user_prompts_other is not None
        else (cfg.get("default_note_user_prompts_other", {}) or {})
    )
    raw_user_tpl = ""
    if isinstance(user_templates_other, dict):
        raw_user_tpl = user_templates_other.get(note_type) or ""
    user_tpl = _cfg_text(raw_user_tpl)

    if not user_tpl:
        user_tpl = (
            "Note type: {NOTE_TYPE}\n"
            "Current date: {CURRENT_DATE}\n"
            "Generate the requested clinical document based on the provided patient data.\n"
        )

    raw_data = _build_patient_data(
        cfg,
        transcription_text=transcription_text,
        old_visits_text=old_visits_text,
        mixed_other_text=mixed_other_text,
        note_type=note_type,
        today=today,
    )

    values = _prompt_values(
        today=today,
        note_type=note_type,
        user_speciality=user_speciality,
        user_location=user_location,
        raw_data=raw_data,
        infer="the provided data",
        user_display_name=user_display_name,
        user_email=user_email,
    )

    system_prompt_filled = _fill_template(system_prompt, values).strip() if system_prompt else ""
    system_prompt_filled = _apply_region_substitution(system_prompt_filled, user_location)
    system_prompt_filled = _ensure_profile_location_in_system(system_prompt_filled, user_location)
    author_label = _author_primary_label(user_display_name, user_email)
    system_prompt_filled = _ensure_profile_author_in_system(system_prompt_filled, author_label)
    user_instructions = _fill_template(user_tpl, values).strip()
    user_block = _compose_user_section(user_instructions, custom_prompt)

    prompt_body = ""
    if system_prompt_filled:
        prompt_body += "SYSTEM:\n" + system_prompt_filled + "\n\n"

    prompt_body += "USER:\n" + user_block + "\n\n"
    prompt_body += "PATIENT DATA:\n" + raw_data + "\n\n"

    prompt_body += "When finished, output END_OF_NOTE on its own line and stop.\n\n"
    prompt_body += "ASSISTANT:\n"

    return prompt_body
