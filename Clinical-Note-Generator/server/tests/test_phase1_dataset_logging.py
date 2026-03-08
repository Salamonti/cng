import json
from pathlib import Path


def test_deid_v1_redacts_common_phi():
    from server.core.deid.v1 import deidentify_text

    text = (
        "Patient: John Smith\n"
        "DOB: 1980-01-02\n"
        "MRN: A1234567\n"
        "Phone: 555-123-4567\n"
        "Email: john@example.com"
    )
    out = deidentify_text(text)

    redacted = out["text"]
    assert "[NAME_REDACTED]" in redacted
    assert "[DATE_REDACTED]" in redacted
    assert "[MRN_REDACTED]" in redacted
    assert "[PHONE_REDACTED]" in redacted
    assert "[EMAIL_REDACTED]" in redacted
    assert out["redaction_counts"]["name"] >= 1
    assert out["leak_flags"]["raw_has_any"] is True


def test_dataset_logger_writes_case_and_event(tmp_path, monkeypatch):
    from server.core.logging.dataset_logger import log_case_event, log_case_record

    monkeypatch.setenv("CNG_DATASET_DIR", str(tmp_path))

    case_record = {
        "case_id": "case-1",
        "created_at": "2026-03-08T00:00:00+00:00",
        "duration_s": 1.2,
        "note_type": "consult",
        "pipeline": "v8_direct",
        "user_id": "u1",
        "model": {"chat_model_name": "m", "model_path": "p", "endpoint_used": "/v1/chat/completions"},
        "prompt": {"system": "s", "user": "u"},
        "input_deid": {"fields": {}, "redaction_counts_total": {}, "leak_flags": {"raw_has_any": False}},
        "output_deid": {"note": "n", "redaction_counts": {}, "leak_flags": {"raw_has_any": False}},
        "tokens": {"prompt_tokens": 1, "completion_tokens": 1, "method": "approx_word_count"},
        "feedback_snapshot": None,
    }
    event_record = {
        "event_id": "evt-1",
        "case_id": "case-1",
        "created_at": "2026-03-08T00:00:01+00:00",
        "event_type": "thumbs_up",
        "rating": 2,
        "user_id": "u1",
    }

    case_path = log_case_record(case_record)
    event_path = log_case_event(event_record)

    case_lines = [json.loads(x) for x in Path(case_path).read_text(encoding="utf-8").splitlines() if x.strip()]
    event_lines = [json.loads(x) for x in Path(event_path).read_text(encoding="utf-8").splitlines() if x.strip()]
    assert case_lines[-1]["case_id"] == "case-1"
    assert event_lines[-1]["event_type"] == "thumbs_up"
