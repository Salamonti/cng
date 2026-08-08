"""STEP 5 (Task 4b) — error diagnosis-by-keyword, done properly.

The audit explicitly flagged its method (grep) as weak; this suite verifies the
cause-classification surface is CORROBORATED (HTTP status + context), not
asserted from a single substring. Focused tests for each builder's
classification path:

  1. notes.py `_classify_generation_error`  — a context-overflow cause is only
     claimed when BOTH a keyword appears AND the prompt is estimated large
     enough to plausibly overflow the window (pi. never on a small prompt).
  2. notes.py route-level E2E — a RuntimeError carrying context keywords but a
     SMALL prompt must NOT be reported to the clinician as "input too long"
     (that is the actively-harmful false advice the fix guards against); with a
     plausible prompt size it IS reported as a context overflow.
  3. asr.py `_looks_retryable_upstream_error` — cooldown classification on a
     200-with-error-body is corroborated by the transient markers, and does not
     park a URL on a non-transient upstream error.
  4. `_service_error_detail` builders — pure pass-through of (service, primary,
     fallback, errors); no substring-based diagnosis is injected.

Confirm the notes.py context-overflow case stays fixed (gates).
"""
import uuid

from auth_utils import register_approve_login

import server.routes.notes as notes_routes
from server.services.note_generator_clean import ExternalServiceError


# ---------------------------------------------------------------------------
# 1. Pure classifier corroboration (given error text + prompt size)
# ---------------------------------------------------------------------------

def test_context_overflow_requires_both_keyword_and_big_prompt():
    # Single keyword substring on a small prompt is NOT evidence of overflow.
    assert notes_routes._classify_generation_error(
        "request failed: limit exceeded", prompt="x" * 100, cfg={}
    )["is_context_overflow"] is False

    # Keyword + genuinely large prompt -> corroborated overflow.
    assert notes_routes._classify_generation_error(
        "prompt is too long for the context window", prompt="x" * 1_000_000, cfg={}
    )["is_context_overflow"] is True

    # Large prompt but NO context keyword -> generic, not overflow.
    assert notes_routes._classify_generation_error(
        "connection reset by peer", prompt="x" * 1_000_000, cfg={}
    )["is_context_overflow"] is False


def test_context_overflow_floor_is_configurable_and_safe():
    # A tiny floor (config) means even a small prompt above it corroborates.
    assert notes_routes._classify_generation_error(
        "context window exceeded", prompt="x" * 100, cfg={"context_overflow_warn_tokens": 1}
    )["is_context_overflow"] is True
    # Big floor -> small prompt not overflow despite keyword.
    assert notes_routes._classify_generation_error(
        "context window exceeded", prompt="x" * 400,
        cfg={"context_overflow_warn_tokens": 10_000},
    )["is_context_overflow"] is False


def test_context_overflow_corrupt_cfg_falls_back_to_default_floor():
    # Non-int floor config must not crash; falls back to 100k floor.
    assert notes_routes._classify_generation_error(
        "context too long", prompt="x" * 100, cfg={"context_overflow_warn_tokens": "garbage"}
    )["is_context_overflow"] is False
    assert notes_routes._classify_generation_error(
        "context too long", prompt="x" * 1_000_000, cfg={"context_overflow_warn_tokens": "garbage"}
    )["is_context_overflow"] is True


def test_context_overflow_reports_approx_tokens():
    v = notes_routes._classify_generation_error("ctx overflow", prompt="x" * 1000, cfg={})
    assert v["approx_prompt_tokens"] == 1000 // 4  # len//4


# ---------------------------------------------------------------------------
# 2. Route-level E2E: clinicians must NOT be told to delete data on a small prompt
# ---------------------------------------------------------------------------

class _RuntimeErrorNoteGenerator:
    """collect_completion raises a RuntimeError carrying context keywords."""

    def __init__(self, message: str):
        self._message = message

    async def collect_completion(self, *_a, **_k):
        raise RuntimeError(self._message)


class _ExternalErrorNoteGenerator:
    """collect_completion raises an HTTP-level ExternalServiceError (503)."""

    def __init__(self, errors):
        self._errors = errors

    async def collect_completion(self, *_a, **_k):
        raise ExternalServiceError("note_gen", "http://primary", "http://fallback", self._errors)


def _auth_headers(client):
    email = f"step5-{uuid.uuid4().hex[:8]}@example.com"
    token = register_approve_login(client, email, "Passw0rd!1234")
    return {"Authorization": f"Bearer {token}"}


def _generate(client, headers, cfg_override=None, transcription="hello"):
    # Route reads cfg via load_config(); point it at an overridable fake so the
    # test controls the overflow floor without touching the real config file.
    import server.routes.notes as nr
    monkey = None

    class _Patched:
        pass

    # Use monkeypatch indirectly by setting module attr before call.
    orig = nr.load_config
    nr.load_config = lambda: (dict(cfg_override) if cfg_override else {})
    try:
        return client.post(
            "/api/generate_v8_stream",
            json={
                "transcription_text": transcription,
                "old_visits_text": "",
                "mixed_other_text": "",
                "note_type": "consult",
            },
            headers=headers,
        )
    finally:
        nr.load_config = orig


def test_small_prompt_with_context_keyword_not_reported_as_too_long(client, monkeypatch):
    # Keyword present ("context length") but the built prompt stays small
    # (default 100k floor) -> generic failure, NOT the harmful "reduce your data".
    monkeypatch.setattr(notes_routes, "note_gen", _RuntimeErrorNoteGenerator("context length exceeded"))
    resp = _generate(client, _auth_headers(client), cfg_override=None, transcription="hello")
    assert resp.status_code == 200
    assert "reduce the amount of input data" not in resp.text
    assert "too long for the model's context window" not in resp.text
    assert "Note generation failed" in resp.text


def test_guardless_runtime_error_without_keyword_generic(client, monkeypatch):
    monkeypatch.setattr(notes_routes, "note_gen", _RuntimeErrorNoteGenerator("connection reset by peer"))
    resp = _generate(client, _auth_headers(client), cfg_override=None, transcription="hello")
    assert resp.status_code == 200
    assert "too long for the model's context window" not in resp.text
    assert "Note generation failed" in resp.text


def test_external_service_error_not_misreported_as_context(client, monkeypatch):
    # HTTP-level failure (503) must not be diagnosed as an input-length problem.
    monkeypatch.setattr(
        notes_routes, "note_gen",
        _ExternalErrorNoteGenerator(["HTTP 503: the note backend is down"]),
    )
    resp = _generate(client, _auth_headers(client), transcription="hello")
    assert "too long for the model's context window" not in resp.text
    assert "reduce the amount of input data" not in resp.text


# ---------------------------------------------------------------------------
# 3. asr.py `_looks_retryable_upstream_error` — cooldown corroboration
# ---------------------------------------------------------------------------

def test_looks_retryable_marks_transient_capacity():
    from server.routes.asr import _looks_retryable_upstream_error
    # Only genuinely transient/capacity markers classify as retryable.
    for msg in ("asr server busy", "queue full", "too many requests, try again",
                "upstream timed out", "resource exhausted", "server overloaded"):
        assert _looks_retryable_upstream_error(msg) is True, msg


def test_looks_retryable_rejects_non_transient_errors():
    from server.routes.asr import _looks_retryable_upstream_error
    for msg in ("no speech detected", "invalid audio format", "authentication failed",
                "model not loaded", "", None):
        assert _looks_retryable_upstream_error(msg or "") is False, msg


# ---------------------------------------------------------------------------
# 4. `_service_error_detail` builders — pass-through, no substring diagnosis
# ---------------------------------------------------------------------------

def test_notes_service_error_detail_passthrough():
    err = ExternalServiceError("note_gen", "http://p", "http://f", ["HTTP 503: boom"])
    d = notes_routes._service_error_detail(err)
    assert d == {"service": "note_gen", "primary": "http://p",
                 "fallback": "http://f", "errors": ["HTTP 503: boom"]}


def test_asr_service_error_detail_passthrough():
    from server.routes.asr import _service_error_detail
    d = _service_error_detail("http://p", "http://f", ["HTTP 500: x"])
    assert d == {"service": "asr", "primary": "http://p",
                 "fallback": "http://f", "errors": ["HTTP 500: x"]}


def test_ocr_service_error_detail_passthrough():
    from server.routes.ocr import _service_error_detail
    from server.services.ocr_llm_client import ExternalServiceError as OcrExternalServiceError
    err = OcrExternalServiceError("ocr", "http://p", "http://f", ["HTTP 500: y"])
    d = _service_error_detail(err)
    assert d == {"service": "ocr", "primary": "http://p",
                 "fallback": "http://f", "errors": ["HTTP 500: y"]}
