"""Phase A regression: pre-note (summary-first) patient materials.

Locked rule R1: when a formal note exists, the note is the ONLY source and
zero extra LLM calls fire. When no note exists, one internal Encounter Data
Sheet is built from live inputs (transcript / prior visits / chart data) and
feeds the unchanged downstream pipeline; results are marked preliminary.

Also covers:
- 422 when neither note_text nor live_source is usable
- provisional gen_id minting + strict per-user ownership (403 cross-user)
- sheet caching by source hash (growing transcript invalidates)
"""
import uuid

from auth_utils import register_approve_login

_PW = "Passw0rd!1234"

_FAKE_MATERIAL = {
    "content": "Avoid high-sodium foods.",
    "source_attribution": [],
    "disclaimer": "_Educational summary._",
    "safety_flags": [],
    "generated_at": "2026-08-29T00:00:00",
    "generation_time_sec": 0.01,
}

_FAKE_SHEET = (
    "Visit Context\nAge 52, male.\n\n"
    "Diagnoses\nAsthma\n\n"
    "Plan\nInhaler daily."
)


class _FakeNoteGen:
    """Counts sheet-build LLM calls; never used for material generation."""

    def __init__(self):
        self.sheet_calls = 0

    async def collect_completion(self, prompt, **kwargs):
        # Count ONLY Encounter Data Sheet calls; the diet/exercise extraction
        # prompt is a different call on the same generator.
        if "Encounter Data Sheet" in prompt:
            self.sheet_calls += 1
        return _FAKE_SHEET


async def _fake_generate_one(self, material_type, note_text, sections=None, patient_data=None):
    entry = dict(_FAKE_MATERIAL)
    entry["_seen_note_text"] = note_text  # what the pipeline actually consumed
    return entry


def _auth_headers(client):
    email = f"live-{uuid.uuid4().hex[:8]}@example.com"
    token = register_approve_login(client, email, _PW)
    return {"Authorization": f"Bearer {token}"}


def _patch(monkeypatch, fake_gen):
    monkeypatch.setattr(
        "server.routes.patient_materials.get_simple_note_generator",
        lambda *a, **k: fake_gen,
    )
    monkeypatch.setattr(
        "server.services.patient_materials_service.PatientMaterialsGenerator.generate_one",
        _fake_generate_one,
    )


def test_note_path_fires_zero_sheet_calls(client, monkeypatch):
    """R1: note present -> note is the source, no summarization LLM call."""
    fake = _FakeNoteGen()
    _patch(monkeypatch, fake)
    headers = _auth_headers(client)
    gen_id = uuid.uuid4().hex

    resp = client.post(
        "/api/patient-materials/generate",
        json={
            "gen_id": gen_id,
            "material_type": "diagnosis",
            "note_text": "Diagnosis\nAsthma",
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["preliminary"] is False
    assert body["source_sheet"] is None
    assert fake.sheet_calls == 0


def test_no_note_uses_sheet_and_marks_preliminary(client, monkeypatch):
    """No note -> sheet becomes the pipeline source, flagged preliminary."""
    fake = _FakeNoteGen()
    _patch(monkeypatch, fake)
    headers = _auth_headers(client)
    mint = client.post("/api/patient-materials/provisional-source", headers=headers)
    assert mint.status_code == 200, mint.text
    gen_id = mint.json()["gen_id"]

    resp = client.post(
        "/api/patient-materials/generate",
        json={
            "gen_id": gen_id,
            "material_type": "diagnosis",
            "live_source": {"transcript": "patient says wheezy", "prior_visits": "last visit asthma", "chart_data": ""},
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["preliminary"] is True
    assert "Asthma" in (body["source_sheet"] or "")
    assert fake.sheet_calls == 1


def test_requires_note_or_live_source(client, monkeypatch):
    """Neither note nor usable live_source -> 422 from the validator."""
    fake = _FakeNoteGen()
    _patch(monkeypatch, fake)
    headers = _auth_headers(client)
    gen_id = uuid.uuid4().hex

    resp = client.post(
        "/api/patient-materials/generate",
        json={"gen_id": gen_id, "material_type": "diagnosis", "note_text": "", "live_source": {"transcript": "  ", "prior_visits": "", "chart_data": None}},
        headers=headers,
    )
    assert resp.status_code == 422
    assert fake.sheet_calls == 0


def test_provisional_gen_id_is_owner_bound(client, monkeypatch):
    """Provisional gen_id issued to user A must 403 for user B."""
    fake = _FakeNoteGen()
    _patch(monkeypatch, fake)
    headers_a = _auth_headers(client)
    headers_b = _auth_headers(client)
    gen_id = client.post("/api/patient-materials/provisional-source", headers=headers_a).json()["gen_id"]

    resp_b = client.post(
        "/api/patient-materials/generate",
        json={"gen_id": gen_id, "material_type": "diagnosis", "live_source": {"transcript": "wheezing"}},
        headers=headers_b,
    )
    assert resp_b.status_code == 403

    resp_a = client.post(
        "/api/patient-materials/generate",
        json={"gen_id": gen_id, "material_type": "diagnosis", "live_source": {"transcript": "wheezing"}},
        headers=headers_a,
    )
    assert resp_a.status_code == 200, resp_a.text


def test_sheet_cached_by_source_hash_growing_transcript_reruns(client, monkeypatch):
    """Same source state reuses the sheet (no 2nd LLM call); a longer
    transcript is a new source state and re-runs the sheet call."""
    fake = _FakeNoteGen()
    _patch(monkeypatch, fake)
    headers = _auth_headers(client)
    gen_id = client.post("/api/patient-materials/provisional-source", headers=headers).json()["gen_id"]

    base = {"transcript": "chunk one", "prior_visits": "pv", "chart_data": ""}
    r1 = client.post(
        "/api/patient-materials/generate",
        json={"gen_id": gen_id, "material_type": "diagnosis", "live_source": base},
        headers=headers,
    )
    assert r1.status_code == 200
    assert fake.sheet_calls == 1

    # Different material, identical source state -> sheet cached (still 1 call).
    # (material cache keyed per source hash; sheet cache keyed by hash)
    r2 = client.post(
        "/api/patient-materials/generate",
        json={"gen_id": gen_id, "material_type": "exercise", "live_source": dict(base)},
        headers=headers,
    )
    assert r2.status_code == 200
    assert fake.sheet_calls == 1

    # Live transcript grew -> new source hash -> sheet rebuilt.
    r3 = client.post(
        "/api/patient-materials/generate",
        json={"gen_id": gen_id, "material_type": "diagnosis", "live_source": {"transcript": "chunk one then chunk two", "prior_visits": "pv", "chart_data": ""}},
        headers=headers,
    )
    assert r3.status_code == 200
    assert fake.sheet_calls == 2
