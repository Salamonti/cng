"""Live encounter source sheet for patient materials (Phase A, summary-first).

Locked rule R1: if a formal note exists, the note is the source of truth and
this module is NEVER invoked. It runs only when no note has been generated yet
and the clinician wants patient materials mid-visit: one LLM call organizes
raw inputs (current transcript chunks, prior visits, chart data) into a note-
shaped "Encounter Data Sheet" that downstream extraction
(parse_note_sections / extract_from_note_*) consumes exactly like a note.

Safety rails:
- The sheet is an internal helper, NOT the medical record. It is never
  persisted to the encounter or the generated-note slot.
- Numeric facts must be copied verbatim; never invented or averaged.
- Sheet cache is keyed by sha256 of the source content (a growing live
  transcript invalidates it), not by gen_id.
"""

import hashlib
import logging
import re
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Section headings deliberately chosen from SECTION_VARIANTS in
# patient_materials_sections.py so parse_note_sections maps them to
# canonical sections (hpi/physical_exam/assessments/plan/...).
_SOURCE_PROMPT = """You are organizing raw encounter data into a structured \
Encounter Data Sheet that will be used to generate patient education materials \
(diet plan, exercise plan, diagnosis explanation, visit summary).

You are given raw inputs: the conversation so far in this visit (possibly \
partial), prior visit records, and chart data. Organize ONLY what these \
inputs state. You are NOT writing a clinical note and NOT making diagnoses.

Return the sheet with EXACTLY these plain-text section headings (no markdown \
hashes, no other headings), skipping a section only if nothing is known:

Visit Context
History
Objective
Diagnoses
Medications
Allergies
Plan

Rules:
- Copy numbers EXACTLY as stated (weight in kg, height in cm, age, BP, labs). \
NEVER invent, estimate, or average a number that is not stated. If vitals \
were spoken in the conversation, use the most recent value stated and note \
it, e.g. "Weight: 92 kg (stated in conversation)".
- Demographics line goes under Visit Context (age, sex) when known.
- Diagnoses: one per line, exactly as named/stated in the inputs.
- Plan: list items stated for the patient (medications, tests, referrals, \
instructions).
- Do not add commentary, disclaimers, or information from outside the inputs.
- If the conversation is partial, include only what was actually said.

CONVERSATION SO FAR:
<CONVO>
{transcript}
</CONVO>

PRIOR VISITS:
<PRIOR>
{prior_visits}
</PRIOR>

CHART DATA:
<CHART>
{chart_data}
</CHART>"""

_MAX_INPUT_CHARS = 9000
_MAX_SHEETS = 200

# sha256(source content) -> sheet text. Bounded; oldest evicted.
_sheet_cache: Dict[str, str] = {}


def build_source_hash(live_source: Dict[str, Any]) -> str:
    """Stable hash of the live source content (cache key)."""
    payload = "\x1f".join(
        str(live_source.get(k) or "").strip()
        for k in ("transcript", "prior_visits", "chart_data")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _clean_sheet(raw: str) -> str:
    """Strip markdown fences/code wrappers the model occasionally adds."""
    text = (raw or "").strip()
    text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    # Cap runaway output; headings matter more than depth.
    return text[:6000]


def has_live_content(live_source: Optional[Dict[str, Any]]) -> bool:
    if not live_source:
        return False
    return any(
        str(live_source.get(k) or "").strip()
        for k in ("transcript", "prior_visits", "chart_data")
    )


def get_cached_sheet(source_hash: str) -> Optional[str]:
    """Previously-built sheet for this source hash, if still in memory."""
    return _sheet_cache.get(source_hash)


async def build_encounter_source(
    live_source: Dict[str, Any],
    note_gen,
    timeout_sec: float = 90.0,
) -> str:
    """Build (or reuse cached) Encounter Data Sheet for the given live inputs.

    Raises RuntimeError when the LLM call or its output is unusable — the
    route turns that into a clean 502 rather than silently generating an
    empty-source material.
    """
    if not has_live_content(live_source):
        raise RuntimeError("No encounter data available to build the source sheet")

    cache_key = build_source_hash(live_source)
    cached = _sheet_cache.get(cache_key)
    if cached:
        return cached

    prompt = _SOURCE_PROMPT.format(
        transcript=str(live_source.get("transcript") or "")[:_MAX_INPUT_CHARS] or "(none)",
        prior_visits=str(live_source.get("prior_visits") or "")[:_MAX_INPUT_CHARS] or "(none)",
        chart_data=str(live_source.get("chart_data") or "")[:_MAX_INPUT_CHARS] or "(none)",
    )
    try:
        raw = await note_gen.collect_completion(
            prompt,
            temperature=0.0,
            max_tokens=1200,
            stop=["__STOP__"],
            timeout_sec=timeout_sec,
        )
    except Exception as exc:  # noqa: BLE001 - surface as clean upstream error
        logger.warning("[PM-SOURCE] sheet LLM call failed: %s", exc)
        raise RuntimeError("Could not summarize encounter data (model unavailable)") from exc

    sheet = _clean_sheet(raw or "")
    if len(sheet) < 20:
        raise RuntimeError("Encounter summary came back empty — please try again")

    if len(_sheet_cache) >= _MAX_SHEETS:
        try:
            _sheet_cache.pop(next(iter(_sheet_cache)))
        except StopIteration:
            pass
    _sheet_cache[cache_key] = sheet
    return sheet
