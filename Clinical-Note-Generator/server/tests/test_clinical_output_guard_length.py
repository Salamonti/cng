"""Regression test (WO-1): a long-but-legitimate note must not be rejected
by the output guard's character caps.

Before WO-1, detect_degenerate_output()'s streaming default (24000 chars)
and validate_clinical_note()'s final check (20000 chars) disagreed, and
both were far below the 8192-token note ceiling (~32000 chars). A note in
that band would stream to completion and then be rejected outright -- the
exact failure mode of the 2026-08-01 outage. All four call sites now share
one MAX_OUTPUT_CHARS = 36000 constant sized for an 8192-token note.
"""
from server.core.clinical_output_guard import (
    MAX_OUTPUT_CHARS,
    detect_degenerate_output,
    validate_clinical_note,
)


def _unique_long_text(num_lines: int) -> str:
    """Build long text with no repeated 5+ word span (avoids the n-gram
    degeneration detector) using a shared, overlapping fixed vocabulary
    (satisfies the source-grounding ratio check) and dense unique numeric
    tokens spaced <=4 words apart (guarantees no 5-word window repeats)."""
    lines = []
    for i in range(num_lines):
        lines.append(
            f"Entry {i}: patient reports code {i + 10_000} with pattern "
            f"{i + 20_000} noted at {i + 30_000} today for review "
            f"{i + 40_000} plan {i + 50_000}."
        )
    return "\n".join(lines)


def test_long_note_over_old_20000_char_cap_is_accepted():
    output = _unique_long_text(250)
    assert 20_000 < len(output) < MAX_OUTPUT_CHARS

    source = _unique_long_text(60)  # long enough to satisfy the
    # proportionality check (allowed_chars = min(MAX_OUTPUT_CHARS, len(source)*6))
    # and shares the same fixed vocabulary so the grounding-ratio check passes.
    prompt = "SYSTEM:\nWrite a note.\n\nUSER:\nPATIENT DATA:\n" + source + "\n\nASSISTANT:"

    result = validate_clinical_note(prompt, output)

    assert result.accepted, f"expected acceptance, got reasons: {result.reasons}"


def test_same_note_would_have_been_rejected_at_the_old_20000_char_cap():
    output = _unique_long_text(250)
    assert len(output) > 20_000

    reasons = detect_degenerate_output(output, max_chars=20_000)

    assert "output exceeded the hard character limit" in reasons


def test_note_over_the_new_cap_is_still_rejected():
    output = _unique_long_text(1200)  # comfortably over 36000 chars
    assert len(output) > MAX_OUTPUT_CHARS

    reasons = detect_degenerate_output(output)

    assert "output exceeded the hard character limit" in reasons


def test_structured_diet_table_header_is_not_a_loop():
    """A 28-option meal plan repeats its markdown table column header once per
    section (~4x). The guard must NOT treat this legitimate structured output
    as a runaway loop (2026-08-07: the old (12,3)/(10,3)/(8,4) thresholds did,
    and the whole good plan was thrown away as 'repeated n-gram loop')."""
    header = "| Option | Foods & portions | Calories | Protein (g) | Carbs (g) | Fat (g) |"
    section = "\n".join([
        "## Breakfast",
        header,
        "| 1 | Oatmeal with milk, 1 banana | 400 | 18 | 60 | 8 |",
        "| 2 | Greek yogurt, blueberries | 350 | 25 | 40 | 8 |",
        "| 3 | Egg scramble, toast | 450 | 25 | 25 | 28 |",
        "| 4 | Smoothie, whey, berries | 420 | 35 | 45 | 12 |",
        "| 5 | Toast with almond butter, apple | 450 | 14 | 55 | 20 |",
        "| 6 | Cottage cheese, pineapple | 400 | 30 | 35 | 18 |",
        "| 7 | Breakfast burrito | 480 | 28 | 40 | 22 |",
    ])
    output = "\n".join([section.replace("Breakfast", m) for m in
                        ["Breakfast", "Lunch", "Dinner", "Snack"]])
    reasons = detect_degenerate_output(output)
    assert "repeated n-gram loop detected" not in reasons, reasons


def test_genuine_macro_loop_still_rejected():
    """Repeating the same per-option macro phrase ~28 times (the list-style
    degeneration that previously occurred) must STILL be caught even with the
    raised thresholds."""
    loop = ("Option: oatmeal with milk. Calories 400, protein 18 g, "
            "carbs 60 g, fat 8 g.\n" * 28)
    reasons = detect_degenerate_output(loop)
    assert "repeated n-gram loop detected" in reasons
