from typing import Any, Dict, List, Optional


async def _call_rag(*, rag_task) -> Dict[str, Any]:
    if rag_task is None:
        return {}
    try:
        return await rag_task
    except Exception as exc:
        return {"error": str(exc)}


async def _qa_rewrite_with_rag(
    *,
    baseline_text: str,
    qa_question_for_verify: str,
    cfg: Dict[str, Any],
    max_tokens: Optional[int],
    rag_task,
    qa_rewrite_prompt,
    collect_note_output,
    clean_model_output_final,
    append_missed_question,
    qa_source_excerpt: str,
    qa_rewrite_temp: Optional[float],
    qa_enhancement_label: str,
) -> Dict[str, Any]:
    final_text = baseline_text
    rewrite_used = False
    used_filters: Dict[str, Any] = {}
    norm_refs: List[Dict[str, Any]] = []
    full_chunks: List[str] = []
    rag_context_aug = ""
    rag_error: Optional[str] = None

    rag_result = await _call_rag(rag_task=rag_task)
    if rag_result:
        used_filters = rag_result.get("used_filters", {}) or {}
        norm_refs = rag_result.get("norm_refs", []) or []
        full_chunks = rag_result.get("full_chunks", []) or []
        rag_context_aug = rag_result.get("context_aug", "") or ""
        rag_error = rag_result.get("error")
        raw_refs = rag_result.get("refs_raw", []) or []

        if rag_result.get("weak_evidence") and not raw_refs:
            append_missed_question(
                {
                    "ts": __import__("time").time(),
                    "question": qa_source_excerpt,
                    "used_filters": used_filters,
                    "reason": "no_or_weak_evidence",
                }
            )

        min_ctx_chars = int(cfg.get("qa_rag_min_context_chars", 80))
        ctx_chars = len(rag_context_aug.strip())
        sufficient_ctx = ctx_chars >= max(20, min_ctx_chars) and bool(rag_context_aug.strip())

        if sufficient_ctx and not rag_error:
            rewrite_prompt = qa_rewrite_prompt(
                qa_question_for_verify or "",
                baseline_text,
                rag_context_aug,
            )
            rewritten = await collect_note_output(
                rewrite_prompt,
                qa_rewrite_temp,
                max_tokens,
                stop_tokens=[],
            )
            rewritten_clean = clean_model_output_final(rewritten).strip()
            if rewritten_clean and rewritten_clean != baseline_text.strip():
                rewrite_used = True
                final_text = (qa_enhancement_label + rewritten_clean).strip()

        if rag_error and not used_filters.get("error"):
            used_filters["error"] = str(rag_error)[:160]

    return {
        "final_text": final_text,
        "rewrite_used": rewrite_used,
        "used_filters": used_filters,
        "norm_refs": norm_refs,
        "full_chunks": full_chunks,
        "rag_context_aug": rag_context_aug,
        "rag_error": rag_error,
    }
