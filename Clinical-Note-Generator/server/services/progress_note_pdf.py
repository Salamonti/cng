# server/services/progress_note_pdf.py
"""Render an encounter note draft into an NSHA-style Progress Notes PDF (A4, Code-39 barcodes).

Refactored from the verified one-off `make_pn_progressnote.py` (10/10 round-trip batch,
Aug 28 2026). Geometry, sanitize map, width-only barcode placement, and {nb} page
totals are preserved verbatim from the proven implementation.
"""
from __future__ import annotations

import io
import re
import tempfile
import os
from typing import Any, Dict, List, Optional, Tuple

from barcode import Code39
from barcode.writer import ImageWriter
from fpdf import FPDF

SWIPER_PAYLOAD = "SWPRNO"
DEFAULT_SERVICE = "Internal Medicine - Respiratory"
BOTTOM_LIMIT = 297 - 30          # body content hard limit (y=267)
FOOTER_RULE_Y = 297 - 26         # y=271

# Code-39 payload validation (shared)
C39_ALLOWED = re.compile(r"^[0-9A-Z\-. $/+%]+$")


def barcode_png(payload: str, path: str) -> str:
    bc = Code39(payload, writer=ImageWriter(), add_checksum=False)
    return bc.save(path, options={
        "module_height": 16.0, "module_width": 0.5,
        "quiet_zone": 2.5, "write_text": False,
    })


_SANITIZE = {
    "\u2022": "- ", "\u2013": "-", "\u2014": " - ", "\u2018": "'", "\u2019": "'",
    "\u201c": '"', "\u201d": '"', "\u00b0": " deg", "\u00b5": "u", "\u2264": "<=",
    "\u2265": ">=", "\u00d7": "x", "\u2192": "->", "\u00a0": " ", "\u2212": "-",
    "\u00b1": "+/-", "\u2248": "~", "\u2026": "...",
}


def sanitize(s: str) -> str:
    for k, v in _SANITIZE.items():
        s = s.replace(k, v)
    return s.encode("latin-1", "ignore").decode("latin-1")


def inline_runs(line: str) -> List[Tuple[str, bool, bool]]:
    parts = []
    for i, seg in enumerate(re.split(r"\*\*", line)):
        b = (i % 2 == 1)
        parts.append((seg, b, False))
    return [p for p in parts if p[0]]


_META_RE = re.compile(
    r"^(\*\*)?(Date|Patient|Age|Sex|MRN|Age/Sex|Attending|Clinician|Author|Location|Encounter|Record)(\*\*)?\s*:?\s*:?",
    re.IGNORECASE,
)


class _NotePDF(FPDF):
    def __init__(self, meta: Dict[str, Any], bc_aj: str, bc_sw: str):
        super().__init__(orientation="P", unit="mm", format="A4")
        self.meta = meta
        self.bc_aj = bc_aj
        self.bc_sw = bc_sw
        self.alias_nb_pages()

    def page_chrome(self, title: str) -> None:
        m = self.meta
        self.add_page()
        self.set_font("Helvetica", "", 8)
        self.text(10, 11, "PERMANENT RECORD")
        self.set_text_color(0, 0, 255)
        self.text(95, 11, sanitize(m.get("clinician", "") or "")[:40])
        self.set_text_color(200, 0, 0)
        self.text(140, 11, sanitize(m.get("ward", "") or "")[:60])
        self.set_text_color(0, 0, 0)
        self.set_xy(170, 7)
        self.cell(30, 4, f"Page {self.page_no()} of {{nb}}", align="R")
        self.line(10, 13, 200, 13)
        rows = [
            [("Last, First Name:", m.get("name", ""), 10, 110),
             ("Sex:", m.get("sex", ""), 125, 15),
             ("Health Card (UPI):", m.get("upi", ""), 145, 55)],
            [("MRN:", m.get("yr", ""), 10, 45),
             ("DOB:", m.get("dob", ""), 60, 40),
             ("Age:", m.get("age", ""), 105, 20),
             ("Encounter Date:", m.get("enc_date", ""), 130, 70)],
            [("Registration #:", m.get("aj", ""), 10, 45),
             ("Service:", m.get("service", DEFAULT_SERVICE), 60, 85),
             ("Ward / Site:", m.get("ward", ""), 150, 50)],
        ]
        y = 19.0
        for row in rows:
            self.set_font("Helvetica", "", 8.5)
            for label, val, x0, fw in row:
                label = sanitize(label)
                val = sanitize(val or "")
                self.text(x0, y, label)
                lw = self.get_string_width(label) + 1
                self.set_font("Helvetica", "B", 9)
                maxv = fw - lw
                vv = val
                while self.get_string_width(vv) > maxv and len(vv) > 3:
                    vv = vv[:-2]
                if vv != val:
                    vv = vv + "..."
                self.text(x0 + lw, y, vv)
                self.set_font("Helvetica", "", 8.5)
                self.line(x0 + lw + self.get_string_width(vv) + 1.5, y + 0.6, x0 + fw, y + 0.6)
            y += 6
        self.line(10, y, 200, y)
        self.set_font("Helvetica", "B", 13)
        self.set_y(y + 2)
        self.cell(0, 8, sanitize(title), align="C")
        self.set_y(y + 12)

    def footer_chrome(self) -> None:
        fy = FOOTER_RULE_Y
        self.set_draw_color(0)
        self.line(10, fy, 200, fy)
        self.set_font("Helvetica", "", 7)
        self.text(12, fy + 5, sanitize(self.meta.get("aj", "")))
        self.image(self.bc_aj, x=12, y=fy + 7, w=60)   # width only — never force height
        self.text(115, fy + 5, "Rev:")
        self.image(self.bc_sw, x=125, y=fy + 6, w=52)
        self.text(180, fy + 5, SWIPER_PAYLOAD)


def render_progress_note_pdf(md: str, meta: Dict[str, Any]) -> bytes:
    """Render draft markdown -> progress-note sheet PDF bytes.

    meta keys: name, sex, upi, yr, dob, age, enc_date, aj, service, ward, clinician.
    """
    aj = (meta.get("aj") or "").strip()
    if not aj or not C39_ALLOWED.match(aj):
        raise ValueError("AJ payload invalid for Code-39 barcode")
    md = sanitize(md or "")
    lines = [l.rstrip() for l in md.splitlines()]
    title = "Progress Note"
    body_start = 0
    for i, l in enumerate(lines):
        t = l.strip().replace("*", "")
        if t:
            title = t
            body_start = i + 1
            break

    tmp = tempfile.mkdtemp(prefix="pn_bc_")
    try:
        bc_aj = barcode_png(aj, os.path.join(tmp, "aj"))
        bc_sw = barcode_png(SWIPER_PAYLOAD, os.path.join(tmp, "sw"))
        pdf = _NotePDF(meta, bc_aj, bc_sw)
        pdf.page_chrome(title)
        pdf.set_line_width(0.2)
        x0, x1 = 12, 200
        for li, l in enumerate(lines[body_start:]):
            s = l.strip()
            if not s:
                pdf.set_y(pdf.get_y() + 1.5)
                continue
            if li < 12 and _META_RE.match(s) and len(s) < 90 and ":" in s:
                continue
            indent = 4 if re.match(r"^[-*]\s|^\d+\.\s|^\*", l) and not l.startswith("#") else 0
            s = re.sub(r"^[-*]\s+", "- ", s)
            runs = inline_runs(s)
            font_base = 9
            pdf.set_font("Helvetica", "", font_base)
            plain = "".join(r[0] for r in runs)
            is_head = len(plain) < 60 and (
                plain.endswith(":")
                or (re.match(r"^[A-Z][A-Za-z /&'\-]+:?$", plain) and len(plain) < 40)
            )
            size = 9.5 if is_head else font_base
            words: List[Tuple[str, bool]] = []
            for txt, b, _i in runs:
                for w in txt.split():
                    words.append((w, b))
            line_words: List[Tuple[str, bool]] = []

            def flush() -> None:
                nonlocal line_words
                if not line_words:
                    return
                y = pdf.get_y()
                # page break if this line would cross the bottom limit
                if y + size * 0.5 > BOTTOM_LIMIT:
                    pdf.footer_chrome()
                    pdf.page_chrome(title + " (cont.)")
                    y = pdf.get_y()
                xx = x0 + indent
                for w, b in line_words:
                    pdf.set_font("Helvetica", "B" if b else "", size)
                    ww = pdf.get_string_width(w + " ")
                    pdf.set_xy(xx, y)
                    pdf.cell(ww, size * 0.5, w + " ")
                    xx += ww
                pdf.set_y(y + size * 0.5)
                line_words = []

            for w, b in words:
                pdf.set_font("Helvetica", "B" if b else "", size)
                trial = " ".join(t[0] for t in line_words + [(w, b)])
                if pdf.get_string_width(trial + " ") > (x1 - x0 - indent):
                    flush()
                line_words.append((w, b))
            flush()
            if is_head:
                if pdf.get_y() + 1 > BOTTOM_LIMIT:
                    pdf.footer_chrome()
                    pdf.page_chrome(title + " (cont.)")
                else:
                    pdf.set_y(pdf.get_y() + 1)
        pdf.set_y(BOTTOM_LIMIT)
        pdf.footer_chrome()
        return bytes(pdf.output())
    finally:
        for f in os.listdir(tmp):
            try:
                os.remove(os.path.join(tmp, f))
            except OSError:
                pass
        os.rmdir(tmp)


def verify_pdf_bytes(pdf_bytes: bytes, expect_aj: str) -> Tuple[bool, List[str]]:
    """Rasterize page 1 at 200 dpi, decode barcodes, assert AJ + SWPRNO round-trip.

    Returns (ok, decoded_payloads). Uses PyMuPDF + zxing-cpp (both in app venv).
    """
    import zxingcpp
    import fitz
    from PIL import Image

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    pix = doc[0].get_pixmap(dpi=200)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    codes = [r.text for r in zxingcpp.read_barcodes(img)]
    ok = (expect_aj in codes) and (SWIPER_PAYLOAD in codes)
    return ok, codes
