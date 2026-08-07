"""STEP 1 (error-handling plan): notes.py post-gen side-work misdirection fix.

The old code wrapped dataset logging + generation-cache update + both autostart
pipelines in ONE try/except that blamed "Dataset case logging failed" for a
crash in any of them (and used print(), not a logger). Now each responsibility
is fault-isolated in `_run_postgen_sidework` and logged under its own label.

Assertions:
- A failure in the ORDER-REQUEST pipeline is logged as "order-request autostart
  failed", NOT misattributed to dataset logging.
- A failure in the CONSULT-COMMENT pipeline is logged under its own label.
- A dataset-logging failure is logged under "dataset case logging failed".
- A failure in any one step never raises out of `_run_postgen_sidework`.
- Every monkeypatch uses the auto-restoring `monkeypatch` fixture so no module
  global leak can contaminate other test modules (e.g. Step 8's quarantine
  suite which drives the real `_log_case_completion`).
"""
import logging

import pytest


@pytest.fixture
def sidework():
    from server.routes import notes
    return notes


def _override_sidework_deps(monkeypatch, notes, *, log_fail=None, consult_fail=None, order_fail=None):
    def _fake_log_completion(**kwargs):
        if log_fail:
            raise log_fail
        return None

    def _fake_consult(*a, **k):
        if consult_fail:
            raise consult_fail

    def _fake_order(*a, **k):
        if order_fail:
            raise order_fail

    monkeypatch.setattr(notes, "_log_case_completion", _fake_log_completion)
    monkeypatch.setattr(notes, "_maybe_autostart_consult_comment", _fake_consult)
    monkeypatch.setattr(notes, "_maybe_autostart_order_requests", _fake_order)


async def _run(notes, gen="g1"):
    await notes._run_postgen_sidework(
        generation_id=gen, combined_output="note", cfg={},
        note_type="clinic", user_speciality=None, created_at="t",
        duration=1.0, prompt="p", transcription_text="t",
        old_visits_text="", mixed_other_text="", custom_prompt="",
        token_count=10, actor={"user_id": "u1"},
    )


@pytest.mark.anyio
async def test_order_pipeline_failure_is_not_misattributed(caplog, monkeypatch, sidework):
    _override_sidework_deps(monkeypatch, sidework, order_fail=RuntimeError("order engine down"))
    with caplog.at_level(logging.WARNING, logger="server.routes.notes"):
        await _run(sidework, "g1")
    joined = "\n".join(r.getMessage() for r in caplog.records)
    assert "order-request autostart failed" in joined
    assert "g1" in joined
    # The old bug misattributed this to dataset logging -- must not happen.
    assert "dataset case logging failed" not in joined


@pytest.mark.anyio
async def test_consult_failure_labeled_correctly(caplog, monkeypatch, sidework):
    _override_sidework_deps(monkeypatch, sidework, consult_fail=RuntimeError("consult engine down"))
    with caplog.at_level(logging.WARNING, logger="server.routes.notes"):
        await _run(sidework, "g2")
    joined = "\n".join(r.getMessage() for r in caplog.records)
    assert "consult-comment autostart failed" in joined
    assert "dataset case logging failed" not in joined


@pytest.mark.anyio
async def test_dataset_logging_failure_labeled_correctly(caplog, monkeypatch, sidework):
    _override_sidework_deps(monkeypatch, sidework, log_fail=RuntimeError("disk full"))
    with caplog.at_level(logging.WARNING, logger="server.routes.notes"):
        await _run(sidework, "g3")
    joined = "\n".join(r.getMessage() for r in caplog.records)
    assert "dataset case logging failed" in joined


@pytest.mark.anyio
async def test_failure_never_raises_out(monkeypatch, sidework):
    _override_sidework_deps(
        monkeypatch, sidework,
        log_fail=RuntimeError("log"), consult_fail=RuntimeError("consult"),
        order_fail=RuntimeError("order"),
    )
    result = await _run(sidework, "g4")
    assert result is None
