import re
import time
import logging
from typing import Any, Dict, List

_logger = logging.getLogger("note_generator")

from server.core.token_limits import (
    ORDER_DETECT_MAX_TOKENS_DEFAULT,
    ORDER_GEN_MAX_TOKENS_DEFAULT,
    ORDER_GEN_MAX_TOKENS_LONG,
)
from server.core.prompt.policy_v2 import canonical_system_prompt

# Titles like "Chest CT review" are often classified as Other; still use imaging paragraph + post-process.
# Include common spellings (mammography, mammogram) and breast imaging phrases — these are radiology orders,
# not referrals to another physician.
_IMAGING_TITLE_HINT = re.compile(
    r"\b(cta|ctv|ct\b|chest\s+ct|hrct|mri|mr\b|pet|pet-?ct|xr|cxr|x-?ray|radiograph"
    r"|ultrasound|echo|tee|tte|doppler|mammogram|mammography|mammographic|breast\s+mri|breast\s+us|breast\s+ultrasound"
    r"|tomosynthesis|dexa|densitometry|bone\s+density|hsg\b|sis\b|sonohysterogram"
    r"|bone\s+scan|v/?q|angiogram|fluoro"
    r"|imaging|radiology|scan)\b",
    re.IGNORECASE,
)


def _title_suggests_imaging(title: str) -> bool:
    return bool(title and _IMAGING_TITLE_HINT.search(title))


def _coerce_imaging_vs_referral(
    category: str,
    title: str,
    use_referral_prompt: bool,
) -> tuple[str, bool, bool]:
    """
    Radiology/imaging studies must not use the physician-to-physician referral letter prompt.
    If the detector mis-labels a study (e.g. mammography) as Referral, coerce to Imaging.
    """
    cat = (category or "Other").strip() or "Other"
    cat_lower = cat.lower()
    t = (title or "").strip()
    imaging_style = cat_lower in ("imaging", "procedure") or _title_suggests_imaging(t)
    if imaging_style:
        use_referral_prompt = False
        if cat_lower == "referral":
            cat = "Imaging"
            cat_lower = "imaging"
    return cat, use_referral_prompt, imaging_style


def _raw_smells_like_radiology_form(text: str) -> bool:
    """Model emitted legacy requisition headers (often empty)."""
    if not text:
        return False
    t = text.lower()
    return "study requested" in t and "clinical indication" in t


def _order_request_text_unusable(text: str) -> bool:
    s = (text or "").strip()
    if not s:
        return True
    lines = [ln.strip() for ln in s.splitlines() if ln.strip()]
    if not lines:
        return True
    if all(":" in ln and not ln.split(":", 1)[-1].strip() for ln in lines):
        return True
    return False


def _imaging_paragraph_prompt(title: str, context_block: str) -> str:
    return (
        "Do not include a Conflicts section or conflict commentary in this output.\n"
        "Write a single short paragraph for a radiology requisition or procedure booking.\n"
        "Output a single flowing paragraph (no labeled form fields such as 'Clinical Indication:').\n"
        "FORBIDDEN: do not print the words 'Study Requested', 'Clinical Indication', "
        "'Pertinent Findings', 'Clinical Question', 'Prior Relevant Imaging', or 'Urgency' as labels or line starters.\n"
        "In one paragraph, briefly cover: (1) the clinical presentation or reason for the test, "
        "(2) why this study or procedure is needed now, and (3) any specific question for the "
        "radiologist or proceduralist if relevant; if none, omit that part.\n"
        "Use only information supported by the note. Do not invent findings or history.\n"
        "Do not use directive phrasing (e.g. 'Order a CT'). Use neutral clinical language.\n"
        "Aim for roughly 3–6 sentences. Start with a clinical sentence (e.g. the patient presentation).\n\n"
        f"ITEM: {title}\n"
        "FULL NOTE:\n"
        f"{context_block}\n\n"
    )


def _imaging_retry_prompt(title: str, context_block: str) -> str:
    return (
        "Do not include a Conflicts section or conflict commentary in this output.\n"
        "Your previous output was invalid. Output ONLY one paragraph of prose (3–6 sentences).\n"
        "Do not use ANY form labels, section headers, colons on their own line, or bullet lists.\n"
        "Never write 'Study Requested:', 'Clinical Indication:', 'Urgency:', or similar.\n"
        "Describe the patient context, why imaging is needed, and any question for radiology, using only the note.\n\n"
        f"ITEM: {title}\n"
        "FULL NOTE:\n"
        f"{context_block}\n\n"
    )


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
    imaging_re = _IMAGING_TITLE_HINT  # reuse existing imaging hint regex
    referral_re = re.compile(r"\b(refer|referral|consult|cardiology|dermatology|orthopedics|neurology|psychiatry|oncology|endocrinology|gastroenterology|nephrology|pulmonology|rheumatology|urology|ophthalmology|otolaryngology|ENT|plastic\s+surgery|general\s+surgery|vascular\s+surgery)\b", re.IGNORECASE)
    procedure_re = re.compile(r"\b(endoscopy|colonoscopy|bronchoscopy|biopsy|catheterization|catheter|surgery|operation|ablation|shunt|stent|lithotripsy|paracentesis|thoracentesis|lumbar\s+puncture|lp\s+puncture)\b", re.IGNORECASE)

    for raw_line in focus_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        line = re.sub(r"^\s*(?:[-*]|\d+[.)]|\(?[a-zA-Z]\)|[ivxlcdm]+[.)])\s+", "", line, flags=re.IGNORECASE)
        if not line or tentative_re.search(line):
            continue
        is_lab = bool(lab_re.search(line))
        is_med = bool(med_unit_re.search(line) or route_re.search(line) or freq_re.search(line))
        is_imaging = bool(imaging_re.search(line))
        is_referral = bool(referral_re.search(line))
        is_procedure = bool(procedure_re.search(line))
        if is_med and not is_lab:
            fallback_items.append(
                {
                    "category": "Medication",
                    "title": line[:120],
                    "need_full_note": False,
                    "use_referral_prompt": False,
                    "used_fallback_extraction": True,
                }
            )
        elif is_lab:
            fallback_items.append(
                {
                    "category": "Lab",
                    "title": line[:120],
                    "need_full_note": False,
                    "use_referral_prompt": False,
                    "used_fallback_extraction": True,
                }
            )
        elif is_imaging:
            fallback_items.append(
                {
                    "category": "Imaging",
                    "title": line[:120],
                    "need_full_note": True,
                    "use_referral_prompt": False,
                    "used_fallback_extraction": True,
                }
            )
        elif is_referral:
            fallback_items.append(
                {
                    "category": "Referral",
                    "title": line[:120],
                    "need_full_note": True,
                    "use_referral_prompt": True,
                    "used_fallback_extraction": True,
                }
            )
        elif is_procedure:
            fallback_items.append(
                {
                    "category": "Procedure",
                    "title": line[:120],
                    "need_full_note": True,
                    "use_referral_prompt": False,
                    "used_fallback_extraction": True,
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
    strip_conflicts_section,
    cfg_text,
    get_order_request_llm,
    extract_json_payload,
    format_imaging_request,
    clean_model_output_final,
    clean_model_output_chunk,
    merge_medication_items,
    dedupe_request_items,
) -> None:
    t0 = time.time()
    _logger.info("[ORDER] gen_id=%s START (note_len=%d)", gen_id, len(note_text))
    try:
        order_store[gen_id] = {"status": "pending"}

        note_body = strip_conflicts_section(note_text)
        plan_text = extract_plan_section(note_body)
        focus_text = plan_text or ""

        if not note_body.strip():
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
        system_prompt_other = canonical_system_prompt(
            cfg.get("default_note_system_prompt")
        )

        if not focus_text.strip():
            order_store[gen_id] = {"status": "done", "items": []}
            return

        detect_max = int(cfg.get("order_detect_max_tokens", ORDER_DETECT_MAX_TOKENS_DEFAULT))
        detect_max = max(256, min(detect_max, 8192))

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
            "- Imaging vs procedure: use Imaging for radiology (CT/MRI/PET/US/x-ray/mammography/DEXA/nuclear/echo). "
            "Use Procedure for endoscopy, biopsy, OR, cardiac cath, etc.\n"
            "- Imaging/Procedure: category=Imaging or Procedure; need_full_note=true.\n"
            "- Referral: ONLY for consult/referral to another clinician or specialty service (e.g. cardiology, "
            "dermatology, orthopedics) when the intent is evaluation or management by that specialty — NOT for "
            "ordering a radiology study.\n"
            "- Mammography, breast imaging, CT, MRI, ultrasound, PET, x-ray, DEXA, etc. are ALWAYS category=Imaging; "
            "use_referral_prompt=false. They are imaging requisitions, not referral letters.\n"
            "- Do NOT label tests/imaging as Referral.\n"
            "- For actual Referral: use_referral_prompt=true and need_full_note=true.\n"
            "- Lab/Medication: need_full_note=false.\n"
            "- Keep titles short.\n\n"
            "PLAN:\n"
            f"{focus_text}\n\n"
            "JSON:\n"
        )

        llm = get_order_request_llm(cfg)
        _logger.info("[ORDER] gen_id=%s DETECT START (prompt_len=%d, max_tokens=%d)", gen_id, len(detect_prompt), detect_max)
        t_detect = time.time()
        detect_raw = await llm.collect_completion(
            detect_prompt,
            temperature=0.05,
            max_tokens=detect_max,
            stop=[],
        )
        _logger.info("[ORDER] gen_id=%s DETECT DONE (%.2fs, output_len=%d)", gen_id, time.time() - t_detect, len(detect_raw))
        detect_payload = extract_json_payload(detect_raw) or {}
        detected_items = _parse_order_items(
            detected_items=detect_payload.get("items"),
            focus_text=focus_text,
            max_items=max_items,
        )
        _logger.info("[ORDER] gen_id=%s DETECTED %d items: %s", gen_id, len(detected_items),
                     [(d.get("title","?"), d.get("category","?")) for d in detected_items])
        if not detected_items:
            order_store[gen_id] = {"status": "done", "items": []}
            _logger.info("[ORDER] gen_id=%s DONE (no items, total=%.2fs)", gen_id, time.time() - t0)
            return

        final_items: List[Dict[str, str]] = []
        t_gen_start = time.time()
        item_num = 0
        for raw_item in detected_items[:max_items]:
            item_num += 1
            category = str(raw_item.get("category") or "Other").strip() or "Other"
            title = str(raw_item.get("title") or "").strip()
            need_full_note = bool(raw_item.get("need_full_note"))
            use_referral_prompt = bool(raw_item.get("use_referral_prompt"))

            if not title:
                continue

            category, use_referral_prompt, imaging_style = _coerce_imaging_vs_referral(
                category, title, use_referral_prompt
            )
            cat_lower = category.lower()

            _no_conflicts_rule = (
                "Do not include a Conflicts section, conflict list, or verification commentary in this output.\n"
            )
            if use_referral_prompt:
                gen_prompt = (
                    "You are writing a referral request letter. Use the system prompt + referral prompt exactly.\n"
                    "Output plain text only.\n"
                    f"{_no_conflicts_rule}\n\n"
                    "SYSTEM PROMPT:\n"
                    f"{system_prompt_other or 'No system prompt provided.'}\n\n"
                    "REFERRAL PROMPT:\n"
                    f"{referral_prompt or 'No referral prompt provided.'}\n\n"
                    "FULL NOTE:\n"
                    f"{note_body}\n\n"
                )
            else:
                context_block = f"{note_body}" if need_full_note else f"{focus_text}"
                if imaging_style:
                    context_block = note_body

                if cat_lower == "medication":
                    gen_prompt = (
                        f"{_no_conflicts_rule}"
                        "Write medication orders, one medication per line.\n"
                        "Include only meds explicitly planned to be started, changed, or discontinued.\n"
                        "Preferred format: Medication Dose Unit Route Frequency.\n"
                        "If dose/route/frequency are missing, still include the medication name and any available details.\n"
                        "Do not add explanations, durations, or justifications.\n"
                        "Do not include extra sentences.\n\n"
                        f"ITEM: {title}\n"
                        "CONTEXT:\n"
                        f"{context_block}\n\n"
                    )
                elif cat_lower == "lab":
                    gen_prompt = (
                        f"{_no_conflicts_rule}"
                        "Write a concise lab order request in one line. No explanations.\n"
                        "Use only labs explicitly documented in the plan.\n\n"
                        f"ITEM: {title}\n"
                        "CONTEXT:\n"
                        f"{context_block}\n\n"
                    )
                elif imaging_style:
                    gen_prompt = _imaging_paragraph_prompt(title, context_block)
                else:
                    gen_prompt = (
                        f"{_no_conflicts_rule}"
                        "Write a copy-ready requisition request for the specified item.\n"
                        "Do not use directive phrasing like 'Order a'.\n"
                        "Use professional, neutral language and keep it concise.\n"
                        "Output one paragraph only.\n\n"
                        f"ITEM: {title}\n"
                        f"CATEGORY: {category}\n\n"
                        "CONTEXT:\n"
                        f"{context_block}\n\n"
                    )

            gen_default = int(cfg.get("order_gen_max_tokens_default", ORDER_GEN_MAX_TOKENS_DEFAULT))
            gen_long = int(cfg.get("order_gen_max_tokens_long", ORDER_GEN_MAX_TOKENS_LONG))
            gen_default = max(256, min(gen_default, 8192))
            gen_long = max(gen_default, min(gen_long, 12288))

            if use_referral_prompt or imaging_style:
                max_tok = gen_long
            else:
                max_tok = gen_default
            _logger.info("[ORDER] gen_id=%s ITEM %d GEN START (title=%r, cat=%s, prompt_len=%d, max_tok=%d)",
                        gen_id, item_num, title[:60], category, len(gen_prompt), max_tok)
            t_item = time.time()
            gen_raw = await llm.collect_completion(
                gen_prompt,
                temperature=0.1,
                max_tokens=max_tok,
                stop=[],
            )
            _logger.info("[ORDER] gen_id=%s ITEM %d GEN DONE (%.2fs, output_len=%d)",
                        gen_id, item_num, time.time() - t_item, len(gen_raw))
            t_clean = time.time()
            interim = clean_model_output_final(gen_raw).strip()
            _logger.info("[ORDER] gen_id=%s ITEM %d clean_model_output_final (%.3fs)", gen_id, item_num, time.time() - t_clean)
            t_sc = time.time()
            interim = strip_conflicts_section(interim)
            _logger.info("[ORDER] gen_id=%s ITEM %d strip_conflicts (%.3fs)", gen_id, item_num, time.time() - t_sc)
            if imaging_style or _raw_smells_like_radiology_form(interim):
                t_fmt = time.time()
                request_text = format_imaging_request(gen_raw)
                _logger.info("[ORDER] gen_id=%s ITEM %d format_imaging (%.3fs)", gen_id, item_num, time.time() - t_fmt)
                request_text = strip_conflicts_section(request_text)
            else:
                request_text = interim
            _logger.info("[ORDER] gen_id=%s ITEM %d POST_PROCESS (%.3fs)", gen_id, item_num, time.time() - t_clean)

            if (
                not use_referral_prompt
                and imaging_style
                and _order_request_text_unusable(request_text)
            ):
                _logger.info("[ORDER] gen_id=%s ITEM %d RETRY START (unusable output)", gen_id, item_num)
                t_retry = time.time()
                gen_raw_retry = await llm.collect_completion(
                    _imaging_retry_prompt(title, note_body),
                    temperature=0.05,
                    max_tokens=max_tok,
                    stop=[],
                )
                _logger.info("[ORDER] gen_id=%s ITEM %d RETRY DONE (%.2fs, output_len=%d)",
                            gen_id, item_num, time.time() - t_retry, len(gen_raw_retry))
                request_text = format_imaging_request(gen_raw_retry)

            if imaging_style and _order_request_text_unusable(request_text):
                request_text = (
                    f"Imaging / procedure request: {title}. "
                    "See the full encounter note above for clinical context; "
                    "complete the requisition narrative from the documentation as needed."
                )

            if not request_text and cat_lower == "medication":
                request_text = title
            if not request_text:
                continue
            request_text = strip_conflicts_section(request_text)
            if not request_text:
                continue
            final_items.append(
                {
                    "category": category[:32],
                    "title": clean_model_output_chunk(title)[:120],
                    "request": clean_model_output_chunk(request_text),
                }
            )
            _logger.info("[ORDER] gen_id=%s ITEM %d APPENDED (total_items=%d, elapsed=%.2fs)",
                        gen_id, item_num, len(final_items), time.time() - t0)

        order_store[gen_id] = {
            "status": "done",
            "items": dedupe_request_items(merge_medication_items(final_items)),
        }
        _logger.info("[ORDER] gen_id=%s DONE (items=%d, total=%.2fs, gen_loop=%.2fs)",
                    gen_id, len(final_items), time.time() - t0, time.time() - t_gen_start)
    except Exception as exc:
        _logger.exception("[ORDER] gen_id=%s ERROR (%.2fs): %s", gen_id, time.time() - t0, exc)
        order_store[gen_id] = {
            "status": "error",
            "error": str(exc)[:200],
            "items": [],
        }
