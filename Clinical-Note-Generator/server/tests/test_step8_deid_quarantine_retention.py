"""Step 8 (M-2 / M-3) tests.

M-2: de-id residual enforcement -- a case record whose input/output still
     has residual_any/ner_error after de-id must be quarantined (off the
     clean training store), not persisted alongside PHI-clean records.
M-3: retention -- date-stamped dataset JSONL files older than the retention
     window are purged, and the unbounded rag_missed_questions.jsonl is
     rotated past a size cap.
"""
import json
import os
from pathlib import Path

import pytest


# --------------------------------------------------------------------------
# M-2: quarantine routing
# --------------------------------------------------------------------------

def _run_log_case_completion(monkeypatch, tmp_path, *, input_text="", output_text=""):
    """Drive _log_case_completion with stub de-id + stubbed model meta so we
    control the leak flags deterministically and capture the store written."""
    from server.routes import notes as notes_mod

    monkeypatch.setattr(
        notes_mod, "get_simple_note_generator", lambda _name: SimpleModelMeta()
    )
    # Re-init note_gen from the stubbed factory so _model_meta() sees it.
    notes_mod.note_gen = SimpleModelMeta()

    written = {"records": [], "quarantines": []}
    monkeypatch.setattr(
        notes_mod, "log_case_record", lambda rec: written["records"].append(rec) or "clean"
    )
    monkeypatch.setattr(
        notes_mod, "log_case_quarantine", lambda rec: written["quarantines"].append(rec) or "quarantine"
    )

    # Stub deidentify_fields (which _deid_fields now calls) to return
    # controllable leak flags per field. The fake delegates per-field to
    # fake_deid so the input/output sentinel logic is unchanged.
    call = {"n": 0}
    def fake_deid(text):
        call["n"] += 1
        # Return residual on the OUTPUT call only when requested by making the
        # caller pass a marker. We instead control via input/output text sentinels.
        if "RESIDUAL_OUTPUT" in (text or ""):
            return {
                "text": "[NAME_REDACTED]",
                "redaction_counts": {"name": 1},
                "leak_flags": {"residual_any": True, "ner_error": False, "raw_has_any": True},
            }
        if "RESIDUAL_INPUT" in (text or ""):
            return {
                "text": "[NAME_REDACTED]",
                "redaction_counts": {"name": 1},
                "leak_flags": {"residual_any": True, "ner_error": False, "raw_has_any": True},
            }
        if "NER_ERROR_TEXT" in (text or ""):
            return {
                "text": "x",
                "redaction_counts": {},
                "leak_flags": {"residual_any": False, "ner_error": True, "raw_has_any": False},
            }
        return {
            "text": "clean",
            "redaction_counts": {},
            "leak_flags": {"residual_any": False, "ner_error": False, "raw_has_any": False},
        }
    def fake_deid_fields(fields):
        return {k: fake_deid(v) for k, v in fields.items()}
    monkeypatch.setattr(notes_mod, "deidentify_fields", fake_deid_fields)
    monkeypatch.setattr(notes_mod, "deidentify_text", fake_deid)

    notes_mod._log_case_completion(
        case_id="case-x",
        created_at="2026-08-07T00:00:00+00:00",
        duration_s=1.0,
        note_type="consult",
        pipeline="v8_direct",
        prompt="system\n\nuser",
        input_fields={"transcription_text": input_text},
        output_text=output_text,
        prompt_tokens=5,
        completion_tokens=5,
        actor={"user_id": "u1", "user_email": "a@b.c"},
    )
    return written


class SimpleModelMeta:
    use_chat_api = False
    chat_model_name = "m"
    model_path = "p"


def test_clean_record_goes_to_training_store(monkeypatch, tmp_path):
    written = _run_log_case_completion(monkeypatch, tmp_path, output_text="plain text")
    assert len(written["records"]) == 1
    assert written["records"][0].get("quarantined") is not True
    assert len(written["quarantines"]) == 0


def test_output_residual_routes_to_quarantine(monkeypatch, tmp_path):
    written = _run_log_case_completion(monkeypatch, tmp_path, output_text="RESIDUAL_OUTPUT x")
    assert len(written["quarantines"]) == 1
    assert len(written["records"]) == 0
    q = written["quarantines"][0]
    assert q["quarantined"] is True
    assert q["quarantine_reason"]["output_residual_any"] is True


def test_input_residual_routes_to_quarantine(monkeypatch, tmp_path):
    written = _run_log_case_completion(monkeypatch, tmp_path, input_text="RESIDUAL_INPUT x")
    assert len(written["quarantines"]) == 1
    assert written["quarantines"][0]["quarantine_reason"]["input_residual_any"] is True


def test_ner_error_routes_to_quarantine(monkeypatch, tmp_path):
    written = _run_log_case_completion(monkeypatch, tmp_path, output_text="NER_ERROR_TEXT x")
    assert len(written["quarantines"]) == 1
    assert written["quarantines"][0]["quarantine_reason"]["output_ner_error"] is True


# --------------------------------------------------------------------------
# M-2: log_case_quarantine writes to a separate file (not the clean store)
# --------------------------------------------------------------------------

def test_log_case_quarantine_writes_separate_store(tmp_path, monkeypatch):
    from server.core.logging.dataset_logger import log_case_quarantine, log_case_record

    monkeypatch.setenv("CNG_DATASET_DIR", str(tmp_path))
    rec = {"case_id": "q1", "quarantined": True}
    clean = {"case_id": "c1"}

    q_path = Path(log_case_quarantine(rec))
    c_path = Path(log_case_record(clean))

    assert "quarantine" in q_path.name
    assert "quarantine" not in c_path.name
    q_lines = [json.loads(x) for x in q_path.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert q_lines[-1]["case_id"] == "q1"


# --------------------------------------------------------------------------
# M-3: retention
# --------------------------------------------------------------------------

def _make_dataset_file(tmp_path, name):
    p = tmp_path / name
    p.write_text('{"a":1}\n', encoding="utf-8")
    return p


def test_purge_deletes_files_older_than_retention(tmp_path, monkeypatch):
    from tools.rotate_dataset_logs import purge_dataset_files

    monkeypatch.setenv("CNG_DATASET_DIR", str(tmp_path))
    monkeypatch.setenv("DATASET_RETENTION_DAYS", "90")
    # Old file (embedded date far in past) should be purged.
    _make_dataset_file(tmp_path, "cases_2026-01-01.jsonl")
    # Recent file kept.
    _make_dataset_file(tmp_path, "cases_2026-08-01.jsonl")
    # Quarantine old file also purged.
    _make_dataset_file(tmp_path, "cases_quarantine_2026-01-02.jsonl")
    _make_dataset_file(tmp_path, "case_events_2026-01-03.jsonl")

    deleted = purge_dataset_files()
    assert deleted == 3
    remaining = sorted(p.name for p in tmp_path.glob("*.jsonl"))
    assert remaining == ["cases_2026-08-01.jsonl"]


def test_purge_keeps_files_within_retention(tmp_path, monkeypatch):
    from tools.rotate_dataset_logs import purge_dataset_files

    monkeypatch.setenv("CNG_DATASET_DIR", str(tmp_path))
    monkeypatch.setenv("DATASET_RETENTION_DAYS", "90")
    _make_dataset_file(tmp_path, "cases_2026-08-07.jsonl")
    # No-date file with recent mtime kept.
    p = tmp_path / "cases_unknown.jsonl"
    p.write_text("{}\n", encoding="utf-8")
    assert purge_dataset_files() == 0
    assert len(list(tmp_path.glob("*.jsonl"))) == 2


def test_rotate_missed_questions_under_cap_is_noop(tmp_path, monkeypatch):
    import tools.rotate_dataset_logs as rot
    from tools.rotate_dataset_logs import rotate_missed_questions

    monkeypatch.setattr(rot, "_logs_dir", lambda: tmp_path)
    p = tmp_path / "rag_missed_questions.jsonl"
    p.write_text("x" * 1000, encoding="utf-8")
    monkeypatch.setenv("RAG_MISSED_MAX_BYTES", "100000000")
    assert rotate_missed_questions() is False
    assert p.exists()


def test_rotate_missed_questions_over_cap_rotates(tmp_path, monkeypatch):
    import tools.rotate_dataset_logs as rot
    from tools.rotate_dataset_logs import rotate_missed_questions

    monkeypatch.setattr(rot, "_logs_dir", lambda: tmp_path)
    p = tmp_path / "rag_missed_questions.jsonl"
    p.write_text("x" * 1000, encoding="utf-8")
    monkeypatch.setenv("RAG_MISSED_MAX_BYTES", "100")
    assert rotate_missed_questions() is True
    assert not p.exists()
    assert len(list(tmp_path.glob("rag_missed_questions.*.jsonl.old"))) == 1
