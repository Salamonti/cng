"""Chunk transcribe ordering guard."""
from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest

from server.core.asr_chunk import assert_chunk_ready_for_transcribe
from server.models.asr_recording_segment import AsrRecordingSegment


def test_assert_chunk_ready_allows_index_zero():
    seg = AsrRecordingSegment(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        encounter_id=uuid.uuid4(),
        chunk_index=0,
        segment_role="chunk",
        recording_session_id="rec_test",
        transcription_status="pending",
    )
    assert_chunk_ready_for_transcribe(MagicMock(), seg)


def test_assert_chunk_ready_blocks_when_prev_missing():
    seg = AsrRecordingSegment(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        encounter_id=uuid.uuid4(),
        chunk_index=2,
        segment_role="chunk",
        recording_session_id="rec_test",
        transcription_status="pending",
    )
    with patch("server.core.asr_chunk._prev_chunk_segment", return_value=None):
        with pytest.raises(ValueError, match="Missing prior chunk"):
            assert_chunk_ready_for_transcribe(MagicMock(), seg)


def test_assert_chunk_ready_blocks_when_prev_not_transcribed():
    prev = AsrRecordingSegment(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        encounter_id=uuid.uuid4(),
        chunk_index=1,
        segment_role="chunk",
        recording_session_id="rec_test",
        transcription_status="pending",
    )
    seg = AsrRecordingSegment(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        encounter_id=uuid.uuid4(),
        chunk_index=2,
        segment_role="chunk",
        recording_session_id="rec_test",
        transcription_status="pending",
    )
    with patch("server.core.asr_chunk._prev_chunk_segment", return_value=prev):
        with pytest.raises(ValueError, match="not transcribed"):
            assert_chunk_ready_for_transcribe(MagicMock(), seg)
