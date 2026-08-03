"""Regression test: GET /list/{gen_id} must find materials cached by
POST /generate and /generate-all.

Before this fix, the write path cached results under
md5(f"{gen_id}:{note_text}") while the read path looked up the bare
gen_id -- two different keys into the same store. Since gen_id is already
a fresh uuid4 per generation (routes/notes.py), folding note_text into the
key added no real collision protection and only broke the read side (which
never has note_text available, only gen_id). GET /list/{gen_id} always
reported every material as not cached, even immediately after a successful
generate-all call.
"""
import uuid

from auth_utils import register_approve_login

_PW = "Passw0rd!1234"

_FAKE_MATERIAL = {
    "content": "Take your medications as prescribed.",
    "source_attribution": [],
    "disclaimer": "_Educational summary._",
    "safety_flags": [],
    "generated_at": "2026-07-14T00:00:00",
    "generation_time_sec": 0.01,
}


async def _fake_generate_one(self, material_type, note_text, sections=None, patient_data=None):
    return dict(_FAKE_MATERIAL)


async def _fake_generate_all(self, note_text, sections=None, patient_data=None):
    from server.services.patient_materials_service import MATERIAL_TYPES
    return {mat_type: dict(_FAKE_MATERIAL) for mat_type in MATERIAL_TYPES}


def _auth_headers(client):
    email = f"pm-{uuid.uuid4().hex[:8]}@example.com"
    token = register_approve_login(client, email, _PW)
    return {"Authorization": f"Bearer {token}"}


def test_generate_all_then_list_finds_cached_materials(client, monkeypatch):
    monkeypatch.setattr(
        "server.services.patient_materials_service.PatientMaterialsGenerator.generate_all",
        _fake_generate_all,
    )
    headers = _auth_headers(client)
    gen_id = uuid.uuid4().hex

    gen_resp = client.post(
        "/api/patient-materials/generate-all",
        json={"gen_id": gen_id, "note_text": "Patient note text here."},
        headers=headers,
    )
    assert gen_resp.status_code == 200, gen_resp.text

    list_resp = client.get(f"/api/patient-materials/list/{gen_id}", headers=headers)
    assert list_resp.status_code == 200, list_resp.text
    materials = list_resp.json()["materials"]
    assert materials, "expected non-empty materials dict after generate-all"
    for mat_type, info in materials.items():
        assert info["cached"] is True, f"{mat_type} should be cached"
        assert info["has_content"] is True, f"{mat_type} should have content"


def test_generate_single_then_list_finds_cached_material(client, monkeypatch):
    monkeypatch.setattr(
        "server.services.patient_materials_service.PatientMaterialsGenerator.generate_one",
        _fake_generate_one,
    )
    headers = _auth_headers(client)
    gen_id = uuid.uuid4().hex

    gen_resp = client.post(
        "/api/patient-materials/generate",
        json={"gen_id": gen_id, "material_type": "medications", "note_text": "Patient note text here."},
        headers=headers,
    )
    assert gen_resp.status_code == 200, gen_resp.text

    list_resp = client.get(f"/api/patient-materials/list/{gen_id}", headers=headers)
    assert list_resp.status_code == 200, list_resp.text
    materials = list_resp.json()["materials"]
    assert materials["medications"]["cached"] is True
    assert materials["medications"]["has_content"] is True


def test_list_before_any_generation_reports_empty(client):
    headers = _auth_headers(client)
    gen_id = uuid.uuid4().hex

    list_resp = client.get(f"/api/patient-materials/list/{gen_id}", headers=headers)
    assert list_resp.status_code == 200, list_resp.text
    assert list_resp.json()["materials"] == {}
