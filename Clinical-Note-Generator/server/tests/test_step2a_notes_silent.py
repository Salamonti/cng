"""STEP 2a (error-handling plan): triage of the 6 silent handlers in routes/notes.py.

Reclassified per the plan's decision rule (safe-to-swallow vs unsafe-silence):
- L79  `load_config`      -> unsafe silence (a corrupt config silently changes
         generation behaviour) -> logger.warning with path+err; still returns {}.
- L166 `_append_missed_question` -> unsafe silence (dropping a missed-Q record
         loses QA data) -> logger.warning with path+err; best-effort, no raise.
- L317 `_meta_year`       -> safe to swallow (missing year is normal, hot path);
         rationale comment only, no logging.
- L651 `clean_model_output_chunk` -> safe to swallow (low-risk transform; on
         failure returns input unchanged); rationale comment only.
- L1533/L1552 `_maybe_autostart_*` -> the inner `except: pass` was a *silent
         swallow* that defeated Step 1's `[note.postgen] ... autostart failed`
         outer wrapper (exceptions never reached it). Removed the inner swallow
         so the failure propagates to the caller-owned fault-isolated wrapper,
         which logs exactly ONE warning under the correct label (no double-log)
         and never raises out of the streaming generator.

Every monkeypatch uses the auto-restoring `monkeypatch` fixture (no module
global leak), matching test_step1_postgen_sidework.py.
"""
import logging

import pytest


@pytest.fixture
def sidework():
    from server.routes import notes
    return notes


# --- L79: config-load failure -----------------------------------------------

def test_load_config_corrupt_logs_warning_and_returns_empty(caplog, monkeypatch, sidework, tmp_path):
    bad = tmp_path / "config.json"
    bad.write_text("{ not valid json !!!")
    monkeypatch.setattr(sidework, "CONFIG_PATH", bad)
    with caplog.at_level(logging.WARNING, logger="server.routes.notes"):
        cfg = sidework.load_config()
    assert cfg == {}
    joined = "\n".join(r.getMessage() for r in caplog.records)
    assert "config load failed" in joined


# --- L166: missed-question JSONL append -------------------------------------

def test_append_missed_question_write_failure_logs_and_doesnt_raise(caplog, monkeypatch, sidework, tmp_path):
    # Point _missed_q_path at a directory so the append-open raises
    # IsADirectoryError -- the record is dropped but must not propagate.
    d = tmp_path / "a_dir"
    d.mkdir()
    monkeypatch.setattr(sidework, "_missed_q_path", lambda: d)
    with caplog.at_level(logging.WARNING, logger="server.routes.notes"):
        sidework._append_missed_question({"q": "does 2mg matter?", "intent": "qa"})
    joined = "\n".join(r.getMessage() for r in caplog.records)
    assert "failed to append missed-question record" in joined


def test_append_missed_question_success_writes_jsonl(tmp_path, monkeypatch, sidework):
    out = tmp_path / "rag_missed_questions.jsonl"
    monkeypatch.setattr(sidework, "_missed_q_path", lambda: out)
    sidework._append_missed_question({"q": "question?", "year": "2026"})
    line = out.read_text(encoding="utf-8").strip()
    assert '"q": "question?"' in line
    assert '"year": "2026"' in line


# --- L317: _meta_year (safe-to-swallow, hot path) ---------------------------

def test_meta_year_garbage_returns_zero_without_logging(sidework):
    assert sidework._meta_year({"timestamp": "not-a-date"}) == 0
    assert sidework._meta_year({"year": "abcd"}) == 0
    assert sidework._meta_year(None) == 0          # non-dict -> 0, not crash
    # Valid values still extract correctly.
    assert sidework._meta_year({"year": 2023}) == 2023
    assert sidework._meta_year({"date": "2024-05-01T10:00"}) == 2024


# --- L651: clean_model_output_chunk (safe-to-swallow) -----------------------

def test_clean_model_output_chunk_strips_note_tags_and_is_safe(sidework):
    out = sidework.clean_model_output_chunk("<note>Assessment plan</note>")
    assert "<note>" not in out and "</note>" not in out
    assert "Assessment plan" in out
    assert sidework.clean_model_output_chunk("") == ""


# --- L1533/L1552: autostart inners now propagate to Step 1's outer wrapper ---

def test_autostart_order_helper_no_longer_silently_swallows(monkeypatch, sidework):
    """The inner except:pass was removed: a store failure must propagate so the
    caller's [note.postgen] order-request autostart failed wrapper can log it."""
    class _BoomStore(dict):
        def get(self, key, default=None):
            raise RuntimeError("store backend failure")
    monkeypatch.setattr(sidework, "_order_request_store", _BoomStore())
    with pytest.raises(RuntimeError):
        sidework._maybe_autostart_order_requests("g1", "note text", {"order_request_autostart": True})


@pytest.mark.anyio
async def test_autostart_consult_failure_logged_once_by_postgen_wrapper(caplog, monkeypatch, sidework):
    """A failure inside the consult-comment autostart is logged exactly ONCE under
    the [note.postgen] consult-comment autostart failed label (no double-log) and
    never raises out of _run_postgen_sidework."""
    class _BoomStore(dict):
        def get(self, key, default=None):
            raise RuntimeError("store backend failure")
    monkeypatch.setattr(sidework, "_consult_comment_store", _BoomStore())
    # Keep this test hermetic: don't write a real dataset record.
    monkeypatch.setattr(sidework, "_log_case_completion", lambda **k: None)
    with caplog.at_level(logging.WARNING, logger="server.routes.notes"):
        await sidework._run_postgen_sidework(
            generation_id="gx", combined_output="Some clinical note.",
            cfg={"consult_comment_autostart": True, "order_request_autostart": False},
            note_type="clinic", user_speciality=None, created_at="t", duration=1.0,
            prompt="p", transcription_text="t", old_visits_text="", mixed_other_text="",
            custom_prompt="", token_count=10, actor={"user_id": "u1"},
        )
    joined = "\n".join(r.getMessage() for r in caplog.records)
    assert "consult-comment autostart failed" in joined
    assert joined.count("consult-comment autostart failed") == 1
