"""DreamCision-owned policy for requests sent to shared model servers."""

from __future__ import annotations

import re
from typing import Any, Dict, List, MutableMapping


_PROFILE_LIMITS = {
    "clinical_note": {"max_tokens": 8192, "max_temperature": 0.0},
    "clinical_extraction": {"max_tokens": 4096, "max_temperature": 0.1},
    "ocr": {"max_tokens": 8192, "max_temperature": 0.0},
    "vision": {"max_tokens": 4096, "max_temperature": 0.3},
    "asr": {"max_tokens": 16384, "max_temperature": 0.1},
    "qa": {"max_tokens": 4096, "max_temperature": 0.4},
}

# Server-side repetition detection (vLLM `repetition_detection`).
#
# These thresholds MUST stay consistent with the app-level guard
# (clinical_output_guard._has_repeated_ngram, tuned 2026-08-07 after it
# false-positived the 28-option patient meal plan). Legitimate structured
# clinical output repeats markdown table header + separator rows once per
# section: a full table block repeats ~6-7x across a document. Genuine
# degeneration repeats the same span 15-30+ times.
#
# The previous values ({3..64, min_count: 3}) killed nearly every document
# containing two or more tables: a table separator row is a ~12-token span
# that trivially hits "3 repeats", so patient materials (medication lists,
# meal plans, reports) came back mid-table with finish_reason="repetition".
#
# min_pattern_size=8 ignores the short spans that recur naturally in
# structured prose; min_count=10 sits between the legitimate maximum
# (<=7 table blocks) and the degeneration minimum observed (15-30x).
#
# Belt-and-braces: collect_completion() treats finish_reason="repetition"
# like "length" (raises ClinicalOutputRejected -> guarded retry), so even a
# false-positive kill can never silently reach the user as truncated text.
_REPETITION_DETECTION = {
    "min_pattern_size": 8,
    "max_pattern_size": 64,
    "min_count": 10,
}


def apply_dreamcision_no_thinking(payload: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    """Force thinking off for this request without changing the shared server default."""
    raw_kwargs = payload.get("chat_template_kwargs")
    kwargs: Dict[str, Any] = dict(raw_kwargs) if isinstance(raw_kwargs, dict) else {}
    kwargs["enable_thinking"] = False
    kwargs["thinking"] = False
    kwargs["reasoning_effort"] = "none"
    payload["chat_template_kwargs"] = kwargs
    payload["reasoning_effort"] = "none"
    return payload


def apply_dreamcision_generation_policy(
    payload: MutableMapping[str, Any],
    *,
    profile: str = "clinical_note",
) -> MutableMapping[str, Any]:
    """Apply DreamCision-owned deterministic safety settings to a request.

    Repetition, frequency, and presence penalties are deliberately neutral.
    vLLM's repetition penalty also covers prompt tokens; non-neutral values can
    suppress ordinary clinical vocabulary in a long chart prompt.
    """
    apply_dreamcision_no_thinking(payload)

    payload["repetition_penalty"] = 1.0
    payload["frequency_penalty"] = 0.0
    payload["presence_penalty"] = 0.0
    payload["ignore_eos"] = False
    payload["min_tokens"] = 0
    payload["repetition_detection"] = dict(_REPETITION_DETECTION)

    limits = _PROFILE_LIMITS.get(profile)
    if limits:
        try:
            payload["max_tokens"] = min(
                max(1, int(payload.get("max_tokens", limits["max_tokens"]))),
                int(limits["max_tokens"]),
            )
        except (TypeError, ValueError):
            payload["max_tokens"] = int(limits["max_tokens"])

        try:
            temperature = max(0.0, float(payload.get("temperature", 0.0)))
        except (TypeError, ValueError):
            temperature = 0.0
        payload["temperature"] = min(temperature, float(limits["max_temperature"]))

    return payload


def prompt_to_chat_messages(prompt: str) -> List[Dict[str, str]]:
    """Convert DreamCision's legacy prompt envelope into real chat roles."""
    value = str(prompt or "").strip()
    if not value:
        return [{"role": "user", "content": ""}]

    assistant_marker = "\n\nASSISTANT:"
    if assistant_marker in value:
        value = value.rsplit(assistant_marker, 1)[0].rstrip()

    system_prefix = "SYSTEM:\n"
    user_marker = "\n\nUSER:\n"
    if value.startswith(system_prefix) and user_marker in value:
        system_text, user_text = value[len(system_prefix) :].split(user_marker, 1)
        messages: List[Dict[str, str]] = []
        if system_text.strip():
            messages.append({"role": "system", "content": system_text.strip()})
        messages.append({"role": "user", "content": user_text.strip()})
        return messages

    if value.startswith("USER:\n"):
        value = value[len("USER:\n") :]
    return [{"role": "user", "content": value.strip()}]


def strip_reasoning_markup(text: str) -> str:
    """Remove reasoning wrappers defensively from a model's visible content."""
    value = (text or "").strip()
    if not value:
        return value

    for tag in ("redacted_thinking", "think", "analysis"):
        value = re.sub(
            rf"<{tag}>.*?</{tag}>",
            "",
            value,
            flags=re.IGNORECASE | re.DOTALL,
        ).strip()
        close = f"</{tag}>"
        lower = value.lower()
        if close in lower:
            value = value[lower.rfind(close) + len(close) :].strip()
        if value.lower().startswith(f"<{tag}>"):
            return ""
    return value
