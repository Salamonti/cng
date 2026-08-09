# server/routes/workspace.py
from datetime import datetime
import copy

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session

from server.core.db import get_session
from server.core.dependencies import get_current_user
from server.core.asr_incident_store import record_sync_incident
from server.core.baseline import get_baseline_workspace
from server.core.encounter_workspace import (
    clear_active_encounter_state,
    compose_client_workspace_state,
    ensure_encounters_for_user,
    encounter_recording_pointers,
    merge_incoming_workspace_state,
)
from server.models.user import User
from server.models.workspace import UserWorkspace
from server.models.user_encounter import UserEncounter
from server.schemas.workspace import WorkspacePayload, WorkspaceResponse, WorkspaceVersionResponse

MAX_WORKSPACE_BYTES = 2 * 1024 * 1024

router = APIRouter(prefix="/api/workspace", tags=["workspace"])


def _workspace_response(ws: UserWorkspace, composed_state: dict) -> WorkspaceResponse:
    return WorkspaceResponse(
        state=composed_state,
        version=ws.version,
        updated_at=ws.updated_at,
    )


def _attach_recording_meta(composed: dict, session: Session, user_id, encounter) -> dict:
    """G1′: canonical recording pointers on workspace payload."""
    if not encounter:
        return composed
    from server.core.feature_flags import ASR_RECORDING_RECONCILE

    if not ASR_RECORDING_RECONCILE:
        return composed
    meta = encounter_recording_pointers(session, user_id, encounter.id)
    out = copy.deepcopy(composed)
    extras = dict(out.get("extras") or {})
    extras.update(meta)
    out["extras"] = extras
    return out


@router.get("/version", response_model=WorkspaceVersionResponse)
def get_workspace_version(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    workspace, encounter = ensure_encounters_for_user(session, current_user.id)
    meta = encounter_recording_pointers(session, current_user.id, encounter.id if encounter else None)
    active_id = None
    if encounter:
        active_id = str(encounter.id)
    return WorkspaceVersionResponse(
        version=workspace.version,
        updated_at=workspace.updated_at,
        activeEncounterId=active_id,
        **meta,
    )


@router.get("/", response_model=WorkspaceResponse)
def get_workspace(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    workspace, encounter = ensure_encounters_for_user(session, current_user.id)
    composed = compose_client_workspace_state(workspace, encounter)
    composed = _attach_recording_meta(composed, session, current_user.id, encounter)
    return _workspace_response(workspace, composed)


@router.put("/", response_model=WorkspaceResponse)
def update_workspace(
    payload: WorkspacePayload,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    payload_size = len(payload.state.model_dump_json())
    if payload_size > MAX_WORKSPACE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Workspace payload too large",
        )
    workspace, encounter = ensure_encounters_for_user(session, current_user.id)
    if not encounter:
        raise HTTPException(status_code=500, detail="Encounter migration failed")

    if payload.version != workspace.version:
        composed = compose_client_workspace_state(workspace, encounter)
        record_sync_incident(
            outcome="put_conflict",
            payload={
                "reason": "version_mismatch",
                "client_version": payload.version,
                "server_version": workspace.version,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "state": composed,
                "version": workspace.version,
            },
        )

    incoming_state = payload.state.model_dump()
    merged = merge_incoming_workspace_state(encounter.state_json or {}, incoming_state)
    now = datetime.utcnow()

    # Atomic compare-and-swap on both rows: the earlier read-then-write here
    # let two concurrent PUTs starting from the same version both pass the
    # version check above and both commit, silently discarding one of them.
    # Guarding the actual UPDATE on version = <what we just read> makes the
    # loser of a race get rowcount=0 -- a real 409 -- instead of a silent
    # overwrite.
    from sqlalchemy import update as sa_update

    encounter_result = session.exec(
        sa_update(UserEncounter)
        .where(UserEncounter.id == encounter.id, UserEncounter.version == encounter.version)
        .values(state_json=merged, version=UserEncounter.version + 1, updated_at=now)
    )
    workspace_result = session.exec(
        sa_update(UserWorkspace)
        .where(UserWorkspace.id == workspace.id, UserWorkspace.version == payload.version)
        .values(version=UserWorkspace.version + 1, updated_at=now)
    )
    if encounter_result.rowcount == 0 or workspace_result.rowcount == 0:
        session.rollback()
        session.refresh(workspace)
        session.refresh(encounter)
        composed = compose_client_workspace_state(workspace, encounter)
        record_sync_incident(
            outcome="put_conflict",
            payload={
                "reason": "cas_rowcount_zero",
                "client_version": payload.version,
                "server_version": workspace.version,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "state": composed,
                "version": workspace.version,
            },
        )

    session.commit()
    session.refresh(workspace)
    session.refresh(encounter)

    composed = compose_client_workspace_state(workspace, encounter)
    return _workspace_response(workspace, composed)


@router.post("/clear", response_model=WorkspaceResponse)
def clear_workspace(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    workspace, encounter = ensure_encounters_for_user(session, current_user.id)
    if encounter:
        clear_active_encounter_state(session, workspace, encounter)
    else:
        baseline = copy.deepcopy(get_baseline_workspace())
        extras = baseline.get("extras") or {}
        extras.update(
            {
                "transcription": "",
                "currentEncounter": "",
                "oldVisits": "",
                "mixedOther": "",
                "generatedNote": "",
                "clearedAt": datetime.utcnow().isoformat() + "Z",
            }
        )
        baseline["extras"] = extras
        workspace.state_json = baseline
        workspace.version = int(workspace.version or 0) + 1
        workspace.updated_at = datetime.utcnow()
        session.add(workspace)

    session.commit()
    session.refresh(workspace)
    if encounter:
        session.refresh(encounter)

    workspace, encounter = ensure_encounters_for_user(session, current_user.id)
    composed = compose_client_workspace_state(workspace, encounter)
    return _workspace_response(workspace, composed)
