# C:\Clinical-Note-Generator\server\routes\notes.py
import asyncio
from datetime import date
import time
import re
import json
import os
import csv
import uuid
import threading
from typing import Dict, Optional, Any, List, Tuple
from pathlib import Path
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse, JSONResponse

from services.note_generator_clean import get_simple_note_generator
from services.rag_http_client import RAGHttpClient
from metrics import metrics as global_metrics


router = APIRouter()

# ---------------------------------------------------------------------------
# Simple CSV logger for prompt/output/rating and an in-memory cache
# ---------------------------------------------------------------------------

_cache_lock = threading.Lock()
_generation_cache: Dict[str, Dict[str, str]] = {}

# RAG / metadata stores
_generation_meta: Dict[str, Dict[str, Any]] = {}
_consult_comment_store: Dict[str, Dict[str, Any]] = {}

def _feedback_csv_path() -> str:
    logs_dir = Path(__file__).resolve().parents[1] / "logs"
    os.makedirs(logs_dir, exist_ok=True)
    return str(logs_dir / "notes_feedback.csv")

def _append_feedback_csv(prompt: str, output: str, rating: int) -> None:
    path = _feedback_csv_path()
    new_file = not os.path.exists(path)
    # Only 3 columns per request: prompt, output, rating
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["prompt", "output", "rating"])  # header with only 3 fields
        w.writerow([prompt, output, rating])

# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------
CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "config.json"


def load_config() -> Dict:
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


# Always use llama-server for consistent performance
print("Using llama-server for note generation (efficient, single persistent process)")
note_gen = get_simple_note_generator()

END_MARKER = "__STREAM_END__"
NOTE_STOP_SEQUENCES = [
    "Thank you for involving me in this patient's care.",
    "Thank you for accepting this referral.",
    "Sincerely,",
]
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x09\x0b-\x1f\x7f]")
_FORMAT_SYMBOLS_RE = re.compile(r"[#*=_+\-]{3,}")


def _has_minimum_signal(text: str, *, min_alnum: int) -> bool:
    """Return True if text still contains enough alphanumeric characters to be useful."""
    if not text:
        return False
    return sum(1 for ch in text if ch.isalnum()) >= min_alnum


def _sanitize_chart_text(text: str) -> str:
    """Reduce sequences that can accidentally trip model stop logic."""
    if not text:
        return ""
    cleaned = _CONTROL_CHARS_RE.sub(" ", text)
    cleaned = _FORMAT_SYMBOLS_RE.sub(" ", cleaned)
    cleaned = re.sub(r"\s{3,}", "  ", cleaned)
    cleaned = cleaned.strip()
    return cleaned if _has_minimum_signal(cleaned, min_alnum=10) else text.strip()


def _sanitize_transcription_text(text: str) -> str:
    if not text:
        return ""
    cleaned = _CONTROL_CHARS_RE.sub(" ", text)
    cleaned = re.sub(r"\s{3,}", "  ", cleaned)
    cleaned = cleaned.strip()
    return cleaned if _has_minimum_signal(cleaned, min_alnum=6) else text.strip()


def _logs_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "logs"


def _missed_q_path() -> Path:
    d = _logs_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d / "rag_missed_questions.jsonl"


def _append_missed_question(record: Dict[str, Any]) -> None:
    try:
        p = _missed_q_path()
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass


def truncate_to_context_length_tokens(text: str, max_tokens: int) -> str:
    """
    Truncate text to approximate token count.

    We treat max_tokens as an approximate token limit and use a 1:1
    word-to-token mapping (max_tokens ≈ max_words). This behaves like
    a hard token cap (e.g. 32k) instead of scaling down by 0.75×.
    """
    words = text.split()
    max_words = max_tokens
    if len(words) > max_words:
        truncated = " ".join(words[:max_words])
        return truncated + "\n\n[Content truncated to fit context length...]"
    return text


def _meta_year(meta: Dict[str, Any]) -> int:
    """Extract a four-digit year from common metadata fields."""
    try:
        y = str(meta.get("timestamp") or meta.get("year") or meta.get("date") or "").strip()
        if len(y) >= 4 and y[:4].isdigit():
            return int(y[:4])
    except Exception:
        pass
    return 0


def _first_nonempty(meta: Dict[str, Any], keys: Tuple[str, ...]) -> str:
    for key in keys:
        val = meta.get(key)
        if val:
            text = str(val).strip()
            if text:
                return text
    return ""


def _normalize_reference_items(
    raw_refs: List[Dict[str, Any]],
    *,
    cap: Optional[int] = None,
    sort_key: Optional[Any] = None,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Flatten RAG results into UI-friendly reference entries plus evidence chunks."""
    entries: List[Dict[str, Any]] = []
    for item in raw_refs or []:
        if not isinstance(item, dict):
            continue
        md = dict(item.get("metadata") or {})
        # Prefer the shorter summary field; fall back to raw text if missing.
        text = str(item.get("summary") or item.get("text") or "")
        if len(text) > 280:
            cutoff = text[:280]
            last_space = cutoff.rfind(" ")
            if last_space > 220:
                cutoff = cutoff[:last_space]
            text = cutoff.strip() + " ..."
        title = _first_nonempty(
            md,
            (
                "title",
                "guideline_type",
                "lab_test",
                "paper_title",
                "section",
                "doc_id",
                "document_id",
                "id",
            ),
        )
        source = _first_nonempty(md, ("source", "society", "publisher"))
        section = _first_nonempty(md, ("section", "heading", "chapter"))
        link = _first_nonempty(md, ("link", "url"))
        year = _meta_year(md)
        try:
            score = float(item.get("score", 0.0))
        except Exception:
            score = 0.0
        doc_id = _first_nonempty(md, ("doc_id", "document_id", "id")) or str(item.get("id") or "").strip()

        if not title:
            if doc_id:
                title = doc_id
            elif source:
                title = source
            else:
                title = "Document"

        entry = {
            "title": title,
            "source": source,
            "section": section,
            "link": link,
            "year": year,
            "score": score,
            "doc_id": doc_id,
            "_text": text,
        }
        entries.append(entry)

    if sort_key is not None:
        entries.sort(key=sort_key, reverse=True)
    if cap is not None and cap >= 0:
        entries = entries[:cap]

    full_chunks: List[str] = []
    for idx, entry in enumerate(entries, start=1):
        text = entry.pop("_text", "")
        if text:
            full_chunks.append(text)
        entry["index"] = idx
    return entries, full_chunks

# ---------------------------------------------------------------------------
# Stream-safe and final cleaners
# ---------------------------------------------------------------------------

def clean_model_output_chunk(chunk: str) -> str:
    """Minimal, stream-safe cleaner: preserves spaces/newlines exactly.
    Only removes NUL characters which sometimes appear in streams.
    """
    if not chunk:
        return ""
    s = chunk.replace("\x00", "")


    # Post-processing for display: strip formatting markers and note tags
    # Keep whitespace as-is; do not trim newlines.
    try:
        s = s.replace("<note>", "").replace("</note>", "")
        # Only remove standalone formatting markers, not content markers
        # s = s.replace("#", "").replace("*", "")  # DISABLED - was breaking XML content
        # Do NOT remove the backend stream terminator here; clients rely on it to detect stream end.
    except Exception:
        pass
    return s


def clean_model_output_final(text: str) -> str:
    """Conservative cleanup applied once at the end of streaming (optional).
    Keep paragraph structure; do not remove legitimate spaces/newlines.
    """
    if not text:
        return ""
    cleaned = text.replace("\x00", "")

    # Remove anything before the actual note content starts
    # GUARD: Only trim if patterns appear in first 200 chars to avoid cutting legitimate content
    first_200 = cleaned[:200]
    if "Patient ID" in first_200:
        # Split at "Patient ID" and keep everything from that point
        parts = cleaned.split("Patient ID", 1)
        if len(parts) > 1:
            cleaned = "Patient ID" + parts[1]
    elif re.search(r'^ID\s*:', first_200, re.MULTILINE):
        # Look for standalone "ID" at the beginning of a line in first 200 chars only
        match = re.search(r'^ID\s*:', cleaned, re.MULTILINE)
        if match and match.start() < 200:
            cleaned = cleaned[match.start():]

    # Remove a single leading/trailing code fence if leaked
    cleaned = re.sub(r"^\s*```[a-zA-Z0-9_-]*\s*\n", "", cleaned)
    cleaned = re.sub(r"\n```+\s*$", "", cleaned)

    # Remove specific leaked XML-ish wrappers (case-insensitive)
    cleaned = re.sub(r"</?(?:transcription_data|chart_data)>", "", cleaned, flags=re.IGNORECASE)

    # Remove explicit note tags and simple formatting markers
    cleaned = cleaned.replace("<note>", "").replace("</note>", "")
    # Remove markdown bold/italic markers
    cleaned = re.sub(r'\*\*([^*]+)\*\*', r'\1', cleaned)  # Remove **bold**
    cleaned = re.sub(r'\*([^*]+)\*', r'\1', cleaned)  # Remove *italic*
    cleaned = cleaned.replace("__STREAM_END__", "")

    # Remove leaked chain-of-thought markers and meta-commentary
    # GUARD: Only match these patterns in the FIRST 300 chars to avoid removing legitimate clinical reasoning
    first_300 = cleaned[:300]
    cleaned = re.sub(r'<\|end\|>.*', '', cleaned, flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r'<\|start\|>.*', '', cleaned, flags=re.DOTALL | re.IGNORECASE)

    # Only remove meta-reasoning patterns if they appear very early (first 300 chars)
    for pattern in [
        r'^"We (are asked|have|need to)',
        r'^The question is',
        r'^Wait, that',
        r'^Actually we should',
        r'^So we need to',
        r'^Let\'s (draft|adjust|craft)',
    ]:
        match = re.search(pattern, first_300, re.IGNORECASE | re.MULTILINE)
        if match:
            # Only cut if match is in first 300 chars
            match_full = re.search(pattern, cleaned, re.IGNORECASE | re.MULTILINE)
            if match_full and match_full.start() < 300:
                cleaned = cleaned[match_full.end():].lstrip()
                break

    # Tame excessive blank lines but keep paragraphing
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)

    # Trim global ends only
    return cleaned.strip()


def _chunk_text_for_stream(text: str, max_chars: int = 600) -> List[str]:
    """Break a fully generated answer into reasonably sized chunks for streaming."""
    if not text:
        return []

    segments: List[str] = []
    buffer = ""
    for para in text.split("\n\n"):
        para = para.strip("\n")
        if not para:
            continue
        if buffer:
            candidate = buffer + "\n\n" + para
            if len(candidate) <= max_chars:
                buffer = candidate
                continue
            segments.append(buffer)
            buffer = para
        else:
            buffer = para
        while len(buffer) > max_chars:
            segments.append(buffer[:max_chars])
            buffer = buffer[max_chars:]
    if buffer:
        segments.append(buffer)

    # Final pass to ensure no segment exceeds the limit
    out: List[str] = []
    for seg in segments:
        if len(seg) <= max_chars:
            out.append(seg)
            continue
        start = 0
        while start < len(seg):
            out.append(seg[start : start + max_chars])
            start += max_chars
    return out


async def _collect_note_output(
    prompt: str,
    temperature: Optional[float],
    max_tokens: Optional[int],
    stop_tokens: Optional[List[str]] = None,
) -> str:
    """Run completion to finish and return cleaned text (used for QA)."""
    text = await note_gen.collect_completion(
        prompt, temperature or 0.2, max_tokens or 2048, stop=stop_tokens
    )
    return clean_model_output_final(text).strip()


def _extract_marker_sentences(text: str, markers: List[str]) -> List[str]:
    sentences: List[str] = []
    if not text:
        return sentences
    for sentence in re.split(r'(?<=[.!?])\s+', text):
        lowered = sentence.lower()
        if any(marker.lower() in lowered for marker in markers):
            sentences.append(sentence.strip())
    return sentences

# Backward-compatible alias if other modules import this name
clean_model_output = clean_model_output_chunk


# ---------------------------------------------------------------------------
# RAG integration helpers
# ---------------------------------------------------------------------------

def _rag_client_from_cfg(cfg: Dict) -> RAGHttpClient:
    base = cfg.get("rag_service_url")
    if not base:
        raise HTTPException(status_code=500, detail="rag_service_url not set in config")
    timeout_ms = int(cfg.get("rag_timeout_ms", 25000))
    return RAGHttpClient(base, timeout=timeout_ms)


def _qa_rewrite_prompt(question: str, draft_answer: str, evidence: str) -> str:
    """Prompt template to refine a baseline answer using RAG evidence."""
    header = (
        "You are reviewing a draft clinical answer. Your job is to keep the draft's structure and clarity, "
        "but tighten accuracy using the evidence that follows.\n\n"
        "RULES:\n"
        "- Preserve helpful sentences from the draft unless they conflict with evidence.\n"
        "- If evidence supports clarifying or updating a statement, rewrite that portion succinctly.\n"
        "- If evidence is silent on part of the draft, keep the draft wording (do NOT invent new details).\n"
        "- If evidence contradicts the draft, correct the statement and mention the key evidence.\n"
        "- If evidence is insufficient overall, keep the draft answer and append a short caution such as "
        "'Current evidence review found no additional guidance.'\n"
        "- Output plain text only; no bullet lists or citations are required.\n"
    )
    return (
        f"{header}\n"
        f"CLINICAL QUESTION:\n{question.strip() or 'Not provided.'}\n\n"
        f"DRAFT ANSWER:\n{draft_answer.strip() or 'No draft answer available.'}\n\n"
        f"EVIDENCE CONTEXT:\n{(evidence or '').strip() or 'Evidence context is empty.'}\n\n"
        "REVISED ANSWER:\n"
    )


def _qa_prompt_with_rag(question: str, context_clean: str) -> str:
    header = (
        "You are a senior consultant answering a clinical question. Your response must be direct, concise, and immediately useful.\n\n"
        "CRITICAL GROUNDING REQUIREMENT:\n"
        "- Answer ONLY using information from the Evidence Context provided below.\n"
        "- If the Evidence Context is empty or insufficient to answer the question, respond with: 'Insufficient evidence available to answer this question reliably. Please consult current clinical guidelines or specialist input.'\n"
        "- Do NOT answer from general medical knowledge if evidence is not provided.\n"
        "- Do NOT invent, assume, or speculate beyond what the evidence explicitly states.\n\n"
        "FORMAT REQUIREMENTS:\n"
        "- If the answer is long, write it in 2-6 paragraphs\n"
        "- Separate each paragraph with 2 blank lines\n"
        "- Use ONLY plain text - no markdown, no bold (**), no italics, no bullets, no special formatting\n"
        "- Include specific drug names with doses, route, and timing when applicable\n\n"
        "CONTENT REQUIREMENTS:\n"
        "- Cover: assessment, risk stratification, treatment options with dosing, monitoring, special considerations\n"
        "- Do NOT include citations or reference numbers\n"
        "- STOP after your answer - no reasoning, no meta-commentary, no alternative questions\n"
        "- DO NOT ADD ANYTHING ELSE TO THE ANSWER BEYOND WHAT IS REQUESTED\n"
        "- YOU DO NOT HAVE TO USE ALL AVAILABLE TOKENS - BE CONCISE AND FOCUSED\n"
        "- DO NOT REPEAT THE QUESTION IN YOUR ANSWER\n"
        "- DO NOT REPEAT INFORMATION ACROSS PARAGRAPHS\n\n"
    )
    ctx_section = f"Evidence Context:\n{context_clean}\n\n" if context_clean.strip() else "Evidence Context: [No evidence available]\n\n"
    q_section = f"Question: {question.strip()}\n\n"
    instruction = "Answer (plain text only, 2-3 paragraphs with blank lines, or state insufficient evidence if applicable):\n\n"
    return header + ctx_section + q_section + instruction


def _weak_evidence(refs: List[Dict[str, Any]], context: str) -> bool:
    if not refs:
        return True
    try:
        # Simple heuristic: if mean score very low and context short
        scores = [float(r.get("score", 0.0)) for r in refs]
        mean_score = sum(scores) / max(1, len(scores))
        return mean_score < 0.1 or len(context.strip()) < 40
    except Exception:
        return False


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        val = value.strip().lower()
        if val in ("true", "1", "yes", "y", "on"):
            return True
        if val in ("false", "0", "no", "n", "off", ""):
            return False
    return default


async def _gather_rag_for_qa(question: str, cfg: Dict) -> Dict[str, Any]:
    """Collect RAG context for QA without blocking the main stream."""
    result: Dict[str, Any] = {
        "context_aug": "",
        "context_raw": "",
        "refs_raw": [],
        "norm_refs": [],
        "full_chunks": [],
        "used_filters": {},
        "error": None,
        "weak_evidence": True,
    }
    try:
        rag_client = _rag_client_from_cfg(cfg)
        try:
            rag_timeout_ms = int(cfg.get("rag_timeout_ms", 25000))
        except Exception:
            rag_timeout_ms = 25000
        timeout_seconds = max(1.0, rag_timeout_ms / 1000.0)

        # Loosen filters: allow broad recall; keywords are advisory only
        include_kws: List[str] = []

        rag_context, rag_refs_raw, used_filters = await asyncio.wait_for(
            rag_client.query(
                question,
                top_k=int(cfg.get("rag_top_k", 16)),
            include_keywords=include_kws,
            date_from="2018-01-01",
        ),
        timeout=timeout_seconds,
    )

        norm_refs, full_chunks = _normalize_reference_items(
            rag_refs_raw or [],
            cap=int(cfg.get("rag_top_k", 16)),
            sort_key=lambda x: (x.get("year", 0), x.get("score", 0.0)),
        )

        ctx_aug = rag_context or ""
        try:
            include_snips = _as_bool(cfg.get("rag_include_snippets", True), True)
        except Exception:
            include_snips = True
        if include_snips and not ctx_aug.strip() and full_chunks:
            try:
                cap_chars = int(cfg.get("rag_evidence_clip_chars", 2000))
            except Exception:
                cap_chars = 2000
            snips_all = "\n\n".join(full_chunks)
            if len(snips_all) > cap_chars:
                snips_all = snips_all[:cap_chars] + "\n[...evidence truncated...]"
            ctx_aug = snips_all

        result.update(
            {
                "context_aug": ctx_aug,
                "context_raw": rag_context or "",
                "refs_raw": rag_refs_raw or [],
                "norm_refs": norm_refs,
                "full_chunks": full_chunks,
                "used_filters": used_filters or {},
                "weak_evidence": _weak_evidence(rag_refs_raw or [], ctx_aug or rag_context or ""),
            }
        )
    except asyncio.TimeoutError:
        result["error"] = "timeout"
        result["used_filters"] = {"error": "timeout"}
    except Exception as exc:
        err_txt = str(exc)
        result["error"] = err_txt
        result["used_filters"] = {"error": err_txt[:160]}
    return result


async def _generate_consult_comment(gen_id: str, note_text: str, cfg: Dict) -> None:
    """Derive a brief evidence-backed comment from Impression/Plan."""
    try:
        _consult_comment_store[gen_id] = {"status": "pending"}
        # Extract Impression/Plan heuristically
        imp = ""
        plan = ""
        m_imp = re.search(r"(?im)^\s*Impression\s*:\s*(.+?)(?:\n\S|\Z)", note_text, flags=re.DOTALL)
        if m_imp:
            imp = m_imp.group(1).strip()
        m_plan = re.search(r"(?im)^\s*Plan\s*:\s*(.+?)(?:\n\S|\Z)", note_text, flags=re.DOTALL)
        if m_plan:
            plan = m_plan.group(1).strip()
        focus = imp or plan or note_text[:800]
        raw_focus_source = focus
        confirmed_markers = cfg.get("consult_confirmed_markers", ["confirmed", "biopsy", "pathology", "definitive"])
        ruledout_markers = cfg.get("consult_ruledout_markers", ["ruled out", "excluded", "negative for", "not consistent with"])
        confirmed_statements = _extract_marker_sentences(f"{imp}\n{plan}", confirmed_markers)
        ruledout_statements = _extract_marker_sentences(f"{imp}\n{plan}", ruledout_markers)

        # Summarize focus to tighten RAG query (always when enabled)
        # GUARD: Only summarize if we have substantial content to avoid hallucination
        try:
            if bool(cfg.get("rag_focus_summary_enable", True)) and len(focus.strip()) >= 100:
                # Aim for ~100 words, clamp to 80–120 regardless of config
                cfg_target = int(cfg.get("rag_focus_summary_words", 150))
                target = max(80, min(120, cfg_target))
                sum_prompt = (
                    "You are extracting a retrieval focus from a clinical Impression/Plan.\n"
                    "CRITICAL: Use ONLY information explicitly present in the TEXT below. Do NOT invent, assume, or add details.\n"
                    "If the TEXT is too brief or unclear, respond with: 'Insufficient detail for reliable summary.'\n\n"
                    "Format: one paragraph ~100 words (80–120 words), no citations, avoid hedging.\n"
                    "Include ONLY: primary diagnoses; key differentials; key investigations with abnormal results; leading treatment decisions (drug, dose, route, frequency); and critical contraindications/comorbid flags.\n"
                    f"Aim for about {target} words. Use concise, factual language in a single paragraph.\n\n"
                    f"TEXT:\n{focus}\n\nFOCUS SUMMARY:\n"
                )
                summary_text = await note_gen.collect_completion(
                    sum_prompt, temperature=0.05, max_tokens=target * 2, stop=[]
                )
                summarized = clean_model_output_final(summary_text).strip()
                # Only use summarized version if it's not a refusal and is substantial
                if summarized and len(summarized) >= 50 and "insufficient" not in summarized.lower():
                    focus = summarized
        except Exception:
            pass
        focus_summary = focus

        # Query RAG for focused evidence with timeout; fallback to empty on error
        ctx = ""
        norm_refs: List[Dict[str, Any]] = []
        used: Dict[str, Any] = {}
        try:
            rag = _rag_client_from_cfg(cfg)
            rag_kws: List[str] = []
            rag_timeout = int(cfg.get("rag_timeout_ms", 25000)) / 1000.0
            focus_words = focus.split()
            focus_word_count = len(focus_words)
            focus_summary_words = int(cfg.get("rag_focus_summary_words", 150))
            consult_cap = max(3, int(cfg.get("rag_consult_top_k_cap", 6)))
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
            norm_refs, _ = _normalize_reference_items(
                (rag_refs or [])[:requested_top_k],
                cap=requested_top_k,
                sort_key=lambda x: x.get("score", 0.0),
            )
        except Exception as e:
            print(f"[RAG] Consult comment evidence unavailable: {e}")
            ctx, norm_refs, used = "", [], {"error": str(e)[:160]}

        # GUARD: If evidence is absent, return a minimal template instead of hallucinating
        if not ctx.strip():
            _consult_comment_store[gen_id] = {
                "status": "done",
                "comment": "Insufficient evidence available to provide evidence-backed differential considerations or management guidance. Recommend consulting current clinical guidelines and specialist input.",
                "refs": []
            }
            _generation_meta[gen_id].update({
                "consult_refs": [],
                "consult_used": used,
                "refs": [],
                "context": "",
                "consult_focus_raw": raw_focus_source,
                "consult_focus_summary": focus_summary,
                "consult_assertions": {
                    "confirmed": confirmed_statements,
                    "ruled_out": ruledout_statements,
                },
            })
            return

        # Build short comment prompt - only proceeds if we have substantial evidence
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
            "You are a senior consultant. Provide a concise, evidence-grounded comment to accompany this clinical note's Impression/Plan.\n"
            "CRITICAL:\n"
            "- Respect the statements from the original note below. If a diagnosis is confirmed, reinforce that certainty; if something is ruled out, do NOT suggest it as a differential.\n"
            "- Use ONLY the Evidence Context provided. If the evidence contradicts the note, flag the discrepancy without discarding the note's conclusion.\n"
            "- If evidence is insufficient, respond with: 'Insufficient evidence available for reliable commentary.'\n\n"
            "FORMAT (plain text, short paragraphs; brief bullets are acceptable if clearer):\n"
            "1) Differential considerations (only if still clinically open; otherwise restate the confirmed diagnosis).\n"
            "2) Impression/Plan alignment with evidence (highlight matches, gaps, or contraindications).\n"
            "3) Key management guidance consistent with the evidence.\n\n"
            f"Original Note Excerpt:\n{note_excerpt}\n\n"
            f"Confirmed / Ruled Statements:\n{assertions_text}\n\n"
            f"Evidence Context:\n{ctx}\n\n"
            f"Focus Summary:\n{focus_summary}\n\n"
            "Comment:\n"
        )

        # Generate via llama-server (collect in background)
        comment_text = await note_gen.collect_completion(
            prompt,
            temperature=float(cfg.get("default_qa_temperature", 0.2)),
            max_tokens=int(cfg.get("consult_comment_max_tokens", 700)),
            stop=[],
        )
        comment = clean_model_output_final(comment_text).replace("'''", "").replace('"""', '').strip()

        # Store consult metadata for UI consumption
        m = _generation_meta.get(gen_id, {}).copy()
        # Expose RAG artifacts under generic keys too so index.html can find them
        m.update({
            "consult_refs": norm_refs,
            "consult_used": used,
            "refs": norm_refs,
            "context": ctx,
            "consult_focus_raw": raw_focus_source,
            "consult_focus_summary": focus_summary,
            "consult_assertions": {
                "confirmed": confirmed_statements,
                "ruled_out": ruledout_statements,
            },
        })
        _generation_meta[gen_id] = m
        _consult_comment_store[gen_id] = {"status": "done", "comment": comment, "refs": norm_refs}
    except Exception as e:
        _consult_comment_store[gen_id] = {"status": "error", "error": str(e)[:200]}

# Backward-compatible alias if other modules import this name
clean_model_output = clean_model_output_chunk


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

def truncate_to_context_length(text: str, max_tokens: int) -> str:
    """Truncate text to approximate token count (rough estimate: 1 token ≈ 0.75 words)."""
    words = text.split()
    max_words = int(max_tokens * 0.75)  # Conservative estimate
    if len(words) > max_words:
        truncated = " ".join(words[:max_words])
        return truncated + "\n\n[Content truncated to fit context length...]"
    return text


def _fill_template(tpl: str, values: dict) -> str:
    """Simple, safe template fill"""
    out = tpl
    for k, v in values.items():
        out = out.replace("{" + k + "}", str(v))
    return out

def _cfg_text(val: Any) -> str:
    """
    Normalize config text fields that may be stored as:
      - string
      - list of strings (your new format)
    Returns a single trimmed string.
    """
    if val is None:
        return ""
    if isinstance(val, str):
        return val.strip()
    if isinstance(val, list):
        parts: List[str] = []
        for x in val:
            if x is None:
                continue
            if isinstance(x, str):
                parts.append(x)
            else:
                parts.append(str(x))
        # Join without adding extra newlines unless you want them.
        # If you prefer each list item as a separate line, use "\n".join(parts)
        return "".join(parts).strip()
    return str(val).strip()



def build_prompt(
    chart_data: str,
    transcription: str,
    note_type: str,
    custom_prompt: Optional[str] = None,
) -> str:
    cfg = load_config()

    if note_type == "qa":
        header = (
            "You are an expert medical assistant with comprehensive knowledge of clinical medicine, diagnosis, and treatment.\n"
            "Provide accurate, clinically useful guidance (assessment, risks, options with dosing/route/timing, monitoring, contraindications).\n"
            "Write 2-4 tight paragraphs separated by blank lines; be concise but include key specifics.\n"
            "Be direct and practical. Avoid fluff or repetition.\n\n"
        )

        question_section = f"Medical Question: {transcription}\n\n" if transcription.strip() else ""
        context_section = f"Patient Context:\n{chart_data}\n\n" if chart_data.strip() else ""

        full_prompt = header + context_section + question_section + "Detailed Medical Response:\n"
        return full_prompt

    # Note generation: with system + user prompt composition
    today = date.today().strftime("%Y-%m-%d")

    chart_section = chart_data.strip()
    trans_section = transcription.strip()

    # Combine chart + transcription as raw data
    raw_parts = []
    if chart_section:
        raw_parts.append("Chart Data:\n" + chart_section)
    if trans_section:
        raw_parts.append("Transcription:\n" + trans_section)
    raw_data = "\n\n".join(raw_parts).strip()

    system_prompt = _cfg_text(cfg.get("default_note_system_prompt", ""))

    user_templates = cfg.get("default_note_user_prompts", {}) or {}
    raw_user_tpl = ""
    if isinstance(user_templates, dict):
        raw_user_tpl = user_templates.get(note_type) or ""
    user_tpl = _cfg_text(raw_user_tpl)


    if not user_tpl:
        # Backward-compatible fallback
        default_prompts = cfg.get("default_prompts", {}) or {}
        legacy = _cfg_text(default_prompts.get(note_type) or "")
        if legacy:
            # Treat legacy text as the "user" prompt body
            user_tpl = (
                f"Note type: {note_type}\n"
                f"Current date: {{CURRENT_DATE}}\n"
                f"{legacy}\n\n"
                "Raw data follows:\n{RAW_DATA}\n"
            )
        else:
            user_tpl = (
                "Note type: {NOTE_TYPE}\n"
                "Current date: {CURRENT_DATE}\n"
                "Reason for visit/referral: {REASON_FOR_VISIT}\n"
                "Start with patient name, age, sex, and reason.\n"
                "Do not fabricate.\n\n"
                "Raw data follows:\n{RAW_DATA}\n"
            )

# Reason fields: if you later add explicit reason in payload, wire it here.
    # For now, keep it unknown and let the model infer from RAW_DATA.
# Prepare all template values
    values = {
        "CURRENT_DATE": today,
        "NOTE_TYPE": note_type,
        "REASON_FOR_VISIT": "Unknown (infer from raw data)",
        "ADMISSION_DX": "Unknown (infer from raw data)",
        "DISCHARGE_DX": "Unknown (infer from raw data)",
        "RAW_DATA": raw_data or "[No chart/transcription provided]"
    }

    # Fill template variables in BOTH system and user prompts
    system_prompt_filled = _fill_template(system_prompt, values).strip() if system_prompt else ""

    # Fill the instruction part of user template
    user_instructions = _fill_template(user_tpl, values).strip()

    # *** CRITICAL FIX: Always append RAW_DATA with clear demarcation ***
    # This ensures chart and transcription data ALWAYS reach the model,
    # regardless of whether {RAW_DATA} is in the user template
    if raw_data:
        data_section = "\n\n" + "=" * 80 + "\nPATIENT DATA\n" + "=" * 80 + "\n\n" + raw_data
    else:
        data_section = "\n\n[No chart or transcription data provided]"

    user_prompt_filled = user_instructions + data_section

    # Compose final prompt with clear separation
    prompt_body = ""
    if system_prompt_filled:
        prompt_body += "SYSTEM:\n" + system_prompt_filled + "\n\n"
    prompt_body += "USER:\n" + user_prompt_filled + "\n\n"

    # Append per-user custom prompt as an extra instruction layer
    if custom_prompt and custom_prompt.strip():
        prompt_body += "USER CUSTOM INSTRUCTIONS:\n" + custom_prompt.strip() + "\n\n"

    prompt_body += "ASSISTANT:\n"

    return prompt_body

# ---------------------------------------------------------------------------
# Route: /generate_stream
# ---------------------------------------------------------------------------

@router.post("/generate_stream")
async def generate_stream(request: Request):
    try:
        chart = trans = custom_prompt = ""
        note_type = "consult"
        temp: Optional[float] = None
        max_tokens = None

        # Support both JSON and multipart/form-data
        ctype = (request.headers.get("content-type") or "").lower()
        cfg = load_config()
        if "application/json" in ctype:
            payload = await request.json()
            chart = payload.get("chart_data", "")
            trans = payload.get("transcription", "")
            note_type = payload.get("note_type", "consult")
            custom_prompt = payload.get("custom_prompt", "")
            if "temperature" in payload and payload.get("temperature") is not None:
                try:
                    temp = float(payload.get("temperature"))
                except Exception:
                    temp = None
            max_tokens = payload.get("max_tokens")
        else:
            form = await request.form()
            chart = str(form.get("chart_data", "") or "")
            trans = str(form.get("transcription", "") or "")
            note_type = str(form.get("note_type", "consult") or "consult")
            custom_prompt = str(form.get("custom_prompt", "") or "")
            t_val = form.get("temperature")
            if t_val is not None and str(t_val).strip() != "":
                try:
                    temp = float(str(t_val))
                except Exception:
                    temp = None
            try:
                max_tokens_val = form.get("max_tokens")
                max_tokens = int(str(max_tokens_val)) if max_tokens_val else None
            except Exception:
                max_tokens = None

        # If temperature not provided by client, honor admin-configured defaults
        if temp is None:
            if note_type == "qa":
                temp = float(cfg.get("default_qa_temperature", 0.2))
            else:
                temp = float(cfg.get("default_note_temperature", 0.2))

        # Defaults for max_tokens
        if max_tokens is None:
            cfg = load_config()
            if note_type == "qa":
                max_tokens = cfg.get("default_qa_max_tokens", 512)
            else:
                max_tokens = cfg.get("default_note_max_tokens", 2048)

        # Build prompt; sanitize chart data to avoid accidental stop triggers
        if note_type == "qa":
            chart = _sanitize_chart_text(chart)
            trans = _sanitize_transcription_text(trans)
        prompt = build_prompt(chart, trans, note_type, custom_prompt)
        if note_type != "qa":
            print(f"[NOTE_PROMPT_DEBUG] prompt start: {prompt[:160]!r}")
            print(f"[NOTE_PROMPT_DEBUG] chart_len={len(chart)}, trans_len={len(trans)}")
        t0 = time.perf_counter()
        token_count = 0
        generation_id = uuid.uuid4().hex
        output_buf: list[str] = []
        raw_note_buf: list[str] = []

        # seed cache entry for later feedback
        with _cache_lock:
            _generation_cache[generation_id] = {"prompt": prompt, "output": ""}

        # IMPORTANT: Populate metadata BEFORE streaming starts to avoid race condition
        cfg2 = load_config()
        is_qa = note_type == "qa"
        qa_question_for_verify: Optional[str] = None
        qa_source_excerpt = (trans or chart or "")[:2000]
        qa_baseline_temp = temp
        qa_rewrite_temp = temp
        qa_baseline_fallback = "Insufficient information to answer this question."
        qa_enhancement_label = "\n\n[Evidence-based update]:\n"
        qa_min_ctx_chars = 0
        qa_allow_empty_ctx = True
        qa_rag_enabled = False
        rag_task: Optional[asyncio.Task] = None

        if is_qa:
            qa_question_for_verify = (trans or chart or "").strip()
            try:
                qa_question_for_verify = re.sub(r"\[IMPORTANT:.*\]$", "", qa_question_for_verify, flags=re.S).strip()
            except Exception:
                pass

            qa_baseline_temp = float(cfg2.get("qa_baseline_temperature", temp))
            qa_rewrite_temp = float(cfg2.get("qa_rag_rewrite_temperature", temp))
            qa_baseline_fallback = str(
                cfg2.get(
                    "qa_baseline_fallback",
                    "Insufficient information to answer this question.",
                )
            )
            qa_enhancement_label = str(
                cfg2.get("qa_rag_enhancement_label", "\n\n[Evidence-based update]:\n")
            )
            qa_min_ctx_chars = int(cfg2.get("qa_rag_min_context_chars", 200))
            qa_allow_empty_ctx = _as_bool(cfg2.get("qa_rag_allow_empty_context", True), True)
            qa_rag_enabled = _as_bool(cfg2.get("qa_rag_rewrite_enable", True), True)

            _generation_meta[generation_id] = {
                "refs": [],
                "used_filters": {},
                "context": "",
                "full_evidence": "",
                "qa": {
                    "baseline": None,
                    "final": None,
                    "rewrite_used": False,
                    "status": "generating_baseline",
                    "rag_context_chars": 0,
                    "rag_error": None,
                },
            }

            if qa_rag_enabled and qa_question_for_verify:
                rag_task = asyncio.create_task(_gather_rag_for_qa(qa_question_for_verify, cfg2))
        else:
            _generation_meta[generation_id] = {
                "refs": [],
                "used_filters": {},
                "context": "",
                "full_evidence": "",
                "qa": {
                    "status": "not_applicable",
                    "baseline": None,
                    "final": None,
                    "rewrite_used": False,
                    "rag_context_chars": 0,
                    "rag_error": None,
                },
            }
            print(f"[RAG] Skipping RAG for note generation - will run for consult comment after")

        async def gen():
            nonlocal token_count
            try:
                # Check approximate prompt size and provide early warning
                prompt_size = len(prompt.split())
                if prompt_size > 100000:  # ~100k words is very large
                    logger.warning(f"[PROMPT_SIZE] Very large prompt: ~{prompt_size} words")

                if is_qa:
                    # Always generate baseline first, but don't stream it yet; we may replace it with RAG rewrite
                    baseline_raw = await note_gen.collect_completion(
                        prompt,
                        temperature=qa_baseline_temp,
                        max_tokens=max_tokens,
                        stop=[],
                    )
                    baseline_text = clean_model_output_final(baseline_raw).strip()
                    if not baseline_text:
                        baseline_text = clean_model_output_final(qa_baseline_fallback).strip() or qa_baseline_fallback.strip()
                    final_text = baseline_text
                    rewrite_used = False

                    meta_entry = _generation_meta.get(generation_id)
                    if not isinstance(meta_entry, dict):
                        meta_entry = {}
                        _generation_meta[generation_id] = meta_entry
                    qa_meta = meta_entry.get("qa")
                    if not isinstance(qa_meta, dict):
                        qa_meta = {}
                        meta_entry["qa"] = qa_meta

                    qa_meta["baseline"] = baseline_text
                    qa_meta["final"] = baseline_text
                    qa_meta["status"] = "baseline_done" if qa_rag_enabled else "done"
                    qa_meta["rewrite_used"] = False
                    qa_meta["rag_context_chars"] = 0
                    qa_meta["rag_error"] = None

                    used_filters = meta_entry.get("used_filters")
                    if not isinstance(used_filters, dict):
                        used_filters = {}
                    norm_refs = meta_entry.get("refs")
                    if not isinstance(norm_refs, list):
                        norm_refs = []
                    full_chunks: List[str] = []
                    rag_context_aug = meta_entry.get("context") or ""
                    rag_error: Optional[str] = None
                    min_ctx_chars = int(cfg2.get("qa_rag_min_context_chars", 80))

                    if rag_task is not None:
                        try:
                            rag_result = await rag_task
                        except Exception as rag_exc:
                            rag_result = None
                            rag_error = str(rag_exc)

                        if rag_result:
                            used_filters = rag_result.get("used_filters", {}) or {}
                            norm_refs = rag_result.get("norm_refs", []) or []
                            full_chunks = rag_result.get("full_chunks", []) or []
                            rag_context_aug = rag_result.get("context_aug", "") or ""
                            rag_error = rag_result.get("error")
                            raw_refs = rag_result.get("refs_raw", []) or []

                            if rag_result.get("weak_evidence") and not raw_refs:
                                _append_missed_question(
                                    {
                                        "ts": int(time.time()),
                                        "question": qa_source_excerpt,
                                        "used_filters": used_filters,
                                        "reason": "no_or_weak_evidence",
                                    }
                                )

                            ctx_chars = len(rag_context_aug.strip())
                            sufficient_ctx = ctx_chars >= max(20, min_ctx_chars) and bool(rag_context_aug.strip())

                            if sufficient_ctx and not rag_error:
                                rewrite_prompt = _qa_rewrite_prompt(
                                    qa_question_for_verify or "",
                                    baseline_text,
                                    rag_context_aug,
                                )
                                rewritten = await _collect_note_output(
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
                                used_filters["error"] = str(rag_error)[:160] if rag_error else None
                            if rag_error:
                                used_filters = {k: v for k, v in used_filters.items() if v is not None}

                    meta_entry["refs"] = norm_refs
                    meta_entry["used_filters"] = used_filters
                    meta_entry["context"] = rag_context_aug
                    meta_entry["full_evidence"] = "\n\n".join(full_chunks)
                    qa_meta["final"] = final_text
                    qa_meta["rewrite_used"] = rewrite_used
                    qa_meta["rag_context_chars"] = len(rag_context_aug.strip())
                    qa_meta["rag_error"] = rag_error
                    qa_meta["status"] = "done"
                    _generation_meta[generation_id] = meta_entry

                    # Stream the final text (baseline or rewritten) now
                    for segment in _chunk_text_for_stream(final_text):
                        cleaned_segment = clean_model_output_chunk(segment)
                        if cleaned_segment:
                            output_buf.append(cleaned_segment)
                            token_count += len(cleaned_segment.split())
                            yield cleaned_segment

                    yield END_MARKER + "\n"
                    return

                debug_seed_logged = False
                stop_phrases = []
                note_text = await note_gen.collect_completion(
                    prompt,
                    temperature=temp,
                    max_tokens=max_tokens,
                    stop=stop_phrases,
                )
                if not is_qa:
                    raw_note_buf.append(note_text)
                    output_buf.append(note_text)
                    token_count += len(note_text.split())
                    yield note_text
                else:
                    cleaned_note = clean_model_output_chunk(note_text)
                    if cleaned_note:
                        output_buf.append(cleaned_note)
                        token_count += len(cleaned_note.split())
                        yield cleaned_note
                yield END_MARKER + "\n"
            except asyncio.CancelledError:
                print("Client disconnected - streaming cancelled")
                raise
            except RuntimeError as e:
                error_msg = str(e).lower()
                # Check if this is a context length error from llama-server
                if any(keyword in error_msg for keyword in [
                    "context", "ctx", "kv", "slot", "too long", "too large",
                    "exceeds", "limit", "overflow", "n_ctx"
                ]):
                    print(f"Context length error: {e}")
                    yield (
                        "ERROR: The input is too long for the model's context window.\n\n"
                        "This note cannot be generated because the combined chart data and transcription "
                        "exceed the model's maximum context length.\n\n"
                        "Please try one of the following:\n"
                        "- Reduce the amount of chart data\n"
                        "- Shorten the transcription\n"
                        "- Use a model with a larger context window\n\n"
                        f"Technical details: {str(e)}\n"
                    )
                else:
                    print(f"Runtime error during streaming: {e}")
                    yield f"Error: {str(e)}\n"
            except Exception as e:
                print(f"Error during streaming: {e}")
                yield f"Error: {str(e)}\n"
            finally:
                if global_metrics is not None:
                    duration = time.perf_counter() - t0
                    # Note: NoteGeneratorServer may not expose model_path; use getattr guard
                    global_metrics.record_note(duration, token_count, getattr(note_gen, 'model_path', None))
                # Persist neutral rating (1) at completion - only for notes, not Q&A
                try:
                    combined_output = "".join(output_buf)
                    raw_final_output = "".join(raw_note_buf).strip()
                    if is_qa:
                        final_output = clean_model_output_final(combined_output)
                    else:
                        final_output = combined_output
                    # Only log clinical notes, not Q&A interactions
                    if not is_qa:
                        _append_feedback_csv(prompt, raw_final_output or final_output, 1)
                        consult_source = clean_model_output_final(final_output)
                        asyncio.create_task(_generate_consult_comment(generation_id, consult_source, cfg2))
                    with _cache_lock:
                        if generation_id in _generation_cache:
                            _generation_cache[generation_id]["output"] = final_output
                except Exception as e:
                    print(f"Feedback CSV write failed: {e}")

        return StreamingResponse(gen(), media_type="text/plain", headers={"X-Generation-Id": generation_id})

    except Exception as e:
        import traceback
        error_detail = f"Generation unavailable: {str(e)}\n\nTraceback:\n{traceback.format_exc()}"
        print(f"Error in generate_stream: {error_detail}")
        raise HTTPException(status_code=503, detail=error_detail)


# ---------------------------------------------------------------------------
# New endpoints: generation meta, consult_comment
# ---------------------------------------------------------------------------


@router.get("/generation/{gen_id}/meta")
async def generation_meta(gen_id: str) -> Dict[str, Any]:
    meta = _generation_meta.get(gen_id)
    if not meta:
        raise HTTPException(status_code=404, detail="generation not found")
    return meta


@router.get("/generation/{gen_id}/consult_comment")
async def get_consult_comment(gen_id: str) -> Dict[str, Any]:
    st = _consult_comment_store.get(gen_id)
    if not st:
        return {"status": "unknown"}
    return st


# ---------------------------------------------------------------------------
# Route: /note_prompts - fetch default note templates (authorized via API key)
# ---------------------------------------------------------------------------

@router.get("/note_prompts")
async def get_note_prompts() -> JSONResponse:
    try:
        cfg = load_config()

        system_prompt = _cfg_text(cfg.get("default_note_system_prompt", ""))

        raw_templates = cfg.get("default_note_user_prompts", {})
        templates_out = {}

        if isinstance(raw_templates, dict):
            for k, v in raw_templates.items():
                templates_out[k] = _cfg_text(v)

        return JSONResponse(
            content={
                "success": True,
                "system": system_prompt,
                "templates": templates_out,
            }
        )
    except Exception as e:
        print(f"[ERROR] Failed to fetch note prompts: {e}")
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": "Failed to fetch prompts"},
        )



# ---------------------------------------------------------------------------
# Route: /feedback - record thumbs up/down for a generation
# ---------------------------------------------------------------------------

@router.post("/feedback")
async def record_feedback(payload: Dict):
    try:
        gen_id = (payload.get("generation_id") or "").strip()
        rating = int(payload.get("rating", 1))
        if rating not in (0, 1, 2):
            raise HTTPException(status_code=400, detail="rating must be 0, 1, or 2")

        # Prefer cache (authoritative prompt/output captured at generation)
        prompt = output = None
        with _cache_lock:
            entry = _generation_cache.get(gen_id)
            if entry:
                prompt = entry.get("prompt")
                output = entry.get("output")

        # Allow client to provide prompt/output if cache entry is gone
        if prompt is None:
            prompt = payload.get("prompt")
        if output is None:
            output = payload.get("output")

        if not prompt or output is None:
            raise HTTPException(status_code=404, detail="generation not found and no prompt/output provided")

        _append_feedback_csv(prompt, output, rating)
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)[:160]})
# trigger reload
