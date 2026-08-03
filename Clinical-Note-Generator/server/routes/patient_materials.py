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


class PatientMaterialRequest(BaseModel):
    gen_id: str = Field(..., description="Generation ID (for ownership verification)")
    material_type: str = Field(..., description="One of: medications, diagnosis, issues_plan, diet, exercise, full_report")
    note_text: str = Field(..., description="The generated clinical note text")
    patient_data: Optional[Dict[str, Any]] = Field(None, description="Patient data for diet/exercise plans")


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
    # the key -- doing so previously produced a hashed key that GET
    # /list/{gen_id} (which only ever has the bare gen_id, never note_text)
    # could never look up.
    cache_key = request.gen_id
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
        )

    # Generate
    note_gen = get_simple_note_generator()
    generator = PatientMaterialsGenerator(note_gen)

    # Parse sections once
    sections = parse_note_sections(request.note_text)

    result = await generator.generate_one(
        material_type=request.material_type,
        note_text=request.note_text,
        sections=sections,
        patient_data=request.patient_data,
    )

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

    results = await generator.generate_all(
        note_text=request.note_text,
        sections=sections,
        patient_data=request.patient_data,
    )

    # Don't cache materials that failed to generate — they may be transient.
    # Still return the full dict (with per-type "error" keys) so the frontend
    # can render failures gracefully.
    any_ok = any(not results[t].get("error") for t in MATERIAL_TYPES)
    if any_ok:
        # Only cache the ones that succeeded; keep error-only entries out.
        cacheable = {t: results[t] for t in MATERIAL_TYPES if not results[t].get("error")}
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
