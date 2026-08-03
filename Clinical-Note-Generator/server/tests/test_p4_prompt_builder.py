# P4 prompt builder: location placeholders, merged encounter addendum, legacy NS substitution.
from server.core.prompt import builder as pb


def test_prompt_values_includes_user_location():
    v = pb._prompt_values(
        today="2026-04-05",
        note_type="consult",
        user_speciality="cardiology",
        user_location="Halifax, NS",
        raw_data="[x]",
    )
    assert v["USER_LOCATION"] == "Halifax, NS"
    assert v["USER_SPECIALITY"] == "cardiology"
    assert v["USER_DISPLAY_NAME"] == "Not specified"
    assert "{USER_LOCATION}" not in v["RAW_DATA"]


def test_prompt_values_author_prefers_display_name():
    v = pb._prompt_values(
        today="2026-04-05",
        note_type="consult",
        user_speciality="x",
        user_location="y",
        raw_data="z",
        user_display_name="Dr. A",
        user_email="a@b.com",
    )
    assert v["USER_DISPLAY_NAME"] == "Dr. A"
    assert v["USER_EMAIL"] == "a@b.com"


def test_prompt_values_author_falls_back_to_email():
    v = pb._prompt_values(
        today="2026-04-05",
        note_type="consult",
        user_speciality="x",
        user_location="y",
        raw_data="z",
        user_display_name=None,
        user_email="only@email.com",
    )
    assert v["USER_DISPLAY_NAME"] == "only@email.com"


def test_region_substitution_replaces_nova_scotia():
    s = "You practice in Nova Scotia, Canada as a hospitalist."
    assert "Anchorage" in pb._apply_region_substitution(s, "Anchorage, AK")
    assert "Nova Scotia" not in pb._apply_region_substitution(s, "Anchorage, AK")
    assert pb._apply_region_substitution(s, "") == s
    assert pb._apply_region_substitution(s, "   ") == s


def test_user_section_merges_addendum():
    u = pb._compose_user_section("Template line one.", "Be brief.")
    assert "Template line one." in u
    assert "CLINICIAN ENCOUNTER-SPECIFIC INSTRUCTION:" in u
    assert "Be brief." in u
    assert u.index("Template") < u.index("INSTRUCTION")


def test_build_prompt_v8_multi_issue_soap_data_block_hints(monkeypatch):
    monkeypatch.setattr(
        pb,
        "load_config",
        lambda: {
            "default_note_system_prompt": "SYS",
            "default_note_user_prompts": {"multi_issue_soap": "USR"},
            "preprocessing": {"enabled": False},
        },
    )
    out = pb.build_prompt_v8(
        transcription_text="Patient says cough",
        old_visits_text="",
        mixed_other_text="CT chest: mass",
        note_type="multi_issue_soap",
    )
    assert "third person" in out.lower()
    assert "CT/MRI" in out or "imaging" in out.lower()
    assert "LABS_IMAGING_OTHER" in out
    assert "CT chest" in out


def test_build_prompt_v8_addendum_inside_user_not_additional_section(monkeypatch):
    monkeypatch.setattr(
        pb,
        "load_config",
        lambda: {
            "default_note_system_prompt": "SYS {USER_SPECIALITY} {USER_LOCATION}",
            "default_note_user_prompts": {"consult": "USR {NOTE_TYPE}"},
            "preprocessing": {"enabled": False},
        },
    )
    out = pb.build_prompt_v8(
        transcription_text="hello patient",
        old_visits_text="",
        mixed_other_text="",
        note_type="consult",
        custom_prompt="FOOTER ADD",
        user_speciality="neurology",
        user_location="Test Clinic",
    )
    assert "SYSTEM:" in out
    assert "USER:" in out
    assert "USR consult" in out
    assert "CLINICIAN ENCOUNTER-SPECIFIC INSTRUCTION:" in out
    assert "FOOTER ADD" in out
    assert "ADDITIONAL INSTRUCTIONS:" not in out
    assert "Test Clinic" in out
    assert "neurology" in out


def test_profile_author_line_appended(monkeypatch):
    monkeypatch.setattr(
        pb,
        "load_config",
        lambda: {
            "default_note_system_prompt": "Short system.",
            "default_note_user_prompts": {"consult": "Do {NOTE_TYPE}."},
            "preprocessing": {"enabled": False},
        },
    )
    out = pb.build_prompt_v8(
        transcription_text="x",
        old_visits_text="",
        mixed_other_text="",
        note_type="consult",
        user_display_name="Dr. Sam",
        user_email="sam@clinic.test",
    )
    assert pb.PROFILE_AUTHOR_MARKER in out
    assert "Dr. Sam" in out


def test_profile_location_line_appended_when_system_has_no_placeholder(monkeypatch):
    """SYSTEM must still orient to profile when config does not use {USER_LOCATION}."""
    monkeypatch.setattr(
        pb,
        "load_config",
        lambda: {
            "default_note_system_prompt": "You are an attending physician. Be concise.",
            "default_note_user_prompts": {"consult": "Do the thing for {NOTE_TYPE}."},
            "preprocessing": {"enabled": False},
        },
    )
    out = pb.build_prompt_v8(
        transcription_text="x",
        old_visits_text="",
        mixed_other_text="",
        note_type="consult",
        user_location="Calgary, AB",
    )
    assert pb.PROFILE_LOCATION_MARKER in out
    assert "Calgary, AB" in out
    assert "SYSTEM:" in out


def test_build_prompt_v8_user_location_default_placeholder(monkeypatch):
    monkeypatch.setattr(
        pb,
        "load_config",
        lambda: {
            "default_note_system_prompt": "LOC={USER_LOCATION}",
            "default_note_user_prompts": {"consult": "X"},
            "preprocessing": {"enabled": False},
        },
    )
    out = pb.build_prompt_v8(
        transcription_text="t",
        old_visits_text="",
        mixed_other_text="",
        note_type="consult",
        user_location=None,
    )
    assert "LOC=Not specified" in out
