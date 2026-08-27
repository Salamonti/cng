"""OCR-profile degeneration thresholds (2026-08-27).

Production incident: a 505x503 photo of an ILD antibody panel transcribed
perfectly (25 rows, "Within Reference Range 0-10 SI Final" repeating 23x),
but the NOTE-profile n-gram guard ((6,9)) rejected it -> 503 on every OCR
attempt. OCR now uses the verbose-loop profile: repetition must dominate the
output before it's flagged. Note generation keeps the old thresholds.
"""
from server.core.clinical_output_guard import detect_degenerate_output


def _ild_panel() -> str:
    """The real production transcription that was being rejected."""
    rows = [
        "Anti-NOR90 Within Reference Range 0-10 SI Final",
        "Anti-SAE1 Within Reference Range 0-10 SI Final",
        "Anti-OJ Within Reference Range 0-10 SI Final",
        "Anti-EJ Within Reference Range 0-10 SI Final",
        "Anti-SRP Within Reference Range 0-10 SI Final",
        "Anti-PL12 Within Reference Range 0-10 SI Final",
        "Anti-PL7 Within Reference Range 0-10 SI Final",
        "Anti-TIF1 gamma Within Reference Range 0-10 SI Final",
        "Anti-NXP2 Within Reference Range 0-10 SI Final",
        "Anti-MDA5 Weak Positive 19 SI Final",
        "Anti-Mi-2 beta Within Reference Range 0-10 SI Final",
        "Anti-Mi-2 alpha Within Reference Range 0-10 SI Final",
        "Anti-Jo-1 Within Reference Range 0-10 SI Final",
        "Anti-CENP A Within Reference Range 0-10 SI Final",
        "Anti-PM-Scl75 Within Reference Range 0-10 SI Final",
        "Anti-PM-Scl100 Within Reference Range 0-10 SI Final",
        "Anti-Ro52/TRIM21 Strong Positive 91 SI Final",
        "Anti-PDFGR Within Reference Range 0-10 SI Final",
        "Anti-Ku Within Reference Range 0-10 SI Final",
        "Anti-Th/70/HP0P Within Reference Range 0-10 SI Final",
        "Anti-Fibrillarin Within Reference Range 0-10 SI Final",
        "Anti-RPL55 Within Reference Range 0-10 SI Final",
        "Anti-RPL11 Within Reference Range 0-10 SI Final",
        "Anti-Scl-70/Topo-1 Within Reference Range 0-10 SI Final",
        "Anti-CENP B Within Reference Range 0-10 SI Final",
    ]
    return (
        "Interstitial Lung Disease (ILD) Antibody Panel\n"
        "Completed: 2026AU004 11:15\n\n"
        "Analyte Interpretation Result Units Status\n"
        + "\n".join(rows)
        + "\nAccession: 26-210-0027"
    )


def test_ild_panel_passes_ocr_profile():
    """The exact production transcription must be accepted by the OCR profile."""
    reasons = detect_degenerate_output(_ild_panel(), max_chars=30000, ocr=True)
    assert reasons == [], f"OCR profile false-positive: {reasons}"


def test_ild_panel_would_still_fail_note_profile():
    """Confirms the incident was profile-specific: the note profile (unchanged)
    rejects this same text, proving the OCR profile is what fixes it."""
    reasons = detect_degenerate_output(_ild_panel(), max_chars=30000)
    assert "repeated n-gram loop detected" in reasons


def test_ocr_verbose_loop_still_rejected():
    """A genuine echo loop (same line repeated 30x) must still be caught."""
    loop = "Sodium 141 mmol/L Chloride 104 mmol/L Potassium 4.2 mmol/L\n" * 30
    reasons = detect_degenerate_output(loop, max_chars=30000, ocr=True)
    assert "repeated n-gram loop detected" in reasons, reasons


def test_ocr_extreme_row_repetition_rejected():
    """A 6-word span repeated 40x is verbose-loop territory even if the rows
    nominally differ (40 identical value phrases = 240 words of pure echo)."""
    loop = "Anti-Ku Within Reference Range 0-10 SI Final\n" * 40
    reasons = detect_degenerate_output(loop, max_chars=30000, ocr=True)
    assert "repeated n-gram loop detected" in reasons, reasons


def test_ocr_verbatim_line_loop_rejected():
    """The real OCR degeneration mode: the model stuck re-emitting the same
    line 30x. Must be caught by the identical-line check."""
    loop = "Sodium 141 mmol/L Chloride 104 mmol/L Potassium 4.2 mmol/L\n" * 30
    reasons = detect_degenerate_output(loop, max_chars=30000, ocr=True)
    assert "repeated n-gram loop detected" in reasons, reasons


def test_ocr_large_diverse_panel_passes():
    """A 60-row lab panel with a shared interpretation phrase (the incident
    pattern at 2.4x scale) must pass: every row's leading words differ."""
    rows = [f"Anti-Ab{i} Within Reference Range 0-10 SI Final" for i in range(60)]
    text = "Panel:\n" + "\n".join(rows)
    assert detect_degenerate_output(text, max_chars=30000, ocr=True) == []


def test_ocr_mixed_panel_with_echo_tail_rejected():
    """A 10-row genuine panel plus a 20x echo of one row: the degeneration
    tail must still be caught."""
    rows = [f"Anti-Ab{i} Within Reference Range 0-10 SI Final" for i in range(10)]
    text = "\n".join(rows) + "\n" + ("Anti-Ku Within Reference Range 0-10 SI Final\n" * 20)
    assert "repeated n-gram loop detected" in detect_degenerate_output(text, max_chars=30000, ocr=True)


def test_ocr_normal_document_unaffected():
    """Diverse clinical text with modest repetition passes comfortably.
    (Each row's tail is unique — a fixed repeated 6-word tail 60x would be a
    genuine verbose loop and the OCR profile must catch that.)"""
    text = "\n".join(
        f"Row {i}: patient reported value {1000 + i} unit {i * 7} result {i * 11} "
        f"status {i * 13} comment {i * 17} note {i * 19} time {i * 23}."
        for i in range(60)
    )
    assert detect_degenerate_output(text, max_chars=30000, ocr=True) == []
