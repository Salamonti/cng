"""P3-1 regression: claim_next_pending_chunk() must let only one caller win
a race on the same chunk. Before this fix, next_pending_chunk() only READ
status, and the caller wrote transcription_status = "transcribing" as a
separate step -- two concurrent /drain requests for the same encounter
(e.g. a stuck-request retry racing the original tab) could both read the
same chunk as pending and both start transcribing it.
"""
from __future__ import annotations

import uuid

import pytest
from sqlmodel import Session, SQLModel, create_engine

from server.core.asr_chunk import claim_next_pending_chunk
from server.models.asr_recording_segment import AsrRecordingSegment
from server.models.user import User  # noqa: F401
from server.models.user_encounter import UserEncounter  # noqa: F401


@pytest.fixture
def db_session(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'test.sqlite').as_posix()}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def _seed_chunk0(session, *, user_id, encounter_id, status="pending"):
    seg = AsrRecordingSegment(
        id=uuid.uuid4(),
        client_recording_id="rec1-c0",
        user_id=user_id,
        encounter_id=encounter_id,
        recording_session_id="rec1",
        segment_role="chunk",
        chunk_index=0,
        transcription_status=status,
        file_name="x.webm",
        mime_type="audio/webm",
        file_size=1,
        sha256="x",
        server_file_key="rec1-c0-key",
    )
    session.add(seg)
    session.commit()
    return seg


def test_claim_wins_and_flips_status_to_transcribing(db_session):
    user_id, encounter_id = uuid.uuid4(), uuid.uuid4()
    _seed_chunk0(db_session, user_id=user_id, encounter_id=encounter_id)

    claimed = claim_next_pending_chunk(
        db_session, user_id=user_id, encounter_id=encounter_id, recording_session_id="rec1"
    )
    assert claimed is not None
    assert claimed.transcription_status == "transcribing"


def test_second_claim_of_an_already_claimed_chunk_returns_none(db_session):
    # Simulates two concurrent /drain requests: the first successfully
    # claims chunk 0 (status -> "transcribing"); a second request racing it
    # must NOT also treat that same chunk as available.
    user_id, encounter_id = uuid.uuid4(), uuid.uuid4()
    _seed_chunk0(db_session, user_id=user_id, encounter_id=encounter_id)

    first = claim_next_pending_chunk(
        db_session, user_id=user_id, encounter_id=encounter_id, recording_session_id="rec1"
    )
    assert first is not None

    second = claim_next_pending_chunk(
        db_session, user_id=user_id, encounter_id=encounter_id, recording_session_id="rec1"
    )
    assert second is None


def test_claim_conditional_update_actually_checks_current_status_not_a_stale_read(db_session, monkeypatch):
    # The core of the TOCTOU fix: the UPDATE's WHERE clause must check the
    # status as it is IN THE DATABASE at UPDATE time, not the status the
    # in-process object was read with earlier. Simulate another writer
    # changing the row's status between candidate-selection and the UPDATE
    # by monkeypatching ordered_chunk_segments to hand back a segment object
    # whose in-memory status is stale relative to a row we mutate directly
    # first.
    import server.core.asr_chunk as asr_chunk_module

    user_id, encounter_id = uuid.uuid4(), uuid.uuid4()
    seg = _seed_chunk0(db_session, user_id=user_id, encounter_id=encounter_id)

    stale_copy = AsrRecordingSegment(**seg.model_dump())
    assert stale_copy.transcription_status == "pending"

    # Another "request" claims it first, for real, in the DB.
    seg.transcription_status = "transcribing"
    db_session.add(seg)
    db_session.commit()

    monkeypatch.setattr(asr_chunk_module, "ordered_chunk_segments", lambda *a, **kw: [stale_copy])

    result = claim_next_pending_chunk(
        db_session, user_id=user_id, encounter_id=encounter_id, recording_session_id="rec1"
    )
    assert result is None
