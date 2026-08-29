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
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
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


class PatientMaterialRequest(BaseModel):
    gen_id: str = Field(..., description="Generation ID (for ownership verification)")
    material_type: str = Field(..., description="One of: medications, diagnosis, issues_plan, diet, exercise, full_report")
    note_text: str = Field(..., description="The generated clinical note text")
    patient_data: Optional[Dict[str, Any]] = Field(None, description="Patient data for diet/exercise plans")
    regenerate: bool = Field(False, description="Explicit user-requested regeneration; invalidates the cached result for this material type")


class GenerateAllRequest(BaseModel):
    gen_id: str = Field(..., description="Generation ID")
    note_text: str = Field(..., description="The generated clinical note text")
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
        return PatientMaterialResponse(
            material_type=request.material_type,
            content=cached[request.material_type]["content"],
            source_attribution=cached[request.material_type].get("source_attribution", []),
            disclaimer=cached[request.material_type].get("disclaimer", ""),
            safety_flags=cached[request.material_type].get("safety_flags", []),
            generated_at=cached[request.material_type].get("generated_at", ""),
            generation_time_sec=cached[request.material_type].get("generation_time_sec", 0),
            error=None,
        )
    # needs_input results are cached too, so the extraction LLM call runs
    # once per generation, not per click. But a resubmit with complete data
    # must invalidate the cached needs_input, not echo it back.
    if cached and request.material_type in cached:
        entry0 = cached[request.material_type]
        if entry0.get("status") == "needs_input":
            note_gen = get_simple_note_generator()
            merged = await _merged_patient_data(
                request.gen_id, request.note_text,
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
    sections = parse_note_sections(request.note_text)

    # Diet/exercise: merge note-based extraction with clinician-entered
    # data (clinician always wins) BEFORE generation, so the blocking
    # check sees note-derived values.
    patient_data = request.patient_data
    if request.material_type in ("diet", "exercise"):
        patient_data = await _merged_patient_data(
            request.gen_id, request.note_text, patient_data, note_gen
        )

    result = await generator.generate_one(
        material_type=request.material_type,
        note_text=request.note_text,
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

    cache_key = request.gen_id

    note_gen = get_simple_note_generator()
    generator = PatientMaterialsGenerator(note_gen)

    # Parse sections once
    sections = parse_note_sections(request.note_text)

    # Diet/exercise read patient_data; merge note-based extraction with the
    # clinician-entered values (clinician always wins). Other types ignore
    # patient_data, so a single merged dict is safe for all of them.
    patient_data = await _merged_patient_data(
        request.gen_id, request.note_text, request.patient_data, note_gen
    )

    results = await generator.generate_all(
        note_text=request.note_text,
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
