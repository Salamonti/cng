# server/schemas/profile.py
from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class ProfileResponse(BaseModel):
    id: str
    email: str
    display_name: Optional[str] = None
    default_specialty: Optional[str] = None
    default_location: Optional[str] = None
    default_note_type: Optional[str] = None
    visible_note_types: Optional[List[str]] = None  # None = show all
    is_admin: bool
    is_approved: bool
    created_at: datetime


class ProfileUpdate(BaseModel):
    display_name: Optional[str] = Field(None, max_length=256)
    default_specialty: Optional[str] = Field(None, max_length=256)
    default_location: Optional[str] = Field(None, max_length=256)


class NoteTypeEntry(BaseModel):
    id: str
    label: str
    kind: str = "builtin"
    scope: str  # standard | other
    baseline_prompt: str
    user_prompt: str


class NoteTypesListResponse(BaseModel):
    note_types: List[NoteTypeEntry]


class NoteTypesPatchRequest(BaseModel):
    templates: Optional[Dict[str, str]] = None
    templates_other: Optional[Dict[str, str]] = None


class NoteTypeCreateRequest(BaseModel):
    """User-defined note type (custom). Prefer label-only; server will generate id."""

    # Backward-compatible: legacy clients may still send explicit id/scope.
    id: Optional[str] = Field(None, min_length=2, max_length=64, description="Lowercase slug, e.g. clinic_intake")
    label: str = Field(..., max_length=128, description="Display name shown in the UI dropdown")
    scope: Optional[str] = Field(None, description='Legacy only. Server forces custom note types to "other".')
    initial_prompt: Optional[str] = None


class NoteTypeUpdateRequest(BaseModel):
    """Rename a custom note type. Built-ins cannot be renamed."""

    label: str = Field(..., max_length=128)


class NoteTypesBulkRevertRequest(BaseModel):
    """Revert built-in templates only; ids must exist in operator baseline for that bucket."""

    standard: Optional[List[str]] = None
    other: Optional[List[str]] = None


class NoteTypePreferencesUpdate(BaseModel):
    """Update note type visibility and default selection."""

    default_note_type: Optional[str] = Field(None, min_length=2, max_length=64, description="Default note type id, e.g. consult")
    visible_note_types: Optional[List[str]] = Field(None, description="List of visible note type ids. None = show all.")
