"""Regression test: diarization failures must not be reported as success.

Previously apply_asr_refine() fell back to returning the raw (undiarized)
text on any failure (no LLM configured, empty response, severe truncation)
with no way for the caller to distinguish that from a real success -- both
_apply_refine_to_pipeline and _apply_refine_to_encounter_transcript
unconditionally set refine_diarize=True regardless of outcome.
"""
import uuid

from server.routes import asr_segments as seg_routes
from server.schemas.asr_segments import AsrPipelineResponse


def test_apply_refine_to_encounter_transcript_reports_failure_honestly(monkeypatch):
    monkeypatch.setattr(seg_routes, "ASR_REFINE_ENABLED", True)
    monkeypatch.setattr(
        seg_routes,
        "apply_asr_refine",
        lambda text, *, diarize, trace_id: (text, text, False),
    )
    resp = seg_routes._apply_refine_to_encounter_transcript(
        None,
        encounter_id=uuid.uuid4(),
        whisper_text="raw whisper text",
        segments=[],
        diarize=True,
        trace_id="t1",
    )
    assert resp.refine_diarize is False
    assert resp.transcript_text == "raw whisper text"


def test_apply_refine_to_encounter_transcript_reports_success_honestly(monkeypatch):
    monkeypatch.setattr(seg_routes, "ASR_REFINE_ENABLED", True)
    monkeypatch.setattr(
        seg_routes,
        "apply_asr_refine",
        lambda text, *, diarize, trace_id: (text, "Doctor: hi\nPatient: hi", True),
    )
    resp = seg_routes._apply_refine_to_encounter_transcript(
        None,
        encounter_id=uuid.uuid4(),
        whisper_text="raw whisper text",
        segments=[],
        diarize=True,
        trace_id="t1",
    )
    assert resp.refine_diarize is True
    assert resp.transcript_text == "Doctor: hi\nPatient: hi"


def test_apply_refine_to_pipeline_reports_failure_honestly(monkeypatch):
    monkeypatch.setattr(seg_routes, "ASR_REFINE_ENABLED", True)
    monkeypatch.setattr(
        seg_routes,
        "apply_asr_refine",
        lambda text, *, diarize, trace_id: (text, text, False),
    )
    pipeline = AsrPipelineResponse(
        encounter_id=uuid.uuid4(),
        state="pending_transcription",
        pending_chunks=0,
        transcribed_chunks=1,
        total_chunks=1,
        merged_transcript_text="raw whisper text",
        can_generate_note=True,
    )
    resp = seg_routes._apply_refine_to_pipeline(pipeline, diarize=True, trace_id="t1")
    assert resp.refine_diarize is False
    assert resp.merged_transcript_text == "raw whisper text"
