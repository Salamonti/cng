"""Regression test: medication-class exercise contraindications must fire
on real drug names, not just the class label itself.

MEDICATION_EXERCISE_WARNINGS is keyed by class labels ("beta blocker",
"blood thinner", "antihypertensive", "steroid") but real patient medication
lists contain actual drug names ("metoprolol 50mg", "warfarin 5mg") -- they
never contain the literal class label. Before this fix, check_exercise_
contraindications() matched the class label directly against the
medication list text, so only a drug literally named "insulin" could ever
match; every other class was permanently unreachable.
"""
from server.services.patient_materials_safety import check_exercise_contraindications


def test_beta_blocker_drug_name_triggers_beta_blocker_warning():
    warnings = check_exercise_contraindications(
        "Patient stable.", patient_data={"medications": "Metoprolol 50mg BID, Metformin 500mg"}
    )
    assert any("perceived exertion" in w for w in warnings)


def test_blood_thinner_drug_name_triggers_warning():
    warnings = check_exercise_contraindications(
        "Patient stable.", patient_data={"medications": "Warfarin 5mg daily"}
    )
    assert any("contact sports" in w for w in warnings)


def test_antihypertensive_drug_name_triggers_warning():
    warnings = check_exercise_contraindications(
        "Patient stable.", patient_data={"medications": "Lisinopril 10mg, Amlodipine 5mg"}
    )
    assert any("position changes" in w for w in warnings)


def test_steroid_drug_name_triggers_warning():
    warnings = check_exercise_contraindications(
        "Patient stable.", patient_data={"medications": "Prednisone 20mg daily"}
    )
    assert any("muscle weakness" in w for w in warnings)


def test_insulin_brand_name_triggers_insulin_warning():
    warnings = check_exercise_contraindications(
        "Patient stable.", patient_data={"medications": "Lantus 20 units at bedtime"}
    )
    assert any("hypoglycemia" in w for w in warnings)


def test_multiple_drug_classes_all_fire_without_duplicates():
    warnings = check_exercise_contraindications(
        "Patient stable.",
        patient_data={"medications": "Metoprolol 50mg, Warfarin 5mg, Lisinopril 10mg"},
    )
    assert any("perceived exertion" in w for w in warnings)
    assert any("contact sports" in w for w in warnings)
    assert any("position changes" in w for w in warnings)
    assert len(warnings) == len(set(warnings))


def test_unrelated_medication_triggers_no_class_warning():
    warnings = check_exercise_contraindications(
        "Patient stable.", patient_data={"medications": "Levothyroxine 50mcg daily"}
    )
    assert warnings == []
