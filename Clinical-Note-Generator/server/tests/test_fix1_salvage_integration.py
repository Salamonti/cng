"""Fix 1 (f1e2): route-level integration — the salvage flag must reach the
dataset record and gate the autostart side-pipelines.

Drives /api/generate_v8_stream end-to-end with a generator that fails
validation on BOTH attempts (truncation exception on attempt 1, streaming a
draft that the guard rejects on attempt 2). Asserts:
  * the dataset record written by _log_case_completion carries
    validation_rejected=True + rejection_reasons;
  * the auto order/consult pipelines are NOT invoked for a salvaged draft.
"""
import uuid

from auth_utils import register_approve_login
from server.core.clinical_output_guard import ClinicalOutputRejected


class _DoubleRejectGenerator:
    """Attempt 1: truncation exception with a partial draft.
    Attempt 2: streams a draft containing leaked reasoning, which the real
    guard rejects with a FATAL reason -> salvage path end-to-end."""

    DRAFT = "SUBJECTIVE: my reasoning is that the patient reports feeling unwell."

    def __init__(self):
        self.calls = 0

    async def stream_completion(self, *_args, **_kwargs):
        self.calls += 1
        if self.calls == 1:
            yield "SUBJECTIVE: patient"
            raise ClinicalOutputRejected(
                "output exceeded the hard character limit",
                draft="SUBJECTIVE: patient",
            )
        # Attempt 2: stream the full draft (fatal: reasoning leak)
        yield self.DRAFT

    async def collect_completion(self, *_args, **_kwargs):
        raise AssertionError("stream path expected")


def _auth_headers(client):
    email = f"fix1e2-{uuid.uuid4().hex[:8]}@example.com"
    token = register_approve_login(client, email, "Passw0rd!1234")
    return {"Authorization": f"Bearer {token}"}


def test_salvaged_draft_flags_dataset_record_and_gates_autostart(
    client, monkeypatch
):
    import server.routes.notes as notes_routes
    from server.core.streaming.helpers import NOTE_FINAL_MARKER

    monkeypatch.setattr(notes_routes, "note_gen", _DoubleRejectGenerator())

    captured = {"records": [], "autostart_calls": []}
    monkeypatch.setattr(
        notes_routes,
        "log_case_record",
        lambda rec: captured["records"].append(rec) or "/tmp/test-salvage.jsonl",
    )
    monkeypatch.setattr(
        notes_routes,
        "log_case_quarantine",
        lambda rec: captured["records"].append(rec) or "/tmp/test-salvage-quar.jsonl",
    )

    def _fake_consult(*a, **k):
        captured["autostart_calls"].append("consult")

    def _fake_orders(*a, **k):
        captured["autostart_calls"].append("orders")

    monkeypatch.setattr(notes_routes, "_maybe_autostart_consult_comment", _fake_consult)
    monkeypatch.setattr(notes_routes, "_maybe_autostart_order_requests", _fake_orders)

    resp = client.post(
        "/api/generate_v8_stream",
        json={
            "transcription_text": "hello",
            "old_visits_text": "",
            "mixed_other_text": "",
            "note_type": "consult",
        },
        headers=_auth_headers(client),
    )

    assert resp.status_code == 200
    text = resp.text
    assert NOTE_FINAL_MARKER in text
    import json as _json
    final_payload = _json.loads(
        text.split(NOTE_FINAL_MARKER, 1)[1].strip().split("\n", 1)[0]
    )
    assert final_payload["salvaged"] is True
    assert any("reasoning" in r for r in final_payload["reasons"])

    # Dataset record carries the salvage flag + reasons.
    assert captured["records"], "dataset record was not written"
    rec = captured["records"][0]
    assert rec.get("validation_rejected") is True
    # Reasons carried are the final (attempt 2) validator reasons.
    assert any("reasoning" in r for r in rec.get("validation_rejection_reasons", []))
    # The salvaged draft is what got logged as the output.
    assert _DoubleRejectGenerator.DRAFT in rec["output_deid"]["note"]

    # Autostart side-pipelines must NOT run on a salvaged draft.
    assert captured["autostart_calls"] == []


def test_validated_note_does_not_gate_autostart(client, monkeypatch):
    """Counter-check: a normally-accepted note still autostarts (flag=False)."""
    import server.routes.notes as notes_routes

    class _GoodGen:
        async def stream_completion(self, *_args, **_kwargs):
            yield "SUBJECTIVE: patient reports feeling better.\n"

        async def collect_completion(self, *_args, **_kwargs):
            return "SUBJECTIVE: patient reports feeling better."

    monkeypatch.setattr(notes_routes, "note_gen", _GoodGen())

    captured = {"records": [], "autostart_calls": []}
    monkeypatch.setattr(
        notes_routes,
        "log_case_record",
        lambda rec: captured["records"].append(rec) or "/tmp/test-ok.jsonl",
    )
    monkeypatch.setattr(
        notes_routes,
        "log_case_quarantine",
        lambda rec: captured["records"].append(rec) or "/tmp/test-ok-quar.jsonl",
    )

    def _fake_consult(*a, **k):
        captured["autostart_calls"].append("consult")

    def _fake_orders(*a, **k):
        captured["autostart_calls"].append("orders")

    monkeypatch.setattr(notes_routes, "_maybe_autostart_consult_comment", _fake_consult)
    monkeypatch.setattr(notes_routes, "_maybe_autostart_order_requests", _fake_orders)

    resp = client.post(
        "/api/generate_v8_stream",
        json={
            "transcription_text": "hello",
            "old_visits_text": "",
            "mixed_other_text": "",
            "note_type": "consult",
        },
        headers=_auth_headers(client),
    )

    assert resp.status_code == 200
    from server.core.streaming.helpers import NOTE_FINAL_MARKER
    assert NOTE_FINAL_MARKER in resp.text
    assert captured["records"], "dataset record was not written"
    rec = captured["records"][0]
    assert rec.get("validation_rejected") is False
    assert "validation_rejection_reasons" not in rec or not rec["validation_rejection_reasons"]
    # Autostart NOT gated for a clean note (whether it actually runs depends
    # on the note type's config; we only assert the gate logic let it through
    # by calling it — the fake records the call).
    assert captured["autostart_calls"], (
        "autostart must be attempted for a validated note"
    )
