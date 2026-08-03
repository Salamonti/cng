"""P3-2 regression: TokenBudgetTruncator.enforce_aggregate_budget() -- each
chart section was independently kept within its OWN budget, but nothing
checked the SUM of all three against the model's real context window. A
complex patient with all three sections near their individual maximums
could still exceed it.
"""
from __future__ import annotations

from server.core.preprocessing.truncation import TokenBudgetTruncator


def _cfg(**truncation_overrides):
    trunc = {
        "prior_visits_budget_tokens": 1000,
        "labs_imaging_other_budget_tokens": 1000,
        "current_encounter_budget_tokens": 1000,
    }
    trunc.update(truncation_overrides)
    return {"preprocessing": {"truncation": trunc}}


def _words(n):
    return " ".join(f"word{i}" for i in range(n))


def test_under_aggregate_budget_is_untouched():
    truncator = TokenBudgetTruncator(_cfg(aggregate_budget_tokens=10_000))
    cur, prior, labs = truncator.enforce_aggregate_budget(
        current_encounter=_words(100), prior_visits=_words(100), labs_imaging_other=_words(100)
    )
    assert cur == _words(100)
    assert prior == _words(100)
    assert labs == _words(100)


def test_over_aggregate_budget_trims_historical_sections_not_current_encounter():
    # Each section is independently under its own 1000-token budget (so
    # truncate_section wouldn't touch any of them), but three sections at
    # ~770 tokens each sum to well over a tight aggregate cap.
    truncator = TokenBudgetTruncator(_cfg(aggregate_budget_tokens=1200))
    current = _words(600)  # ~780 est. tokens (1.3x word count)
    prior = _words(600)
    labs = _words(600)

    out_current, out_prior, out_labs = truncator.enforce_aggregate_budget(
        current_encounter=current, prior_visits=prior, labs_imaging_other=labs
    )

    # current_encounter -- today's live dictation -- must survive untouched.
    assert out_current == current
    # The two historical sections must have been trimmed to fit.
    total_after = (
        TokenBudgetTruncator.estimate_tokens(out_current)
        + TokenBudgetTruncator.estimate_tokens(out_prior)
        + TokenBudgetTruncator.estimate_tokens(out_labs)
    )
    assert total_after <= 1200
    assert len(out_prior) < len(prior)
    assert len(out_labs) < len(labs)


def test_aggregate_budget_disabled_when_zero():
    truncator = TokenBudgetTruncator(_cfg(aggregate_budget_tokens=0))
    current, prior, labs = _words(5000), _words(5000), _words(5000)
    out = truncator.enforce_aggregate_budget(
        current_encounter=current, prior_visits=prior, labs_imaging_other=labs
    )
    assert out == (current, prior, labs)


def test_default_aggregate_budget_is_generous_enough_not_to_change_current_production_behavior():
    # Real production config.json sets each of the 3 sections to 12288 --
    # summing to 36864. The default aggregate backstop must not start
    # truncating that unless something is already going badly wrong (e.g.
    # truncate_section's own >80%-removal safety override letting one
    # section balloon well past its nominal budget).
    truncator = TokenBudgetTruncator(_cfg())
    assert truncator.aggregate_budget >= 36_864
