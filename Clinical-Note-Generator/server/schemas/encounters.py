# server/schemas/encounters.py
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class EncounterCreate(BaseModel):
    label: Optional[str] = Field(None, max_length=256)


class EncounterRename(BaseModel):
    label: str = Field(..., min_length=1, max_length=256)


class EncounterDeleteRequest(BaseModel):
    confirm: bool = Field(..., description="Must be true to permanently delete the encounter.")


class EncounterSummary(BaseModel):
    id: UUID
    label: str
    version: int
    updated_at: datetime
    is_active: bool

    class Config:
        from_attributes = True


class EncounterListResponse(BaseModel):
    encounters: List[EncounterSummary]


class EncounterDetailResponse(BaseModel):
    id: UUID
    label: str
    version: int
    updated_at: datetime
    is_active: bool
    state: Dict[str, Any]

    class Config:
        from_attributes = True
