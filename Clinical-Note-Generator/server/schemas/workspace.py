# C:\Clinical-Note-Generator\server\schemas\workspace.py
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class WorkspaceDocument(BaseModel):
    id: str
    title: str
    summary: Optional[str] = None


class WorkspaceSettings(BaseModel):
    theme: str = Field(default="light", pattern="^(light|dark)$")
    language: str = "en"


class WorkspaceState(BaseModel):
    settings: WorkspaceSettings
    documents: List[WorkspaceDocument]
    draft: Optional[str] = None
    extras: Dict[str, Any] = Field(default_factory=dict)


class WorkspacePayload(BaseModel):
    state: WorkspaceState
    version: int


class WorkspaceResponse(BaseModel):
    state: WorkspaceState
    version: int
    updated_at: datetime
