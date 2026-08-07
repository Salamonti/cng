"""Guard-retry in PatientMaterialsGenerator._call_llm (Step 2d follow-up).

A stochastic model degeneration (repeated n-gram loop / truncation) must not
hard-fail the patient-material. _call_llm retries once at temperature 0.0 with
build_guard_retry_prompt, mirroring the note-stream path (_stream_response_v8).
"""
import pytest

from server.core.clinical_output_guard import ClinicalOutputRejected
from server.services.patient_materials_service import PatientMaterialsError, PatientMaterialsGenerator


class _FakeNoteGen:
    def __init__(self, results):
        # results: list of outcomes, each either a str (return) or Exception (raise)
        self.results = list(results)
        self.calls = []  # (prompt, temperature, max_tokens)

    async def collect_completion(self, prompt, *, temperature, max_tokens, timeout_sec=None):
        self.calls.append((prompt, temperature, max_tokens))
        outcome = self.results.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.mark.anyio
async def test_retries_after_single_loop_and_returns_good_content():
    gen = PatientMaterialsGenerator(
        _FakeNoteGen([
            ClinicalOutputRejected("repeated n-gram loop detected"),
            "GOOD PATIENT EDUCATION CONTENT",
        ])
    )
    out = await gen._call_llm("SYS", "USER", "diagnosis")
    assert out == "GOOD PATIENT EDUCATION CONTENT"
    # two LLM calls
    assert len(gen.note_gen.calls) == 2
    # retry must be greedy + use the corrective prompt (different from first)
    first, second = gen.note_gen.calls
    assert first[1] != 0.5          # original temperature (0.15 default)
    assert second[1] == 0.5         # retry at a moderate temp for a fresh sample
    assert second[1] != first[1]    # retry must not deterministically re-roll
    assert second[0] != first[0]    # build_guard_retry_prompt changed the prompt
    assert "repeated n-gram loop" in second[0]


@pytest.mark.anyio
async def test_raises_after_two_rejections_with_guard_reason():
    gen = PatientMaterialsGenerator(
        _FakeNoteGen([
            ClinicalOutputRejected("repeated n-gram loop detected"),
            ClinicalOutputRejected("repeated n-gram loop detected"),
        ])
    )
    with pytest.raises(PatientMaterialsError) as ei:
        await gen._call_llm("SYS", "USER", "diagnosis")
    assert "repeated n-gram loop detected" in str(ei.value)
    assert len(gen.note_gen.calls) == 2


@pytest.mark.anyio
async def test_non_guard_failure_still_surfaces_as_patient_materials_error():
    class _BlowUp:
        async def collect_completion(self, *a, **k):
            raise RuntimeError("backend offline")
    gen = PatientMaterialsGenerator(_BlowUp())
    with pytest.raises(PatientMaterialsError) as ei:
        await gen._call_llm("SYS", "USER", "diagnosis")
    assert "backend offline" in str(ei.value)


@pytest.mark.anyio
async def test_truncation_also_retries_then_succeeds():
    gen = PatientMaterialsGenerator(
        _FakeNoteGen([
            ClinicalOutputRejected("output truncated by max_tokens cap (finish_reason=length)"),
            "CONTENT",
        ])
    )
    out = await gen._call_llm("SYS", "USER", "diagnosis")
    assert out == "CONTENT"
    assert len(gen.note_gen.calls) == 2
