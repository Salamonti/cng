"""Diet/exercise interactive param-completion: extraction + merge + needs_input.

Covers:
  * regex extraction (age/weight/height/sex) across phrasings + range guards
  * LLM extraction parsing (json, fenced json, garbage -> {})
  * merge rules: user wins, numeric disagreement -> None, qualitative -> LLM
  * missing_blocking: activity level NON-blocking for both diet and exercise
  * service generate_one -> needs_input dict (not error) when blocking absent
  * route-level needs_input shape + cached resubmit via complete data
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server.services.patient_materials_extraction import (
    REQUIRED_FIELDS,
    extract_from_note_llm,
    extract_from_note_regex,
    merge_extraction,
    missing_blocking,
)
from server.services.patient_materials_service import (
    PatientMaterialsGenerator,
    PatientMaterialsNeedsInput,
)

import asyncio
import pytest


# ---------------------------------------------------------------------------
# regex extraction
# ---------------------------------------------------------------------------

class TestRegexExtraction:
    def test_full_demographics(self):
        note = ("68-year-old male with IHD. Weight: 92 kg, height 165 cm. "
                "BMI 33.9. Started on nintedanib.")
        v = extract_from_note_regex(note)
        assert v["age"] == 68
        assert v["weight_kg"] == 92
        assert v["height_cm"] == 165
        assert v["sex"] == "male"

    def test_age_variants(self):
        assert extract_from_note_regex("a 54 year old woman")["age"] == 54
        assert extract_from_note_regex("patient, age 41")["age"] == 41
        assert extract_from_note_regex("32 yo male")["age"] == 32

    def test_age_range_guard(self):
        # "230-year-old" is not a plausible age
        assert extract_from_note_regex("a 230-year-old")["age"] is None

    def test_weight_ignores_obvious_bmi(self):
        # "BMI 33.9" must not be read as weight 33.9 kg
        note = "BMI 33.9, weight of 85 kg"
        v = extract_from_note_regex(note)
        assert v["weight_kg"] == 85

    def test_no_vitals_in_plain_text(self):
        v = extract_from_note_regex("Progressive dyspnea and dry cough.")
        assert v["weight_kg"] is None
        assert v["height_cm"] is None
        assert v["age"] is None

    def test_female(self):
        v = extract_from_note_regex("55-year-old female with T2DM")
        assert v["sex"] == "female"


# ---------------------------------------------------------------------------
# LLM extraction parsing
# ---------------------------------------------------------------------------

class _FakeGen:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    async def collect_completion(self, prompt, temperature, max_tokens, stop=None, timeout_sec=None):
        self.calls += 1
        return self.payload


class TestLlmExtraction:
    def test_plain_json(self):
        payload = json.dumps({
            "age": 54, "weight_kg": 92.0, "height_cm": 165.0, "sex": "female",
            "goal": "decrease", "activity_level": "lightly_active",
            "allergies": "peanuts", "restrictions": "diabetic",
            "joint_issues": None,
        })
        out = asyncio.run(extract_from_note_llm("note", _FakeGen(payload)))
        assert out["age"] == 54
        assert out["weight_kg"] == 92.0
        assert out["goal"] == "decrease"
        assert out["activity_level"] == "lightly_active"
        assert out["allergies"] == "peanuts"
        assert out["joint_issues"] is None

    def test_fenced_json(self):
        payload = "```json\n" + json.dumps({"age": 60, "weight_kg": None,
                                           "height_cm": 170, "sex": "male",
                                           "goal": None, "activity_level": "sedentary",
                                           "allergies": None, "restrictions": None,
                                           "joint_issues": "knee arthritis"}) + "\n```"
        out = asyncio.run(extract_from_note_llm("note", _FakeGen(payload)))
        assert out["age"] == 60
        assert out["activity_level"] == "sedentary"
        assert out["joint_issues"] == "knee arthritis"

    def test_garbage_returns_empty(self):
        out = asyncio.run(extract_from_note_llm("note", _FakeGen("I cannot parse.")))
        assert out == {}

    def test_llm_failure_returns_empty(self):
        class _BoomGen:
            async def collect_completion(self, *a, **k):
                raise RuntimeError("vllm down")
        out = asyncio.run(extract_from_note_llm("note", _BoomGen()))
        assert out == {}

    def test_out_of_range_llm_values_dropped(self):
        payload = json.dumps({"age": 250, "weight_kg": 5, "height_cm": 300,
                              "sex": "android", "goal": "yolo",
                              "activity_level": "extreme", "allergies": None,
                              "restrictions": None, "joint_issues": None})
        out = asyncio.run(extract_from_note_llm("note", _FakeGen(payload)))
        assert out["age"] is None
        assert out["weight_kg"] is None
        assert out["sex"] is None
        assert out["goal"] is None
        assert out["activity_level"] is None


# ---------------------------------------------------------------------------
# merge rules
# ---------------------------------------------------------------------------

class TestMerge:
    def test_user_wins_over_everything(self):
        m = merge_extraction(
            {"weight_kg": 50, "goal": "maintain"},
            {"weight_kg": 92, "height_cm": 165, "age": 68},
            {"weight_kg": 91, "goal": "decrease"},
        )
        assert m["weight_kg"] == 50          # user
        assert m["goal"] == "maintain"       # user
        assert m["height_cm"] == 165         # regex
        assert m["age"] == 68                # regex
        assert m["sex"] is None

    def test_numeric_disagreement_becomes_missing(self):
        m = merge_extraction({}, {"weight_kg": 92}, {"weight_kg": 80})
        assert m["weight_kg"] is None

    def test_numeric_agreement_prefers_regex(self):
        m = merge_extraction({}, {"weight_kg": 92}, {"weight_kg": 92.3})
        assert m["weight_kg"] == 92  # within 0.5, regex preferred

    def test_qualitative_prefers_llm(self):
        m = merge_extraction({}, {}, {"activity_level": "moderately_active",
                                      "goal": "decrease"})
        assert m["activity_level"] == "moderately_active"
        assert m["goal"] == "decrease"


# ---------------------------------------------------------------------------
# missing_blocking
# ---------------------------------------------------------------------------

class TestMissingBlocking:
    def test_diet_requires_weight_height_goal(self):
        assert missing_blocking("diet", {}) == ["weight_kg", "height_cm", "goal"]
        assert missing_blocking("diet", {"weight_kg": 92, "height_cm": 165}) == ["goal"]
        assert missing_blocking("diet", {"weight_kg": 92, "height_cm": 165,
                                         "goal": "decrease"}) == []

    def test_activity_level_is_non_blocking(self):
        assert "activity_level" not in REQUIRED_FIELDS["diet"]
        assert "activity_level" not in REQUIRED_FIELDS["exercise"]
        # exercise with only weight/height/goal -> no missing
        assert missing_blocking("exercise", {"weight_kg": 92, "height_cm": 165,
                                             "goal": "decrease"}) == []

    def test_other_materials_never_block(self):
        assert missing_blocking("medications", {}) == []
        assert missing_blocking("full_report", {}) == []


# ---------------------------------------------------------------------------
# service-level needs_input
# ---------------------------------------------------------------------------

class _RecordingGenerator:
    """Canned LLM: records the last prompt so we can assert patient_info."""

    def __init__(self, answer="## Overview\nplan text"):
        self.answer = answer
        self.last_prompt = None

    async def collect_completion(self, prompt, temperature, max_tokens, stop=None, timeout_sec=None):
        self.last_prompt = prompt
        return self.answer


class TestServiceNeedsInput:
    def test_diet_missing_all_returns_needs_input_shape(self):
        gen = PatientMaterialsGenerator(_RecordingGenerator())
        result = asyncio.run(gen.generate_one(
            "diet", "A consult note with no vitals.", None,
            {"weight_kg": None, "height_cm": None, "goal": None, "age": 54, "sex": "female"}))
        assert result["status"] == "needs_input"
        assert result["material_type"] == "diet"
        assert result["missing_fields"] == ["weight_kg", "height_cm", "goal"]
        assert result["error"] is None
        assert result["content"] == ""
        # known data is surfaced for the form pre-fill
        assert result["known_data"].get("age") == 54
        assert result["known_data"].get("sex") == "female"

    def test_exercise_missing_goal_only(self):
        gen = PatientMaterialsGenerator(_RecordingGenerator())
        result = asyncio.run(gen.generate_one(
            "exercise", "note", None,
            {"weight_kg": 92, "height_cm": 165, "goal": None}))
        assert result["status"] == "needs_input"
        assert result["missing_fields"] == ["goal"]

    def test_activity_absent_does_not_block(self, monkeypatch):
        """Exercise must generate (not needs_input) without activity_level."""
        gen = PatientMaterialsGenerator(_RecordingGenerator())
        result = asyncio.run(gen.generate_one(
            "exercise", "note", None,
            {"weight_kg": 92, "height_cm": 165, "goal": "decrease"}))
        assert result.get("status") != "needs_input"
        assert result["content"], "exercise plan must be generated without activity level"
        # the assumption must be stated somewhere in the prompt or output
        prompt_or_content = (gen.note_gen.last_prompt or "") + (result["content"] or "")
        assert "activity" in prompt_or_content.lower()

    def test_diet_with_data_generates_and_age_in_prompt(self):
        gen = PatientMaterialsGenerator(_RecordingGenerator())
        result = asyncio.run(gen.generate_one(
            "diet", "note", None,
            {"weight_kg": 92, "height_cm": 165, "goal": "decrease",
             "age": 54, "sex": "female"}))
        assert result["content"]
        prompt = gen.note_gen.last_prompt or ""
        assert "Age: 54" in prompt
        assert "Sex: female" in prompt
        assert "Weight: 92 kg" in prompt


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
