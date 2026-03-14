# server/routes/ocr.py
import io
import os
import time
import logging
import traceback
from typing import Dict

from fastapi import APIRouter, File, UploadFile, HTTPException
from PIL import Image, ImageOps
try:
    import pillow_heif  # type: ignore
    pillow_heif.register_heif_opener()  # Enable HEIC/HEIF support if installed
except Exception:
    pass
import fitz  # PyMuPDF

from services.ocr_llm_client import OCRLLMEngine, ExternalServiceError
from concurrent.futures import ThreadPoolExecutor, as_completed
from metrics import metrics as global_metrics


router = APIRouter()
logger = logging.getLogger("ocr")

# Allow override via env; default to local llama-server
OCR_PDF_DPI = int(os.environ.get("OCR_PDF_DPI", "200"))
OCR_IMAGE_MAX_DIM = int(os.environ.get("OCR_IMAGE_MAX_DIM", "3200"))
OCR_ENABLE_TEXT_FIRST = os.environ.get("OCR_TEXT_FIRST", "0") == "1"
OCR_ENABLE_PARALLEL_PAGES = os.environ.get("OCR_PARALLEL_PAGES", "0") == "1"

# Dynamically resolve OCR server URL from env (single source of truth)
_CACHED_OCR_URL: str | None = None
_OCR_CLIENT: OCRLLMEngine | None = None


def _get_ocr_client() -> OCRLLMEngine:
    global _CACHED_OCR_URL, _OCR_CLIENT
    url = str(os.environ.get("OCR_URL_PRIMARY") or "").strip().rstrip("/")
    if _OCR_CLIENT is None or _CACHED_OCR_URL != url:
        _OCR_CLIENT = OCRLLMEngine(url=url)
        _CACHED_OCR_URL = url
    return _OCR_CLIENT


def _service_error_detail(err: ExternalServiceError) -> Dict:
    return {
        "service": err.service,
        "primary": err.primary_url,
        "fallback": err.fallback_url,
        "errors": err.errors,
    }


def _pdf_first_page_to_png_bytes(pdf_bytes: bytes, dpi: int = OCR_PDF_DPI) -> bytes:
    """Render first page of PDF to PNG bytes using PyMuPDF."""
    zoom = dpi / 72.0
    mat = fitz.Matrix(zoom, zoom)
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        if doc.page_count == 0:
            raise ValueError("Empty PDF")
        page = doc.load_page(0)
        pix = page.get_pixmap(matrix=mat)
        return pix.tobytes("png")


def _ensure_image_png_bytes(data: bytes) -> bytes:
    """Load bytes with Pillow and re-encode to PNG bytes."""
    with Image.open(io.BytesIO(data)) as im:
        # Convert to RGB to normalize modes (e.g., RGBA, P)
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        out = io.BytesIO()
        im.save(out, format="PNG")
        return out.getvalue()


def _downscale_if_needed(im: Image.Image, max_dim: int) -> Image.Image:
    try:
        w, h = im.size
        if max(w, h) <= max_dim:
            return im
        # Preserve aspect ratio
        im = ImageOps.contain(im, (max_dim, max_dim))
        return im
    except Exception:
        return im


def _pdf_extract_text_first(pdf_bytes: bytes) -> str:
    """Attempt text extraction for selectable PDFs; return empty if none."""
    try:
        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            if doc.page_count == 0:
                return ""
            parts = []
            for i in range(doc.page_count):
                page = doc.load_page(i)
                txt = page.get_text("text") or ""
                txt = txt.strip()
                if txt:
                    parts.append(txt)
            return "\n\n".join(parts).strip()
    except Exception:
        return ""


@router.post("/ocr")
def ocr(file: UploadFile = File(...)) -> Dict:
    try:
        data = file.file.read()
        if not data:
            raise HTTPException(status_code=400, detail="Empty file")

        filename = (file.filename or "").lower()
        content_type = (file.content_type or "").lower()

        t0 = time.perf_counter()

        # Convert PDFs to PNG (multi-page support); otherwise verify image and pass optimized bytes
        if content_type == "application/pdf" or filename.endswith(".pdf"):
            # Process all pages of PDF
            try:
                # First try selectable text extraction to avoid rasterization entirely
                extracted_text = _pdf_extract_text_first(data) if OCR_ENABLE_TEXT_FIRST else ""
                if extracted_text:
                    elapsed = time.perf_counter() - t0
                    if global_metrics:
                        try:
                            global_metrics.record_ocr(elapsed, 0.95)
                        except Exception:
                            pass
                    return {
                        "success": True,
                        "text": extracted_text,
                        "confidence": 0.95,
                        "engine_used": "pdf-text",
                        "processing_time": round(elapsed, 3),
                        "pages_processed": None,
                    }

                with fitz.open(stream=data, filetype="pdf") as doc:
                    if doc.page_count == 0:
                        raise HTTPException(status_code=400, detail="Empty PDF")

                    # Process pages sequentially or in parallel based on toggle
                    total_confidence = 0.0
                    if OCR_ENABLE_PARALLEL_PAGES and doc.page_count > 1:
                        results: list[tuple[str, float] | None] = [None] * doc.page_count
                        def ocr_page(page_num: int) -> tuple[int, str, float]:
                            page = doc.load_page(page_num)
                            pix = page.get_pixmap(matrix=fitz.Matrix(OCR_PDF_DPI/72, OCR_PDF_DPI/72))
                            page_img_bytes = pix.tobytes("png")
                            page_text, page_conf = _get_ocr_client().ocr_image_bytes(page_img_bytes, mime_type="image/png")
                            return page_num, page_text, page_conf
                        with ThreadPoolExecutor(max_workers=4) as ex:
                            futures = [ex.submit(ocr_page, i) for i in range(doc.page_count)]
                            for fut in as_completed(futures):
                                try:
                                    idx, txt, conf = fut.result()
                                    results[idx] = (txt, conf)
                                    total_confidence += conf
                                except Exception as e:
                                    pass
                        combined_parts: list[str] = []
                        for i in range(doc.page_count):
                            pair = results[i]
                            if pair is None:
                                combined_parts.append(f"--- Page {i+1} ---\n[OCR Error: unknown]")
                            else:
                                txt, _c = pair
                                combined_parts.append(f"--- Page {i+1} ---\n{txt}")
                        combined_text = "\n\n".join(combined_parts)
                        avg_confidence = total_confidence / doc.page_count if doc.page_count > 0 else 0
                    else:
                        all_text_results = []
                        for page_num in range(doc.page_count):
                            page = doc.load_page(page_num)
                            pix = page.get_pixmap(matrix=fitz.Matrix(OCR_PDF_DPI/72, OCR_PDF_DPI/72))
                            page_img_bytes = pix.tobytes("png")
                            try:
                                page_text, page_conf = _get_ocr_client().ocr_image_bytes(page_img_bytes, mime_type="image/png")
                                all_text_results.append(f"--- Page {page_num + 1} ---\n{page_text}")
                                total_confidence += page_conf
                            except Exception as e:
                                all_text_results.append(f"--- Page {page_num + 1} ---\n[OCR Error: {str(e)}]")
                        combined_text = "\n\n".join(all_text_results)
                        avg_confidence = total_confidence / doc.page_count if doc.page_count > 0 else 0

                    # Return early for multi-page PDFs
                    elapsed = time.perf_counter() - t0
                    if global_metrics:
                        try:
                            global_metrics.record_ocr(elapsed, avg_confidence)
                        except Exception:
                            pass
                    return {
                        "success": True,
                        "text": combined_text,
                        "confidence": round(avg_confidence, 2),
                        "engine_used": _get_ocr_client().model_name,
                        "processing_time": round(elapsed, 3),
                        "pages_processed": doc.page_count
                    }

            except HTTPException:
                raise
            except Exception:
                raise HTTPException(status_code=400, detail="Invalid PDF file")
        else:
            # Single image processing: downscale large images and encode PNG for uniformity
            try:
                with Image.open(io.BytesIO(data)) as im:
                    # Normalize colorspace
                    if im.mode not in ("RGB", "L"):
                        im = im.convert("RGB")
                    im = _downscale_if_needed(im, OCR_IMAGE_MAX_DIM)
                    buf = io.BytesIO()
                    im.save(buf, format="PNG")
                    img_bytes = buf.getvalue()
                    content_type = "image/png"
            except Exception:
                # Fallback to raw bytes
                img_bytes = data
                if not content_type or not content_type.startswith("image/"):
                    content_type = "image/png"
        try:
            # Optional preflight: when debugging, probe /v1/models to surface wrong backends
            if os.environ.get("DEBUG_OCR_ERRORS", "0") == "1":
                try:
                    import requests as _rq
                    client = _get_ocr_client()
                    rmodels = _rq.get(f"{client.url}/v1/models", timeout=2)
                    if rmodels.ok:
                        md = rmodels.json()
                        # Log a short summary to server logs
                        mcount = len(md.get("data", [])) if isinstance(md, dict) else None
                        logger.info("OCR preflight /v1/models ok: count=%s", mcount)
                        try:
                            sample = []
                            for item in (md.get("data", []) if isinstance(md, dict) else [])[:3]:
                                if isinstance(item, dict):
                                    sample.append(item.get("id") or item.get("name"))
                            if sample:
                                logger.info("OCR models sample: %s", sample)
                        except Exception:
                            pass
                except Exception as _e:
                    logger.info("OCR preflight /v1/models error: %s", _e)

            text, conf = _get_ocr_client().ocr_image_bytes(img_bytes, mime_type=content_type)
        except ExternalServiceError as e:
            raise HTTPException(status_code=503, detail=_service_error_detail(e))
        except Exception as e:
            # llama-server unreachable/timeout or other error
            if os.environ.get("DEBUG_OCR_ERRORS", "0") == "1":
                # Also log to server console for easy capture
                try:
                    logger.error(f"OCR debug error: {e}", exc_info=True)
                except Exception:
                    print(f"OCR debug error: {e}\n{traceback.format_exc()}")
                raise HTTPException(status_code=503, detail=f"OCR service unavailable: {e}")
            raise HTTPException(status_code=503, detail="OCR service unavailable")

        elapsed = time.perf_counter() - t0
        if global_metrics:
            try:
                global_metrics.record_ocr(elapsed, conf)
            except Exception:
                pass
        return {
            "success": True,
            "text": text,
            "confidence": conf,
            "engine_used": _get_ocr_client().model_name,
            "processing_time": round(elapsed, 3),
        }

    except HTTPException:
        raise
    except ExternalServiceError as e:
        raise HTTPException(status_code=503, detail=_service_error_detail(e))
    except Exception as e:
        # Don't queue - tell client to fallback
        if os.environ.get("DEBUG_OCR_ERRORS", "0") == "1":
            # Also log to server console for easy capture
            try:
                logger.error(f"OCR debug error: {e}", exc_info=True)
            except Exception:
                print(f"OCR debug error: {e}\n{traceback.format_exc()}")
            raise HTTPException(status_code=503, detail=f"OCR service unavailable: {e}")
        raise HTTPException(status_code=503, detail="OCR service unavailable")
