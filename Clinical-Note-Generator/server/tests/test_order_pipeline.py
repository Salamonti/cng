"""Tests for order/referral extraction helpers (imaging vs referral)."""

import sys
from pathlib import Path

# Match server/app.py: `core.*` resolves when the server package root is on sys.path.
_SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(_SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(_SERVER_ROOT))

from server.core.order.pipeline import (
    _coerce_imaging_vs_referral,
    _title_suggests_imaging,
)


def test_title_suggests_imaging_mammography_spelling():
    assert _title_suggests_imaging("Screening mammography")
    assert _title_suggests_imaging("bilateral mammography")
    assert _title_suggests_imaging("Mammogram diagnostic")


def test_title_suggests_imaging_common_modalities():
    assert _title_suggests_imaging("CT chest without contrast")
    assert _title_suggests_imaging("MRI brain")
    assert _title_suggests_imaging("PET-CT")
    assert _title_suggests_imaging("Abdominal ultrasound")
    assert _title_suggests_imaging("DEXA bone density")


def test_coerce_referral_to_imaging_when_title_is_radiology():
    cat, use_ref, imaging = _coerce_imaging_vs_referral(
        "Referral", "Screening mammography", True
    )
    assert cat == "Imaging"
    assert use_ref is False
    assert imaging is True


def test_coerce_keeps_true_referral_when_not_imaging():
    cat, use_ref, imaging = _coerce_imaging_vs_referral(
        "Referral", "Cardiology consult for chest pain", True
    )
    assert cat == "Referral"
    assert use_ref is True
    assert imaging is False


def test_coerce_clears_referral_prompt_when_category_imaging_but_flag_wrong():
    cat, use_ref, imaging = _coerce_imaging_vs_referral("Imaging", "CT abdomen", True)
    assert cat == "Imaging"
    assert use_ref is False
    assert imaging is True
