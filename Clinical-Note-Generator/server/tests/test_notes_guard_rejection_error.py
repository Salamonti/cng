"""Regression test (WO-3 C-3): a clinical-output-guard rejection must not be
misreported as "input too long", and the rejected draft (when the guard
captured one) must be surfaced to the clinician instead of silently
discarding a full encounter's dictation.

ClinicalOutputRejected subclasses RuntimeError, and its messages (e.g.
"output exceeded the hard character limit") can contain the same keywords
("limit") that generate_v8_stream's generic RuntimeError branch uses to
detect a context-length error. Before this fix, a guard rejection landed
in that branch and told clinicians "The input is too long for the model's
context window. Please try reducing the amount of input data." -- which
pushed them to delete good chart context to fix a problem that had
nothing to do with input length.
"""
import uuid

from auth_utils import register_approve_login
from server.core.clinical_output_guard import ClinicalOutputRejected


class _RejectingNoteGenerator:
    def __init__(self, draft=""):
        self._draft = draft

    async def collect_completion(self, *_args, **_kwargs):
        raise ClinicalOutputRejected(
            "output exceeded the hard character limit",
            draft=self._draft,
        )


def _auth_headers(client):
    email = f"guardrej-{uuid.uuid4().hex[:8]}@example.com"
    token = register_approve_login(client, email, "Passw0rd!1234")
    return {"Authorization": f"Bearer {token}"}


def _generate(client, headers):
    return client.post(
        "/api/generate_v8_stream",
        json={
            "transcription_text": "hello",
            "old_visits_text": "",
            "mixed_other_text": "",
            "note_type": "consult",
        },
        headers=headers,
    )


def test_guard_rejection_does_not_report_input_too_long(client, monkeypatch):
    import server.routes.notes as notes_routes

    monkeypatch.setattr(notes_routes, "note_gen", _RejectingNoteGenerator())

    resp = _generate(client, _auth_headers(client))

    assert resp.status_code == 200
    text = resp.text
    assert "too long for the model's context window" not in text
    assert "reducing the amount of input data" not in text
    assert "NOTE NOT GENERATED" in text
    assert "safety guard could not verify" in text
    assert "output exceeded the hard character limit" in text


def test_guard_rejection_surfaces_the_unverified_draft(client, monkeypatch):
    """Double-rejection with a captured draft now SALVAGES: the stream ends
    with a __NOTE_FINAL__ marker carrying the draft text and salvaged=true,
    instead of the old '----- UNVERIFIED DRAFT -----' error block. The draft
    is retained so the clinician can review/fix it; the client renders the
    rejection cause in the separate Conflicts panel."""
    import json

    import server.routes.notes as notes_routes
    from server.core.streaming.helpers import NOTE_FINAL_MARKER

    monkeypatch.setattr(
        notes_routes,
        "note_gen",
        _RejectingNoteGenerator(draft="SUBJECTIVE: patient reports feeling unwell."),
    )

    resp = _generate(client, _auth_headers(client))

    assert resp.status_code == 200
    text = resp.text
    assert NOTE_FINAL_MARKER in text
    payload = json.loads(
        text.split(NOTE_FINAL_MARKER, 1)[1].strip().split("\n", 1)[0]
    )
    assert payload["salvaged"] is True
    assert "SUBJECTIVE: patient reports feeling unwell." in payload["text"]
    assert any("hard character limit" in r for r in payload["reasons"])
    # The old error-style block must not be emitted for a salvaged draft.
    assert "UNVERIFIED DRAFT" not in text
    assert "NOTE NOT GENERATED" not in text


def test_guard_rejection_without_draft_omits_unverified_draft_block(client, monkeypatch):
    import server.routes.notes as notes_routes

    monkeypatch.setattr(notes_routes, "note_gen", _RejectingNoteGenerator(draft=""))

    resp = _generate(client, _auth_headers(client))

    assert resp.status_code == 200
    assert "UNVERIFIED DRAFT" not in resp.text
