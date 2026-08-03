"""P3-4 regression: merge_incoming_workspace_state()'s anti-stomp guard
restores existing transcription/currentEncounter when the incoming payload
has them empty AND extras.transcriptionCleared isn't set -- protecting
against a stale/partial payload silently wiping real data. But no client
code ever actually sent that flag (confirmed via repo-wide grep), so this
guard could never be correctly bypassed: a doctor deliberately clearing
their transcription by hand had that edit silently undone on the very
next save. auth_workspace.js's collectWorkspaceState() now sends the flag
based on dirty-field tracking; these tests confirm the server side
actually honors it correctly.
"""
from __future__ import annotations

from server.core.encounter_workspace import merge_incoming_workspace_state


def test_empty_incoming_without_cleared_flag_restores_existing():
    existing = {"extras": {"transcription": "Patient reports chest pain.", "currentEncounter": "raw asr text"}}
    incoming = {"extras": {"transcription": "", "currentEncounter": ""}}

    merged = merge_incoming_workspace_state(existing, incoming)

    assert merged["extras"]["transcription"] == "Patient reports chest pain."
    assert merged["extras"]["currentEncounter"] == "raw asr text"


def test_empty_incoming_with_cleared_flag_is_honored():
    existing = {"extras": {"transcription": "Patient reports chest pain.", "currentEncounter": "raw asr text"}}
    incoming = {"extras": {"transcription": "", "currentEncounter": "", "transcriptionCleared": True}}

    merged = merge_incoming_workspace_state(existing, incoming)

    assert merged["extras"]["transcription"] == ""
    assert merged["extras"]["currentEncounter"] == ""


def test_non_empty_incoming_is_never_overridden_regardless_of_flag():
    existing = {"extras": {"transcription": "old text", "currentEncounter": "old raw"}}
    incoming = {"extras": {"transcription": "new dictation", "currentEncounter": "new raw"}}

    merged = merge_incoming_workspace_state(existing, incoming)

    assert merged["extras"]["transcription"] == "new dictation"
    assert merged["extras"]["currentEncounter"] == "new raw"
