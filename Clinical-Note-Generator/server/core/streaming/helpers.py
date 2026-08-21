import json
from typing import Any, AsyncIterator, Callable, Dict, List, Optional

from server.core.clinical_output_guard import (
    ClinicalOutputRejected,
    build_guard_retry_prompt,
    sanitize_clinical_note,
    validate_clinical_note,
)

# Wire markers. The v8 stream is plain text (media_type text/plain), so control
# messages travel as whole lines the client recognises. NOTE_RETRY tells the
# client to clear the box (attempt 1 failed; attempt 2 is about to type in).
# NOTE_FINAL carries the authoritative outcome as JSON on one line:
#   __NOTE_FINAL__{"text": "...", "salvaged": false, "reasons": []}
# "salvaged": true means both generation attempts failed validation and "text"
# is the last draft, retained for clinician review (never silently discarded).
NOTE_RETRY_MARKER = "__NOTE_RETRY__"
NOTE_FINAL_MARKER = "__NOTE_FINAL__"

_NOTE_END_TOKEN = "END_OF_NOTE"


def build_note_final_marker(*, text: str, salvaged: bool, reasons: List[str]) -> str:
    payload = {
        "text": text,
        "salvaged": bool(salvaged),
        "reasons": [str(r) for r in (reasons or [])],
    }
    return NOTE_FINAL_MARKER + json.dumps(payload, ensure_ascii=False)


def parse_note_final_marker(line: str) -> Optional[Dict[str, Any]]:
    """Parse a __NOTE_FINAL__ line. Returns None for any other line."""
    if not (line or "").startswith(NOTE_FINAL_MARKER):
        return None
    raw = line[len(NOTE_FINAL_MARKER):].strip()
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return {"text": raw, "salvaged": False, "reasons": []}
    if not isinstance(data, dict):
        return None
    return {
        "text": str(data.get("text") or ""),
        "salvaged": bool(data.get("salvaged")),
        "reasons": [str(r) for r in (data.get("reasons") or [])],
    }


async def _stream_response(
    *,
    note_gen,
    prompt: str,
    temperature: Optional[float],
    max_tokens: Optional[int],
    stop_tokens: Optional[List[str]],
    clean_chunk: Callable[[str], str],
) -> AsyncIterator[str]:
    note_text = await note_gen.collect_completion(
        prompt,
        temperature=temperature,
        max_tokens=max_tokens,
        stop=stop_tokens or [],
    )
    cleaned_note = clean_chunk(note_text)
    if cleaned_note:
        yield cleaned_note


def _note_final_reasons_detail(reasons: List[str]) -> str:
    return "; ".join(str(r) for r in reasons if str(r).strip())


class _LineBuffer:
    """Accumulate streamed text and yield complete lines (newline-terminated).

    A partial trailing line is held back until the next chunk completes it,
    so the client never receives a half-line. This is what makes the live
    preview look like the note is being typed, line by line.
    """

    def __init__(self) -> None:
        self._pending = ""

    def add(self, chunk: str) -> List[str]:
        self._pending += str(chunk or "")
        parts = self._pending.split("\n")
        self._pending = parts.pop()
        return parts

    def flush(self) -> str:
        value = self._pending
        self._pending = ""
        return value


async def _stream_response_v8(
    *,
    note_gen,
    prompt: str,
    temperature: Optional[float],
    max_tokens: Optional[int],
    stop_tokens: Optional[List[str]],
    clean_chunk: Callable[[str], str],
) -> AsyncIterator[str]:
    """True line-buffered streaming with the bounded two-attempt guard.

    Yields, in order:
      * live model text, one complete line at a time, so the clinician sees
        the note typing in from the first second (perceived-speed fix);
      * a NOTE_RETRY_MARKER line when attempt 1 fails validation -- the
        client clears the box and watches attempt 2 type in;
      * exactly one NOTE_FINAL_MARKER JSON line at the very end carrying the
        authoritative text (clean_chunk + sanitize_clinical_note, validated)
        plus the salvage flag and rejection reasons.

    A draft rejected by BOTH attempts is salvaged, not discarded: the final
    marker carries the last draft with salvaged=True so the clinician can
    read it, fix the flagged part, and keep it. Only a total failure (no
    draft text at all) raises ClinicalOutputRejected, which the route
    renders as the existing error text.

    The live lines are raw model output (per-line, unvalidated preview);
    the NOTE_FINAL text is authoritative and is what the client puts in the
    box. Validation semantics are unchanged: same sanitize + validate calls
    as the pre-fix generate-then-yield path, same two-attempt budget, same
    greedy retry prompt.
    """
    attempt_prompt = prompt
    last_reasons: List[str] = []
    last_draft = ""
    can_stream = hasattr(note_gen, "stream_completion")

    for attempt in range(2):
        raw_parts: List[str] = []  # raw model chunks; join = full streamed text
        attempt_failed = False
        try:
            if can_stream:
                stream = note_gen.stream_completion(
                    attempt_prompt,
                    temperature=0.0 if attempt else temperature,
                    max_tokens=max_tokens,
                    stop=stop_tokens or [],
                )
                line_buffer = _LineBuffer()
                async for chunk in stream:
                    raw_parts.append(chunk)
                    for line in line_buffer.add(chunk):
                        yield line + "\n"
                # The trailing partial line never saw its newline; emit it
                # newline-terminated so the live preview ends cleanly and the
                # NOTE_FINAL marker that follows stays on its own line. (It is
                # part of the chunks already in raw_parts, so do NOT
                # re-append it.)
                tail = line_buffer.flush()
                if tail:
                    yield tail + "\n"
            else:
                # Defensive fallback for generators without a streaming
                # method (test doubles, exotic backends): collect then yield.
                note_text = await note_gen.collect_completion(
                    attempt_prompt,
                    temperature=0.0 if attempt else temperature,
                    max_tokens=max_tokens,
                    stop=stop_tokens or [],
                )
                raw_parts.append(note_text or "")
                fallback_lines = _LineBuffer()
                for line in fallback_lines.add(note_text or ""):
                    yield line + "\n"
                tail = fallback_lines.flush()
                if tail:
                    yield tail
        except ClinicalOutputRejected as exc:
            # Truncation / degeneration / engine stop: this attempt's text is
            # unusable. Fall through to attempt 2 (or salvage, below).
            # NOTE: ExternalServiceError (model unreachable) is NOT caught here
            # on purpose — it propagates to the route, which owns the
            # collect_completion fallback exactly as before.
            attempt_failed = True
            last_reasons = [str(exc)]
            # Partial streamed text is the salvage candidate; the exception's
            # own draft (set by collect_completion on truncation) wins when
            # nothing was streamed.
            last_draft = "".join(raw_parts) or getattr(exc, "draft", "") or last_draft

        if not attempt_failed:
            raw_text = "".join(raw_parts)
            idx = raw_text.find(_NOTE_END_TOKEN)
            if idx != -1:
                raw_text = raw_text[:idx]
            cleaned = sanitize_clinical_note(attempt_prompt, clean_chunk(raw_text or ""))
            result = validate_clinical_note(attempt_prompt, cleaned)
            if result.accepted:
                yield build_note_final_marker(
                    text=cleaned, salvaged=False, reasons=[]
                )
                return
            last_reasons = [str(r) for r in result.reasons]
            last_draft = cleaned

        if attempt == 0:
            # Attempt 1 failed and already streamed its lines live. Tell the
            # client to clear the box; attempt 2 types in fresh.
            yield NOTE_RETRY_MARKER + "\n"
            attempt_prompt = build_guard_retry_prompt(prompt, last_reasons)

    if last_draft.strip():
        # Salvage: persist the last draft for clinician review instead of
        # discarding a full encounter's dictation. The rejection reasons ride
        # along so the client can explain, in the Conflicts panel, exactly
        # what failed validation.
        yield build_note_final_marker(
            text=last_draft, salvaged=True, reasons=last_reasons
        )
        return

    raise ClinicalOutputRejected(
        "DreamCision rejected two unsafe note drafts and no usable draft text "
        f"was produced. Validation details: {_note_final_reasons_detail(last_reasons) or 'unsafe model output'}",
        draft="",
    )


async def _stream_qa_response(
    *,
    final_text: str,
    chunker: Callable[[str], List[str]],
    clean_chunk: Callable[[str], str],
) -> AsyncIterator[str]:
    for segment in chunker(final_text):
        cleaned_segment = clean_chunk(segment)
        if cleaned_segment:
            yield cleaned_segment
