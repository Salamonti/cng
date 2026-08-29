"""
Patient materials API routes.

POST /api/patient-materials/generate
  Generate patient materials from a clinical note.

POST /api/patient-materials/generate-all
  Generate all 6 patient materials at once.

GET /api/patient-materials/list/{gen_id}
  List available materials for a generation ID.
"""

import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlmodel import Session

from server.services.note_generator_clean import get_simple_note_generator
from server.services.patient_materials_extraction import (
    extract_from_note_llm,
    extract_from_note_regex,
    merge_extraction,
)
from server.services.patient_materials_service import MATERIAL_TYPES, PatientMaterialsGenerator
from server.services.patient_materials_sections import parse_note_sections
from server.services.patient_materials_source import (
    build_encounter_source,
    build_source_hash,
    get_cached_sheet,
    has_live_content,
)
from server.core.dependencies import get_current_user, get_session
from server.core.stores.generation_store import (
    _generation_meta,
    _patient_materials_store,
    cache_lock as _cache_lock,
)
from server.models.user import User
from server.models.user_encounter import UserEncounter

router = APIRouter(prefix="/patient-materials", tags=["Patient Materials"])

# In-memory cache of merged note-extraction per gen_id (session-scoped;
# lost on restart, which just re-runs the one small LLM call — harmless).
_extraction_cache: Dict[str, Dict[str, Any]] = {}


async def _resolve_source(
    note_text: Optional[str],
    live_source: Optional["LiveSource"],
) -> "tuple[str, bool, Optional[str]]":
    """Return (source_text, preliminary, source_hash).

    Rule R1: a formal note is ALWAYS the source when present — this fires
    zero extra LLM calls in that case and behavior is byte-identical to the
    note flow. Only when no note exists do we build the internal Encounter
    Data Sheet (never persisted, never shown as the medical record).
    """
    if (note_text or "").strip():
        return str(note_text), False, None
    ls = live_source.model_dump() if live_source else {}
    note_gen = get_simple_note_generator()
    try:
        sheet = await build_encounter_source(ls, note_gen)
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
    return sheet, True, build_source_hash(ls)


async def _merged_patient_data(
    gen_id: str,
    note_text: str,
    user_data: Optional[Dict[str, Any]],
    note_gen,
) -> Dict[str, Any]:
    """Merge note-based extraction (regex + LLM) with clinician-entered data.

    Clinician-entered values always win. Used only for diet/exercise;
    the other material types are passed their patient_data untouched.
    """
    cached = _extraction_cache.get(gen_id)
    if cached is None:
        regex_vals = extract_from_note_regex(note_text)
        llm_vals = await extract_from_note_llm(note_text, note_gen)
        cached = merge_extraction({}, regex_vals, llm_vals)
        _extraction_cache[gen_id] = cached
        if len(_extraction_cache) > 500:  # bounded; oldest evicted
            oldest_key = next(iter(_extraction_cache))
            _extraction_cache.pop(oldest_key, None)
    return merge_extraction(user_data, cached, {})


class LiveSource(BaseModel):
    """Raw encounter inputs used ONLY when no formal note exists (rule R1)."""
    transcript: Optional[str] = Field(None, description="Transcript chunks so far this visit")
    prior_visits: Optional[str] = Field(None, description="Prior visit records / uploaded PDFs")
    chart_data: Optional[str] = Field(None, description="Extra chart/labs data (mixed other data)")


class PatientMaterialRequest(BaseModel):
    gen_id: str = Field(..., description="Generation ID (for ownership verification)")
    material_type: str = Field(..., description="One of: medications, diagnosis, issues_plan, diet, exercise, full_report")
    note_text: Optional[str] = Field(None, description="The generated clinical note text. Required unless live_source is provided.")
    live_source: Optional[LiveSource] = Field(None, description="Pre-note mode: build an internal Encounter Data Sheet from these raw inputs and use it as the source instead of a note")
    patient_data: Optional[Dict[str, Any]] = Field(None, description="Patient data for diet/exercise plans")
    regenerate: bool = Field(False, description="Explicit user-requested regeneration; invalidates the cached result for this material type")

    @model_validator(mode="after")
    def _require_note_or_live_source(self) -> "PatientMaterialRequest":
        if not (self.note_text or "").strip() and not has_live_content(
            self.live_source.model_dump() if self.live_source else None
        ):
            raise ValueError("note_text or non-empty live_source is required")
        return self


class GenerateAllRequest(BaseModel):
    gen_id: str = Field(..., description="Generation ID")
    note_text: Optional[str] = Field(None, description="The generated clinical note text. Required unless live_source is provided.")
    live_source: Optional[LiveSource] = Field(None, description="Pre-note mode (see PatientMaterialRequest)")
    patient_data: Optional[Dict[str, Any]] = Field(None, description="Patient data for diet/exercise plans")


class PatientMaterialResponse(BaseModel):
    material_type: str
    content: str
    source_attribution: list[Dict[str, str]]
    disclaimer: str
    safety_flags: list[str]
    generated_at: str
    generation_time_sec: float
    error: Optional[str] = None
    preliminary: bool = Field(False, description="True when built from a pre-note Encounter Data Sheet instead of a formal note")
    source_sheet: Optional[str] = Field(None, description="The internal Encounter Data Sheet this material was generated from (pre-note mode only)")


async def _verify_gen_id_ownership(
    gen_id: str,
    user_id: uuid.UUID,
    session: Session,
) -> None:
    """Raise 403 if gen_id does not belong to an encounter owned by user_id."""
    meta = _generation_meta.get(gen_id)
    if not meta:
        # No metadata — generation may have expired. Allow through (user already has note text).
        return
    # Provisional pre-note sources are bound to the issuing user directly
    # (no encounter yet) — enforce that binding strictly.
    if meta.get("provisional"):
        if meta.get("user_id") != str(user_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Generation does not belong to this user",
            )
        return
    encounter_id = meta.get("encounter_id")
    if not encounter_id:
        return
    # Encounter ID from _generation_meta is stored as a string (see notes.py),
    # but UserEncounter.id is a uuid.UUID column. Convert to UUID so SQLAlchemy's
    # type processor doesn't try .hex on a string, which would 500.
    try:
        eid = uuid.UUID(str(encounter_id))
    except ValueError:
        # Malformed UUID in meta — skip verification rather than crashing.
        return
    # Check that the encounter belongs to this user
    result = session.execute(
        select(UserEncounter.id).where(
            UserEncounter.id == eid,
            UserEncounter.user_id == user_id,
        )
    )
    if not result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Generation does not belong to this user",
        )


class ProvisionalSourceResponse(BaseModel):
    gen_id: str


@router.post("/provisional-source")
async def create_provisional_source(
    user: User = Depends(get_current_user),
):
    """Mint a provisional gen_id for pre-note patient materials.

    Mid-visit (no formal note yet), the clinician can still generate patient
    materials from live encounter data. The materials endpoints require a
    gen_id for ownership; this registers a provisional generation bound to
    the authenticated user so _verify_gen_id_ownership stays intact instead
    of being bypassed. Entries expire from the TTL store like any generation;
    nothing is persisted to the encounter.
    """
    gen_id = uuid.uuid4().hex
    _generation_meta[gen_id] = {
        "refs": [],
        "used_filters": {},
        "context": "",
        "full_evidence": "",
        "pipeline": "pre_note_materials",
        "encounter_id": None,
        "user_id": str(user.id),
        "provisional": True,
    }
    return ProvisionalSourceResponse(gen_id=gen_id)


@router.post("/generate")
async def generate_patient_material(
    request: PatientMaterialRequest,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Generate a single patient material from a clinical note."""
    if request.material_type not in MATERIAL_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid material type. Must be one of: {', '.join(MATERIAL_TYPES.keys())}",
        )

    # Verify ownership
    await _verify_gen_id_ownership(request.gen_id, user.id, session)

    # Check cache first. gen_id is a fresh uuid4 per generation (see
    # routes/notes.py), already unique with no need to fold note_text into
    # the key -- doing that previously produced a hashed key that GET
    # /list/{gen_id} (which only ever has the bare gen_id, never note_text)
    # could never look up.
    # Pre-note (preliminary) mode folds the live-source hash into the key so
    # a growing transcript invalidates cached materials; note mode keeps the
    # bare gen_id key (byte-identical to existing behavior, rule R1).
    preliminary = not (request.note_text or "").strip()
    source_hash: Optional[str] = None
    if preliminary:
        source_hash = build_source_hash(
            request.live_source.model_dump() if request.live_source else {}
        )
        cache_key = f"{request.gen_id}:src:{source_hash}"
    else:
        cache_key = request.gen_id
    # Explicit "Regenerate" from the clinician (e.g. after correcting weight
    # or height) must NOT be served the previously cached result — drop the
    # cached entry for this material type before the cache check.
    if request.regenerate:
        with _cache_lock:
            cur = _patient_materials_store.get(cache_key)
            if cur and request.material_type in cur:
                cur = dict(cur)
                cur.pop(request.material_type, None)
                _patient_materials_store.put(cache_key, cur)
    cached = _patient_materials_store.get(cache_key)
    if cached and request.material_type in cached and cached[request.material_type].get("content"):
        entry = cached[request.material_type]
        return PatientMaterialResponse(
            material_type=request.material_type,
            content=entry["content"],
            source_attribution=entry.get("source_attribution", []),
            disclaimer=entry.get("disclaimer", ""),
            safety_flags=entry.get("safety_flags", []),
            generated_at=entry.get("generated_at", ""),
            generation_time_sec=entry.get("generation_time_sec", 0),
            error=None,
            preliminary=preliminary,
            source_sheet=(get_cached_sheet(source_hash) if preliminary and source_hash else None),
        )
    # Resolve the source: note text when present (rule R1 — zero extra LLM
    # calls, byte-identical behavior); otherwise the internal Encounter Data
    # Sheet (cached by source hash; LLM runs once per distinct source state).
    source_text, _, _ = await _resolve_source(request.note_text, request.live_source)

    # needs_input results are cached too, so the extraction LLM call runs
    # once per generation, not per click. But a resubmit with complete data
    # must invalidate the cached needs_input, not echo it back.
    if cached and request.material_type in cached:
        entry0 = cached[request.material_type]
        if entry0.get("status") == "needs_input":
            note_gen = get_simple_note_generator()
            merged = await _merged_patient_data(
                request.gen_id, source_text,
                request.patient_data, note_gen
            )
            from server.services.patient_materials_extraction import missing_blocking
            if not missing_blocking(request.material_type, merged):
                # Clinician completed the gaps — drop the stale needs_input
                # and generate for real.
                with _cache_lock:
                    cur = _patient_materials_store.get(cache_key) or {}
                    cur.pop(request.material_type, None)
                    _patient_materials_store.put(cache_key, cur)
            else:
                return {
                    "status": "needs_input",
                    "material_type": request.material_type,
                    "missing_fields": entry0.get("missing_fields", []),
                    "known_data": entry0.get("known_data", {}),
                }

    # Generate
    note_gen = get_simple_note_generator()
    generator = PatientMaterialsGenerator(note_gen)

    # Parse sections once
    sections = parse_note_sections(source_text)

    # Diet/exercise: merge note-based extraction with clinician-entered
    # data (clinician always wins) BEFORE generation, so the blocking
    # check sees note-derived values.
    patient_data = request.patient_data
    if request.material_type in ("diet", "exercise"):
        patient_data = await _merged_patient_data(
            request.gen_id, source_text, patient_data, note_gen
        )

    result = await generator.generate_one(
        material_type=request.material_type,
        note_text=source_text,
        sections=sections,
        patient_data=patient_data,
    )

    # needs_input: surface structured gaps; cache so re-clicks don't re-run
    # the extraction LLM call.
    if result.get("status") == "needs_input":
        with _cache_lock:
            existing = _patient_materials_store.get(cache_key) or {}
            existing[request.material_type] = {
                "status": "needs_input",
                "missing_fields": result.get("missing_fields", []),
                "known_data": result.get("known_data", {}),
            }
            _patient_materials_store.put(cache_key, existing)
        return {
            "status": "needs_input",
            "material_type": request.material_type,
            "missing_fields": result.get("missing_fields", []),
            "known_data": result.get("known_data", {}),
        }

    # Don't cache errors — they may be transient (LLM downtime, etc.)
    if not result.get("error"):
        with _cache_lock:
            existing = _patient_materials_store.get(cache_key) or {}
            existing[request.material_type] = result
            _patient_materials_store.put(cache_key, existing)

    return PatientMaterialResponse(
        material_type=request.material_type,
        content=result.get("content", ""),
        source_attribution=result.get("source_attribution", []),
        disclaimer=result.get("disclaimer", ""),
        safety_flags=result.get("safety_flags", []),
        generated_at=result.get("generated_at", ""),
        generation_time_sec=result.get("generation_time_sec", 0),
        error=result.get("error"),
        preliminary=preliminary,
        source_sheet=(get_cached_sheet(source_hash) if preliminary and source_hash else None),
    )


@router.post("/generate-all")
async def generate_all_patient_materials(
    request: GenerateAllRequest,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Generate all 6 patient materials from a clinical note."""
    # Verify ownership
    await _verify_gen_id_ownership(request.gen_id, user.id, session)

    # Pre-note mode: cache keyed with source hash (see /generate).
    preliminary = not (request.note_text or "").strip()
    if preliminary:
        source_hash = build_source_hash(
            request.live_source.model_dump() if request.live_source else {}
        )
        cache_key = f"{request.gen_id}:src:{source_hash}"
    else:
        cache_key = request.gen_id

    # Resolve source (rule R1: note wins whenever present; sheet otherwise).
    source_text, _, _ = await _resolve_source(request.note_text, request.live_source)

    note_gen = get_simple_note_generator()
    generator = PatientMaterialsGenerator(note_gen)

    # Parse sections once
    sections = parse_note_sections(source_text)

    # Diet/exercise read patient_data; merge note-based extraction with the
    # clinician-entered values (clinician always wins). Other types ignore
    # patient_data, so a single merged dict is safe for all of them.
    patient_data = await _merged_patient_data(
        request.gen_id, source_text, request.patient_data, note_gen
    )

    results = await generator.generate_all(
        note_text=source_text,
        sections=sections,
        patient_data=patient_data,
    )

    # Don't cache materials that failed to generate — they may be transient.
    # Still return the full dict (with per-type "error" keys) so the frontend
    # can render failures gracefully.
    needs_input_entries = {
        t: {"status": "needs_input",
            "missing_fields": results[t].get("missing_fields", []),
            "known_data": results[t].get("known_data", {})}
        for t in MATERIAL_TYPES if results[t].get("status") == "needs_input"
    }
    # Pre-note mode: mark each material so any consumer can tell the source
    # was the internal Encounter Data Sheet, not a formal note.
    if preliminary:
        for _entry in results.values():
            if isinstance(_entry, dict):
                _entry["preliminary"] = True

    any_ok = any(not results[t].get("error") for t in MATERIAL_TYPES)
    if any_ok:
        # Only cache the ones that succeeded; keep error-only entries out.
        cacheable = {t: results[t] for t in MATERIAL_TYPES
                     if not results[t].get("error")
                     and results[t].get("status") != "needs_input"}
        # needs_input entries are deterministic per gen_id — cache them so
        # /list and re-clicks don't re-run the extraction LLM call.
        cacheable.update(needs_input_entries)
        with _cache_lock:
            _patient_materials_store.put(cache_key, cacheable)

    return results


@router.get("/list/{gen_id}")
async def list_patient_materials(
    gen_id: str,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """List available patient materials for a generation ID."""
    # Verify ownership
    await _verify_gen_id_ownership(gen_id, user.id, session)

    cached = _patient_materials_store.get(gen_id)
    if not cached:
        return {"gen_id": gen_id, "materials": {}}

    available = {}
    for mat_type in MATERIAL_TYPES:
        mat_data = cached.get(mat_type) or {}
        available[mat_type] = {
            "name": MATERIAL_TYPES[mat_type],
            "cached": bool(mat_data),
            "has_content": bool(mat_data.get("content")),
            "safety_flags_count": len(mat_data.get("safety_flags", [])),
        }

    return {"gen_id": gen_id, "materials": available}
