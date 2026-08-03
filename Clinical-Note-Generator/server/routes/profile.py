# server/routes/profile.py
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session

from server.core.db import get_session
from server.core.dependencies import get_current_user
from server.core.profile_service import (
    apply_profile_field_patch,
    create_custom_note_type,
    delete_custom_note_type,
    ensure_user_preferences,
    get_preferences_row,
    list_all_note_types_for_api,
    list_note_types_for_api,
    rename_custom_note_type,
    patch_note_templates,
    revert_all_builtin_templates,
    revert_note_template,
    revert_note_templates_bulk,
    update_note_type_preferences,
)
from server.models.user import User
from server.schemas.profile import (
    NoteTypeCreateRequest,
    NoteTypeEntry,
    NoteTypePreferencesUpdate,
    NoteTypesBulkRevertRequest,
    NoteTypesListResponse,
    NoteTypesPatchRequest,
    NoteTypeUpdateRequest,
    ProfileResponse,
    ProfileUpdate,
)

router = APIRouter(tags=["profile"])


def _to_profile_response(user: User, session: Session) -> ProfileResponse:
    prefs = get_preferences_row(session, user.id)
    pj = {}
    if prefs and prefs.preferences_json:
        pj = prefs.preferences_json
    return ProfileResponse(
        id=str(user.id),
        email=user.email,
        display_name=user.display_name,
        default_specialty=user.default_specialty,
        default_location=user.default_location,
        default_note_type=pj.get("default_note_type"),
        visible_note_types=pj.get("visible_note_types"),
        is_admin=user.is_admin,
        is_approved=user.is_approved,
        created_at=user.created_at,
    )


@router.get("/profile", response_model=ProfileResponse)
def get_profile(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> ProfileResponse:
    return _to_profile_response(current_user, session)


@router.put("/profile", response_model=ProfileResponse)
def put_profile(
    body: ProfileUpdate,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> ProfileResponse:
    u = session.get(User, current_user.id)
    if not u:
        raise HTTPException(status_code=404, detail="User not found")
    data = body.model_dump(exclude_unset=True)
    if data:
        u = apply_profile_field_patch(session, u, data)
    return _to_profile_response(u, session)


@router.put("/note-type-preferences", response_model=ProfileResponse)
def put_note_type_preferences(
    body: NoteTypePreferencesUpdate,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> ProfileResponse:
    u = session.get(User, current_user.id)
    if not u:
        raise HTTPException(status_code=404, detail="User not found")
    try:
        update_note_type_preferences(
            session,
            u,
            default_note_type=body.default_note_type,
            visible_note_types=body.visible_note_types,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return _to_profile_response(u, session)


@router.get("/note-types", response_model=NoteTypesListResponse)
def get_note_types(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> NoteTypesListResponse:
    ensure_user_preferences(session, current_user)
    raw = list_note_types_for_api(session, current_user)
    return NoteTypesListResponse(note_types=[NoteTypeEntry(**x) for x in raw])


@router.get("/note-types/all", response_model=NoteTypesListResponse)
def get_all_note_types(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> NoteTypesListResponse:
    """Return ALL note types ignoring visible_note_types filter.

    Used by the frontend to build the visibility checkbox list so hidden types
    remain re-checkable.
    """
    ensure_user_preferences(session, current_user)
    raw = list_all_note_types_for_api(session, current_user)
    return NoteTypesListResponse(note_types=[NoteTypeEntry(**x) for x in raw])


@router.put("/note-types", response_model=NoteTypesListResponse)
def put_note_types(
    body: NoteTypesPatchRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> NoteTypesListResponse:
    if body.templates is None and body.templates_other is None:
        raise HTTPException(status_code=422, detail="No templates provided")
    u = session.get(User, current_user.id)
    if not u:
        raise HTTPException(status_code=404, detail="User not found")
    try:
        patch_note_templates(
            session,
            u,
            templates=body.templates,
            templates_other=body.templates_other,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    raw = list_note_types_for_api(session, u)
    return NoteTypesListResponse(note_types=[NoteTypeEntry(**x) for x in raw])


@router.post("/note-types/custom", response_model=NoteTypesListResponse)
def post_create_custom_note_type(
    body: NoteTypeCreateRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> NoteTypesListResponse:
    u = session.get(User, current_user.id)
    if not u:
        raise HTTPException(status_code=404, detail="User not found")
    try:
        create_custom_note_type(
            session,
            u,
            type_id=(body.id or "").strip(),
            label=(body.label or "").strip(),
            initial_prompt=body.initial_prompt,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    raw = list_note_types_for_api(session, u)
    return NoteTypesListResponse(note_types=[NoteTypeEntry(**x) for x in raw])


@router.patch("/note-types/{note_type_id}", response_model=NoteTypesListResponse)
def patch_custom_note_type(
    note_type_id: str,
    body: NoteTypeUpdateRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> NoteTypesListResponse:
    u = session.get(User, current_user.id)
    if not u:
        raise HTTPException(status_code=404, detail="User not found")
    try:
        rename_custom_note_type(
            session,
            u,
            note_type_id,
            new_label=(body.label or "").strip(),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    raw = list_note_types_for_api(session, u)
    return NoteTypesListResponse(note_types=[NoteTypeEntry(**x) for x in raw])


@router.delete("/note-types/{note_type_id}", response_model=NoteTypesListResponse)
def delete_note_type(
    note_type_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> NoteTypesListResponse:
    u = session.get(User, current_user.id)
    if not u:
        raise HTTPException(status_code=404, detail="User not found")
    try:
        delete_custom_note_type(session, u, note_type_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    raw = list_note_types_for_api(session, u)
    return NoteTypesListResponse(note_types=[NoteTypeEntry(**x) for x in raw])


@router.post("/note-types/revert-builtins", response_model=NoteTypesListResponse)
def post_revert_all_builtin_templates(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> NoteTypesListResponse:
    u = session.get(User, current_user.id)
    if not u:
        raise HTTPException(status_code=404, detail="User not found")
    revert_all_builtin_templates(session, u)
    raw = list_note_types_for_api(session, u)
    return NoteTypesListResponse(note_types=[NoteTypeEntry(**x) for x in raw])


@router.post("/note-types/revert-bulk", response_model=NoteTypesListResponse)
def post_revert_note_templates_bulk(
    body: NoteTypesBulkRevertRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> NoteTypesListResponse:
    u = session.get(User, current_user.id)
    if not u:
        raise HTTPException(status_code=404, detail="User not found")
    revert_note_templates_bulk(session, u, standard=body.standard, other=body.other)
    raw = list_note_types_for_api(session, u)
    return NoteTypesListResponse(note_types=[NoteTypeEntry(**x) for x in raw])


@router.post("/note-types/{note_type_id}/revert", response_model=NoteTypesListResponse)
def post_revert_note_type(
    note_type_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
    other: bool = Query(False, description="Revert a template in templates_other (referral/summarize/...)"),
) -> NoteTypesListResponse:
    nid = (note_type_id or "").strip()
    if not nid:
        raise HTTPException(status_code=400, detail="Invalid note type id")
    u = session.get(User, current_user.id)
    if not u:
        raise HTTPException(status_code=404, detail="User not found")
    revert_note_template(session, u, nid, other=other)
    raw = list_note_types_for_api(session, u)
    return NoteTypesListResponse(note_types=[NoteTypeEntry(**x) for x in raw])
