# server/core/profile_service.py
"""P3: user profile fields + per-user note-type templates (server source of truth)."""
from __future__ import annotations

import copy
import re
from typing import Any, Dict, List, Optional, Tuple

from sqlmodel import Session, select

from server.core.prompt.builder import _cfg_text, load_config
from server.core.prompt.policy_v2 import OTHER_NOTE_PROMPTS, STANDARD_NOTE_PROMPTS
from server.models.user import User
from server.models.user_preferences import UserPreferences

PREF_SCHEMA_VERSION = 2

# P3b: slug for user-defined note types (lowercase, a-z0-9_, 2–64 chars)
CUSTOM_NOTE_TYPE_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,62}$")

# P1-6: custom template bodies had no length cap at all -- an oversized
# template gets embedded into every note-generation prompt for that user,
# inflating LLM cost/latency on every request, and is stored unbounded in
# preferences_json. Generous for any legitimate prompt (typically a few
# hundred to a couple thousand characters based on the built-in templates).
MAX_CUSTOM_TEMPLATE_CHARS = 20_000


def default_preferences_blob() -> Dict[str, Any]:
    return {
        "schema_version": PREF_SCHEMA_VERSION,
        "default_note_type": "consult",
        "visible_note_types": None,  # None = show all (backward compatible)
        "templates": {},
        "templates_other": {},
        "custom_note_types": [],
    }


# Must match server.routes.notes.OTHER_NOTE_TYPES for routing to build_prompt_other.
DEFAULT_OTHER_NOTE_TYPES = frozenset(
    {"referral", "summarize", "custom", "procedure", "pre_encounter_prep"}
)


def _coerce_preferences_shape(pj: Any) -> Dict[str, Any]:
    if not isinstance(pj, dict):
        return default_preferences_blob()
    out = copy.deepcopy(pj)
    out.setdefault("schema_version", PREF_SCHEMA_VERSION)
    out.setdefault("default_note_type", "consult")
    # visible_note_types: None = show all, empty list = show none (edge case handled elsewhere)
    if "visible_note_types" not in out:
        out["visible_note_types"] = None
    if not isinstance(out.get("templates"), dict):
        out["templates"] = {}
    if not isinstance(out.get("templates_other"), dict):
        out["templates_other"] = {}
    cn = out.get("custom_note_types")
    if not isinstance(cn, list):
        out["custom_note_types"] = []
    return out


def all_builtin_ids(cfg: Optional[Dict[str, Any]] = None) -> set:
    std, oth = baseline_templates_from_config(cfg)
    return set(std.keys()) | set(oth.keys())


def validate_custom_note_type_id(nid: str, cfg: Optional[Dict[str, Any]] = None) -> str:
    s = (nid or "").strip().lower()
    if not s:
        raise ValueError("Note type id is required")
    if not CUSTOM_NOTE_TYPE_ID_RE.match(s):
        raise ValueError(
            "Invalid id: use 2–64 characters, lowercase letters, digits, underscores; start with a letter."
        )
    if s in all_builtin_ids(cfg):
        raise ValueError("That id is reserved for a built-in note type.")
    return s


def _label_key(label: str) -> str:
    return " ".join(str(label or "").strip().split()).lower()


def _slugify_note_type_id_from_label(label: str) -> str:
    """
    Generate a candidate id from a user label.
    - lowercase
    - non [a-z0-9] -> underscore
    - trim underscores
    """
    s = str(label or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = re.sub(r"^_+|_+$", "", s)
    if not s:
        s = "custom_note"
    # Ensure starts with a letter.
    if not s[:1].isalpha():
        s = "note_" + s
    # Cap so suffixes can be added while staying <= 64 and matching regex.
    if len(s) > 56:
        s = s[:56].rstrip("_")
    return s


def _generate_unique_custom_note_type_id(
    *,
    label: str,
    cfg: Dict[str, Any],
    preferences_json: Dict[str, Any],
) -> str:
    """
    Create a validated id from the label and ensure no collisions with
    built-ins or existing custom note types.
    """
    pj = _coerce_preferences_shape(preferences_json)
    custom = pj.get("custom_note_types") or []
    used = set()
    for e in custom:
        if isinstance(e, dict):
            used.add((e.get("id") or "").strip().lower())
    used |= all_builtin_ids(cfg)

    base = _slugify_note_type_id_from_label(label)
    # validate_custom_note_type_id enforces 2-64 and reserved ids.
    for i in range(1, 1000):
        cand = base if i == 1 else f"{base}_{i}"
        if cand in used:
            continue
        try:
            return validate_custom_note_type_id(cand, cfg)
        except ValueError:
            # If cand invalid due to length, shorten base and retry.
            base2 = base
            if len(base2) > 40:
                base2 = base2[:40].rstrip("_")
            base = base2 or "custom_note"
            continue
    raise ValueError("Could not generate a unique id for this note type.")


def _assert_no_duplicate_custom_label(pj: Dict[str, Any], label: str, *, exclude_id: Optional[str] = None) -> None:
    want = _label_key(label)
    if not want:
        raise ValueError("Label is required.")
    ex = (exclude_id or "").strip().lower()
    for entry in (pj.get("custom_note_types") or []):
        if not isinstance(entry, dict):
            continue
        nid = (entry.get("id") or "").strip().lower()
        if ex and nid == ex:
            continue
        if _label_key(entry.get("label") or "") == want:
            raise ValueError("A custom note type with this name already exists.")


def note_type_uses_other_builder(note_type: str, preferences_json: Optional[Dict[str, Any]]) -> bool:
    """True → use build_prompt_other / templates_other; False → build_prompt_v8 / templates."""
    nt = (note_type or "").strip().lower()
    if nt in DEFAULT_OTHER_NOTE_TYPES:
        return True
    pj = preferences_json if isinstance(preferences_json, dict) else {}
    for entry in pj.get("custom_note_types") or []:
        if isinstance(entry, dict) and (entry.get("id") or "").strip().lower() == nt:
            # Custom note types always use the "other" builder.
            return True
    return False


def _normalize_templates_map(raw: Any) -> Dict[str, str]:
    out: Dict[str, str] = {}
    if not isinstance(raw, dict):
        return out
    for k, v in raw.items():
        if v is None or not str(k).strip():
            continue
        key = str(k).strip()
        out[key] = _cfg_text(v)
    return out


def baseline_templates_from_config(cfg: Optional[Dict[str, Any]] = None) -> Tuple[Dict[str, str], Dict[str, str]]:
    cfg = cfg if isinstance(cfg, dict) else load_config()
    std: Dict[str, str] = dict(STANDARD_NOTE_PROMPTS)
    oth: Dict[str, str] = dict(OTHER_NOTE_PROMPTS)
    for k, v in (cfg.get("default_note_user_prompts") or {}).items():
        std[str(k)] = _cfg_text(v)
    for k, v in (cfg.get("default_note_user_prompts_other") or {}).items():
        oth[str(k)] = _cfg_text(v)
    return std, oth


def merge_templates_for_generation(
    cfg: Dict[str, Any],
    preferences_json: Optional[Dict[str, Any]],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Return (standard, other) template maps in the same shape the prompt builder expects (values are strings)."""
    base_std, base_oth = baseline_templates_from_config(cfg)
    pj = preferences_json if isinstance(preferences_json, dict) else {}
    user_std = _normalize_templates_map(pj.get("templates"))
    user_oth = _normalize_templates_map(pj.get("templates_other"))
    out_std: Dict[str, Any] = {**base_std}
    out_oth: Dict[str, Any] = {**base_oth}
    for k, v in user_std.items():
        if v:
            out_std[k] = v
    for k, v in user_oth.items():
        if v:
            out_oth[k] = v
    return out_std, out_oth


def get_preferences_row(session: Session, user_id) -> Optional[UserPreferences]:
    return session.exec(select(UserPreferences).where(UserPreferences.user_id == user_id)).one_or_none()


def ensure_user_preferences(session: Session, user: User) -> UserPreferences:
    row = get_preferences_row(session, user.id)
    if row:
        return row
    row = UserPreferences(
        user_id=user.id,
        preferences_json=default_preferences_blob(),
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def _touch_prefs(row: UserPreferences) -> None:
    from datetime import datetime

    row.updated_at = datetime.utcnow()


def apply_profile_field_patch(session: Session, user: User, patch: Dict[str, Any]) -> User:
    """Apply only keys present in patch (use model_dump(exclude_unset) from the route)."""
    from datetime import datetime

    for key in ("display_name", "default_specialty", "default_location"):
        if key not in patch:
            continue
        v = patch.get(key)
        val = None if v is None else (str(v).strip() or None)
        setattr(user, key, val)
    user.profile_updated_at = datetime.utcnow()
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def patch_note_templates(
    session: Session,
    user: User,
    *,
    templates: Optional[Dict[str, str]] = None,
    templates_other: Optional[Dict[str, str]] = None,
) -> UserPreferences:
    row = ensure_user_preferences(session, user)
    pj = _coerce_preferences_shape(row.preferences_json)
    pj = copy.deepcopy(pj)
    pj.setdefault("schema_version", PREF_SCHEMA_VERSION)
    t = pj.setdefault("templates", {})
    to = pj.setdefault("templates_other", {})
    if isinstance(templates, dict):
        for k, v in templates.items():
            key = str(k).strip()
            if not key:
                continue
            val = (v or "").strip()
            if val:
                if len(val) > MAX_CUSTOM_TEMPLATE_CHARS:
                    raise ValueError(
                        f"Template for '{key}' exceeds {MAX_CUSTOM_TEMPLATE_CHARS} characters."
                    )
                t[key] = val
            else:
                t.pop(key, None)
    if isinstance(templates_other, dict):
        for k, v in templates_other.items():
            key = str(k).strip()
            if not key:
                continue
            val = (v or "").strip()
            if val:
                if len(val) > MAX_CUSTOM_TEMPLATE_CHARS:
                    raise ValueError(
                        f"Template for '{key}' exceeds {MAX_CUSTOM_TEMPLATE_CHARS} characters."
                    )
                to[key] = val
            else:
                to.pop(key, None)
    row.preferences_json = pj
    _touch_prefs(row)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def revert_note_template(session: Session, user: User, note_type_id: str, *, other: bool = False) -> UserPreferences:
    row = ensure_user_preferences(session, user)
    pj = _coerce_preferences_shape(row.preferences_json)
    pj = copy.deepcopy(pj)
    key_bucket = "templates_other" if other else "templates"
    bucket = pj.setdefault(key_bucket, {})
    bucket.pop(note_type_id.strip(), None)
    row.preferences_json = pj
    _touch_prefs(row)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


_BUILTIN_NOTE_TYPE_LABELS: Dict[str, str] = {
    "multi_issue_soap": "Multi-Issue SOAP",
    "pre_encounter_prep": "Pre-Encounter Preparation",
}


def note_type_label_for_id(nid: str) -> str:
    key = (nid or "").strip().lower()
    if key in _BUILTIN_NOTE_TYPE_LABELS:
        return _BUILTIN_NOTE_TYPE_LABELS[key]
    s = key.replace("_", " ")
    if not s:
        return ""
    return s[:1].upper() + s[1:]


def list_note_types_for_api(session: Session, user: User) -> List[Dict[str, Any]]:
    cfg = load_config()
    std_base, oth_base = baseline_templates_from_config(cfg)
    row = get_preferences_row(session, user.id)
    pj = _coerce_preferences_shape(row.preferences_json if row and row.preferences_json else {})
    user_std = _normalize_templates_map(pj.get("templates"))
    user_oth = _normalize_templates_map(pj.get("templates_other"))
    custom_raw = pj.get("custom_note_types") or []

    # Get visible_note_types filter (None = show all)
    visible = pj.get("visible_note_types")
    if visible is not None and not isinstance(visible, list):
        visible = None  # Treat invalid as "show all"
    if visible is not None:
        visible = set(str(v).strip().lower() for v in visible if str(v).strip())

    out: List[Dict[str, Any]] = []
    for nid in sorted(std_base.keys()):
        # Filter by visible_note_types
        if visible is not None and nid not in visible:
            continue
        bl = std_base.get(nid) or ""
        uq = user_std.get(nid) or bl
        out.append(
            {
                "id": nid,
                "label": note_type_label_for_id(nid),
                "kind": "builtin",
                "scope": "standard",
                "baseline_prompt": bl,
                "user_prompt": uq,
            }
        )
    for nid in sorted(oth_base.keys()):
        # Filter by visible_note_types
        if visible is not None and nid not in visible:
            continue
        bl = oth_base.get(nid) or ""
        uq = user_oth.get(nid) or bl
        out.append(
            {
                "id": nid,
                "label": note_type_label_for_id(nid),
                "kind": "builtin",
                "scope": "other",
                "baseline_prompt": bl,
                "user_prompt": uq,
            }
        )

    seen_custom: set = set()
    for entry in custom_raw:
        if not isinstance(entry, dict):
            continue
        nid = str(entry.get("id") or "").strip().lower()
        if not nid or nid in seen_custom:
            continue
        # Filter by visible_note_types
        if visible is not None and nid not in visible:
            continue
        seen_custom.add(nid)
        # Custom note types always use the "other" pipeline.
        label = (entry.get("label") or "").strip() or note_type_label_for_id(nid)
        bl = oth_base.get(nid, "")
        uq = user_oth.get(nid) or bl
        out.append(
            {
                "id": nid,
                "label": label[:128],
                "kind": "custom",
                "scope": "other",
                "baseline_prompt": bl,
                "user_prompt": uq,
            }
        )

    return out


def list_all_note_types_for_api(session: Session, user: User) -> List[Dict[str, Any]]:
    """Return ALL note types (built-in + custom) ignoring visible_note_types filter.

    Used by the frontend to build the visibility checkbox list so hidden types
    remain re-checkable.
    """
    cfg = load_config()
    std_base, oth_base = baseline_templates_from_config(cfg)
    row = get_preferences_row(session, user.id)
    pj = _coerce_preferences_shape(row.preferences_json if row and row.preferences_json else {})
    user_std = _normalize_templates_map(pj.get("templates"))
    user_oth = _normalize_templates_map(pj.get("templates_other"))
    custom_raw = pj.get("custom_note_types") or []

    out: List[Dict[str, Any]] = []
    for nid in sorted(std_base.keys()):
        bl = std_base.get(nid) or ""
        uq = user_std.get(nid) or bl
        out.append(
            {
                "id": nid,
                "label": note_type_label_for_id(nid),
                "kind": "builtin",
                "scope": "standard",
                "baseline_prompt": bl,
                "user_prompt": uq,
            }
        )
    for nid in sorted(oth_base.keys()):
        bl = oth_base.get(nid) or ""
        uq = user_oth.get(nid) or bl
        out.append(
            {
                "id": nid,
                "label": note_type_label_for_id(nid),
                "kind": "builtin",
                "scope": "other",
                "baseline_prompt": bl,
                "user_prompt": uq,
            }
        )

    seen_custom: set = set()
    for entry in custom_raw:
        if not isinstance(entry, dict):
            continue
        nid = str(entry.get("id") or "").strip().lower()
        if not nid or nid in seen_custom:
            continue
        seen_custom.add(nid)
        label = (entry.get("label") or "").strip() or note_type_label_for_id(nid)
        bl = oth_base.get(nid, "")
        uq = user_oth.get(nid) or bl
        out.append(
            {
                "id": nid,
                "label": label[:128],
                "kind": "custom",
                "scope": "other",
                "baseline_prompt": bl,
                "user_prompt": uq,
            }
        )

    return out


def update_note_type_preferences(
    session: Session,
    user: User,
    *,
    default_note_type: Optional[str] = None,
    visible_note_types: Optional[List[str]] = None,
) -> UserPreferences:
    """Update user's note type preferences (default + visibility)."""
    row = ensure_user_preferences(session, user)
    pj = _coerce_preferences_shape(row.preferences_json)
    pj = copy.deepcopy(pj)

    # Update default_note_type
    if default_note_type is not None:
        dt = str(default_note_type).strip().lower()
        if dt:
            pj["default_note_type"] = dt

    # Update visible_note_types
    if visible_note_types is not None:
        if not isinstance(visible_note_types, list):
            raise ValueError("visible_note_types must be a list")
        # Validate: at least one note type must be visible
        if len(visible_note_types) == 0:
            raise ValueError("At least one note type must be visible")
        # Normalize to lowercase stripped strings
        pj["visible_note_types"] = [str(v).strip().lower() for v in visible_note_types if str(v).strip()]
        # Safety: ensure at least one remains after normalization
        if not pj["visible_note_types"]:
            raise ValueError("At least one note type must be visible")

    row.preferences_json = pj
    _touch_prefs(row)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def create_custom_note_type(
    session: Session,
    user: User,
    *,
    label: str,
    type_id: str = "",
    initial_prompt: Optional[str] = None,
) -> UserPreferences:
    cfg = load_config()
    label_t = (label or "").strip()
    if not label_t:
        raise ValueError("Label is required.")
    if len(label_t) > 128:
        raise ValueError("Label must be at most 128 characters.")
    row = ensure_user_preferences(session, user)
    pj = _coerce_preferences_shape(row.preferences_json)
    _assert_no_duplicate_custom_label(pj, label_t)
    # Always create custom note types under the "other" pipeline.
    sc = "other"
    nid = (
        validate_custom_note_type_id(type_id, cfg)
        if (type_id or "").strip()
        else _generate_unique_custom_note_type_id(label=label_t, cfg=cfg, preferences_json=pj)
    )
    custom = pj.setdefault("custom_note_types", [])
    if any(isinstance(e, dict) and (e.get("id") or "").strip().lower() == nid for e in custom):
        raise ValueError("A note type with this id already exists.")

    custom.append({"id": nid, "label": label_t, "scope": sc})
    t = pj.setdefault("templates", {})
    to = pj.setdefault("templates_other", {})
    ip = _cfg_text(initial_prompt or "")
    if ip:
        if len(ip) > MAX_CUSTOM_TEMPLATE_CHARS:
            raise ValueError(f"Template exceeds {MAX_CUSTOM_TEMPLATE_CHARS} characters.")
        to[nid] = ip
    # Safety: never keep a shadow template in the standard bucket for custom note types.
    t.pop(nid, None)

    row.preferences_json = pj
    _touch_prefs(row)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def rename_custom_note_type(session: Session, user: User, note_type_id: str, *, new_label: str) -> UserPreferences:
    cfg = load_config()
    nid = validate_custom_note_type_id(note_type_id, cfg)
    label_t = (new_label or "").strip()
    if not label_t:
        raise ValueError("Label is required.")
    if len(label_t) > 128:
        raise ValueError("Label must be at most 128 characters.")

    row = ensure_user_preferences(session, user)
    pj = _coerce_preferences_shape(row.preferences_json)
    pj = copy.deepcopy(pj)
    _assert_no_duplicate_custom_label(pj, label_t, exclude_id=nid)
    custom = pj.get("custom_note_types") or []
    found = False
    for entry in custom:
        if isinstance(entry, dict) and (entry.get("id") or "").strip().lower() == nid:
            entry["label"] = label_t
            # keep scope forced to other
            entry["scope"] = "other"
            found = True
            break
    if not found:
        raise ValueError("Not a custom note type or id not found.")

    row.preferences_json = pj
    _touch_prefs(row)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def delete_custom_note_type(session: Session, user: User, note_type_id: str) -> UserPreferences:
    nid = (note_type_id or "").strip().lower()
    if not nid:
        raise ValueError("Invalid note type id")

    row = ensure_user_preferences(session, user)
    pj = _coerce_preferences_shape(row.preferences_json)
    custom_in = pj.get("custom_note_types") or []
    custom_out = [e for e in custom_in if not (isinstance(e, dict) and (e.get("id") or "").strip().lower() == nid)]
    if len(custom_out) == len(custom_in):
        raise ValueError("Not a custom note type or id not found.")

    pj["custom_note_types"] = custom_out
    pj.setdefault("templates", {}).pop(nid, None)
    pj.setdefault("templates_other", {}).pop(nid, None)
    row.preferences_json = pj
    _touch_prefs(row)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def revert_all_builtin_templates(session: Session, user: User) -> UserPreferences:
    """Clear overrides for all built-in ids in both buckets; keep custom_note_types and their templates."""
    cfg = load_config()
    std_base, oth_base = baseline_templates_from_config(cfg)
    row = ensure_user_preferences(session, user)
    pj = _coerce_preferences_shape(row.preferences_json)
    t = pj.setdefault("templates", {})
    to = pj.setdefault("templates_other", {})
    for k in std_base.keys():
        t.pop(k, None)
    for k in oth_base.keys():
        to.pop(k, None)
    row.preferences_json = pj
    _touch_prefs(row)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def revert_note_templates_bulk(
    session: Session,
    user: User,
    *,
    standard: Optional[List[str]] = None,
    other: Optional[List[str]] = None,
) -> UserPreferences:
    """Clear overrides for listed built-in ids only (must exist in config baseline for that bucket)."""
    std_ids = [str(x).strip() for x in (standard or []) if str(x).strip()]
    oth_ids = [str(x).strip() for x in (other or []) if str(x).strip()]
    cfg = load_config()
    std_base, oth_base = baseline_templates_from_config(cfg)
    row = ensure_user_preferences(session, user)
    pj = _coerce_preferences_shape(row.preferences_json)
    t = pj.setdefault("templates", {})
    to = pj.setdefault("templates_other", {})
    builtin_std = set(std_base.keys())
    builtin_oth = set(oth_base.keys())
    for k in std_ids:
        if k in builtin_std:
            t.pop(k, None)
    for k in oth_ids:
        if k in builtin_oth:
            to.pop(k, None)
    row.preferences_json = pj
    _touch_prefs(row)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row
