"""Regression test (P1-6): OCR uploads must be capped in size and, for
PDFs, in page count -- neither was bounded before, so a single very large
file or a many-hundred-page PDF could tie up an OCR worker (only
OCR_MAX_CONCURRENT of them exist) for a very long time.
"""
import io

import fitz
import uuid

import server.routes.ocr as ocr_routes


class _FakeOCRClient:
    model_name = "fake-ocr"

    def ocr_image_bytes(self, _img_bytes, mime_type="image/png"):
        return "FAKE_OCR_TEXT", 0.99


def _override_user(client):
    from server.app import app
    from server.core.dependencies import get_current_user, require_api_bearer
    from server.models.user import User

    fake_user = User(
        id=uuid.uuid4(),
        email=f"ocr-cap-{uuid.uuid4().hex[:8]}@example.com",
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


def test_oversized_file_is_rejected(client, monkeypatch):
    _override_user(client)
    monkeypatch.setattr(ocr_routes, "_get_ocr_client", lambda: _FakeOCRClient())
    monkeypatch.setattr(ocr_routes, "OCR_MAX_FILE_BYTES", 1000)

    payload = b"\xff\xd8\xff" + (b"0" * 2000)  # larger than the 1000-byte cap
    resp = client.post("/api/ocr", files={"file": ("big.jpg", payload, "image/jpeg")})
    assert resp.status_code == 413
    assert "limit" in resp.text.lower()


def test_file_within_size_cap_is_accepted(client, monkeypatch):
    _override_user(client)
    monkeypatch.setattr(ocr_routes, "_get_ocr_client", lambda: _FakeOCRClient())
    monkeypatch.setattr(ocr_routes, "OCR_MAX_FILE_BYTES", 10_000_000)

    pdf_bytes = _make_pdf_bytes(1)
    resp = client.post("/api/ocr", files={"file": ("small.pdf", pdf_bytes, "application/pdf")})
    assert resp.status_code == 200, resp.text
    assert resp.json()["success"] is True


def test_pdf_over_page_cap_is_rejected(client, monkeypatch):
    _override_user(client)
    monkeypatch.setattr(ocr_routes, "_get_ocr_client", lambda: _FakeOCRClient())
    monkeypatch.setattr(ocr_routes, "OCR_MAX_PDF_PAGES", 3)

    pdf_bytes = _make_pdf_bytes(5)
    resp = client.post("/api/ocr", files={"file": ("many-pages.pdf", pdf_bytes, "application/pdf")})
    assert resp.status_code == 413
    assert "page" in resp.text.lower()


def test_pdf_within_page_cap_is_accepted(client, monkeypatch):
    _override_user(client)
    monkeypatch.setattr(ocr_routes, "_get_ocr_client", lambda: _FakeOCRClient())
    monkeypatch.setattr(ocr_routes, "OCR_MAX_PDF_PAGES", 3)

    pdf_bytes = _make_pdf_bytes(2)
    resp = client.post("/api/ocr", files={"file": ("few-pages.pdf", pdf_bytes, "application/pdf")})
    assert resp.status_code == 200, resp.text
    assert resp.json()["pages_processed"] == 2
