"""STEP 2g -- reclassified silent handlers in routes/admin.py (13 total).

13 bare-pass sites triaged; 3 became logged (operator-facing silent failures),
10 documented as safe best-effort / parse guards. This file pins the 3 logging
changes plus a representative parse guard:
  - _reload_llm_clients_from_disk(): note_gen and qa_text reload failures now
    each log a warning (operator config-save action that didn't take effect).
  - _load_cfg(): corrupt *present* admin config now logs a warning while still
    falling back to documented defaults.
  - _parse_port_from_url(): malformed URL -> default port, never raises.
"""
import logging
import sys
import uuid

import server.routes.admin as admin


def test_admin_services_status_route_e2e(client):
    """User-style E2E of the affected admin route through the real app HTTP
    layer: /api/admin/services/status exercises _load_cfg + _parse_port_from_url
    + the status builders (incl. the documented cleanup sites) end-to-end."""
    from server.app import app
    from server.core.dependencies import get_current_admin
    from server.models.user import User

    app.dependency_overrides[get_current_admin] = lambda: User(
        id=uuid.uuid4(),
        email="adm-silent@example.com",
        hashed_password="x",
        is_active=True,
        is_approved=True,
        is_admin=True,
    )
    try:
        resp = client.get("/api/admin/services/status")
    finally:
        app.dependency_overrides.pop(get_current_admin, None)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "services" in body


def test_admin_reload_llm_clients_logs_failures(monkeypatch, caplog):
    """Both config-reload attempts log a warning when they fail (no silent)."""

    class _FailingReload:
        @staticmethod
        def reload_config():
            raise RuntimeError("reload exploded")

    class _FakeNotes:
        note_gen = _FailingReload()

    class _FakeQaText:
        @staticmethod
        def get_simple_note_generator(_name):
            return _FailingReload()

    monkeypatch.setitem(sys.modules, "routes.notes", _FakeNotes)
    monkeypatch.setitem(sys.modules, "services.note_generator_clean", _FakeQaText)
    caplog.set_level(logging.WARNING, logger=admin.logger.name)

    # Must not raise even though both reloads fail.
    admin._reload_llm_clients_from_disk()

    msgs = [r.getMessage() for r in caplog.records]
    assert any("note_gen.reload_config() failed" in m for m in msgs)
    assert any("qa_text.reload_config() failed" in m for m in msgs)


def test_admin_load_cfg_corrupt_logs_warning_and_uses_defaults(monkeypatch, caplog):
    class _CorruptConfig:
        def exists(self):
            return True

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        def read(self, *_a, **_k):
            return "this is {{{ not valid json"

    monkeypatch.setattr(admin, "CONFIG_PATH", _CorruptConfig())
    caplog.set_level(logging.WARNING, logger=admin.logger.name)

    cfg = admin._load_cfg()
    assert isinstance(cfg, dict)
    assert any("Failed to load admin config/config.json" in r.getMessage()
               for r in caplog.records)


def test_admin_parse_port_from_url_malformed_returns_default():
    """Malformed URL must yield the default port, never raise (parse guard)."""
    assert admin._parse_port_from_url("not a url :: x", 8004) == 8004
    assert admin._parse_port_from_url(None, 8095) == 8095
    assert admin._parse_port_from_url("http://127.0.0.1:8011/health", 8004) == 8011
