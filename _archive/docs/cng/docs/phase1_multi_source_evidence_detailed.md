# Phase 1: Multi-Source Evidence Gathering — Detailed Implementation Plan

## Overview
Add web search (SearXNG) to consult comments, broaden the RAG query scope, and parallelize evidence gathering.

## Current State (Verified)

### Files Involved
- `server/core/consult/pipeline.py` — Main consult generation logic (337 lines)
- `server/routes/notes.py` — Route that calls pipeline, helper functions (2044 lines)
- `server/services/rag_http_client.py` — RAG client (233 lines)
- `server/services/qa_web_search.py` — Web search (110 lines, used by QA chat)
- `config/config.json` — Configuration (434 lines)

### Current Flow (Line-by-line trace)
1. `notes.py` line 1339-1362: `_generate_consult_comment()` wrapper calls `_generate_consult_comment_impl()`
2. `pipeline.py` line 20-337: Main function
   - Lines 41-48: Extract Impression + Plan via regex
   - Lines 50-56: Extract confirmed/ruled-out markers
   - Lines 58-98: Build focus based on strategy (`sections`, `full_note`, or `llm_query`)
   - Lines 117-194: Query RAG (single query)
   - Lines 209-223: Build note excerpt from Impression + Plan
   - Lines 225-250: Build prompt
   - Lines 252-260: Call LLM
   - Lines 262-318: Structure retry + refusal retry
   - Lines 320-335: Store results

### What Needs to Change
1. Add web search alongside RAG (parallel)
2. Broaden RAG query to include HPI, Physical Exam, Labs (not just Impression/Plan)
3. Combine all evidence into the prompt
4. New config keys in config.json

## Files to Create

### File 1: `server/services/consult_focus_builder.py`

**Purpose:** Extract clinical data (symptoms, findings, results) separately from clinician conclusions (diagnosis, plan).

**Why needed:** Current code only queries based on Impression + Plan, which creates confirmation bias.

**Content:**
```python
import re
from typing import Any, Dict, List, Tuple, Optional


def extract_section_by_heading(
    note_text: str,
    heading: str,
    *,
    aliases: Optional[List[str]] = None,
) -> str:
    """Extract a section body by matching its heading (## or plain text).
    
    Args:
        note_text: Full note text
        heading: Primary heading to match (e.g., "History of Present Illness")
        aliases: Alternative heading names (e.g., ["HPI", "History"])
    
    Returns:
        Section body text, or empty string if not found
    """
    if not note_text:
        return ""
    
    # Build regex pattern that matches:
    # - ## Heading
    # - ### Heading  
    # - Heading:
    # - Heading -
    # Case-insensitive
    all_names = [heading] + (aliases or [])
    name_pattern = "|".join(re.escape(n) for n in all_names)
    
    # Match heading as its own line or with content on same line
    heading_re = re.compile(
        rf"(?im)^(?:#{0,3}\s*)?({name_pattern})\s*(?::|-)?\s*$",
    )
    
    # Any subsequent heading ends this section
    any_heading_re = re.compile(r"(?im)^\s*#{1,3}\s+\S")
    
    # 1) Find standalone heading line
    m = heading_re.search(note_text)
    if m:
        start = m.end()
        rest = note_text[start:]
        
        # Find next heading
        next_m = any_heading_re.search(rest)
        end = next_m.start() if next_m else len(rest)
        
        return rest[:end].strip()
    
    return ""


def extract_clinical_data_sections(note_text: str) -> Dict[str, str]:
    """Extract all clinical data sections from the note.
    
    Returns:
        Dict mapping section names to their body text:
        - "history_of_present_illness": HPI text
        - "physical_exam": Physical Exam text
        - "investigations": Labs/Imaging text
        - "subjective": Subjective text
        - "objective": Objective text
        - "impression": Impression text
        - "plan": Plan text
        - "medications": Medications text
        - "past_medical_history": PMH text
        - "allergies": Allergies text
        - "review_of_systems": ROS text
    """
    sections: Dict[str, str] = {}
    
    # Section extraction with common aliases
    section_configs = [
        ("history_of_present_illness", "History of Present Illness", 
         ["HPI", "History of Present Illness"]),
        ("physical_exam", "Physical Exam",
         ["Physical Examination", "Physical Exam", "Exam", "On Examination", "PE"]),
        ("investigations", "Investigations",
         ["Labs and Results", "Investigations", "Lab Results", "Labs", 
          "Relevant Investigations", "Imaging", "Test Results"]),
        ("subjective", "Subjective",
         ["Subjective", "S"]),
        ("objective", "Objective",
         ["Objective", "O"]),
        ("impression", "Impression",
         ["Impression", "Impressions", "Assessment", "A"]),
        ("plan", "Plan",
         ["Plan", "P", "Assessment and Plan", "A/P", "Assessment & Plan"]),
        ("medications", "Medications",
         ["Medications", "Current Medications", "Med List", "Meds"]),
        ("past_medical_history", "Past Medical History",
         ["Past Medical History", "PMH", "Past History", "Medical History"]),
        ("allergies", "Allergies",
         ["Allergies", "Allergies/Adverse Drug Reactions", "ADR"]),
        ("review_of_systems", "Review of Systems",
         ["Review of Systems", "ROS"]),
    ]
    
    for key, primary, aliases in section_configs:
        text = extract_section_by_heading(note_text, primary, aliases=aliases)
        if text:
            sections[key] = text
    
    return sections


def build_differential_query(note_text: str, cfg: Dict[str, Any]) -> str:
    """Build a RAG query focused on SYMPTOMS and FINDINGS (not conclusions).
    
    This is the key anti-confirmation-bias function. Instead of searching for
    "Hypertension" (the diagnosis), we search for "headache, dizziness, 
    BP 150/95, age 65" (the clinical data).
    
    Args:
        note_text: Full note text
        cfg: Configuration dict
    
    Returns:
        Query string optimized for differential diagnosis retrieval
    """
    sections = extract_clinical_data_sections(note_text)
    
    parts: List[str] = []
    
    # 1) HPI - the patient's symptoms (most important for differential)
    hpi = sections.get("history_of_present_illness", "")
    if hpi:
        # Limit to 500 chars to keep query focused
        hpi_limit = hpi[:500]
        parts.append(f"Symptoms: {hpi_limit}")
    
    # 2) Physical Exam - objective findings
    pe = sections.get("physical_exam", "")
    if pe:
        pe_limit = pe[:300]
        parts.append(f"Exam findings: {pe_limit}")
    
    # 3) Investigations - lab/imaging results
    inv = sections.get("investigations", "")
    if inv:
        inv_limit = inv[:300]
        parts.append(f"Lab/imaging results: {inv_limit}")
    
    # 4) Subjective - what patient says
    subj = sections.get("subjective", "")
    if subj and not hpi:  # Don't duplicate if HPI already covers it
        subj_limit = subj[:300]
        parts.append(f"Subjective: {subj_limit}")
    
    # 5) Add demographic context if available
    # Extract age, sex from note
    age_match = re.search(r"(\d{1,3})\s*year-old", note_text, re.IGNORECASE)
    sex_match = re.search(r"(?:man|woman|male|female)", note_text, re.IGNORECASE)
    
    if age_match or sex_match:
        demo_parts = []
        if age_match:
            demo_parts.append(f"Age: {age_match.group(1)}")
        if sex_match:
            sex = sex_match.group(1).lower()
            demo_parts.append(f"Sex: {sex}")
        parts.insert(0, f"Patient: {' '.join(demo_parts)}")
    
    # Build query
    if not parts:
        # Fallback: use the first 300 words of the note
        words = note_text.split()[:300]
        return " ".join(words)
    
    query = "\n".join(parts)
    
    # Limit total query length
    max_query_chars = int(cfg.get("consult_max_query_chars", 1500))
    if len(query) > max_query_chars:
        query = query[:max_query_chars - 50] + "... [truncated]"
    
    return query.strip()


def build_guideline_query(note_text: str, cfg: Dict[str, Any]) -> str:
    """Build a RAG query focused on CONFIRMED DIAGNOSES for guideline checking.
    
    This searches for guidelines, treatment protocols, and management
    recommendations for the diagnoses the clinician has already made.
    
    Args:
        note_text: Full note text
        cfg: Configuration dict
    
    Returns:
        Query string optimized for guideline retrieval
    """
    sections = extract_clinical_data_sections(note_text)
    
    parts: List[str] = []
    
    # 1) Impression - the diagnoses
    impression = sections.get("impression", "")
    if impression:
        parts.append(f"Diagnoses: {impression[:500]}")
    
    # 2) Plan - what the clinician is doing
    plan = sections.get("plan", "")
    if plan:
        parts.append(f"Plan: {plan[:400]}")
    
    # 3) Add guideline-specific keywords
    parts.append("guidelines management treatment recommendations")
    
    if not parts:
        return ""
    
    query = "\n".join(parts)
    
    max_query_chars = int(cfg.get("consult_max_query_chars", 1200))
    if len(query) > max_query_chars:
        query = query[:max_query_chars - 50] + "... [truncated]"
    
    return query.strip()


def build_safety_query(note_text: str, cfg: Dict[str, Any]) -> str:
    """Build a RAG query focused on MEDICATIONS and SAFETY concerns.
    
    This searches for drug interactions, contraindications, dosing issues,
    pregnancy safety, renal adjustments, etc.
    
    Args:
        note_text: Full note text
        cfg: Configuration dict
    
    Returns:
        Query string optimized for safety/interaction retrieval
    """
    sections = extract_clinical_data_sections(note_text)
    
    parts: List[str] = []
    
    # 1) Medications - what the patient is taking
    meds = sections.get("medications", "")
    if meds:
        parts.append(f"Medications: {meds[:400]}")
    
    # 2) Allergies - important for safety
    allergies = sections.get("allergies", "")
    if allergies:
        parts.append(f"Allergies: {allergies[:200]}")
    
    # 3) Add safety-specific keywords
    parts.append("drug interactions contraindications side effects dosing renal hepatic adjustment pregnancy safety")
    
    if not parts:
        return ""
    
    query = "\n".join(parts)
    
    max_query_chars = int(cfg.get("consult_max_query_chars", 800))
    if len(query) > max_query_chars:
        query = query[:max_query_chars - 50] + "... [truncated]"
    
    return query.strip()


def build_full_note_summary(note_text: str, cfg: Dict[str, Any]) -> str:
    """Build a compact summary of the ENTIRE note for context.
    
    This provides the LLM with a complete picture of what the clinician wrote,
    so it can compare its analysis against the clinician's conclusions.
    
    Args:
        note_text: Full note text
        cfg: Configuration dict
    
    Returns:
        Compact note summary (max 2000 chars)
    """
    sections = extract_clinical_data_sections(note_text)
    
    parts: List[str] = []
    
    # Priority order: most important sections first
    section_order = [
        ("impression", "Impression"),
        ("history_of_present_illness", "History"),
        ("physical_exam", "Physical Exam"),
        ("plan", "Plan"),
        ("investigations", "Labs/Imaging"),
        ("medications", "Medications"),
        ("allergies", "Allergies"),
        ("past_medical_history", "PMH"),
        ("subjective", "Subjective"),
        ("objective", "Objective"),
        ("review_of_systems", "ROS"),
    ]
    
    max_per_section = int(cfg.get("consult_section_summary_chars", 300))
    total_max = int(cfg.get("consult_summary_max_chars", 2000))
    
    for key, label in section_order:
        text = sections.get(key, "")
        if text:
            limited = text[:max_per_section]
            if len(limited) < len(text):
                limited = limited.rstrip() + " ..."
            parts.append(f"{label}: {limited}")
    
    summary = "\n\n".join(parts)
    
    if len(summary) > total_max:
        summary = summary[:total_max - 50] + "... [summary truncated]"
    
    return summary.strip()
```

**Why this file:** Current code extracts only Impression + Plan (lines 41-48 of pipeline.py). This file adds extraction for ALL sections, enabling:
- `build_differential_query()`: Search based on symptoms/findings (anti-bias)
- `build_guideline_query()`: Search for guidelines on confirmed diagnoses
- `build_safety_query()`: Search for medication safety info
- `build_full_note_summary()`: Give the LLM context of the full note

### File 2: `server/services/consult_evidence_aggregator.py`

**Purpose:** Combine RAG + Web evidence into a single context string for the LLM.

**Why needed:** Current code only uses RAG. This file adds web search integration.

**Content:**
```python
from typing import Any, Dict, List, Optional, Tuple
import re


def format_web_results(web_items: List[Dict[str, Any]]) -> str:
    """Format web search results for inclusion in the LLM prompt.
    
    Args:
        web_items: List of dicts from searx_search() with keys:
            - title: str
            - url: str
            - snippet: str
            - source: str (always "web")
    
    Returns:
        Formatted string of web results
    """
    if not web_items:
        return ""
    
    parts: List[str] = []
    for i, item in enumerate(web_items, start=1):
        title = item.get("title", "").strip()
        url = item.get("url", "").strip()
        snippet = item.get("snippet", "").strip()
        
        # Clean up snippet
        if len(snippet) > 400:
            snippet = snippet[:397] + "..."
        
        line = f"[WEB {i}] {title}"
        if url:
            line += f" ({url})"
        if snippet:
            line += f"\n{snippet}"
        
        parts.append(line)
    
    return "\n\n".join(parts)


def combine_evidence(
    rag_context: str,
    web_context: str,
    rag_refs: List[Dict[str, Any]],
    web_items: List[Dict[str, Any]],
    cfg: Dict[str, Any],
) -> Tuple[str, List[Dict[str, Any]], Dict[str, Any]]:
    """Combine RAG and web evidence into a single context for the LLM.
    
    Args:
        rag_context: Context string from RAG query
        web_context: Context string from web search
        rag_refs: Normalized reference list from RAG
        web_items: Web results from searx_search
        cfg: Configuration dict
    
    Returns:
        Tuple of (combined_context, combined_refs, used_filters)
    """
    parts: List[str] = []
    combined_refs: List[Dict[str, Any]] = []
    used_filters: Dict[str, Any] = {}
    
    # 1) Add RAG evidence
    if rag_context.strip():
        parts.append("## Local Evidence Index (Guidelines, Clinical Papers)")
        parts.append(rag_context.strip())
        used_filters["rag_used"] = True
    else:
        used_filters["rag_used"] = False
    
    # 2) Add web evidence
    if web_context.strip():
        parts.append("## Current Web Evidence (Recent Guidelines, Safety Alerts)")
        parts.append(web_context.strip())
        used_filters["web_used"] = True
    else:
        used_filters["web_used"] = False
    
    # 3) Build combined references
    # RAG refs get index 1-N
    for ref in rag_refs:
        ref_entry = dict(ref)
        ref_entry["source_type"] = "rag"
        combined_refs.append(ref_entry)
    
    # Web refs get index N+1 onwards
    for i, item in enumerate(web_items):
        web_ref = {
            "index": len(combined_refs) + 1,
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "snippet": item.get("snippet", "")[:200],
            "source_type": "web",
            "source": item.get("source", "web"),
        }
        combined_refs.append(web_ref)
    
    # 4) Apply total character cap
    combined_context = "\n\n".join(parts)
    max_chars = int(cfg.get("consult_max_combined_chars", 8000))
    
    if len(combined_context) > max_chars:
        # Prioritize: RAG first, then web
        rag_part = parts[0] if parts else ""
        web_part = parts[1] if len(parts) > 1 else ""
        
        # Keep full RAG, trim web
        if len(rag_part) > max_chars * 0.7:
            rag_part = rag_part[:int(max_chars * 0.7)] + "... [RAG evidence truncated]"
        
        remaining = max_chars - len(rag_part) - 100
        if remaining > 100:
            web_part = web_part[:remaining] + "... [Web evidence truncated]"
        else:
            web_part = ""
        
        combined_context = rag_part + "\n\n" + web_part
    
    # 5) Record stats
    used_filters["rag_chars"] = len(rag_context)
    used_filters["web_chars"] = len(web_context)
    used_filters["combined_chars"] = len(combined_context)
    used_filters["rag_refs"] = len(rag_refs)
    used_filters["web_refs"] = len(web_items)
    
    return combined_context, combined_refs, used_filters
```

**Why this file:** Current pipeline has no way to combine RAG + Web. This file:
- Formats web results for the prompt
- Combines both evidence types with priority (RAG first, web second)
- Respects character limits
- Builds combined reference list with source types

### File 3: `server/core/consult/multi_query_pipeline.py`

**Purpose:** New consult comment generator that runs multiple RAG queries + web search in parallel.

**Why needed:** Current pipeline does ONE RAG query. This replaces it with parallel queries.

**Content:**
```python
import asyncio
import re
import json
from typing import Any, Dict, List, Optional, Tuple

from server.services.qa_web_search import searx_search


async def _query_rag_parallel(
    focus_query: str,
    rag_client,
    cfg: Dict[str, Any],
) -> Tuple[str, List[Dict[str, Any]], Dict[str, Any]]:
    """Query RAG with timeout.
    
    Args:
        focus_query: Query string
        rag_client: RAGHttpClient instance
        cfg: Configuration dict
    
    Returns:
        Tuple of (context, refs, used_filters)
    """
    rag_timeout = int(cfg.get("rag_timeout_ms", 25000)) / 1000.0
    
    async def _do():
        return await rag_client.query(focus_query)
    
    try:
        return await asyncio.wait_for(_do(), timeout=rag_timeout)
    except asyncio.TimeoutError:
        return "", [], {"error": "RAG query timed out"}
    except Exception as e:
        return "", [], {"error": str(e)[:200]}


async def _build_and_format_web_context(
    query: str,
    cfg: Dict[str, Any],
) -> str:
    """Run web search and format results.
    
    Args:
        query: Search query string
        cfg: Configuration dict
    
    Returns:
        Formatted web context string
    """
    from server.services.consult_evidence_aggregator import format_web_results
    
    web_k = int(cfg.get("consult_web_k", 6))
    web_items = await searx_search(query, limit=web_k)
    return format_web_results(web_items)


async def generate_consult_comment_v2(
    gen_id: str,
    note_text: str,
    cfg: Dict[str, Any],
    *,
    consult_store,
    generation_meta,
    rag_client,
    get_rag_comment_llm,
    normalize_reference_items,
    clean_model_output_final,
) -> None:
    """Generate consult comment using multi-source evidence (RAG + Web).
    
    This is the new pipeline that replaces the old single-query approach.
    
    Steps:
    1. Extract clinical data sections from the note
    2. Build three RAG queries: differential, guideline, safety
    3. Build web search query
    4. Run all queries in parallel
    5. Combine evidence
    6. Generate comment with structured output
    7. Store results
    """
    try:
        consult_store[gen_id] = {"status": "pending"}
        
        # STEP 1: Extract sections
        from server.services.consult_focus_builder import (
            extract_clinical_data_sections,
            build_differential_query,
            build_guideline_query,
            build_safety_query,
            build_full_note_summary,
        )
        
        sections = extract_clinical_data_sections(note_text)
        
        # STEP 2: Build queries
        diff_query = build_differential_query(note_text, cfg)
        guideline_query = build_guideline_query(note_text, cfg)
        safety_query = build_safety_query(note_text, cfg)
        
        # Determine which queries to run based on config
        queries_to_run = cfg.get("consult_rag_queries", ["differential", "guideline", "safety"])
        
        # STEP 3: Run all queries in parallel
        tasks: List[asyncio.Task] = []
        
        # RAG queries
        rag_queries = []
        if "differential" in queries_to_run and diff_query:
            rag_queries.append(("differential", diff_query))
        if "guideline" in queries_to_run and guideline_query:
            rag_queries.append(("guideline", guideline_query))
        if "safety" in queries_to_run and safety_query:
            rag_queries.append(("safety", safety_query))
        
        # Create RAG query tasks
        for label, query in rag_queries:
            task = asyncio.create_task(
                _query_rag_parallel(query, rag_client, cfg)
            )
            tasks.append(task)
        
        # Web search task (runs parallel to RAG)
        web_enabled = cfg.get("consult_web_enabled", True)
        web_context_task = None
        if web_enabled and diff_query:
            web_context_task = asyncio.create_task(
                _build_and_format_web_context(diff_query, cfg)
            )
            tasks.append(web_context_task)
        
        # STEP 4: Wait for all queries
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Parse results
        rag_results: Dict[str, Tuple[str, List[Dict], Dict]] = {}
        web_context = ""
        
        for i, (label, _) in enumerate(rag_queries):
            if i < len(results):
                result = results[i]
                if isinstance(result, Exception):
                    rag_results[label] = ("", [], {"error": str(result)[:200]})
                else:
                    rag_results[label] = result
        
        # Get web results
        if web_context_task and tasks.index(web_context_task) < len(results):
            result = results[tasks.index(web_context_task)]
            if isinstance(result, Exception):
                web_context = ""
            else:
                web_context = result
        
        # STEP 5: Combine evidence
        from server.services.consult_evidence_aggregator import combine_evidence
        
        # Combine RAG contexts (differential is primary)
        combined_rag_ctx = ""
        combined_rag_refs = []
        
        # Prioritize: differential > guideline > safety
        priority_labels = ["differential", "guideline", "safety"]
        
        max_rag_chars = int(cfg.get("consult_rag_combined_chars", 5000))
        
        for label in priority_labels:
            if label in rag_results:
                ctx, refs, used = rag_results[label]
                if ctx.strip():
                    if combined_rag_ctx and len(combined_rag_ctx) + len(ctx) > max_rag_chars:
                        # Trim existing to make room
                        remaining = max_rag_chars - len(ctx) - 200
                        if remaining > 0:
                            combined_rag_ctx = combined_rag_ctx[:remaining] + "... [prior RAG evidence truncated]"
                        else:
                            break
                    
                    if combined_rag_ctx:
                        combined_rag_ctx += "\n\n"
                    combined_rag_ctx += f"### RAG Results ({label})\n" + ctx.strip()
                    combined_rag_refs.extend(refs)
        
        # Combine all evidence
        combined_context, combined_refs, used_filters = combine_evidence(
            combined_rag_ctx,
            web_context,
            combined_rag_refs,
            [],  # web refs already formatted in web_context
            cfg,
        )
        
        # STEP 6: Build the prompt
        # Extract note summary for LLM context
        note_summary = build_full_note_summary(note_text, cfg)
        
        # Extract confirmed/ruled-out markers
        imp = sections.get("impression", "")
        plan = sections.get("plan", "")
        
        confirmed_markers = cfg.get("consult_confirmed_markers", ["confirmed", "biopsy", "pathology", "definitive"])
        ruledout_markers = cfg.get("consult_ruledout_markers", ["ruled out", "excluded", "negative for", "not consistent with"])
        
        # Simple marker extraction
        confirmed_statements = []
        ruledout_statements = []
        
        imp_plan_text = f"{imp}\n{plan}"
        for sentence in re.split(r'(?<=[.!?])\s+', imp_plan_text):
            lowered = sentence.lower()
            if any(m.lower() in lowered for m in confirmed_markers):
                confirmed_statements.append(sentence.strip())
            if any(m.lower() in lowered for m in ruledout_markers):
                ruledout_statements.append(sentence.strip())
        
        # STEP 7: Build the prompt with structured output request
        prompt = _build_consult_prompt(
            note_summary=note_summary,
            confirmed=confirmed_statements,
            ruled_out=ruledout_statements,
            evidence_context=combined_context,
            cfg=cfg,
        )
        
        # STEP 8: Call LLM
        rag_llm = get_rag_comment_llm(cfg)
        consult_temp = float(cfg.get("consult_comment_temperature", 0.3))
        max_tokens = int(cfg.get("consult_comment_max_tokens", 4096))
        
        comment_text = await rag_llm.collect_completion(
            prompt,
            temperature=consult_temp,
            max_tokens=max_tokens,
            stop=[],
        )
        comment = clean_model_output_final(comment_text).strip()
        
        # STEP 9: Try to parse structured output
        structured = _try_parse_structured(comment)
        
        # STEP 10: Store results
        norm_refs, _ = normalize_reference_items(
            combined_refs,
            cap=len(combined_refs),
            sort_key=lambda x: x.get("score", 0.0),
        )
        
        consult_store[gen_id] = {
            "status": "done",
            "comment": comment,
            "refs": norm_refs,
        }
        
        # Store metadata
        m = (generation_meta.get(gen_id) or {}).copy()
        m.update({
            "consult_refs": norm_refs,
            "consult_used": used_filters,
            "consult_context": combined_context,
            "consult_sections": list(sections.keys()),
            "consult_queries": {
                "differential": diff_query,
                "guideline": guideline_query,
                "safety": safety_query,
            },
        })
        generation_meta[gen_id] = m
        
    except Exception as exc:
        import traceback
        error_msg = f"{str(exc)[:200]}\n{traceback.format_exc()[:200]}"
        consult_store[gen_id] = {
            "status": "error",
            "error": error_msg,
        }


def _build_consult_prompt(
    *,
    note_summary: str,
    confirmed: List[str],
    ruled_out: List[str],
    evidence_context: str,
    cfg: Dict[str, Any],
) -> str:
    """Build the LLM prompt for consult comment generation.
    
    This is a COMPLETELY REDESIGNED prompt that forces critical analysis.
    """
    
    confirmed_text = "\n".join(f"- {s}" for s in confirmed) if confirmed else "None explicitly stated."
    ruled_out_text = "\n".join(f"- {s}" for s in ruled_out) if ruled_out else "None explicitly stated."
    
    prompt = f"""/nothink

You are a senior clinical consultant reviewing this case. YOUR JOB IS TO IDENTIFY WHAT IS MISSING OR WRONG, NOT TO CONFIRM WHAT THE CLINICIAN ALREADY WROTE.

CRITICAL INSTRUCTIONS:
1. Start from the PATIENT'S SYMPTOMS, EXAM FINDINGS, AND LABS — NOT the clinician's diagnosis.
2. Build your OWN differential diagnosis from the clinical data.
3. Compare your differential with the clinician's diagnosis.
4. Flag diagnoses the clinician missed that are supported by the clinical data.
5. Check guidelines for confirmed diagnoses — does the plan include all required steps?
6. Check for safety issues: medication interactions, contraindications, missing safety labs.
7. Highlight RED FLAGS: conditions requiring immediate action or that could be dangerous.

OUTPUT FORMAT:

**SAFETY ALERTS** (only if found, otherwise omit this section):
- 🔴 CRITICAL: [Issue description] — [Guideline/source] — [Recommended action]
- 🟡 WARNING: [Issue description] — [Guideline/source] — [Recommended action]
- 🔵 INFO: [Issue description] — [Guideline/source] — [Recommended action]

**MISSING DIAGNOSES TO CONSIDER** (only if found, otherwise omit):
- [Condition] — Probability: [High/Medium/Low] — Supported by: [Key findings] — Guideline: [Reference]

**GUIDELINE GAPS** (check for each confirmed diagnosis):
- [Diagnosis] → Missing: [What guideline says should be done] — Guideline: [Name, Year]

**MISSING WORKUP**:
- [Test] — Reason: [Why needed] — Urgency: [Urgent/Routine] — Guideline: [Reference]

**DIFFERENTIAL DIAGNOSIS** (ranked by probability):
1. [Condition] — [Probability] — [Supporting findings] — [Already considered by clinician: Yes/No]
2. [Condition] — [Probability] — [Supporting findings] — [Already considered by clinician: Yes/No]

**MANAGEMENT ADJUSTMENTS** (only if found):
- [Recommendation] — Reason: [Explanation] — Guideline: [Reference]

**WHAT IS APPROPRIATE** (confirm good practice):
- [What clinician did right] — Guideline support: [Reference]

RULES:
- Every recommendation must cite evidence from the context below.
- Mark uncertain items as low confidence.
- Do not repeat the same point across sections.
- Be concise — each section should be scannable in under 30 seconds.
- Use 🔴 for critical (immediate action needed), 🟡 for warning (important), 🔵 for info (nice to have).
- If you find NO issues, write: "The clinical approach appears comprehensive based on available evidence."
- DO NOT echo back what the clinician already wrote — add VALUE by identifying gaps.

CLINICAL DATA:
{note_summary}

CONFIRMED DIAGNOSES:
{confirmed_text}

RULED OUT:
{ruled_out_text}

EVIDENCE CONTEXT:
{evidence_context}

COMMENT:
"""
    return prompt


def _try_parse_structured(comment: str) -> Optional[Dict[str, Any]]:
    """Try to parse structured JSON from the comment.
    
    Returns the parsed JSON if found, otherwise None.
    """
    # Look for JSON block
    json_match = re.search(r'\{[^{}]*\}', comment, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group(0))
        except json.JSONDecodeError:
            pass
    return None
```

**Why this file:** This is the NEW consult pipeline. Key changes vs old:
1. **Parallel queries:** differential + guideline + safety + web search all run in parallel (asyncio.gather)
2. **Broader evidence:** RAG searches symptoms/findings, not just clinician conclusions
3. **Web search:** Uses SearXNG for real-time evidence
4. **Redesigned prompt:** Forces critical analysis, not echo-back
5. **Structured output:** Requests organized sections with severity levels

## Config Changes (`config/config.json`)

Add these new keys to the RAG section:
```json
{
  "consult_web_enabled": true,
  "consult_web_k": 6,
  "consult_rag_queries": ["differential", "guideline", "safety"],
  "consult_max_query_chars": 1500,
  "consult_section_summary_chars": 300,
  "consult_summary_max_chars": 2000,
  "consult_rag_combined_chars": 5000,
  "consult_max_combined_chars": 8000,
  "consult_comment_max_tokens": 4096,
  "consult_comment_temperature": 0.3
}
```

## Integration Changes (`server/routes/notes.py`)

### Change 1: Update `_generate_consult_comment()` wrapper (line 1339-1362)

**Current code:**
```python
async def _generate_consult_comment(
    gen_id: str,
    note_text: str,
    cfg: Dict,
    *,
    strategy: str = "sections",
) -> None:
    await _generate_consult_comment_impl(
        gen_id,
        note_text,
        cfg,
        strategy=strategy,
        consult_store=_consult_comment_store,
        generation_meta=_generation_meta,
        extract_marker_sentences=_extract_marker_sentences,
        extract_focus_sections=_extract_rag_focus_sections,
        fallback_focus_from_note=_fallback_focus_from_note,
        rag_tail_window=_rag_tail_window,
        rag_head_window=_rag_head_window,
        rag_client_from_cfg=_rag_client_from_cfg,
        get_rag_comment_llm=_get_rag_comment_llm,
        normalize_reference_items=_normalize_reference_items,
        clean_model_output_final=clean_model_output_final,
    )
```

**New code:**
```python
async def _generate_consult_comment(
    gen_id: str,
    note_text: str,
    cfg: Dict,
    *,
    strategy: str = "sections",
) -> None:
    # Use new multi-source pipeline
    try:
        rag_client = _rag_client_from_cfg(cfg)
        await generate_consult_comment_v2(
            gen_id,
            note_text,
            cfg,
            consult_store=_consult_comment_store,
            generation_meta=_generation_meta,
            rag_client=rag_client,
            get_rag_comment_llm=_get_rag_comment_llm,
            normalize_reference_items=_normalize_reference_items,
            clean_model_output_final=clean_model_output_final,
        )
    except Exception as e:
        # Fallback to old pipeline if new one fails
        import logging
        logging.warning(f"Consult v2 failed, falling back to v1: {e}")
        await _generate_consult_comment_impl(
            gen_id,
            note_text,
            cfg,
            strategy=strategy,
            consult_store=_consult_comment_store,
            generation_meta=_generation_meta,
            extract_marker_sentences=_extract_marker_sentences,
            extract_focus_sections=_extract_rag_focus_sections,
            fallback_focus_from_note=_fallback_focus_from_note,
            rag_tail_window=_rag_tail_window,
            rag_head_window=_rag_head_window,
            rag_client_from_cfg=_rag_client_from_cfg,
            get_rag_comment_llm=_get_rag_comment_llm,
            normalize_reference_items=_normalize_reference_items,
            clean_model_output_final=clean_model_output_final,
        )
```

**Why:** Keep old pipeline as fallback in case new one has bugs. This is a safety net.

### Change 2: Add import at the top of `notes.py`

Add to the import section:
```python
from core.consult.multi_query_pipeline import generate_consult_comment_v2
```

## Testing Plan

### Test 1: Section extraction
**Test file:** `tests/test_consult_focus_builder.py`

```python
import pytest
from server.services.consult_focus_builder import (
    extract_section_by_heading,
    extract_clinical_data_sections,
    build_differential_query,
    build_guideline_query,
    build_safety_query,
    build_full_note_summary,
)

SAMPLE_NOTE = """
## Patient Identification
Mr. John Doe, 65 year-old man, presented today for assessment regarding chest pain.

## History of Present Illness
Patient reports sudden onset of sharp chest pain 2 days ago. Pain is worse with exertion and improves with rest. Associated with shortness of breath and palpitations. No nausea, no diaphoresis.

## Physical Exam
Cardiovascular: HR 92, BP 150/95, regular rhythm, no murmurs.
Respiratory: Clear to auscultation bilaterally.
General: No distress.

## Investigations
ECG: Sinus tachycardia, ST depression in leads V4-V6.
Troponin: 0.05 ng/mL (normal <0.04).
CBC: Normal.

## Impression
1. Acute Coronary Syndrome (unstable angina)
2. Hypertension, Stage 2

## Plan
1. Start aspirin 81mg daily
2. Start metoprolol 25mg twice daily
3. Refer for cardiac catheterization
4. Follow up in 2 weeks
"""

CFG = {
    "consult_max_query_chars": 1500,
    "consult_section_summary_chars": 300,
    "consult_summary_max_chars": 2000,
}


def test_extract_section_by_heading():
    hpi = extract_section_by_heading(SAMPLE_NOTE, "History of Present Illness", 
                                     aliases=["HPI"])
    assert "chest pain" in hpi.lower()
    assert "shortness of breath" in hpi.lower()


def test_extract_clinical_data_sections():
    sections = extract_clinical_data_sections(SAMPLE_NOTE)
    assert "history_of_present_illness" in sections
    assert "physical_exam" in sections
    assert "investigations" in sections
    assert "impression" in sections
    assert "plan" in sections


def test_build_differential_query():
    query = build_differential_query(SAMPLE_NOTE, CFG)
    # Should include symptoms, NOT the diagnosis
    assert "chest pain" in query.lower()
    assert "shortness of breath" in query.lower()
    assert "65" in query  # Age
    # Should NOT include clinician conclusions
    assert "acute coronary syndrome" not in query.lower()


def test_build_guideline_query():
    query = build_guideline_query(SAMPLE_NOTE, CFG)
    # Should include diagnoses AND plan
    assert "acute coronary syndrome" in query.lower()
    assert "hypertension" in query.lower()
    assert "guidelines" in query.lower()


def test_build_safety_query():
    query = build_safety_query(SAMPLE_NOTE, CFG)
    # Should include medications
    assert "aspirin" in query.lower()
    assert "metoprolol" in query.lower()
    assert "interactions" in query.lower()


def test_build_full_note_summary():
    summary = build_full_note_summary(SAMPLE_NOTE, CFG)
    assert len(summary) <= CFG["consult_summary_max_chars"]
    assert "chest pain" in summary.lower()
```

### Test 2: Evidence aggregation
**Test file:** `tests/test_consult_evidence_aggregator.py`

```python
import pytest
from server.services.consult_evidence_aggregator import (
    format_web_results,
    combine_evidence,
)

CFG = {
    "consult_max_combined_chars": 8000,
}


def test_format_web_results():
    web_items = [
        {
            "title": "ACC/AHA Guidelines for ACS",
            "url": "https://example.com/guideline",
            "snippet": "Aspirin 81mg daily is recommended for all ACS patients...",
            "source": "web",
        },
    ]
    formatted = format_web_results(web_items)
    assert "[WEB 1]" in formatted
    assert "ACC/AHA Guidelines" in formatted


def test_combine_evidence():
    rag_ctx = "RAG evidence about ACS management..."
    web_ctx = "Web evidence about recent guidelines..."
    rag_refs = [{"title": "ACC/AHA 2023", "score": 0.9}]
    web_items = []
    
    combined, refs, filters = combine_evidence(
        rag_ctx, web_ctx, rag_refs, web_items, CFG
    )
    
    assert "RAG evidence" in combined
    assert "Web evidence" in combined
    assert filters["rag_used"] is True
    assert filters["web_used"] is True
```

### Test 3: Integration test (mock RAG + Web)
**Test file:** `tests/test_multi_query_pipeline.py`

```python
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock
from core.consult.multi_query_pipeline import generate_consult_comment_v2


async def test_generate_consult_comment_v2():
    # Mock dependencies
    mock_store = {}
    mock_meta = {}
    mock_rag_client = MagicMock()
    mock_rag_client.query = AsyncMock(return_value=(
        "RAG evidence text",  # context
        [{"title": "Test Guideline"}],  # refs
        {},  # used_filters
    ))
    
    mock_llm = MagicMock()
    mock_llm.collect_completion = AsyncMock(return_value="""
**SAFETY ALERTS**:
- 🔴 CRITICAL: Missing ECG — ACC/AHA guidelines require ECG within 10 minutes

**GUIDELINE GAPS**:
- ACS → Missing: Cardiology referral within 24 hours — ACC/AHA 2023

**WHAT IS APPROPRIATE**:
- Aspirin 81mg daily — ACC/AHA guidelines
""")
    
    await generate_consult_comment_v2(
        gen_id="test-123",
        note_text=SAMPLE_NOTE,  # From test_consult_focus_builder.py
        cfg=CFG,
        consult_store=mock_store,
        generation_meta=mock_meta,
        rag_client=mock_rag_client,
        get_rag_comment_llm=lambda cfg: mock_llm,
        normalize_reference_items=lambda refs, **kw: (refs, []),
        clean_model_output_final=lambda x: x.strip(),
    )
    
    assert mock_store["test-123"]["status"] == "done"
    assert "SAFETY ALERTS" in mock_store["test-123"]["comment"]
    assert mock_rag_client.query.called  # RAG was queried
```

## Implementation Order

1. **Create `server/services/consult_focus_builder.py`** — Section extraction logic
2. **Create `server/services/consult_evidence_aggregator.py`** — Evidence combination logic
3. **Create `server/core/consult/multi_query_pipeline.py`** — New pipeline
4. **Update `config/config.json`** — Add new config keys
5. **Update `server/routes/notes.py`** — Import + fallback wrapper
6. **Create tests** — `tests/test_consult_focus_builder.py`, `tests/test_consult_evidence_aggregator.py`, `tests/test_multi_query_pipeline.py`
7. **Run tests** — `pytest tests/test_consult_*.py -v`
8. **Test manually** — Generate a note, check consult comments

## Risk Assessment

| Risk | Mitigation |
|------|-----------|
| New pipeline fails | Fallback to old pipeline (try/except in notes.py) |
| Web search is slow | 12-second timeout in searx_search, parallel execution |
| RAG returns empty | Old pipeline has retry logic, new pipeline checks for empty context |
| Prompt too long | Character caps at each stage (query, section, summary, combined) |
| LLM doesn't follow format | Prompt has explicit instructions, can add retry if headers missing |
