import re
from typing import Any, Dict, List


def _parse_order_items(
    *,
    detected_items: Any,
    focus_text: str,
    max_items: int,
) -> List[Dict[str, Any]]:
    if isinstance(detected_items, list) and detected_items:
        return [x for x in detected_items if isinstance(x, dict)][:max_items]

    fallback_items: List[Dict[str, Any]] = []
    med_unit_re = re.compile(r"\b\d+(?:\.\d+)?\s*(mg|mcg|g|kg|units|u|ml|mL)\b", re.IGNORECASE)
    route_re = re.compile(r"\b(PO|IV|IM|SC|SQ|SUBQ|SL|PR|TOP|INH)\b", re.IGNORECASE)
    freq_re = re.compile(r"\b(qd|bid|tid|qid|qhs|qod|q\d+h|daily|weekly|monthly|prn)\b", re.IGNORECASE)
    lab_re = re.compile(r"\b(ferritin|liver\s+panel|lft|cmp|bmp|cbc|a1c|hba1c|tsh|lipid|panel|labs?|testing)\b", re.IGNORECASE)
    tentative_re = re.compile(r"\b(consider|considering|may|might|could|if needed|if indicated|discuss|discussion)\b", re.IGNORECASE)

    for raw_line in focus_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        line = re.sub(r"^\s*(?:[-*]|\d+[.)]|\(?[a-zA-Z]\)|[ivxlcdm]+[.)])\s+", "", line, flags=re.IGNORECASE)
        if not line or tentative_re.search(line):
            continue
        is_lab = bool(lab_re.search(line))
        is_med = bool(med_unit_re.search(line) or route_re.search(line) or freq_re.search(line))
        if is_med and not is_lab:
            fallback_items.append(
                {
                    "category": "Medication",
                    "title": line[:120],
                    "need_full_note": False,
                    "use_referral_prompt": False,
                }
            )
        elif is_lab:
            fallback_items.append(
                {
                    "category": "Lab",
                    "title": line[:120],
                    "need_full_note": False,
                    "use_referral_prompt": False,
                }
            )

    return fallback_items[:max_items]


async def _generate_order_requests(
    gen_id: str,
    note_text: str,
    cfg: Dict,
    *,
    order_store,
    extract_plan_section,
    cfg_text,
    get_order_request_llm,
    extract_json_payload,
    format_imaging_request,
    clean_model_output_final,
    clean_model_output_chunk,
    merge_medication_items,
    dedupe_request_items,
) -> None:
    try:
        order_store[gen_id] = {"status": "pending"}

        plan_text = extract_plan_section(note_text)
        focus_text = plan_text or ""

        if not note_text.strip():
            order_store[gen_id] = {
                "status": "error",
                "error": "Missing note content.",
                "items": [],
            }
            return

        max_items = int(cfg.get("order_request_max_items", 8))
        max_items = max(1, min(max_items, 16))

        referral_prompt = ""
        try:
            other_prompts = cfg.get("default_note_user_prompts_other", {}) or {}
            if isinstance(other_prompts, dict):
                referral_prompt = cfg_text(other_prompts.get("referral"))
        except Exception:
            referral_prompt = ""
        system_prompt_other = cfg_text(cfg.get("default_note_system_prompt_other", ""))

        if not focus_text.strip():
            order_store[gen_id] = {"status": "done", "items": []}
            return

        detect_prompt = (
            "Extract orders/referrals explicitly mentioned in the PLAN section.\n"
            "Return STRICT JSON only (no prose, no markdown):\n"
            "{\n"
            "  \"items\": [\n"
            "    {\n"
            "      \"category\": \"Imaging|Lab|Referral|Medication|Procedure|Other\",\n"
            "      \"title\": \"Short label (e.g., PET-CT chest)\",\n"
            "      \"need_full_note\": true|false,\n"
            "      \"use_referral_prompt\": true|false\n"
            "    }\n"
            "  ]\n"
            "}\n"
            f"Rules:\n- Max {max_items} items.\n"
            "- If none, return {\"items\": []}.\n"
            "- Do not invent items. Only include orders explicitly stated or clearly planned.\n"
            "- Medication items: ONLY include meds that are planned to be started, changed, or discontinued.\n"
            "- Exclude tentative language such as consider, may, might, could, if needed, or discuss.\n"
            "- Imaging/Procedure (CT/MRI/PET/Echo/US/etc): category=Imaging or Procedure; need_full_note=true.\n"
            "- Referral: ONLY if the plan explicitly says referral/consult to another service or specialist.\n"
            "- Do NOT label tests/imaging as Referral.\n"
            "- For actual Referral: use_referral_prompt=true and need_full_note=true.\n"
            "- Lab/Medication: need_full_note=false.\n"
            "- Keep titles short.\n\n"
            "PLAN:\n"
            f"{focus_text}\n\n"
            "JSON:\n"
        )

        llm = get_order_request_llm(cfg)
        detect_raw = await llm.collect_completion(
            detect_prompt,
            temperature=0.05,
            max_tokens=500,
            stop=[],
        )
        detect_payload = extract_json_payload(detect_raw) or {}
        detected_items = _parse_order_items(
            detected_items=detect_payload.get("items"),
            focus_text=focus_text,
            max_items=max_items,
        )
        if not detected_items:
            order_store[gen_id] = {"status": "done", "items": []}
            return

        final_items: List[Dict[str, str]] = []
        for raw_item in detected_items[:max_items]:
            category = str(raw_item.get("category") or "Other").strip() or "Other"
            title = str(raw_item.get("title") or "").strip()
            need_full_note = bool(raw_item.get("need_full_note"))
            use_referral_prompt = bool(raw_item.get("use_referral_prompt"))

            if not title:
                continue

            if use_referral_prompt:
                gen_prompt = (
                    "You are writing a referral request letter. Use the system prompt + referral prompt exactly.\n"
                    "Output plain text only.\n\n"
                    "SYSTEM PROMPT:\n"
                    f"{system_prompt_other or 'No system prompt provided.'}\n\n"
                    "REFERRAL PROMPT:\n"
                    f"{referral_prompt or 'No referral prompt provided.'}\n\n"
                    "FULL NOTE:\n"
                    f"{note_text}\n\n"
                )
            else:
                context_block = f"{note_text}" if need_full_note else f"{focus_text}"
                if category.lower() == "medication":
                    gen_prompt = (
                        "Write medication orders in plain text, one medication per line.\n"
                        "Include only meds explicitly planned to be started, changed, or discontinued.\n"
                        "Preferred format: Medication Dose Unit Route Frequency.\n"
                        "If dose/route/frequency are missing, still include the medication name and any available details.\n"
                        "Do not add explanations, durations, or justifications.\n"
                        "Do not include extra sentences.\n\n"
                        f"ITEM: {title}\n"
                        "CONTEXT:\n"
                        f"{context_block}\n\n"
                    )
                elif category.lower() == "lab":
                    gen_prompt = (
                        "Write a concise lab order request in one line. No explanations.\n"
                        "Use only labs explicitly documented in the plan.\n\n"
                        f"ITEM: {title}\n"
                        "CONTEXT:\n"
                        f"{context_block}\n\n"
                    )
                elif category.lower() == "imaging":
                    gen_prompt = (
                        "Write a radiology-ready requisition that is clinically informative and specific.\n"
                        "Output plain text only with these headers exactly:\n"
                        "Study Requested:\n"
                        "Clinical Indication:\n"
                        "Pertinent Findings / History:\n"
                        "Clinical Question to Answer:\n"
                        "Prior Relevant Imaging:\n"
                        "Urgency:\n"
                        "Do not use directive phrasing (no 'Order a').\n"
                        "Use only details supported by the note/context.\n\n"
                        f"ITEM: {title}\n"
                        "FULL NOTE:\n"
                        f"{context_block}\n\n"
                    )
                else:
                    gen_prompt = (
                        "Write a copy-ready requisition request for the specified item.\n"
                        "Do not use directive phrasing like 'Order a'.\n"
                        "Use professional, neutral language and keep it concise.\n"
                        "Output one paragraph only.\n\n"
                        f"ITEM: {title}\n"
                        f"CATEGORY: {category}\n\n"
                        "CONTEXT:\n"
                        f"{context_block}\n\n"
                    )

            max_tokens = 500
            if use_referral_prompt or category.lower() == "imaging":
                max_tokens = 1200
            gen_raw = await llm.collect_completion(
                gen_prompt,
                temperature=0.1,
                max_tokens=max_tokens,
                stop=[],
            )
            if category.lower() == "imaging":
                request_text = format_imaging_request(gen_raw)
            else:
                request_text = clean_model_output_final(gen_raw).strip()
            if not request_text and category.lower() == "medication":
                request_text = title
            if not request_text:
                continue
            final_items.append(
                {
                    "category": category[:32],
                    "title": clean_model_output_chunk(title)[:120],
                    "request": clean_model_output_chunk(request_text),
                }
            )

        order_store[gen_id] = {
            "status": "done",
            "items": dedupe_request_items(merge_medication_items(final_items)),
        }
    except Exception as exc:
        order_store[gen_id] = {
            "status": "error",
            "error": str(exc)[:200],
            "items": [],
        }
