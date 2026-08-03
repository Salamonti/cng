"""P3-1 regression: real_cumulative_window_start() must use the client-measured
duration_sec of prior chunks instead of assuming every chunk fired at exactly
COMMIT_STRIDE_SEC (25s). MediaRecorder's requested timeslice is not a
hardware guarantee -- actual firing drifts, and that drift compounds over a
long encounter, pushing boundary words outside the theoretical grid.
"""
from __future__ import annotations

import uuid

import pytest
from sqlmodel import Session, SQLModel, create_engine

from server.core.asr_chunk import real_cumulative_window_start
from server.models.asr_recording_segment import AsrRecordingSegment
from server.models.user import User  # noqa: F401  -- registers FK target tables
from server.models.user_encounter import UserEncounter  # noqa: F401


@pytest.fixture
def db_session(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'test.sqlite').as_posix()}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def _make_segment(*, chunk_index, duration_sec, session_id="rec1", role="chunk", user_id=None, encounter_id=None):
    return AsrRecordingSegment(
        id=uuid.uuid4(),
        client_recording_id=f"{session_id}-c{chunk_index}",
        user_id=user_id or uuid.uuid4(),
        encounter_id=encounter_id or uuid.uuid4(),
        recording_session_id=session_id,
        segment_role=role,
        chunk_index=chunk_index,
        duration_sec=duration_sec,
        file_name="x.webm",
        mime_type="audio/webm",
        file_size=1,
        sha256="x",
        server_file_key=f"{session_id}-c{chunk_index}-key",
    )


def test_chunk_zero_is_always_zero(db_session):
    seg = _make_segment(chunk_index=0, duration_sec=None)
    db_session.add(seg)
    db_session.commit()
    assert real_cumulative_window_start(db_session, seg) == 0.0


def test_sums_real_jittered_durations_not_theoretical_25s(db_session):
    user_id = uuid.uuid4()
    encounter_id = uuid.uuid4()
    # Three prior chunks whose REAL durations drift from the theoretical 25.0s
    # each (24.87, 25.31, 24.95) -- exactly the MediaRecorder timeslice jitter
    # this fix exists for.
    durations = [24.87, 25.31, 24.95]
    for i, dur in enumerate(durations):
        db_session.add(_make_segment(
            chunk_index=i, duration_sec=dur, user_id=user_id, encounter_id=encounter_id
        ))
    db_session.commit()

    current = _make_segment(chunk_index=3, duration_sec=None, user_id=user_id, encounter_id=encounter_id)
    db_session.add(current)
    db_session.commit()

    real_start = real_cumulative_window_start(db_session, current)
    theoretical = 3 * 25.0
    assert real_start == pytest.approx(sum(durations))
    # The whole point: real jittered timing diverges from the naive grid.
    assert real_start != theoretical


def test_falls_back_to_none_when_a_prior_duration_is_missing(db_session):
    user_id = uuid.uuid4()
    encounter_id = uuid.uuid4()
    db_session.add(_make_segment(chunk_index=0, duration_sec=25.1, user_id=user_id, encounter_id=encounter_id))
    db_session.add(_make_segment(chunk_index=1, duration_sec=None, user_id=user_id, encounter_id=encounter_id))
    db_session.commit()

    current = _make_segment(chunk_index=2, duration_sec=None, user_id=user_id, encounter_id=encounter_id)
    db_session.add(current)
    db_session.commit()

    assert real_cumulative_window_start(db_session, current) is None


def test_falls_back_to_none_when_no_recording_session_id(db_session):
    seg = _make_segment(chunk_index=2, duration_sec=None, session_id=None)
    seg.recording_session_id = None
    db_session.add(seg)
    db_session.commit()
    assert real_cumulative_window_start(db_session, seg) is None


def test_scoped_to_the_right_session_user_and_encounter(db_session):
    # A same-index chunk in a DIFFERENT session must not be counted.
    user_id = uuid.uuid4()
    encounter_id = uuid.uuid4()
    db_session.add(_make_segment(
        chunk_index=0, duration_sec=25.0, session_id="rec1", user_id=user_id, encounter_id=encounter_id
    ))
    db_session.add(_make_segment(
        chunk_index=0, duration_sec=999.0, session_id="other_session", user_id=user_id, encounter_id=encounter_id
    ))
    db_session.commit()

    current = _make_segment(
        chunk_index=1, duration_sec=None, session_id="rec1", user_id=user_id, encounter_id=encounter_id
    )
    db_session.add(current)
    db_session.commit()

    assert real_cumulative_window_start(db_session, current) == pytest.approx(25.0)
