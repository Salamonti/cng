from typing import AsyncIterator, Callable, List, Optional

from server.core.clinical_output_guard import (
    ClinicalOutputRejected,
    build_guard_retry_prompt,
    sanitize_clinical_note,
    validate_clinical_note,
)


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


async def _stream_response_v8(
    *,
    note_gen,
    prompt: str,
    temperature: Optional[float],
    max_tokens: Optional[int],
    stop_tokens: Optional[List[str]],
    clean_chunk: Callable[[str], str],
) -> AsyncIterator[str]:
    """Generate, validate, then return a note so unsafe partial text never streams."""
    attempt_prompt = prompt
    last_reasons: tuple[str, ...] = ()
    last_draft = ""
    for attempt in range(2):
        try:
            note_text = await note_gen.collect_completion(
                attempt_prompt,
                temperature=0.0 if attempt else temperature,
                max_tokens=max_tokens,
                stop=stop_tokens or [],
            )
            cleaned = sanitize_clinical_note(
                attempt_prompt,
                clean_chunk(note_text or ""),
            )
            result = validate_clinical_note(attempt_prompt, cleaned)
            if result.accepted:
                if cleaned:
                    yield cleaned
                return
            last_reasons = result.reasons
            last_draft = cleaned
        except ClinicalOutputRejected as exc:
            last_reasons = (str(exc),)
            last_draft = getattr(exc, "draft", "") or last_draft

        if attempt == 0:
            attempt_prompt = build_guard_retry_prompt(prompt, last_reasons)

    details = "; ".join(last_reasons) or "unsafe model output"
    raise ClinicalOutputRejected(
        "DreamCision rejected two unsafe note drafts. No generated note was accepted. "
        f"Validation details: {details}",
        draft=last_draft,
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
