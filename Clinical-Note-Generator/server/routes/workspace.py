# C:\Clinical-Note-Generator\server\routes\workspace.py
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select

from server.core.baseline import get_baseline_workspace
from server.core.db import get_session
from server.core.dependencies import get_current_user
from server.models.user import User
from server.models.workspace import UserWorkspace
from server.schemas.workspace import WorkspacePayload, WorkspaceResponse

MAX_WORKSPACE_BYTES = 256 * 1024

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
    workspace.state_json = payload.state.model_dump()
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
    baseline = get_baseline_workspace()
    if not workspace:
        workspace = UserWorkspace(user_id=current_user.id, state_json=baseline)
    workspace.state_json = baseline
    workspace.version = 1
    workspace.updated_at = datetime.utcnow()
    session.add(workspace)
    session.commit()
    session.refresh(workspace)
    return _workspace_response(workspace)
