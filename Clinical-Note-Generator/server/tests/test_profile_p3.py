# server/tests/test_profile_p3.py — P3 profile & note-type templates API
import uuid

from auth_utils import register_approve_login
from server.core.profile_service import baseline_templates_from_config, merge_templates_for_generation
from server.core.prompt.builder import load_config


def test_profile_get_put(client):
    email = f"p3-{uuid.uuid4().hex[:8]}@example.com"
    password = "Passw0rd!1234"
    token = register_approve_login(client, email, password)
    h = {"Authorization": f"Bearer {token}"}

    me = client.get("/api/profile", headers=h)
    assert me.status_code == 200
    body = me.json()
    assert body["email"] == email
    assert body["display_name"] is None

    up = client.put(
        "/api/profile",
        headers=h,
        json={"display_name": " Dr. Test ", "default_specialty": "Pulmonology", "default_location": "NS"},
    )
    assert up.status_code == 200
    assert up.json()["display_name"] == "Dr. Test"
    assert up.json()["default_specialty"] == "Pulmonology"
    assert up.json()["default_location"] == "NS"


def test_note_types_patch_revert(client):
    email = f"p3nt-{uuid.uuid4().hex[:8]}@example.com"
    password = "Passw0rd!1234"
    token = register_approve_login(client, email, password)
    h = {"Authorization": f"Bearer {token}"}

    lst = client.get("/api/note-types", headers=h)
    assert lst.status_code == 200
    items = lst.json()["note_types"]
    assert any(x["id"] == "consult" for x in items)
    consult = next(x for x in items if x["id"] == "consult")
    baseline = consult["baseline_prompt"]
    assert consult["user_prompt"] == baseline

    patch = client.put(
        "/api/note-types",
        headers=h,
        json={"templates": {"consult": "CUSTOM CONSULT TEMPLATE UNIQUE"}},
    )
    assert patch.status_code == 200
    consult2 = next(x for x in patch.json()["note_types"] if x["id"] == "consult")
    assert "CUSTOM CONSULT TEMPLATE UNIQUE" in consult2["user_prompt"]
    assert consult2["baseline_prompt"] == baseline

    rev = client.post("/api/note-types/consult/revert", headers=h)
    assert rev.status_code == 200
    consult3 = next(x for x in rev.json()["note_types"] if x["id"] == "consult")
    assert consult3["user_prompt"] == baseline


def test_merge_templates_untouched_user_matches_config_baseline():
    """Empty preferences_json → merged maps equal config baselines (pre-P3 effective behavior)."""
    cfg = load_config()
    base_std, base_oth = baseline_templates_from_config(cfg)
    merged_std, merged_oth = merge_templates_for_generation(cfg, {"schema_version": 1, "templates": {}, "templates_other": {}})
    assert merged_std == base_std
    assert merged_oth == base_oth


def test_multi_issue_soap_builtin_label_and_template(client):
    cfg = load_config()
    std, _ = baseline_templates_from_config(cfg)
    assert "multi_issue_soap" in std
    # v2 replaced the fixed "several patient issues" phrasing with a
    # problem-oriented SOAP directive; assert on the stable intent rather
    # than exact prose so this doesn't break on every wording tweak.
    assert "problem-oriented soap note" in std["multi_issue_soap"].lower()

    email = f"p3mis-{uuid.uuid4().hex[:8]}@example.com"
    password = "Passw0rd!1234"
    token = register_approve_login(client, email, password)
    h = {"Authorization": f"Bearer {token}"}
    lst = client.get("/api/note-types", headers=h)
    assert lst.status_code == 200
    entry = next(x for x in lst.json()["note_types"] if x["id"] == "multi_issue_soap")
    assert entry["label"] == "Multi-Issue SOAP"
    assert entry["kind"] == "builtin"
    assert entry["scope"] == "standard"


def test_pre_encounter_prep_builtin_label_and_template(client):
    cfg = load_config()
    _, oth = baseline_templates_from_config(cfg)
    assert "pre_encounter_prep" in oth
    # v2 renamed the fixed "pre-encounter summary" phrasing to "pre-encounter
    # chart review"; "why the patient is being seen" is unchanged in v2.
    assert "pre-encounter chart review" in oth["pre_encounter_prep"].lower()
    assert "why the patient is being seen" in oth["pre_encounter_prep"].lower()

    email = f"p3pec-{uuid.uuid4().hex[:8]}@example.com"
    password = "Passw0rd!1234"
    token = register_approve_login(client, email, password)
    h = {"Authorization": f"Bearer {token}"}
    lst = client.get("/api/note-types", headers=h)
    assert lst.status_code == 200
    entry = next(x for x in lst.json()["note_types"] if x["id"] == "pre_encounter_prep")
    assert entry["label"] == "Pre-Encounter Preparation"
    assert entry["kind"] == "builtin"
    assert entry["scope"] == "other"


def test_note_prompts_includes_baseline(client):
    email = f"p3np-{uuid.uuid4().hex[:8]}@example.com"
    password = "Passw0rd!1234"
    token = register_approve_login(client, email, password)
    h = {"Authorization": f"Bearer {token}"}

    np = client.get("/api/note_prompts", headers=h)
    assert np.status_code == 200
    data = np.json()
    assert data["success"] is True
    assert "templates" in data
    assert "templates_baseline" in data
    assert "consult" in data["templates"]
    assert data["templates"]["consult"] == data["templates_baseline"]["consult"]

    client.put("/api/note-types", headers=h, json={"templates": {"consult": "XYZ_NOTE_PROMPTS_TEST"}})
    np2 = client.get("/api/note_prompts", headers=h)
    assert np2.json()["templates"]["consult"] == "XYZ_NOTE_PROMPTS_TEST"
    assert "consult" in np2.json()["templates_baseline"]
    assert np2.json()["templates_baseline"]["consult"] != "XYZ_NOTE_PROMPTS_TEST"


def test_custom_note_type_create_label_only_no_duplicates_and_rename(client):
    email = f"p3ct-{uuid.uuid4().hex[:8]}@example.com"
    password = "Passw0rd!1234"
    token = register_approve_login(client, email, password)
    h = {"Authorization": f"Bearer {token}"}

    # Create label-only (server generates id, forces "other")
    resp = client.post(
        "/api/note-types/custom",
        headers=h,
        json={"label": "My Intake Note", "initial_prompt": "Please be concise."},
    )
    assert resp.status_code == 200
    items = resp.json()["note_types"]
    created = next(x for x in items if x["kind"] == "custom" and x["label"] == "My Intake Note")
    assert created["id"]
    assert created["scope"] == "other"
    # initial_prompt lands in user_prompt for that id (baseline likely empty)
    assert "Please be concise." in created["user_prompt"]

    # No duplicates (case-insensitive)
    dup = client.post("/api/note-types/custom", headers=h, json={"label": " my intake note "})
    assert dup.status_code == 400

    # Rename custom
    nid = created["id"]
    ren = client.patch(f"/api/note-types/{nid}", headers=h, json={"label": "My Intake Note v2"})
    assert ren.status_code == 200
    items2 = ren.json()["note_types"]
    updated = next(x for x in items2 if x["id"] == nid)
    assert updated["label"] == "My Intake Note v2"
    assert updated["kind"] == "custom"
    assert updated["scope"] == "other"

    # Cannot rename built-ins
    bad = client.patch("/api/note-types/consult", headers=h, json={"label": "Nope"})
    assert bad.status_code == 400


def test_note_types_all_returns_hidden_types(client):
    """GET /note-types/all should return ALL note types even when some are hidden."""
    email = f"p3all-{uuid.uuid4().hex[:8]}@example.com"
    password = "Passw0rd!1234"
    token = register_approve_login(client, email, password)
    h = {"Authorization": f"Bearer {token}"}

    # First hide multi_issue_soap via visibility preferences
    all_resp = client.get("/api/note-types/all", headers=h)
    assert all_resp.status_code == 200
    all_ids = [x["id"] for x in all_resp.json()["note_types"]]
    assert "multi_issue_soap" in all_ids

    # Hide multi_issue_soap by setting visible_note_types to everything except it
    visible = [nid for nid in all_ids if nid != "multi_issue_soap"]
    put_resp = client.put(
        "/api/note-type-preferences",
        headers=h,
        json={"visible_note_types": visible},
    )
    assert put_resp.status_code == 200

    # /note-types should NOT include multi_issue_soap (filtered)
    filtered = client.get("/api/note-types", headers=h)
    assert filtered.status_code == 200
    filtered_ids = [x["id"] for x in filtered.json()["note_types"]]
    assert "multi_issue_soap" not in filtered_ids

    # /note-types/all SHOULD still include multi_issue_soap (unfiltered)
    unfiltered = client.get("/api/note-types/all", headers=h)
    assert unfiltered.status_code == 200
    unfiltered_ids = [x["id"] for x in unfiltered.json()["note_types"]]
    assert "multi_issue_soap" in unfiltered_ids
