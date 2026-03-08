from typing import AsyncIterator, Callable, List, Optional


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
    async for chunk in note_gen.stream_completion(
        prompt,
        temperature=temperature,
        max_tokens=max_tokens,
        stop=stop_tokens or [],
    ):
        cleaned = clean_chunk(chunk or "")
        if cleaned:
            yield cleaned


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
