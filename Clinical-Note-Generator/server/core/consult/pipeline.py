import asyncio
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from server.services.consult_focus_builder import (
    build_consult_focus,
)
from server.services.consult_comment_formatter import (
    build_structured_prompt,
    parse_consult_json,
    is_valid_structured,
)
from server.services.consult_safety_checker import (
    extract_medications_from_note,
    check_red_flags,
    build_safety_context,
)
from server.services.qa_web_search import searx_search

# ---------------------------------------------------------------------------
# In-memory cache for consult comments (fast re-fetch)
# ---------------------------------------------------------------------------
_consult_cache: Dict[str, Dict[str, Any]] = {}
_CACHE_TTL_SEC = 3600  # 1 hour


def _cache_get(gen_id: str) -> Optional[Dict[str, Any]]:
    """Return cached comment if valid, else None."""
    entry = _consult_cache.get(gen_id)
    if entry and (time.time() - entry["_ts"]) < _CACHE_TTL_SEC:
        return entry
    return None


def _cache_set(gen_id: str, data: Dict[str, Any]) -> None:
    """Store comment in cache, purging expired entries."""
    data["_ts"] = time.time()
    _consult_cache[gen_id] = data
    # Purge expired entries on each write to prevent unbounded growth
    now = time.time()
    expired = [
        k for k, v in _consult_cache.items()
        if (now - v.get("_ts", 0)) >= _CACHE_TTL_SEC
    ]
    for k in expired:
        del _consult_cache[k]


def _extract_references(
    raw_refs: List[Dict[str, Any]],
    *,
    cap: int,
    normalize_reference_items,
) -> List[Dict[str, Any]]:
    norm_refs, _ = normalize_reference_items(
        raw_refs or [],
        cap=cap,
        sort_key=lambda x: x.get("score", 0.0),
    )
    return norm_refs


async def _generate_consult_comment(
    gen_id: str,
    note_text: str,
    cfg: Dict,
    *,
    strategy: str = "sections",
    consult_store,
    generation_meta,
    extract_marker_sentences,
    extract_focus_sections,
    fallback_focus_from_note,
    rag_tail_window,
    rag_head_window,
    rag_client_from_cfg,
    get_rag_comment_llm,
    normalize_reference_items,
    clean_model_output_final,
    user_speciality: Optional[str] = None,
) -> None:
    try:
        # Check cache first — instant return on re-fetch
        cached = _cache_get(gen_id)
        if cached:
            consult_store[gen_id] = cached
            return

        consult_store[gen_id] = {"status": "pending"}

        # ---- Use consult_focus_builder for richer section extraction ----
        confirmed_markers = cfg.get("consult_confirmed_markers", ["confirmed", "biopsy", "pathology", "definitive"])
        ruledout_markers = cfg.get("consult_ruledout_markers", ["ruled out", "excluded", "negative for", "not consistent with"])

        # Extract Impression + Plan using the heading-boundary logic from consult_focus_builder
        from server.services.consult_focus_builder import extract_section_by_heading
        imp = extract_section_by_heading(note_text, "Impression", aliases=["Assessment", "Diagnosis", "Impressions"])
        plan = extract_section_by_heading(note_text, "Plan", aliases=["Management", "Recommendations", "Plan of Care", "Treatment Plan"])

        confirmed_statements = extract_marker_sentences(f"{imp}\n{plan}", confirmed_markers)
        ruledout_statements = extract_marker_sentences(f"{imp}\n{plan}", ruledout_markers)

        # Build focus using the new multi-section extractor
        head_text = rag_head_window(note_text, max_tokens=220, min_tokens=60)
        tail_text = rag_tail_window(note_text, max_tokens=450, min_tokens=160)
        fallback_text = fallback_focus_from_note(note_text)

        focus, used_sections = build_consult_focus(
            note_text,
            strategy=strategy,
            head_text=head_text,
            tail_text=tail_text,
            fallback_text=fallback_text,
        )

        raw_focus_source = focus
        focus_summary = focus

        if not focus.strip():
            consult_store[gen_id] = {
                "status": "error",
                "error": "Unable to derive focus for RAG query.",
            }
            return

        ctx = ""
        web_ctx = ""
        norm_refs: List[Dict[str, Any]] = []
        web_refs: List[Dict[str, Any]] = []
        used: Dict[str, Any] = {}

        # ---- Web search config ----
        web_search_enabled = cfg.get("consult_web_search_enabled", True)
        web_search_limit = int(cfg.get("consult_web_search_limit", 5))
        web_search_timeout = float(cfg.get("consult_web_search_timeout_sec", 10.0))

        try:
            rag = rag_client_from_cfg(cfg)
            rag_kws: List[str] = []
            rag_timeout = int(cfg.get("rag_timeout_ms", 25000)) / 1000.0
            focus_words = focus.split()
            focus_word_count = len(focus_words)
            focus_summary_words = int(cfg.get("rag_focus_summary_words", 150))
            consult_cap = max(3, int(cfg.get("rag_consult_top_k_cap", 5)))
            base_top_k = int(cfg.get("rag_top_k", 16))
            requested_top_k = base_top_k
            if focus_word_count >= max(90, focus_summary_words):
                requested_top_k = min(base_top_k, consult_cap)
            requested_top_k = max(3, requested_top_k)
            # Use encounter-level specialty (from payload/profile) instead of config fallback
            specialty_hint = (user_speciality or "").strip() or None
            # Optional ISO date lower bound (e.g. "2018-01-01"). Default: none — same as QA chat.
            # The RAG API drops chunks whose metadata has no parseable date when date_from is set,
            # which often yields empty context while QA (no date filter) still returns hits.
            date_from_opt = (cfg.get("rag_consult_date_from") or "").strip() or None

            async def _query_rag(
                focus_q: str,
                *,
                date_from: Optional[str],
                spec: Optional[str],
                top_k: int,
            ) -> Tuple[str, List[Dict[str, Any]], Dict[str, Any]]:
                async def _do():
                    return await rag.query(
                        focus_q,
                        top_k=top_k,
                        include_keywords=rag_kws if rag_kws else None,
                        date_from=date_from,
                        specialty=spec,
                    )

                return await asyncio.wait_for(_do(), timeout=rag_timeout)

            ctx, rag_refs, used = await _query_rag(
                focus, date_from=date_from_opt, spec=specialty_hint, top_k=requested_top_k
            )

            # --- Web search in parallel ---
            web_ctx = ""
            web_refs = []
            if web_search_enabled:
                try:
                    web_query = " ".join(focus.split()[:50])  # first ~50 words as query
                    web_results = await asyncio.wait_for(
                        searx_search(web_query, limit=web_search_limit),
                        timeout=web_search_timeout,
                    )
                    ctx_snippets: List[str] = []
                    for i, r in enumerate(web_results, 1):
                        title = (r.get("title") or "").strip()[:120]
                        url = (r.get("url") or "").strip()[:200]
                        snippet = (r.get("content") or r.get("snippet") or "").strip()[:500]
                        if snippet:
                            ctx_snippets.append(f"[WEB-{i}] {title}\nURL: {url}\n{snippet}")
                            web_refs.append({"source": "web", "index": i, "title": title, "url": url})
                    if ctx_snippets:
                        web_ctx = "\n\n".join(ctx_snippets)
                except asyncio.TimeoutError:
                    used["web_search"] = "timeout"
                except Exception as exc:
                    used["web_search_error"] = str(exc)[:120]

            used["requested_top_k"] = requested_top_k
            retries: List[str] = []

            if not (ctx or "").strip() and date_from_opt:
                ctx, rag_refs, used = await _query_rag(
                    focus, date_from=None, spec=specialty_hint, top_k=requested_top_k
                )
                retries.append("dropped_date_from")

            if not (ctx or "").strip() and specialty_hint:
                ctx, rag_refs, used = await _query_rag(
                    focus, date_from=None, spec=None, top_k=min(max(requested_top_k * 2, 8), base_top_k)
                )
                retries.append("dropped_specialty")

            if not (ctx or "").strip():
                fb = fallback_focus_from_note(note_text)
                if fb.strip() and fb.strip() != focus.strip():
                    ctx, rag_refs, used = await _query_rag(
                        fb, date_from=None, spec=None, top_k=base_top_k
                    )
                    retries.append("fallback_focus")

            if not (ctx or "").strip():
                short_q = " ".join((note_text or "").split()[:120])
                if short_q.strip():
                    ctx, rag_refs, used = await _query_rag(
                        short_q, date_from=None, spec=None, top_k=base_top_k
                    )
                    retries.append("short_query")

            if retries:
                used["consult_rag_retries"] = retries

            # Merge RAG + web evidence context after all retries
            full_ctx = ctx or ""
            if web_ctx.strip():
                separator = "\n\n=== Web Search Evidence ===\n"
                full_ctx = full_ctx + separator + web_ctx

            norm_refs = _extract_references(
                (rag_refs or [])[: max(requested_top_k, 8)],
                cap=max(requested_top_k, 8),
                normalize_reference_items=normalize_reference_items,
            )
        except Exception as exc:
            consult_store[gen_id] = {
                "status": "error",
                "error": str(exc)[:160],
            }
            return

        if not full_ctx.strip() and not web_ctx.strip():
            consult_store[gen_id] = {
                "status": "error",
                "error": "No evidence returned for the RAG query.",
            }
            return

        note_excerpt_parts: List[str] = []
        if imp:
            note_excerpt_parts.append(f"Impression:\n{imp}")
        if plan:
            note_excerpt_parts.append(f"Plan:\n{plan}")
        note_excerpt = "\n\n".join(note_excerpt_parts) or raw_focus_source

        assertions_lines: List[str] = []
        if confirmed_statements:
            assertions_lines.append("Confirmed findings/diagnoses:")
            assertions_lines.extend(f"- {s}" for s in confirmed_statements)
        if ruledout_statements:
            assertions_lines.append("Ruled-out or excluded items:")
            assertions_lines.extend(f"- {s}" for s in ruledout_statements)
        assertions_text = "\n".join(assertions_lines) if assertions_lines else "No explicit confirmed or ruled-out statements were identified in the note."

        # --- Phase 4: Medication safety + red flags + guideline checks ---
        medications = extract_medications_from_note(note_text)
        red_flags = check_red_flags(note_text)
        safety_context = build_safety_context(note_text, medications, red_flags)

        # Inject safety context into evidence for the LLM
        enhanced_evidence = full_ctx
        if safety_context.strip():
            enhanced_evidence = full_ctx + "\n\n" + safety_context

        prompt = build_structured_prompt(
            note_excerpt=note_excerpt,
            assertions_text=assertions_text,
            evidence_context=enhanced_evidence,
            focus_summary=focus_summary,
        )

        rag_llm = get_rag_comment_llm(cfg)
        consult_temp = float(cfg.get("consult_comment_temperature", 0.4))
        comment_text = await rag_llm.collect_completion(
            prompt,
            temperature=consult_temp,
            max_tokens=int(cfg.get("consult_comment_max_tokens", 8196)),
            stop=[],
        )
        raw_comment = clean_model_output_final(comment_text).replace("'''", "").replace('"""', "").strip()

        # Parse as structured JSON; fall back to raw text
        parsed = parse_consult_json(raw_comment)
        comment = raw_comment  # always store raw for display
        structured_comment = parsed if isinstance(parsed, dict) and "raw" not in parsed else None

        # If not valid structured output, retry with stricter prompt
        if not structured_comment or not is_valid_structured(parsed):
            retry_prompt = (
                "The previous output was not valid JSON matching the required schema.\n"
                "You MUST output ONLY valid JSON — no markdown, no extra text.\n"
                "Include ALL required fields: differential_diagnosis, recommended_workup, "
                "management_adjustments, safety_alerts, evidence_summary.\n\n"
                f"Evidence Context:\n{enhanced_evidence}\n\n"
                f"Focus Summary:\n{focus_summary}\n\n"
                "Now output ONLY valid JSON:"
            )
            retry_text = await rag_llm.collect_completion(
                retry_prompt,
                temperature=0.2,  # lower temp for structured retry
                max_tokens=int(cfg.get("consult_comment_max_tokens", 8196)),
                stop=[],
            )
            retry_raw = clean_model_output_final(retry_text).replace("'''", "").replace('"""', "").strip()
            retry_parsed = parse_consult_json(retry_raw)
            if isinstance(retry_parsed, dict) and "raw" not in retry_parsed and is_valid_structured(retry_parsed):
                structured_comment = retry_parsed
                comment = retry_raw

        # If still no evidence/insufficient, try one more time
        if full_ctx.strip() and (
            not structured_comment
            or "insufficient evidence" in (structured_comment.get("evidence_summary", {}).get("evidence_quality", "") or "")
        ):
            evidence_retry_prompt = build_structured_prompt(
                note_excerpt=note_excerpt,
                assertions_text=assertions_text,
                evidence_context=f"{enhanced_evidence}\n\nIMPORTANT: Evidence IS available. Do NOT say insufficient. "
                                "Provide best-effort clinical guidance with explicit confidence qualifiers.",
                focus_summary=focus_summary,
            )
            evidence_retry = await rag_llm.collect_completion(
                evidence_retry_prompt,
                temperature=0.25,
                max_tokens=int(cfg.get("consult_comment_max_tokens", 8196)),
                stop=[],
            )
            evidence_raw = clean_model_output_final(evidence_retry).replace("'''", "").replace('"""', "").strip()
            evidence_parsed = parse_consult_json(evidence_raw)
            if isinstance(evidence_parsed, dict) and "raw" not in evidence_parsed:
                structured_comment = evidence_parsed
                comment = evidence_raw

        m = (generation_meta.get(gen_id) or {}).copy()
        m.update({
            "consult_refs": norm_refs,
            "consult_web_refs": web_refs,
            "consult_used": used,
            "refs": norm_refs,
            "context": full_ctx,
            "consult_focus_raw": raw_focus_source,
            "consult_focus_summary": focus_summary,
            "consult_focus_sections": used_sections,
            "consult_structured": structured_comment,
            "consult_safety": {
                "medications": medications,
                "red_flags": red_flags,
            },
            "consult_assertions": {
                "confirmed": confirmed_statements,
                "ruled_out": ruledout_statements,
            },
        })
        generation_meta[gen_id] = m
        consult_store[gen_id] = {
            "status": "done",
            "comment": comment,
            "refs": norm_refs,
            "web_refs": web_refs,
            "structured": structured_comment,
        }
        # Cache for fast re-fetch (polls, repeated views)
        _cache_set(gen_id, consult_store[gen_id].copy())
    except Exception as exc:
        consult_store[gen_id] = {"status": "error", "error": str(exc)[:200]}
