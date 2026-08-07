# server/core/encounter_workspace.py
"""P5: UserEncounter storage + UserWorkspace shell (activeEncounterId) + migration."""
from __future__ import annotations

import copy
import os
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from sqlmodel import Session, select

from server.core.baseline import get_baseline_workspace
from server.models.queued_job import QueuedJob
from server.models.user_encounter import UserEncounter
from server.models.asr_recording_segment import AsrRecordingSegment
from server.models.workspace import UserWorkspace


def _parse_uuid(raw: Any) -> Optional[uuid.UUID]:
    if raw is None:
        return None
    try:
        return uuid.UUID(str(raw).strip())
    except (ValueError, TypeError, AttributeError):
        return None


def get_active_encounter_id(workspace_state: Optional[Dict[str, Any]]) -> Optional[uuid.UUID]:
    if not workspace_state:
        return None
    extras = workspace_state.get("extras") or {}
    return _parse_uuid(extras.get("activeEncounterId"))


def workspace_shell_state(active_encounter_id: uuid.UUID, settings: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    st = get_baseline_workspace()
    if settings:
        st["settings"] = {**st.get("settings", {}), **settings}
    st["extras"] = {"activeEncounterId": str(active_encounter_id)}
    return st


def compose_client_workspace_state(
    workspace_row: UserWorkspace,
    encounter: Optional[UserEncounter],
) -> Dict[str, Any]:
    """Full state the SPA expects: encounter body + activeEncounterId in extras."""
    if encounter is None:
        return copy.deepcopy(workspace_row.state_json) if workspace_row.state_json else get_baseline_workspace()
    base = copy.deepcopy(encounter.state_json) if encounter.state_json else get_baseline_workspace()
    extras = dict(base.get("extras") or {})
    extras["activeEncounterId"] = str(encounter.id)
    base["extras"] = extras
    return base


def encounter_recording_pointers(
    session: Session,
    user_id: uuid.UUID,
    encounter_id: Optional[uuid.UUID],
) -> Dict[str, Any]:
    """G1′: server-authoritative recording pointers for active encounter."""
    if not encounter_id:
        return {
            "has_encounter_recording": False,
            "recording_segment_ids": [],
            "recording_download_ready": False,
        }
    segs = list(
        session.exec(
            select(AsrRecordingSegment)
            .where(
                AsrRecordingSegment.user_id == user_id,
                AsrRecordingSegment.encounter_id == encounter_id,
            )
            .order_by(AsrRecordingSegment.sequence_index)
        ).all()
    )
    ready = any(
        (s.status or "").lower() in {"uploaded", "ready", "transcribed", "complete"}
        and bool(s.server_file_key)
        for s in segs
    )
    return {
        "has_encounter_recording": bool(segs),
        "recording_segment_ids": [str(s.id) for s in segs],
        "recording_download_ready": ready,
    }


def merge_incoming_workspace_state(
    existing_encounter_state: Dict[str, Any],
    incoming_state: Dict[str, Any],
) -> Dict[str, Any]:
    """Same merge rules as legacy workspace PUT (ASR / clear guards), applied to encounter state."""
    incoming_state = copy.deepcopy(incoming_state)
    incoming_extras = incoming_state.get("extras") or {}
    existing_state = copy.deepcopy(existing_encounter_state) if existing_encounter_state else get_baseline_workspace()
    existing_extras = existing_state.get("extras") or {}

    cleared_flag = bool(incoming_extras.get("transcriptionCleared"))
    if cleared_flag:
        incoming_extras.pop("transcriptionCleared", None)

    incoming_cleared_at = str(incoming_extras.get("clearedAt") or "").strip()
    existing_cleared_at = str(existing_extras.get("clearedAt") or "").strip()
    if existing_cleared_at and incoming_cleared_at and existing_cleared_at > incoming_cleared_at:
        incoming_extras["clearedAt"] = existing_cleared_at
    incoming_state["extras"] = incoming_extras

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

    # Preserve lastGenerationId when a partial client payload omits it (sync race / older clients).
    ex_ui = existing_extras.get("ui") if isinstance(existing_extras.get("ui"), dict) else {}
    in_ui = incoming_extras.get("ui") if isinstance(incoming_extras.get("ui"), dict) else {}
    lg_ex = str(ex_ui.get("lastGenerationId") or "").strip()
    lg_in = str(in_ui.get("lastGenerationId") or "").strip()
    if lg_ex and not lg_in:
        incoming_extras["ui"] = {**in_ui, "lastGenerationId": lg_ex}
        incoming_state["extras"] = incoming_extras

    return incoming_state


def ensure_encounters_for_user(session: Session, user_id: uuid.UUID) -> Tuple[UserWorkspace, Optional[UserEncounter]]:
    """
    Backfill: one UserEncounter from legacy workspace row; maintain activeEncounterId.
    Creates UserWorkspace if missing. Returns (workspace_row, active_encounter_or_none).
    """
    ws = session.exec(select(UserWorkspace).where(UserWorkspace.user_id == user_id)).one_or_none()
    if not ws:
        ws = UserWorkspace(user_id=user_id, state_json=get_baseline_workspace())
        session.add(ws)
        session.commit()
        session.refresh(ws)

    # Opportunistic TTL cleanup: keeps the system scheduler-free and ensures old encounters don't accumulate.
    # (Runs for this user only; bounded by user's encounter count.)
    ws = _prune_user_encounters_by_ttl(session, ws)

    encs = list(session.exec(select(UserEncounter).where(UserEncounter.user_id == user_id)).all())
    extras = (ws.state_json or {}).get("extras") or {}
    aid = _parse_uuid(extras.get("activeEncounterId"))

    if encs:
        if not aid:
            latest = max(encs, key=lambda e: e.updated_at or datetime.min)
            aid = latest.id
            new_extras = {**extras, "activeEncounterId": str(aid)}
            ws.state_json = {**(ws.state_json or get_baseline_workspace()), "extras": new_extras}
            ws.version = int(ws.version or 1) + 1
            ws.updated_at = datetime.utcnow()
            session.add(ws)
            session.commit()
            session.refresh(ws)
        active = session.get(UserEncounter, aid)
        if active is None or active.user_id != user_id:
            latest = max(encs, key=lambda e: e.updated_at or datetime.min)
            active = latest
            new_extras = {**((ws.state_json or {}).get("extras") or {}), "activeEncounterId": str(active.id)}
            ws.state_json = {**(ws.state_json or get_baseline_workspace()), "extras": new_extras}
            ws.version = int(ws.version or 1) + 1
            ws.updated_at = datetime.utcnow()
            session.add(ws)
            session.commit()
            session.refresh(ws)
        return ws, active

    # No encounters: migrate legacy workspace payload into first encounter
    legacy = copy.deepcopy(ws.state_json) if ws.state_json else get_baseline_workspace()
    enc = UserEncounter(
        user_id=user_id,
        label="Default",
        state_json=legacy,
        version=max(1, int(ws.version or 1)),
    )
    session.add(enc)
    session.flush()
    settings = legacy.get("settings") or {"theme": "light", "language": "en"}
    ws.state_json = workspace_shell_state(enc.id, settings)
    ws.version = int(ws.version or 1) + 1
    ws.updated_at = datetime.utcnow()
    session.add(ws)
    session.commit()
    session.refresh(ws)
    session.refresh(enc)
    return ws, enc


def _queue_storage_root() -> Path:
    """Mirror server/routes/queue.py storage root: project_root/data/queue_files."""
    data_dir = Path(__file__).resolve().parents[2] / "data"
    queue_dir = data_dir / "queue_files"
    queue_dir.mkdir(parents=True, exist_ok=True)
    return queue_dir


def _delete_queued_file_safely(server_file_key: str) -> None:
    root = _queue_storage_root()
    file_path = root / server_file_key
    try:
        if file_path.exists():
            file_path.unlink()
    except OSError:
        # Cleanup guard: best-effort delete of a queued file; a leftover/locked
        # file is not user-visible (the DB row is removed by the caller).
        pass


def _asr_segment_storage_root() -> Path:
    data_dir = Path(__file__).resolve().parents[2] / "data"
    return data_dir / "asr_recording_segments"


def _delete_asr_segment_file_safely(server_file_key: str) -> None:
    if not server_file_key:
        return
    file_path = _asr_segment_storage_root() / server_file_key
    try:
        if file_path.exists():
            file_path.unlink()
    except OSError:
        # Cleanup guard: best-effort delete of an ASR segment file; an orphan
        # is not user-visible and the DB row removal is the caller's concern.
        pass


def _encounter_ttl_days() -> int:
    # Default requirement: auto-delete after 9 days. Allow env override for operators/tests.
    raw = str(os.environ.get("ENCOUNTER_TTL_DAYS", "9")).strip()
    try:
        v = int(raw)
    except ValueError:
        v = 9
    return max(1, v)


def _prune_user_encounters_by_ttl(session: Session, ws: UserWorkspace) -> UserWorkspace:
    """Delete encounters older than TTL (by updated_at), including their queued jobs/files.

    If the active encounter is deleted, or if deletion leaves none remaining, a new encounter is created
    and the workspace shell is updated to point at it.
    """
    user_id = ws.user_id
    ttl_days = _encounter_ttl_days()
    # DB columns store naive UTC datetimes (default_factory=datetime.utcnow).
    # Use naive cutoff to keep comparisons compatible.
    cutoff = datetime.utcnow() - timedelta(days=ttl_days)

    extras = (ws.state_json or {}).get("extras") or {}
    active_id = _parse_uuid(extras.get("activeEncounterId"))

    encs = list(session.exec(select(UserEncounter).where(UserEncounter.user_id == user_id)).all())
    if not encs:
        return ws

    expired = [e for e in encs if (e.updated_at or e.created_at) < cutoff]
    if not expired:
        return ws

    # Delete expired encounters + their queued jobs + their ASR audio segments.
    # P1-7: this opportunistic TTL path used to delete only the encounter and
    # its queued jobs -- unlike the manual DELETE /encounters/{id} route, it
    # never touched AsrRecordingSegment rows/files. SQLite doesn't enforce
    # the encounter_id foreign key here (no PRAGMA foreign_keys=ON), so
    # those segments -- audio, i.e. PHI -- were silently orphaned: the
    # parent encounter gone, the segment row and file left behind forever.
    expired_ids = {e.id for e in expired}
    jobs = session.exec(select(QueuedJob).where(QueuedJob.encounter_id.in_(list(expired_ids)))).all()
    for job in jobs:
        if job.server_file_key:
            _delete_queued_file_safely(job.server_file_key)
        session.delete(job)
    segments = session.exec(
        select(AsrRecordingSegment).where(AsrRecordingSegment.encounter_id.in_(list(expired_ids)))
    ).all()
    for segment in segments:
        _delete_asr_segment_file_safely(segment.server_file_key)
        session.delete(segment)
    for e in expired:
        session.delete(e)

    session.flush()

    remaining = list(session.exec(select(UserEncounter).where(UserEncounter.user_id == user_id)).all())
    if not remaining:
        # Always keep at least one encounter so the UI has a place to save state.
        enc = UserEncounter(
            user_id=user_id,
            label="Encounter",
            state_json=get_baseline_workspace(),
            version=1,
        )
        session.add(enc)
        session.flush()
        settings = (enc.state_json or {}).get("settings") or {"theme": "light", "language": "en"}
        ws.state_json = workspace_shell_state(enc.id, settings)
        ws.version = int(ws.version or 1) + 1
        ws.updated_at = datetime.utcnow()
        session.add(ws)
        session.commit()
        session.refresh(ws)
        return ws

    # If we deleted the active encounter, repoint to the most recent remaining.
    if active_id and active_id in expired_ids:
        pick = max(remaining, key=lambda e: e.updated_at or datetime.min)
        settings = (pick.state_json or {}).get("settings") or {"theme": "light", "language": "en"}
        ws.state_json = workspace_shell_state(pick.id, settings)
        ws.version = int(ws.version or 0) + 1
        ws.updated_at = datetime.utcnow()
        session.add(ws)

    # Commit all deletions (and workspace pointer change if needed).
    session.commit()
    session.refresh(ws)
    return ws


def get_workspace_and_active_encounter(
    session: Session, user_id: uuid.UUID
) -> Tuple[UserWorkspace, Optional[UserEncounter]]:
    return ensure_encounters_for_user(session, user_id)


def clear_active_encounter_state(session: Session, workspace: UserWorkspace, encounter: UserEncounter) -> None:
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
    encounter.state_json = baseline
    encounter.version = int(encounter.version or 0) + 1
    encounter.updated_at = datetime.utcnow()
    workspace.version = int(workspace.version or 0) + 1
    workspace.updated_at = datetime.utcnow()
    session.add(encounter)
    session.add(workspace)
