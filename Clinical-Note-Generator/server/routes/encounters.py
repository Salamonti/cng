# server/routes/encounters.py
"""P5: CRUD + activate + delete (confirmed) for user_encounter."""
from __future__ import annotations

import copy
import os
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, status
from sqlmodel import Session, select, func

from server.core.baseline import get_baseline_workspace
from server.core.db import get_session
from server.core.dependencies import get_current_user
from server.core.phi_audit import log_phi_access
from server.core.encounter_workspace import (
    ensure_encounters_for_user,
    get_active_encounter_id,
    workspace_shell_state,
)
from server.models.queued_job import QueuedJob
from server.models.asr_recording_segment import AsrRecordingSegment
from server.models.user import User
from server.models.user_encounter import UserEncounter
from server.routes.queue import delete_queued_file
from server.routes.asr_segments import delete_asr_segment_file
from server.schemas.encounters import (
    EncounterCreate,
    EncounterDeleteRequest,
    EncounterDetailResponse,
    EncounterListResponse,
    EncounterRename,
    EncounterSummary,
)

router = APIRouter(prefix="/api/encounters", tags=["encounters"])


def _encounter_max_per_user() -> int:
    # Requirement: limit to 24 encounters/user. Allow env override for operators/tests.
    raw = str(os.environ.get("ENCOUNTER_MAX_PER_USER", "24")).strip()
    try:
        v = int(raw)
    except ValueError:
        v = 24
    return max(1, v)


def _summary(enc: UserEncounter, active_id: Optional[uuid.UUID]) -> EncounterSummary:
    return EncounterSummary(
        id=enc.id,
        label=enc.label,
        version=enc.version,
        updated_at=enc.updated_at,
        is_active=bool(active_id and enc.id == active_id),
    )


@router.get("/", response_model=EncounterListResponse)
def list_encounters(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    ws, _ = ensure_encounters_for_user(session, current_user.id)
    active_id = get_active_encounter_id(ws.state_json if ws else None)
    rows = session.exec(
        select(UserEncounter)
        .where(UserEncounter.user_id == current_user.id)
        .order_by(UserEncounter.updated_at.desc())
    ).all()
    return EncounterListResponse(encounters=[_summary(e, active_id) for e in rows])


@router.post("/", response_model=EncounterDetailResponse, status_code=status.HTTP_201_CREATED)
def create_encounter(
    body: EncounterCreate,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    ensure_encounters_for_user(session, current_user.id)
    # Enforce per-user encounter cap (after TTL cleanup in ensure_encounters_for_user).
    max_enc = _encounter_max_per_user()
    existing_count = session.exec(
        select(func.count()).select_from(UserEncounter).where(UserEncounter.user_id == current_user.id)
    ).one()
    if existing_count >= max_enc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Encounter limit reached ({max_enc}). Delete or reuse an existing encounter.",
        )
    label = (body.label or "").strip() or "Encounter"
    baseline = copy.deepcopy(get_baseline_workspace())
    extras = dict(baseline.get("extras") or {})
    spec = (current_user.default_specialty or "").strip()
    if spec:
        extras["userSpeciality"] = spec
    baseline["extras"] = extras
    enc = UserEncounter(
        user_id=current_user.id,
        label=label[:256],
        state_json=baseline,
        version=1,
    )
    session.add(enc)
    session.commit()
    session.refresh(enc)
    ws, active = ensure_encounters_for_user(session, current_user.id)
    active_id = active.id if active else None
    return EncounterDetailResponse(
        id=enc.id,
        label=enc.label,
        version=enc.version,
        updated_at=enc.updated_at,
        is_active=bool(active_id and enc.id == active_id),
        state=enc.state_json or {},
    )


@router.get("/{encounter_id}", response_model=EncounterDetailResponse)
def get_encounter(
    encounter_id: uuid.UUID,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    enc = session.get(UserEncounter, encounter_id)
    if not enc or enc.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Encounter not found")
    ws, _ = ensure_encounters_for_user(session, current_user.id)
    active_id = get_active_encounter_id(ws.state_json if ws else None)
    return EncounterDetailResponse(
        id=enc.id,
        label=enc.label,
        version=enc.version,
        updated_at=enc.updated_at,
        is_active=bool(active_id and enc.id == active_id),
        state=enc.state_json or {},
    )


@router.patch("/{encounter_id}", response_model=EncounterSummary)
def rename_encounter(
    encounter_id: uuid.UUID,
    body: EncounterRename,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    enc = session.get(UserEncounter, encounter_id)
    if not enc or enc.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Encounter not found")
    enc.label = body.label.strip()[:256]
    enc.updated_at = datetime.utcnow()
    enc.version = int(enc.version or 0) + 1
    session.add(enc)
    ws, _ = ensure_encounters_for_user(session, current_user.id)
    if ws:
        ws.version = int(ws.version or 0) + 1
        ws.updated_at = datetime.utcnow()
        session.add(ws)
    session.commit()
    session.refresh(enc)
    ws, _ = ensure_encounters_for_user(session, current_user.id)
    active_id = get_active_encounter_id(ws.state_json if ws else None)
    return _summary(enc, active_id)


@router.post("/{encounter_id}/activate", response_model=EncounterDetailResponse)
def activate_encounter(
    encounter_id: uuid.UUID,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    enc = session.get(UserEncounter, encounter_id)
    if not enc or enc.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Encounter not found")
    ws, _ = ensure_encounters_for_user(session, current_user.id)
    if not ws:
        raise HTTPException(status_code=500, detail="Workspace missing")
    settings = (enc.state_json or {}).get("settings") or {"theme": "light", "language": "en"}
    ws.state_json = workspace_shell_state(enc.id, settings)
    ws.version = int(ws.version or 0) + 1
    ws.updated_at = datetime.utcnow()
    session.add(ws)
    session.commit()
    session.refresh(ws)
    session.refresh(enc)
    return EncounterDetailResponse(
        id=enc.id,
        label=enc.label,
        version=enc.version,
        updated_at=enc.updated_at,
        is_active=True,
        state=enc.state_json or {},
    )


@router.delete("/{encounter_id}", response_model=EncounterListResponse)
def delete_encounter(
    encounter_id: uuid.UUID,
    body: EncounterDeleteRequest = Body(...),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    if not body.confirm:
        raise HTTPException(
            status_code=400,
            detail='Set "confirm": true in the JSON body to permanently delete this encounter.',
        )
    enc = session.get(UserEncounter, encounter_id)
    if not enc or enc.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Encounter not found")

    ws, active_enc = ensure_encounters_for_user(session, current_user.id)
    was_active = bool(active_enc and active_enc.id == enc.id)

    jobs = session.exec(select(QueuedJob).where(QueuedJob.encounter_id == enc.id)).all()
    for job in jobs:
        delete_queued_file(job.server_file_key)
        session.delete(job)

    segments = session.exec(select(AsrRecordingSegment).where(AsrRecordingSegment.encounter_id == enc.id)).all()
    for segment in segments:
        delete_asr_segment_file(segment.server_file_key)
        session.delete(segment)

    log_phi_access(
        session,
        user_id=current_user.id,
        action="delete_encounter",
        resource_type="encounter",
        resource_id=str(enc.id),
        encounter_id=enc.id,
        detail=f"{len(jobs)} job(s), {len(segments)} segment(s)",
    )

    session.delete(enc)
    session.flush()

    remaining = list(
        session.exec(select(UserEncounter).where(UserEncounter.user_id == current_user.id)).all()
    )
    if not remaining:
        new_e = UserEncounter(
            user_id=current_user.id,
            label="Encounter",
            state_json=get_baseline_workspace(),
            version=1,
        )
        session.add(new_e)
        session.flush()
        if ws:
            ws.state_json = workspace_shell_state(new_e.id)
            ws.version = int(ws.version or 0) + 1
            ws.updated_at = datetime.utcnow()
            session.add(ws)
    elif was_active and ws:
        pick = max(remaining, key=lambda e: e.updated_at or datetime.min)
        settings = (pick.state_json or {}).get("settings") or {"theme": "light", "language": "en"}
        ws.state_json = workspace_shell_state(pick.id, settings)
        ws.version = int(ws.version or 0) + 1
        ws.updated_at = datetime.utcnow()
        session.add(ws)

    session.commit()

    ws2, _ = ensure_encounters_for_user(session, current_user.id)
    active_id = get_active_encounter_id(ws2.state_json if ws2 else None)
    rows = session.exec(
        select(UserEncounter)
        .where(UserEncounter.user_id == current_user.id)
        .order_by(UserEncounter.updated_at.desc())
    ).all()
    return EncounterListResponse(encounters=[_summary(e, active_id) for e in rows])
