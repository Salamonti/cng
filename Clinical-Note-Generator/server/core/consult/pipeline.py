import asyncio
import re
from typing import Any, Dict, List, Optional, Tuple


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
    rag_client_from_cfg,
    get_rag_comment_llm,
    normalize_reference_items,
    clean_model_output_final,
) -> None:
    try:
        consult_store[gen_id] = {"status": "pending"}

        imp = ""
        plan = ""
        m_imp = re.search(r"(?im)^\s*Impression\s*:\s*(.+?)(?:\n\S|\Z)", note_text, flags=re.DOTALL)
        if m_imp:
            imp = m_imp.group(1).strip()
        m_plan = re.search(r"(?im)^\s*Plan\s*:\s*(.+?)(?:\n\S|\Z)", note_text, flags=re.DOTALL)
        if m_plan:
            plan = m_plan.group(1).strip()

        focus = ""
        used_sections: List[str] = []
        raw_focus_source = ""
        confirmed_markers = cfg.get("consult_confirmed_markers", ["confirmed", "biopsy", "pathology", "definitive"])
        ruledout_markers = cfg.get("consult_ruledout_markers", ["ruled out", "excluded", "negative for", "not consistent with"])
        confirmed_statements = extract_marker_sentences(f"{imp}\n{plan}", confirmed_markers)
        ruledout_statements = extract_marker_sentences(f"{imp}\n{plan}", ruledout_markers)

        if strategy == "full_note":
            focus = rag_tail_window(note_text, max_tokens=500, min_tokens=300)
            used_sections = ["Tail Window"]
        elif strategy == "llm_query":
            rag_llm = get_rag_comment_llm(cfg)
            query_prompt = (
                "Generate a short search query for clinical evidence retrieval.\n"
                "Return a single line (8-20 words) capturing main diagnoses, key symptoms, and key tests.\n"
                "Do not include quotes or extra text.\n\n"
                f"NOTE:\n{note_text}\n\n"
                "QUERY:\n"
            )
            query_text = await rag_llm.collect_completion(
                query_prompt,
                temperature=0.12,
                max_tokens=80,
                stop=[],
            )
            focus = clean_model_output_final(query_text).strip()
            used_sections = ["LLM Query"]
        else:
            focus, used_sections = extract_focus_sections(note_text)

        if not focus.strip():
            focus = fallback_focus_from_note(note_text)
            used_sections = ["Heuristic Fallback"]

        raw_focus_source = focus
        if not focus.strip():
            focus = rag_tail_window(note_text, max_tokens=300, min_tokens=140)
            used_sections = ["Tail Window Fallback"]

        if not focus.strip():
            consult_store[gen_id] = {
                "status": "error",
                "error": "Unable to derive focus for RAG query.",
            }
            return

        focus_summary = focus
        ctx = ""
        norm_refs: List[Dict[str, Any]] = []
        used: Dict[str, Any] = {}

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
            specialty_hint = (cfg.get("consult_default_specialty") or "").strip() or None

            async def _do():
                return await rag.query(
                    focus,
                    top_k=requested_top_k,
                    include_keywords=rag_kws if rag_kws else None,
                    date_from="2018-01-01",
                    specialty=specialty_hint,
                )

            ctx, rag_refs, used = await asyncio.wait_for(_do(), timeout=rag_timeout)
            used["requested_top_k"] = requested_top_k
            norm_refs = _extract_references(
                (rag_refs or [])[:requested_top_k],
                cap=requested_top_k,
                normalize_reference_items=normalize_reference_items,
            )
        except Exception as exc:
            consult_store[gen_id] = {
                "status": "error",
                "error": str(exc)[:160],
            }
            return

        if not ctx.strip():
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

        prompt = (
            "You are a senior consultant writing an evidence-grounded consult addendum for this note.\n"
            "Use ONLY Evidence Context. Do not invent facts or cite outside knowledge.\n"
            "If evidence is limited, provide best-effort guidance with confidence qualifiers instead of refusing.\n"
            "Respect confirmed diagnoses and ruled-out conditions from the original note.\n\n"
            "OUTPUT REQUIREMENTS (plain text only):\n"
            "- Target about 350-500 tokens (hard cap is system-side).\n"
            "- Keep concise and clinically actionable.\n"
            "- Use clear section headers exactly as below.\n\n"
            "Sections:\n"
            "1) Differential to Consider (ranked, brief rationale)\n"
            "2) Workup to Add Now\n"
            "3) Management Adjustments to Consider\n"
            "4) Safety / Red Flags\n"
            "5) What Is Already Appropriate in Current Plan\n\n"
            "Rules:\n"
            "- Every recommendation must be traceable to Evidence Context.\n"
            "- Mark uncertain items as low confidence.\n"
            "- Do not repeat the same point across sections.\n"
            "- Avoid generic textbook phrasing.\n\n"
            f"Original Note Excerpt:\n{note_excerpt}\n\n"
            f"Confirmed / Ruled Statements:\n{assertions_text}\n\n"
            f"Evidence Context:\n{ctx}\n\n"
            f"Focus Summary:\n{focus_summary}\n\n"
            "Comment:\n"
        )

        rag_llm = get_rag_comment_llm(cfg)
        consult_temp = float(cfg.get("consult_comment_temperature", 0.4))
        comment_text = await rag_llm.collect_completion(
            prompt,
            temperature=consult_temp,
            max_tokens=int(cfg.get("consult_comment_max_tokens", 700)),
            stop=[],
        )
        comment = clean_model_output_final(comment_text).replace("'''", "").replace('"""', "").strip()

        required_headers = [
            "Differential to Consider",
            "Workup to Add Now",
            "Management Adjustments to Consider",
            "Safety / Red Flags",
            "What Is Already Appropriate in Current Plan",
        ]
        if not all(h.lower() in comment.lower() for h in required_headers[:3]):
            structure_retry_prompt = (
                "Rewrite the following comment using the REQUIRED section headers exactly.\n"
                "Keep content evidence-grounded and concise.\n\n"
                "Required headers:\n"
                "1) Differential to Consider (ranked, brief rationale)\n"
                "2) Workup to Add Now\n"
                "3) Management Adjustments to Consider\n"
                "4) Safety / Red Flags\n"
                "5) What Is Already Appropriate in Current Plan\n\n"
                f"Evidence Context:\n{ctx}\n\n"
                f"Draft Comment:\n{comment}\n\n"
                "Rewritten Comment:\n"
            )
            structure_retry = await rag_llm.collect_completion(
                structure_retry_prompt,
                temperature=consult_temp,
                max_tokens=int(cfg.get("consult_comment_max_tokens", 700)),
                stop=[],
            )
            structure_clean = clean_model_output_final(structure_retry).replace("'''", "").replace('"""', "").strip()
            if structure_clean:
                comment = structure_clean

        if ctx.strip() and "insufficient evidence available" in comment.lower():
            retry_prompt = (
                "Evidence Context is available below.\n"
                "Do not refuse. Provide best-effort clinical guidance with explicit confidence qualifiers where needed.\n"
                "If evidence is weak, still provide conservative next steps and safety checks.\n\n"
                "Use required headers exactly:\n"
                "1) Differential to Consider (ranked, brief rationale)\n"
                "2) Workup to Add Now\n"
                "3) Management Adjustments to Consider\n"
                "4) Safety / Red Flags\n"
                "5) What Is Already Appropriate in Current Plan\n\n"
                f"Original Note Excerpt:\n{note_excerpt}\n\n"
                f"Confirmed / Ruled Statements:\n{assertions_text}\n\n"
                f"Evidence Context:\n{ctx}\n\n"
                f"Focus Summary:\n{focus_summary}\n\n"
                "Comment:\n"
            )
            retry_text = await rag_llm.collect_completion(
                retry_prompt,
                temperature=consult_temp,
                max_tokens=int(cfg.get("consult_comment_max_tokens", 700)),
                stop=[],
            )
            retry_clean = clean_model_output_final(retry_text).replace("'''", "").replace('"""', "").strip()
            if retry_clean and "insufficient evidence available" not in retry_clean.lower():
                comment = retry_clean

        m = (generation_meta.get(gen_id) or {}).copy()
        m.update({
            "consult_refs": norm_refs,
            "consult_used": used,
            "refs": norm_refs,
            "context": ctx,
            "consult_focus_raw": raw_focus_source,
            "consult_focus_summary": focus_summary,
            "consult_focus_sections": used_sections,
            "consult_assertions": {
                "confirmed": confirmed_statements,
                "ruled_out": ruledout_statements,
            },
        })
        generation_meta[gen_id] = m
        consult_store[gen_id] = {"status": "done", "comment": comment, "refs": norm_refs}
    except Exception as exc:
        consult_store[gen_id] = {"status": "error", "error": str(exc)[:200]}
