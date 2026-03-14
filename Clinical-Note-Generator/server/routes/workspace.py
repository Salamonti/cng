# server/routes/workspace.py
from datetime import datetime
import copy

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select

from server.core.baseline import get_baseline_workspace
from server.core.db import get_session
from server.core.dependencies import get_current_user
from server.models.user import User
from server.models.workspace import UserWorkspace
from server.schemas.workspace import WorkspacePayload, WorkspaceResponse

MAX_WORKSPACE_BYTES = 2 * 1024 * 1024

router = APIRouter(prefix="/api/workspace", tags=["workspace"])


def _workspace_response(ws: UserWorkspace) -> WorkspaceResponse:
    return WorkspaceResponse(
        state=ws.state_json,
        version=ws.version,
        updated_at=ws.updated_at,
    )


@router.get("/", response_model=WorkspaceResponse)
def get_workspace(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    workspace = session.exec(
        select(UserWorkspace).where(UserWorkspace.user_id == current_user.id)
    ).one_or_none()
    if not workspace:
        workspace = UserWorkspace(
            user_id=current_user.id,
            state_json=get_baseline_workspace(),
        )
        session.add(workspace)
        session.commit()
        session.refresh(workspace)
    return _workspace_response(workspace)


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
    workspace = session.exec(
        select(UserWorkspace).where(UserWorkspace.user_id == current_user.id)
    ).one_or_none()
    if not workspace:
        workspace = UserWorkspace(
            user_id=current_user.id,
            state_json=get_baseline_workspace(),
        )
        session.add(workspace)
        session.commit()
        session.refresh(workspace)
    if payload.version != workspace.version:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "state": workspace.state_json,
                "version": workspace.version,
            },
        )
    incoming_state = payload.state.model_dump()
    incoming_extras = incoming_state.get("extras") or {}
    existing_state = workspace.state_json or {}
    existing_extras = existing_state.get("extras") or {}
    cleared_flag = bool(incoming_extras.get("transcriptionCleared"))
    if cleared_flag:
        incoming_extras.pop("transcriptionCleared", None)

    # Respect explicit clear markers across devices.
    incoming_cleared_at = str(incoming_extras.get("clearedAt") or "").strip()
    existing_cleared_at = str(existing_extras.get("clearedAt") or "").strip()
    if existing_cleared_at and incoming_cleared_at and existing_cleared_at > incoming_cleared_at:
        incoming_extras["clearedAt"] = existing_cleared_at
    incoming_state["extras"] = incoming_extras

    # Prevent stale client state from wiping ASR results unless explicitly cleared.
    incoming_transcription = (incoming_extras.get("transcription") or "").strip()
    incoming_current = (incoming_extras.get("currentEncounter") or "").strip()
    existing_transcription = (existing_extras.get("transcription") or "").strip()
    existing_current = (existing_extras.get("currentEncounter") or "").strip()
    existing_last_asr = existing_extras.get("lastAsrJobId")
    incoming_last_asr = incoming_extras.get("lastAsrJobId")
    if not cleared_flag and not incoming_transcription and not incoming_current and (existing_transcription or existing_current):
        incoming_extras["transcription"] = existing_extras.get("transcription") or ""
        incoming_extras["currentEncounter"] = existing_extras.get("currentEncounter") or ""
        if existing_last_asr and not incoming_last_asr:
            incoming_extras["lastAsrJobId"] = existing_last_asr
        incoming_state["extras"] = incoming_extras

    workspace.state_json = incoming_state
    workspace.version += 1
    workspace.updated_at = datetime.utcnow()
    session.add(workspace)
    session.commit()
    session.refresh(workspace)
    return _workspace_response(workspace)


@router.post("/clear", response_model=WorkspaceResponse)
def clear_workspace(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    workspace = session.exec(
        select(UserWorkspace).where(UserWorkspace.user_id == current_user.id)
    ).one_or_none()
    baseline = copy.deepcopy(get_baseline_workspace())
    if not workspace:
        workspace = UserWorkspace(user_id=current_user.id, state_json=baseline)

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
    # Keep monotonic versioning across clears (critical for cross-device sync)
    workspace.version = int(workspace.version or 0) + 1
    workspace.updated_at = datetime.utcnow()
    session.add(workspace)
    session.commit()
    session.refresh(workspace)
    return _workspace_response(workspace)
