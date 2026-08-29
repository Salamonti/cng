# server/services/progress_note_meta.py
"""Extract progress-note print metadata (AJ / name / DOB / MRN / ward ...) from encounter state.

Sources, in priority order (per field):
  1. "emr"   — Med Access chart dump lines inside extras (mixedOther / chart / oldVisits):
               demographics row format `LAST, FIRST MIDDLE  MM/DD/YYYY  admit  S  AGE Y  YRn  AJnnnn/yy  Facility`
               (anchored; cross-checked against the draft's **Patient:** line when available).
  2. "saved" — extras.progress_print (values from the last print of THIS encounter).
  3. "note"  — the draft preamble (**Patient:** / **Date:** / **Age/Sex:** ...) — model-generated, lowest trust.
AJ is validated against Code-39 charset before being offered or accepted.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Dict, Optional

# Code 39 accepted charset (uppercase)
C39_RE = re.compile(r"^[0-9A-Z\-. $/+% ]+$")
AJ_SHAPE_RE = re.compile(r"^AJ[\s#-]?\d{3,}(\s?[/\-]\s?\d{1,4})?$", re.I)

# Demographics row from the Med Access dump.
# GOULDEN, CHARLES OSBORNE 07/20/1942 08/25/2026 20:01 M 84 Y YR00109061 AJ0001948/26 Yarmouth Regional (YRH)
DEMO_RE = re.compile(
    r"(?P<name>[A-Z][A-Za-z'`\-]+(?:\s+[A-Z][A-Za-z'`\-\.]+)*,\s+[A-Z][A-Za-z'`\-]+(?:\s+[A-Z][A-Za-z'`\-\.]+)*)"
    r"\s+(?P<dob>\d{2}/\d{2}/\d{4})"
    r"\s+(?P<admit>\d{2}/\d{2}/\d{4}(?:\s+\d{1,2}:\d{2})?)"
    r"\s+(?P<sex>[MF])\s+(?P<age>\d{1,3})\s*Y?"
    r"\s+(?P<yr>YR\.?\s?\d{5,})"
    r"\s+(?P<aj>AJ[\s#-]?\d{3,}(?:[/\-]\s?\d{1,4})?)"
    r"\s+(?P<facility>[^\n]{2,60}?)(?=\n|$)"
)

AJ_FIND_RE = re.compile(r"\b(AJ[\s#-]?\d{3,}(?:[/\-]\s?\d{1,4})?)\b")
MRN_FIND_RE = re.compile(r"\b(YR\.?\s?\d{5,})\b")

# Draft preamble keys
PRE_PATIENT_RE = re.compile(r"^\s*\*{0,2}Patient\*{0,2}\s*:\s*(?P<v>.+?)\s*$", re.I)
PRE_DATE_RE = re.compile(r"^\s*\*{0,2}Date\*{0,2}\s*:\s*(?P<v>\d{4}-\d{2}-\d{2}.{0,10})\s*$", re.I)
PRE_AGESEX_RE = re.compile(r"^\s*\*{0,2}Age/Sex\*{0,2}\s*:\s*(?P<v>.+?)\s*$", re.I)
PRE_LOCATION_RE = re.compile(r"^\s*\*{0,2}Location\*{0,2}\s*:\s*(?P<v>.+?)\s*$", re.I)


def normalize_aj(raw: str) -> str:
    """Canonicalize an AJ payload for barcode use: uppercase, no inner spaces.

    'AJ0001948/26' stays verbatim; 'AJ 0001948 / 26' -> 'AJ0001948/26'.
    A trailing dash suffix ('AJ0001980-26') is the OCR slash variant used in
    Med Access dumps and is normalized to '/26' — the NSHA account format is
    AJ#######/YY. Returns "" for non-plausible AJ or Code-39 violations.
    """
    s = (raw or "").strip().upper()
    if not s or not AJ_SHAPE_RE.match(s):
        return ""
    s = re.sub(r"^AJ[\s#-]+", "AJ", s)
    s = re.sub(r"\s*/\s*", "/", s)
    if re.match(r"^AJ\d+-\d{1,4}$", s):
        s = s.replace("-", "/", 1)
    s = s.replace(" ", "")
    if not C39_RE.match(s):
        return ""
    return s


def _extras_blob(state: Dict[str, Any]) -> str:
    """Concatenate the extras text fields that carry the Med Access dump."""
    ex = (state or {}).get("extras") or {}
    parts = []
    for k in ("chart", "mixedOther", "oldVisits", "transcription", "currentEncounter"):
        v = ex.get(k)
        if isinstance(v, str) and v:
            parts.append(v)
    return "\n".join(parts)


def _fmt_dob(iso_mmdd: str) -> str:
    try:
        d = datetime.strptime(iso_mmdd, "%m/%d/%Y")
        return f"{d.day} {d.strftime('%b')} {d.year}"
    except Exception:
        return ""


def _parse_draft_preamble(draft: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    if not draft:
        return out
    for line in draft.splitlines()[:12]:
        m = PRE_PATIENT_RE.match(line)
        if m and "patient_name" not in out:
            out["patient_name"] = m.group("v").strip().strip("*").strip()
        m = PRE_DATE_RE.match(line)
        if m and "enc_date" not in out:
            out["enc_date"] = m.group("v").strip()[:10]
        m = PRE_AGESEX_RE.match(line)
        if m and "age_sex" not in out:
            out["age_sex"] = m.group("v").strip()
        m = PRE_LOCATION_RE.match(line)
        if m and "location" not in out:
            out["location"] = m.group("v").strip()
    return out


def _name_tokens(name: str) -> set:
    """Significant tokens for cross-check (surname + first name)."""
    parts = re.split(r"[,\s]+", (name or "").upper())
    return {p for p in parts if len(p) >= 4 and p not in {"MR", "MRS", "MS", "DR"}}


def _norm(name: str) -> str:
    return re.sub(r"[^A-Z]", "", (name or "").upper())


def _name_match(emr_name: str, note_name: str) -> bool:
    """True if surname from one appears in the other (both directions safe)."""
    if not emr_name or not note_name:
        return False
    emr_s = _norm(emr_name.split(",")[0])
    note_blob = _norm(note_name)
    emr_blob = _norm(emr_name.replace(",", " "))
    note_s = ""
    # note names are usually First Middle Last
    toks = [t for t in re.split(r"\s+", note_name.strip()) if len(t) >= 4]
    if toks:
        note_s = _norm(toks[-1])
    return bool((emr_s and emr_s in note_blob) or (note_s and note_s in emr_blob))


def extract_meta(state: Dict[str, Any]) -> Dict[str, Any]:
    """Return prefilled fields + provenance tags for the print dialog.

    keys: patient_name (formatted 'Last, First'), name_fmt_source, dob, sex, age,
          mrn, aj, aj_candidates, aj_confidence, facility, enc_date, ward, upi,
          sources {field: 'emr'|'saved'|'note'}
    """
    state = state or {}
    draft = state.get("draft") or ""
    ex = state.get("extras") or {}
    saved = ex.get("progress_print") if isinstance(ex.get("progress_print"), dict) else {}
    blob = _extras_blob(state)
    preamble = _parse_draft_preamble(draft)

    meta: Dict[str, Any] = {"sources": {}}
    s = meta["sources"]

    # ---- EMR demographics rows (may be several; keep candidates for the cross-check) ----
    # The same textarea is mirrored across extras.chart/oldVisits by the client,
    # so dedupe identical (name, dob, aj) rows before scoring.
    rows = []
    seen_rows = set()
    for m in DEMO_RE.finditer(blob):
        aj = normalize_aj(m.group("aj"))
        if not aj:
            continue
        key = (_norm(m.group("name")), _norm(m.group("dob")), aj)
        if key in seen_rows:
            continue
        seen_rows.add(key)
        rows.append(m)

    note_name = preamble.get("patient_name", "")
    best = None
    aj_candidates = []
    for m in rows:
        emr_name = m.group("name")
        aj = normalize_aj(m.group("aj"))
        if aj not in aj_candidates:
            aj_candidates.append(aj)
        match = _name_match(emr_name, note_name) or _name_match(emr_name, state.get("label", ""))
        if match and best is None:
            best = m
    if best is None and len(rows) == 1 and not note_name:
        # single EMR row, nothing to contradict it: plausible, medium confidence
        best = rows[0]

    if best is not None:
        s = meta["sources"]
        # format: LAST, First Middle — keep EMR casing structure, title-case given names
        last, _, rest = best.group("name").partition(",")
        meta["patient_name"] = last.strip().upper() + ", " + " ".join(
            w.capitalize() for w in rest.split()
        )
        s["patient_name"] = "emr"
        meta["dob"] = _fmt_dob(best.group("dob")); s["dob"] = "emr" if meta["dob"] else ""
        meta["sex"] = best.group("sex"); s["sex"] = "emr"
        meta["age"] = best.group("age") + "Y"; s["age"] = "emr"
        meta["mrn"] = best.group("yr").replace(". ", "").replace(".", "").replace(" ", ""); s["mrn"] = "emr"
        meta["aj"] = normalize_aj(best.group("aj")); s["aj"] = "emr"
        meta["facility"] = best.group("facility").strip(); s["facility"] = "emr"
        meta["admit_date"] = best.group("admit").split(" ")[0]
        # admit date is a lower-trust enc_date default
    meta["aj_candidates"] = aj_candidates
    n_match = sum(1 for m in rows if _name_match(m.group("name"), note_name or state.get("label", "")))
    if meta.get("aj") and n_match == 1:
        meta["aj_confidence"] = "high"
    elif meta.get("aj"):
        meta["aj_confidence"] = "medium"
    else:
        meta["aj_confidence"] = "none"

    # ---- saved (this encounter's last print) ----
    for k in ("patient_name", "dob", "sex", "age", "mrn", "aj", "upi", "ward", "enc_date", "bed"):
        v = str(saved.get(k) or "").strip()
        if v and k == "aj":
            v = normalize_aj(v)
        if v and (k not in meta or not meta.get(k)):
            meta[k] = v
            meta["sources"][k] = "saved"

    # ---- note preamble fallbacks (model-generated: lowest trust) ----
    s = meta["sources"]
    if not meta.get("patient_name") and note_name:
        meta["patient_name"] = note_name  # 'First Last' — front will show as-is
        s["patient_name"] = "note"
    if not meta.get("enc_date") and preamble.get("enc_date"):
        meta["enc_date"] = preamble["enc_date"]; s["enc_date"] = "note"
    if not meta.get("age_sex") and preamble.get("age_sex"):
        m = re.match(r"(\d{1,3})\s*[- ]?(?:year[- ]old)?\s*(M|F|Male|Female)?", preamble["age_sex"], re.I)
        if m:
            if not meta.get("age"):
                meta["age"] = m.group(1) + "Y"; s["age"] = "note"
            if not meta.get("sex") and m.group(2):
                meta["sex"] = m.group(2)[0].upper(); s["sex"] = "note"
    if not meta.get("ward") and preamble.get("location"):
        meta["ward"] = preamble["location"]; s["ward"] = "note"

    if not meta.get("enc_date"):
        meta["enc_date"] = datetime.now().strftime("%Y-%m-%d")
        s.setdefault("enc_date", "default")

    # strip empties
    return {k: v for k, v in meta.items() if v not in ("", None, [], {})}
