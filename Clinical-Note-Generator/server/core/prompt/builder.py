import re
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional
import json
from server.core.preprocessing import PreprocessingPipeline, TokenBudgetTruncator

CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "config.json"

NUMERIC_UNIT_STYLE_INSTRUCTION = (
    "FINAL OUTPUT STYLE: Use numerals with compact clinical units in the final note "
    "(e.g., 5 mg, 100 mcg, 10 mL, 2 units). "
    "Do not spell out dose numbers/units when a compact form is appropriate. "
    "For medication lines, prefer: Medication Dose Unit Route Frequency when available."
)

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
        return "".join(parts).strip()
    return str(val).strip()


def _apply_preprocessing(
    cfg: Dict,
    transcription_text: str,
    old_visits_text: str,
    mixed_other_text: str,
) -> tuple[str, str, str]:
    trans_clean = _sanitize_transcription_text(transcription_text).strip()
    old_clean = _sanitize_chart_text(old_visits_text).strip()
    mixed_clean = _sanitize_chart_text(mixed_other_text).strip()

    preprocessing_cfg = (cfg or {}).get("preprocessing") or {}
    if not bool(preprocessing_cfg.get("enabled", False)):
        return trans_clean, old_clean, mixed_clean

    pipeline = PreprocessingPipeline(cfg)
    truncator = TokenBudgetTruncator(cfg)

    trans_clean = pipeline.normalize_whitespace(trans_clean).strip()
    old_clean = pipeline.process(old_clean).strip()
    mixed_clean = pipeline.process(mixed_clean).strip()

    if old_clean:
        old_clean = truncator.truncate_section(old_clean, section="prior_visits").strip()
    if mixed_clean:
        mixed_clean = truncator.truncate_section(mixed_clean, section="labs_imaging_other").strip()

    return trans_clean, old_clean, mixed_clean


def build_prompt_v8(
    transcription_text: str,
    old_visits_text: str,
    mixed_other_text: str,
    note_type: str,
    custom_prompt: Optional[str] = None,
    user_speciality: Optional[str] = None,
) -> str:
    cfg = load_config()
    today = date.today().strftime("%Y-%m-%d")

    system_prompt = _cfg_text(cfg.get("default_note_system_prompt", ""))

    user_templates = cfg.get("default_note_user_prompts", {}) or {}
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

    sections = []
    trans_clean, old_clean, mixed_clean = _apply_preprocessing(
        cfg, transcription_text, old_visits_text, mixed_other_text
    )
    if trans_clean:
        sections.append(
            "<CURRENT_ENCOUNTER>\n"
            "DATE: " + today + "\n"
            "This is the transcription from today's clinical encounter.\n"
            "Treat all information in this section as CURRENT.\n"
            + trans_clean + "\n"
            "</CURRENT_ENCOUNTER>"
        )

    if old_clean:
        sections.append(
            "<PRIOR_VISITS>\n"
            "These are notes from previous encounters.\n"
            "Treat all information in this section as HISTORICAL unless explicitly dated as recent.\n"
            + old_clean + "\n"
            "</PRIOR_VISITS>"
        )

    if mixed_clean:
        sections.append(
            "<LABS_IMAGING_OTHER>\n"
            "This section contains laboratory results, imaging reports, and other clinical data.\n"
            "Pay attention to dates on each item to determine recency.\n"
            + mixed_clean + "\n"
            "</LABS_IMAGING_OTHER>"
        )

    raw_data = "\n\n".join(sections) if sections else "[No patient data provided]"

    speciality = (user_speciality or "").strip() or "internal medicine"
    values = {
        "CURRENT_DATE": today,
        "NOTE_TYPE": note_type,
        "USER_SPECIALITY": speciality,
        "REASON_FOR_VISIT": "Unknown (infer from the current encounter data)",
        "ADMISSION_DX": "Unknown (infer from the data)",
        "DISCHARGE_DX": "Unknown (infer from the data)",
        "RAW_DATA": raw_data,
    }

    system_prompt_filled = _fill_template(system_prompt, values).strip() if system_prompt else ""
    user_instructions = _fill_template(user_tpl, values).strip()

    prompt_body = ""
    if system_prompt_filled:
        prompt_body += "SYSTEM:\n" + system_prompt_filled + "\n\n"

    prompt_body += "USER:\n" + user_instructions + "\n\n"
    prompt_body += "PATIENT DATA:\n" + raw_data + "\n\n"

    if custom_prompt and custom_prompt.strip():
        prompt_body += "ADDITIONAL INSTRUCTIONS:\n" + custom_prompt.strip() + "\n\n"

    prompt_body += "STYLE REQUIREMENTS:\n" + NUMERIC_UNIT_STYLE_INSTRUCTION + "\n\n"
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
) -> str:
    cfg = load_config()
    today = date.today().strftime("%Y-%m-%d")

    system_prompt = _cfg_text(cfg.get("default_note_system_prompt_other", ""))

    user_templates_other = cfg.get("default_note_user_prompts_other", {}) or {}
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

    trans_clean, old_clean, mixed_clean = _apply_preprocessing(
        cfg, transcription_text, old_visits_text, mixed_other_text
    )

    data_blocks = [b for b in [trans_clean, old_clean, mixed_clean] if b]
    raw_data = "\n\n".join(data_blocks) if data_blocks else "[No patient data provided]"

    speciality = (user_speciality or "").strip() or "internal medicine"
    values = {
        "CURRENT_DATE": today,
        "NOTE_TYPE": note_type,
        "USER_SPECIALITY": speciality,
        "REASON_FOR_VISIT": "Unknown (infer from the provided data)",
        "ADMISSION_DX": "Unknown (infer from the provided data)",
        "DISCHARGE_DX": "Unknown (infer from the provided data)",
        "RAW_DATA": raw_data,
    }

    system_prompt_filled = _fill_template(system_prompt, values).strip() if system_prompt else ""
    user_instructions = _fill_template(user_tpl, values).strip()

    prompt_body = ""
    if system_prompt_filled:
        prompt_body += "SYSTEM:\n" + system_prompt_filled + "\n\n"

    prompt_body += "USER:\n" + user_instructions + "\n\n"
    prompt_body += "PATIENT DATA:\n" + raw_data + "\n\n"

    if custom_prompt and custom_prompt.strip():
        prompt_body += "ADDITIONAL INSTRUCTIONS:\n" + custom_prompt.strip() + "\n\n"

    prompt_body += "STYLE REQUIREMENTS:\n" + NUMERIC_UNIT_STYLE_INSTRUCTION + "\n\n"
    prompt_body += "When finished, output END_OF_NOTE on its own line and stop.\n\n"
    prompt_body += "ASSISTANT:\n"

    return prompt_body


def build_note_prompt_legacy(
    chart_data: str,
    transcription: str,
    note_type: str,
    custom_prompt: Optional[str] = None,
    user_speciality: Optional[str] = None,
) -> str:
    cfg = load_config()
    today = date.today().strftime("%Y-%m-%d")

    chart_section = chart_data.strip()
    trans_section = transcription.strip()

    raw_parts = []
    if chart_section:
        raw_parts.append("Chart Data:\n" + chart_section)
    if trans_section:
        raw_parts.append("Transcription:\n" + trans_section)
    raw_data = "\n\n".join(raw_parts).strip()

    system_prompt = _cfg_text(cfg.get("default_note_system_prompt", ""))

    user_templates = cfg.get("default_note_user_prompts", {}) or {}
    raw_user_tpl = ""
    if isinstance(user_templates, dict):
        raw_user_tpl = user_templates.get(note_type) or ""
    user_tpl = _cfg_text(raw_user_tpl)

    if not user_tpl:
        default_prompts = cfg.get("default_prompts", {}) or {}
        legacy = _cfg_text(default_prompts.get(note_type) or "")
        if legacy:
            user_tpl = (
                f"Note type: {note_type}\n"
                f"Current date: {{CURRENT_DATE}}\n"
                f"{legacy}\n\n"
                "Raw data follows:\n{RAW_DATA}\n"
            )
        else:
            user_tpl = (
                "Note type: {NOTE_TYPE}\n"
                "Current date: {CURRENT_DATE}\n"
                "Reason for visit/referral: {REASON_FOR_VISIT}\n"
                "Start with patient name, age, sex, and reason.\n"
                "Do not fabricate.\n\n"
                "Raw data follows:\n{RAW_DATA}\n"
            )

    speciality = (user_speciality or "").strip() or "internal medicine"
    values = {
        "CURRENT_DATE": today,
        "NOTE_TYPE": note_type,
        "USER_SPECIALITY": speciality,
        "REASON_FOR_VISIT": "Unknown (infer from raw data)",
        "ADMISSION_DX": "Unknown (infer from raw data)",
        "DISCHARGE_DX": "Unknown (infer from raw data)",
        "RAW_DATA": raw_data or "[No chart/transcription provided]",
    }

    system_prompt_filled = _fill_template(system_prompt, values).strip() if system_prompt else ""
    user_instructions = _fill_template(user_tpl, values).strip()

    if raw_data:
        data_section = "\n\n" + "=" * 80 + "\nPATIENT DATA\n" + "=" * 80 + "\n\n" + raw_data
    else:
        data_section = "\n\n[No chart or transcription data provided]"

    user_prompt_filled = user_instructions + data_section

    prompt_body = ""
    if system_prompt_filled:
        prompt_body += "SYSTEM:\n" + system_prompt_filled + "\n\n"
    prompt_body += "USER:\n" + user_prompt_filled + "\n\n"

    if custom_prompt and custom_prompt.strip():
        prompt_body += "USER CUSTOM INSTRUCTIONS:\n" + custom_prompt.strip() + "\n\n"

    prompt_body += "STYLE REQUIREMENTS:\n" + NUMERIC_UNIT_STYLE_INSTRUCTION + "\n\n"
    prompt_body += "ASSISTANT:\n"

    return prompt_body
