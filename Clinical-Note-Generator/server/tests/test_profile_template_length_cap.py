"""Regression test (P1-6): custom note-type template bodies must be capped
in length. Before this fix, patch_note_templates() and
create_custom_note_type() stored whatever text was submitted with no
length check -- an oversized template gets embedded into every
note-generation prompt for that user on every request.
"""
import uuid

from auth_utils import register_approve_login
from server.core.profile_service import MAX_CUSTOM_TEMPLATE_CHARS


def test_patch_note_templates_rejects_oversized_template(client):
    email = f"tpl-cap-{uuid.uuid4().hex[:8]}@example.com"
    token = register_approve_login(client, email, "Passw0rd!1234")
    h = {"Authorization": f"Bearer {token}"}

    oversized = "x" * (MAX_CUSTOM_TEMPLATE_CHARS + 1)
    resp = client.put(
        "/api/note-types",
        headers=h,
        json={"templates": {"consult": oversized}},
    )
    assert resp.status_code == 400
    assert "exceeds" in resp.text.lower()


def test_patch_note_templates_accepts_template_within_cap(client):
    email = f"tpl-ok-{uuid.uuid4().hex[:8]}@example.com"
    token = register_approve_login(client, email, "Passw0rd!1234")
    h = {"Authorization": f"Bearer {token}"}

    within_cap = "y" * (MAX_CUSTOM_TEMPLATE_CHARS - 1)
    resp = client.put(
        "/api/note-types",
        headers=h,
        json={"templates": {"consult": within_cap}},
    )
    assert resp.status_code == 200


def test_create_custom_note_type_rejects_oversized_initial_prompt(client):
    email = f"tpl-create-cap-{uuid.uuid4().hex[:8]}@example.com"
    token = register_approve_login(client, email, "Passw0rd!1234")
    h = {"Authorization": f"Bearer {token}"}

    oversized = "z" * (MAX_CUSTOM_TEMPLATE_CHARS + 1)
    resp = client.post(
        "/api/note-types/custom",
        headers=h,
        json={"label": "Oversized Type", "initial_prompt": oversized},
    )
    assert resp.status_code == 400
    assert "exceeds" in resp.text.lower()
