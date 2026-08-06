"""Regression test: source-block framing must match what the note type is.

Reported 2026-08-06: selecting "summarize" on a pasted referral letter produced
a consultation note written in the first person ("I evaluated Lucy Dugas..."),
for a visit that never happened. Routing and the note-type prompt were both
correct -- the prompt literally said "Produce a concise clinical summary". The
scaffolding around it contradicted them: every note type except
multi_issue_soap had its source wrapped as "the transcription from today's
clinical encounter / treat as CURRENT", and the universal system prompt asks
for the clinician's voice.

Second, narrower defect: the same preamble told the model that examination
findings belong in "the note's Physical Exam section" -- a heading prompt
policy v2 emits for NO note type (it is "Objective Findings", "Relevant
Examination and Results", "Examination", "Current Findings", "Findings", or
absent). Naming a section the template does not define either invents a stray
heading or is ignored.
"""
import json
from pathlib import Path

import pytest

from server.core.prompt.builder import (
    DOCUMENT_ORIENTED_NOTE_TYPES,
    is_document_oriented_note_type,
)
from server.routes.notes import build_prompt_other, build_prompt_v8

_CFG = json.loads(
    (Path(__file__).resolve().parents[2] / "config" / "config.json").read_text(encoding="utf-8")
)
_SOURCE = (
    "Dear Dr. Eissa, Thank you for seeing Lucy Dugas, a 71year old female "
    "with persistent leukocytosis. Please assess for myeloproliferative disorder."
)


def _other(note_type):
    return build_prompt_other(
        transcription_text=_SOURCE, old_visits_text="", mixed_other_text="",
        note_type=note_type, custom_prompt="", user_speciality="Internal Medicine",
        merged_user_prompts_other=_CFG["default_note_user_prompts_other"],
        user_location=None, user_display_name="Islam Eissa", user_email="x@y.ca",
    )


def _standard(note_type):
    return build_prompt_v8(
        transcription_text=_SOURCE, old_visits_text="", mixed_other_text="",
        note_type=note_type, custom_prompt="", user_speciality="Internal Medicine",
        merged_user_prompts=_CFG["default_note_user_prompts"],
        user_location=None, user_display_name="Islam Eissa", user_email="x@y.ca",
    )


class TestDocumentOrientedFraming:
    @pytest.mark.parametrize("note_type", sorted(DOCUMENT_ORIENTED_NOTE_TYPES))
    def test_not_framed_as_an_encounter_you_conducted(self, note_type):
        prompt = _other(note_type)
        assert "source material supplied for review" in prompt
        assert "transcription from today's clinical encounter" not in prompt
        assert "Do not assume it describes an encounter you conducted" in prompt

    def test_summarize_is_document_oriented(self):
        assert is_document_oriented_note_type("summarize")
        assert is_document_oriented_note_type("pre_encounter_prep")
        assert is_document_oriented_note_type("custom")

    def test_real_encounter_types_keep_encounter_framing(self):
        """Demoting the framing must not leak into genuine encounter notes."""
        for note_type in ("consult", "progress", "admission", "multi_issue_soap"):
            prompt = _standard(note_type)
            assert "transcription from today's clinical encounter" in prompt, note_type
            assert not is_document_oriented_note_type(note_type)


class TestNoPhantomSectionNames:
    @pytest.mark.parametrize(
        "note_type", ["consult", "progress", "followup", "admission", "discharge", "transfer"]
    )
    def test_standard_types_do_not_name_a_physical_exam_section(self, note_type):
        """No prompt-policy-v2 note type defines a 'Physical Exam' heading."""
        assert "Physical Exam section" not in _standard(note_type)

    @pytest.mark.parametrize("note_type", ["referral", "procedure", "summarize"])
    def test_other_types_do_not_name_a_physical_exam_section(self, note_type):
        assert "Physical Exam section" not in _other(note_type)

    def test_multi_issue_soap_still_routes_to_objective(self):
        """SOAP genuinely defines Objective -- that instruction must survive."""
        assert "## Objective" in _standard("multi_issue_soap")
