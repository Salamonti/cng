# P9: token cap helpers

from server.core.token_limits import (
    DEFAULT_SECTION_BUDGET_TOKENS,
    MAX_COMPLETION_TOKENS_CAP,
    SECTION_BUDGET_TOKENS,
    clamp_completion_tokens,
    round_to_nearest_1024,
    truncate_text_to_approx_token_budget_str,
)
from server.core.preprocessing.truncation import TokenBudgetTruncator


def test_round_to_nearest_1024():
    assert round_to_nearest_1024(100) == 1024
    assert round_to_nearest_1024(1500) == 1024  # closer to 1024 than to 2048
    assert round_to_nearest_1024(2000) == 2048
    assert round_to_nearest_1024(12288) == 12288


def test_clamp_completion_tokens():
    assert clamp_completion_tokens(500) == 500
    assert clamp_completion_tokens(20000) == MAX_COMPLETION_TOKENS_CAP
    assert clamp_completion_tokens(256) == 256
    assert clamp_completion_tokens(100) == 100  # no high floor; small limits are allowed
    assert clamp_completion_tokens(1) == 1


def test_truncate_inserts_marker():
    words = " ".join([f"w{i}" for i in range(20)])
    out = truncate_text_to_approx_token_budget_str(words, 5)
    assert "[Content truncated" in out
    assert "w0" in out and "w4" in out
    assert "w5" not in out


def test_truncate_noop_when_short():
    t = "one two three"
    assert truncate_text_to_approx_token_budget_str(t, 100) == t


def test_section_budget_defaults_24k():
    assert SECTION_BUDGET_TOKENS == 24 * 1024
    assert DEFAULT_SECTION_BUDGET_TOKENS == SECTION_BUDGET_TOKENS
    truncator = TokenBudgetTruncator({})
    assert truncator.budgets["prior_visits"] == SECTION_BUDGET_TOKENS
    assert truncator.budgets["current_encounter"] == SECTION_BUDGET_TOKENS
