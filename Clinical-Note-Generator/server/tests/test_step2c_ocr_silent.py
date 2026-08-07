"""STEP 2c -- reclassified silent handlers in routes/ocr.py.

Two behavior classes are pinned:
1. Best-effort OCR metrics telemetry is *safe* to swallow: if the metrics
   backend raises, the OCR result returned to the user must be unaffected
   (still 200 + real text), and the swallow now logs at debug (was bare
   `pass`).
2. A failing page in the *parallel* PDF path is fault-tolerant by design
   (that page becomes "[OCR Error: unknown]"), but it now records the real
   error with logger.exception (was bare `pass`) so it is no longer silent.

pure-comment changes (optional pillow_heif import guard at L14, the
DEBUG_OCR_ERRORS-only model-sample derivation at L284) need no behavior test.
"""
import logging
import uuid

import fitz

import server.routes.ocr as ocr_routes


class _FakeOCRClient:
    model_name = "fake-ocr"

    def __init__(self, raise_on_page: bool = False):
        self.raise_on_page = raise_on_page
        self.calls = 0

    def ocr_image_bytes(self, _img_bytes, mime_type="image/png"):
        self.calls += 1
        if self.raise_on_page:
            raise RuntimeError("ocr backend page failure")
        return "FAKE_OCR_TEXT", 0.99


class _RaisingMetrics:
    def record_ocr(self, *_a, **_k):
        raise RuntimeError("metrics backend down")


def _override_user(client):
    from server.app import app
    from server.core.dependencies import get_current_user, require_api_bearer
    from server.models.user import User

    fake_user = User(
        id=uuid.uuid4(),
        email=f"ocr-silent-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="x",
        is_active=True,
        is_approved=True,
    )
    app.dependency_overrides[get_current_user] = lambda: fake_user
    app.dependency_overrides[require_api_bearer] = lambda: True


def _make_pdf_bytes(num_pages: int) -> bytes:
    doc = fitz.open()
    for _ in range(num_pages):
        page = doc.new_page()
        page.insert_text((72, 72), "test page")
    data = doc.tobytes()
    doc.close()
    return data


def test_metrics_failure_does_not_break_ocr_result(client, monkeypatch, caplog):
    """Safe-to-swallow telemetry: metric record raising must not change the
    OCR text the user receives, and it now logs at debug."""
    _override_user(client)
    monkeypatch.setattr(ocr_routes, "_get_ocr_client", lambda: _FakeOCRClient())
    monkeypatch.setattr(ocr_routes, "OCR_MAX_FILE_BYTES", 10_000_000)
    monkeypatch.setattr(ocr_routes, "global_metrics", _RaisingMetrics())

    caplog.set_level(logging.DEBUG, logger="ocr")
    payload = b"\xff\xd8\xff\xe0" + (b"x" * 64)
    resp = client.post("/api/ocr", files={"file": ("img.jpg", payload, "image/jpeg")})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is True
    assert body["text"] == "FAKE_OCR_TEXT"
    assert any(r.name == "ocr" and "OCR metrics record failed" in r.getMessage()
               for r in caplog.records)


def test_parallel_page_failure_is_logged_not_silent(client, monkeypatch, caplog):
    """Per-page OCR failure in the parallel path must not abort the PDF (page
    becomes '[OCR Error: unknown]') AND the real error is logged."""
    _override_user(client)
    monkeypatch.setattr(ocr_routes, "OCR_ENABLE_PARALLEL_PAGES", True)
    monkeypatch.setattr(ocr_routes, "OCR_PARALLEL_PAGE_WORKERS", 4)
    monkeypatch.setattr(ocr_routes, "OCR_MAX_CONCURRENT", 4)
    monkeypatch.setattr(ocr_routes, "_get_ocr_client", lambda: _FakeOCRClient(raise_on_page=True))
    monkeypatch.setattr(ocr_routes, "OCR_MAX_PDF_PAGES", 10)

    caplog.set_level(logging.WARNING, logger="ocr")
    pdf_bytes = _make_pdf_bytes(2)
    resp = client.post("/api/ocr", files={"file": ("multi.pdf", pdf_bytes, "application/pdf")})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is True
    assert "[OCR Error: unknown]" in body["text"]
    assert any(r.name == "ocr" and "OCR parallel page task failed" in r.getMessage()
               for r in caplog.records)
