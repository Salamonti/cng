"""STEP 2d -- reclassified silent handlers in routes/asr_segments.py.

Behavior classes for the three sites:
- L93 delete_asr_segment_file -- best-effort segment-file cleanup (cleanup-guard):
  an unlink failure must never raise into the caller. Now carries an explicit
  rationale comment.
- L696 race-loser orphan cleanup (inside segment create) -- same cleanup-guard
  class; explicit comment.
- L819 whole-file fallback (drain): a fallback-engine failure silently decays to
  the already-computed per-chunk pipeline. That recovery is kept, but the
  failure is now logged with trace_id (was bare `pass`).
"""
import logging
import uuid
from types import SimpleNamespace

import pytest
from sqlmodel import Session, SQLModel, create_engine

import server.routes.asr_segments as asr_seg


@pytest.fixture
def db_session(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'test.sqlite').as_posix()}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def test_delete_asr_segment_file_never_raises_on_unlink_failure(monkeypatch):
    """Cleanup-guard class: a failed unlink must not propagate to the caller."""
    class _NoUnlink:
        def __truediv__(self, _key):
            return self

        def exists(self):
            return True

        def unlink(self, *_a, **_k):
            raise OSError("file locked")

    monkeypatch.setattr(asr_seg, "get_asr_segment_storage_root", lambda: _NoUnlink())
    # Must not raise despite unlink() failing.
    asr_seg.delete_asr_segment_file("rec-key")
    # Empty key short-circuits too.
    asr_seg.delete_asr_segment_file("")


def _seed_chunk(db_session, *, user_id, encounter_id, recording_session_id="sess1"):
    """Persist the user + ownership-verified encounter + one claimable chunk so
    the drain call can own the encounter and see a pending chunk."""
    from server.models.user import User
    from server.models.user_encounter import UserEncounter
    from server.models.asr_recording_segment import AsrRecordingSegment

    user = User(
        id=user_id,
        email=f"drain-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="x",
        is_active=True,
        is_approved=True,
    )
    db_session.add(user)
    db_session.add(UserEncounter(id=encounter_id, user_id=user_id))
    db_session.add(AsrRecordingSegment(
        id=uuid.uuid4(),
        client_recording_id="rec-1",
        user_id=user_id,
        encounter_id=encounter_id,
        recording_session_id=recording_session_id,
        segment_role="chunk",
        chunk_index=0,
        transcription_status="pending",
        file_name="x.webm",
        mime_type="audio/webm",
        file_size=1,
        sha256="x",
        server_file_key="k",
    ))
    db_session.commit()


def test_drain_fallback_failure_is_logged_not_silent(db_session, monkeypatch, caplog):
    """Best-effort fallback: when the whole-file fallback engine fails, the
    request still returns a pipeline (no crash) AND logs the failure with
    trace_id -- it is no longer a bare silent swallow."""
    user_id, encounter_id = uuid.uuid4(), uuid.uuid4()
    _seed_chunk(db_session, user_id=user_id, encounter_id=encounter_id)

    def _boom(*_a, **_k):
        raise RuntimeError("simulated transcribe / fallback failure")

    monkeypatch.setattr(asr_seg, "transcribe_chunk_segment", _boom)
    monkeypatch.setattr(asr_seg, "_fallback_whole_file_transcribe", _boom)
    monkeypatch.setattr(asr_seg, "CHUNK_ASR_ENABLED", True)

    from server.models.user import User
    current_user = User(id=user_id, email="x@example.com", hashed_password="x",
                        is_active=True, is_approved=True)

    caplog.set_level(logging.WARNING, logger="asr_segments")
    request = SimpleNamespace(headers={"x-asr-trace-id": "trace123"})
    resp = asr_seg.drain_encounter_chunk_queue(
        encounter_id,
        request,
        recording_session_id="sess1",
        allow_fallback=True,
        diarize=False,
        session=db_session,
        current_user=current_user,
    )

    # Base per-chunk pipeline is still returned (recovery preserved, no crash);
    # the failed fallback must not set fallback_used=True.
    assert resp is not None
    assert resp.fallback_used is False
    assert any("fallback whole-file transcribe failed" in r.getMessage()
               and "trace123" in r.getMessage()
               for r in caplog.records)
