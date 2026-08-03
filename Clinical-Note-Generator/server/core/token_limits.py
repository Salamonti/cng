# Central completion and source-section budgets.
from __future__ import annotations

from typing import Optional, Tuple

# Clinical drafts should finish well below this ceiling. A bounded completion
# reduces the blast radius of malformed generation while still allowing long
# discharge and admission documents.
#
# WO-1 Part A: this is a third, independent clamp on note-generation
# max_tokens, alongside config.json's default_note_max_tokens and
# llm_request_policy.py's clinical_note profile limit. All three must move
# together -- notes.py's clamp_completion_tokens(raw_max) call uses this cap
# as its default, so leaving it at 4096 silently reduced the 8192-token
# ceiling right back down to 4096 even after the other two were raised.
MAX_COMPLETION_TOKENS_CAP: int = 8 * 1024
DEFAULT_COMPLETION_TOKENS: int = 2 * 1024

# Per chart section (prior / labs-imaging / current): truncate only when above this budget.
SECTION_BUDGET_TOKENS: int = 24 * 1024  # 24576 (~24k tokens)
DEFAULT_SECTION_BUDGET_TOKENS: int = SECTION_BUDGET_TOKENS

# Order / imaging extraction (structured JSON + short requisitions)
ORDER_DETECT_MAX_TOKENS_DEFAULT: int = 1536
ORDER_GEN_MAX_TOKENS_DEFAULT: int = 1024
ORDER_GEN_MAX_TOKENS_LONG: int = 3072  # imaging, procedure, referral letter body


def round_to_nearest_1024(n: int) -> int:
    if n <= 0:
        return 1024
    return max(1024, int(round(n / 1024)) * 1024)


def clamp_completion_tokens(
    n: Optional[int],
    *,
    cap: int = MAX_COMPLETION_TOKENS_CAP,
    floor: int = 1,
) -> int:
    """Clamp **completion** `max_tokens` to [floor, cap].

    This does **not** relate to empty chart fields — those only shorten the prompt. For API
    requests, treat ``max_tokens <= 0`` as omitted and use ``default_note_max_tokens`` instead
    of calling this with zero (see notes route)."""
    try:
        v = int(n) if n is not None else DEFAULT_COMPLETION_TOKENS
    except (TypeError, ValueError):
        v = DEFAULT_COMPLETION_TOKENS
    return max(floor, min(cap, v))


def truncate_text_to_approx_token_budget(text: str, max_tokens: int) -> Tuple[str, bool]:
    """
    Word-split truncation with a visible marker (≈1 word per token, matches legacy notes route).
    Returns (text, was_truncated).
    """
    if max_tokens <= 0:
        return "", True
    words = (text or "").split()
    if len(words) <= max_tokens:
        return text or "", False
    truncated = " ".join(words[:max_tokens])
    return truncated + "\n\n[Content truncated to fit context length...]", True


def truncate_text_to_approx_token_budget_str(text: str, max_tokens: int) -> str:
    """Single-string return for callers that ignore the boolean."""
    out, _ = truncate_text_to_approx_token_budget(text, max_tokens)
    return out
