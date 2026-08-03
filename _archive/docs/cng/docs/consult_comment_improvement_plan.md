# Evidence-Based Comments: Comprehensive Improvement Plan

## Executive Summary

The current Evidence Based Comments system produces repetitive, low-value output that mostly echoes the clinician's note. It needs to become an active clinical safety and quality tool that identifies what the clinician missed, verifies guideline compliance, and provides actionable alerts.

## Current Architecture

### Backend (`server/core/consult/pipeline.py`)
- **Strategy modes:** `sections` (default), `full_note`, `llm_query`
- **Focus extraction:** Pulls Impression + Plan sections, uses heuristic markers for "confirmed" vs "ruled out"
- **RAG query:** Single query using extracted focus, up to 16 top-k results
- **LLM prompt:** ~500 tokens asking for 5 sections (Differential, Workup, Management, Safety, What's Appropriate)
- **Max output:** 8,196 tokens (configurable)
- **Temperature:** 0.4
- **Safety:** Structure retry if required headers missing, refusal retry if "insufficient evidence"
- **No web search:** Consult relies entirely on RAG (local index)

### Frontend (`PCHost/web/js/workspace_app.js`)
- Button opens a card, polls `/generation/{gen_id}/consult_comment`
- Shows "Generating..." then polls until status is "done" or "error"
- Renders markdown via `renderMarkdownSimple`
- Shows references list

### Config (`config/config.json`)
```json
"rag_timeout_ms": 25000,
"rag_top_k": 16,
"rag_consult_top_k_cap": 6,
"rag_evidence_clip_chars": 2000,
"rag_focus_summary_words": 250,
"rag_max_context_words": 2400,
"consult_confirmed_markers": ["confirmed", "biopsy", "pathology", "definitive"],
"consult_ruledout_markers": ["ruled out", "excluded", "negative for", "not consistent with"],
"consult_comment_max_tokens": 8196,
"consult_comment_temperature": 0.4
```

### QA Chat (for comparison: `server/routes/qa_chat.py`)
- **Does both RAG + Web search** (SearXNG)
- **Max output:** 6,144 tokens
- **Prompt:** Allows knowledge fallback when evidence is weak
- **Web evidence:** 6 results via SearXNG
- **Prompt structure:** Conversation context + summary + RAG + Web + user question

## Root Cause Analysis: Why It's Useless

### 1. Narrow RAG Query (Primary Issue)
The consult pipeline extracts only the Impression + Plan sections, which are exactly what the clinician already decided. This means:
- RAG retrieves evidence matching the clinician's CONCLUSIONS
- The LLM then generates comments aligned with those conclusions
- Result: "Your differential includes X, which is appropriate based on evidence Y" — just echoing back

**Example:** Clinician writes "Impression: Hypertension, Stage 2" → RAG searches "hypertension stage 2" → Returns guidelines confirming hypertension treatment → LLM says "Your diagnosis of hypertension is consistent with guidelines" — zero value added.

### 2. No Web Search
The QA pathway uses SearXNG for real-time web evidence. Consult does NOT. This means:
- Missing recent guidelines (post-index-date)
- No access to drug safety alerts, FDA warnings, new recommendations
- Stale or incomplete evidence base

### 3. Prompt Doesn't Force Critical Analysis
The current prompt asks for:
```
1) Differential to Consider (ranked, brief rationale)
2) Workup to Add Now
3) Management Adjustments to Consider
4) Safety / Red Flags
5) What Is Already Appropriate in Current Plan
```

But the LLM sees only RAG results that match the clinician's conclusions, so it defaults to confirming rather than challenging.

### 4. Focus Extraction is Too Narrow
- Only Impression + Plan sections are used
- HPI (symptoms), Physical Exam (findings), and Labs/Imaging (results) are ignored for RAG query
- This means the differential is built from the clinician's diagnosis, not from the clinical data

### 5. Evidence Context is Capped Too Low
- `rag_max_context_words: 2400` — only ~2,400 words of evidence
- `rag_evidence_clip_chars: 2000` — only ~2,000 characters per evidence snippet
- For comprehensive guideline checking, this is insufficient

### 6. No Structured Output Format
The comment is free-form text with required headers. There's no structured data format for the UI to render:
- No severity levels (⚠️ Critical vs ℹ️ Informational)
- No categorization (Guideline gap vs Safety alert vs Missed opportunity)
- No prioritization (Must act now vs Consider)

## Improvement Plan

### Phase 1: Multi-Source Evidence Gathering (Backend)

**Goal:** Expand the evidence base beyond RAG to include web search, and broaden the query scope.

**Changes:**

#### 1.1 Add Web Search to Consult (like QA chat)
```python
# server/routes/notes.py
async def _generate_consult_comment(...):
    # Run RAG + Web in parallel
    rag_task = asyncio.create_task(_rag_query(focus, cfg))
    web_task = asyncio.create_task(searx_search(focus, limit=6))
    rag_ctx, rag_refs = await rag_task
    web_items = await web_task
    
    # Combine evidence
    combined_ctx = f"""
    Local Evidence Index (guidelines, papers):
    {rag_ctx}
    
    Current Web Evidence (recent guidelines, safety alerts):
    {web_ctx}
    """
```

#### 1.2 Broaden Focus Query
Instead of querying only Impression + Plan, query the clinical data:
```python
# New strategy: "clinical_data"
def build_clinical_data_focus(note_text):
    """Extract key clinical facts for RAG query, NOT clinician conclusions."""
    hpi = extract_section(note_text, "History of Present Illness")
    physical_exam = extract_section(note_text, "Physical Exam")
    labs = extract_section(note_text, "Labs and Results") or extract_section(note_text, "Investigations")
    imaging = extract_section(note_text, "Imaging")
    
    # Build a query focused on SYMPTOMS + FINDINGS + RESULTS
    # NOT the clinician's diagnosis
    focus = f"""
    Patient presentation: {hpi[:500]}
    Physical findings: {physical_exam[:300]}
    Lab results: {labs[:300]}
    Imaging: {imaging[:200]}
    """
    return focus.strip()
```

#### 1.3 Separate RAG Queries for Different Analysis Types
Instead of one monolithic query, run parallel queries:
```python
# Differential diagnosis query
diff_query = build_clinical_data_focus(note_text)

# Guideline compliance query (based on what the clinician diagnosed)
guideline_query = extract_impression_and_plan(note_text)

# Safety check query (medications, allergies, contraindications)
safety_query = extract_medications_and_allergies(note_text)

# Run all three in parallel
diff_task = asyncio.create_task(_rag_query(diff_query, cfg))
guideline_task = asyncio.create_task(_rag_query(guideline_query, cfg))
safety_task = asyncio.create_task(_rag_query(safety_query, cfg))
web_task = asyncio.create_task(searx_search(diff_query, limit=6))
```

**Files to create:**
- `server/services/consult_focus_builder.py` — Functions to extract clinical data vs conclusions
- `server/core/consult/multi_query.py` — Parallel RAG + Web query orchestration

**Config changes:**
```json
"consult_web_enabled": true,
"consult_web_k": 6,
"consult_rag_queries": ["differential", "guideline", "safety"],
"consult_max_context_words": 4800,
"consult_evidence_clip_chars": 4000
```

### Phase 2: Redesigned Prompt (Backend)

**Goal:** Force the LLM to produce critical analysis, not confirmation bias.

**Key changes to the prompt:**

#### 2.1 Explicit Instructions for Critical Analysis
```
You are a senior clinical consultant reviewing this case for potential gaps in diagnosis, workup, 
management, and safety. YOUR JOB IS TO IDENTIFY WHAT IS MISSING OR WRONG, NOT TO CONFIRM 
WHAT THE CLINICIAN ALREADY WROTE.

ANALYSIS FRAMEWORK:
1. Start from the PATIENT'S SYMPTOMS, EXAM FINDINGS, AND LABS — NOT the clinician's diagnosis.
2. Build your OWN differential diagnosis from the clinical data.
3. Compare YOUR differential with the clinician's diagnosis.
4. Flag any diagnoses the clinician missed that are supported by the clinical data.
5. Check current guidelines for the confirmed diagnoses — does the plan include all required steps?
6. Check for safety issues: medication interactions, contraindications, missing safety labs.
7. Highlight RED FLAGS: conditions that require immediate action or could be dangerous.

OUTPUT FORMAT (structured):
```

#### 2.2 Structured Output Format
```json
{
  "safety_alerts": [
    {
      "severity": "critical|warning|info",
      "category": "missed_diagnosis|guideline_gap|safety_risk|missing_workup|medication_issue",
      "finding": "Specific issue found",
      "evidence": "Guideline/source",
      "action": "What the clinician should do"
    }
  ],
  "differential": [
    {
      "rank": 1,
      "condition": "Condition name",
      "probability": "high|medium|low",
      "supported_by": "Key clinical findings",
      "already_considered_by_clinician": true|false,
      "guideline": "Relevant guideline"
    }
  ],
  "guideline_gaps": [
    {
      "diagnosis": "Diagnosis being checked",
      "missing_action": "What guideline says should be done",
      "guideline": "Guideline name and year",
      "severity": "critical|recommended|optional"
    }
  ],
  "missing_workup": [
    {
      "test": "Test name",
      "reason": "Why it's needed based on findings",
      "guideline": "Guideline reference",
      "urgency": "urgent|routine"
    }
  ],
  "what_is_appropriate": [
    {
      "action": "What clinician did right",
      "guideline": "Guideline that supports this"
    }
  ]
}
```

#### 2.3 Confidence Levels
Each finding should include a confidence level:
- `high`: Strong guideline support, clear clinical correlation
- `medium`: Reasonable evidence, some uncertainty
- `low`: Weak evidence, worth considering but not certain

**Files to modify:**
- `server/core/consult/pipeline.py` — New prompt builder, structured output parsing

### Phase 3: Frontend UI Improvements

**Goal:** Present the evidence-based comment as an actionable, prioritized safety tool.

#### 3.1 Card-Based Layout
Instead of plain text, render structured data as cards:
```
┌─ ⚠️ CRITICAL SAFETY ALERT ──────────────────────────────┐
│ Missing HbA1c for diabetes management                     │
│ ADA 2024 guidelines require HbA1c monitoring               │
│ → Action: Order HbA1c                                       │
│ Confidence: High | Guideline: ADA Standards of Care 2024   │
└───────────────────────────────────────────────────────────┘

┌─ ℹ️ GUIDELINE GAP ────────────────────────────────────────┐
│ No statin prescribed for secondary prevention              │
│ ACC/AHA 2023 recommends statin for all CAD patients        │
│ → Action: Consider statin initiation                        │
│ Confidence: Medium | Guideline: ACC/AHA 2023               │
└───────────────────────────────────────────────────────────┘

┌─ ✅ APPROPRIATE ────────────────────────────────────────────┐
│ Aspirin dose (81mg) is correct for cardioprotection         │
│ Matches current guideline recommendation                    │
└─────────────────────────────────────────────────────────────┘
```

#### 3.2 Severity-Based Color Coding
- 🔴 **Critical** (red) — Immediate action required, potential patient harm
- 🟡 **Warning** (yellow) — Important guideline gap or missing workup
- 🔵 **Info** (blue) — Optional recommendations, nice-to-haves
- 🟢 **Appropriate** (green) — Confirms good practice

#### 3.3 Collapsible Sections
```
[▼] Safety Alerts (2 critical, 1 warning)
[▼] Guideline Gaps (3)
[▼] Missing Workup (4)
[▼] Differential Diagnoses (5)
[▼] What Is Appropriate (3)
```

**Files to modify:**
- `PCHost/web/js/workspace_app.js` — New rendering function for structured comment
- `PCHost/web/css/workspace.css` — Card styles, severity colors, collapsible sections

### Phase 4: Enhanced Analysis Capabilities

**Goal:** Go deeper into clinical reasoning to provide truly actionable insights.

#### 4.1 Medication Safety Check
```python
# Extract all medications from the note
medications = extract_medications_from_note(note_text)

# Query for interactions, contraindications, dosing issues
interaction_query = f"""
Medications: {medications}
Check for: drug interactions, contraindications, renal/hepatic dosing adjustments, 
pregnancy safety, age-related dosing, duplicate therapies
"""
```

#### 4.2 Guideline Compliance Checklist
For each confirmed diagnosis, check against the relevant guideline:
```python
# Example: Hypertension
hypertension_checklist = {
    "diagnosis": "Hypertension",
    "guideline": "ACC/AHA 2017",
    "required_actions": [
        "Confirm diagnosis with multiple readings",
        "Assess cardiovascular risk (10-year ASCVD)",
        "Check for secondary causes if resistant",
        "Order baseline labs: CBC, BMP, lipid panel, urinalysis",
        "First-line medication: ACEi/ARB/CCB/thiazide",
        "Lifestyle modification: DASH diet, sodium restriction, exercise"
    ],
    "severity_levels": {
        "CBC and BMP": "critical",  # Must have before starting meds
        "Cardiovascular risk": "recommended",
        "DASH diet": "recommended"
    }
}
```

#### 4.3 Red Flag Detection
Patterns that indicate potential diagnostic error or delay:
```python
red_flag_patterns = {
    "chest_pain_no_ecg": "Chest pain without ECG in the last 10 minutes",
    "fever_no_cultures": "Fever >38°C without blood cultures",
    "headache_no_imaging": "New headache with neurological signs without imaging",
    "jaundice_no_lfts": "Jaundice without liver function tests",
    "leg_swelling_no_dvt": "Unilateral leg swelling without DVT exclusion",
    "abdominal_pain_no_ct": "Severe abdominal pain without imaging"
}
```

#### 4.4 Diagnostic Probability Assessment
For each differential diagnosis, estimate probability based on clinical data:
```
Differential Diagnosis (ranked by probability):

1. 🏆 PULMONARY EMBOLISM (High probability: 65-85%)
   - Supported by: Sudden onset dyspnea, tachycardia, unilateral leg swelling
   - Missing: D-dimer, CT pulmonary angiography
   → Action: Order D-dimer STAT, prepare for CTPA if positive

2. 🏆 COPD EXACERBATION (Medium probability: 25-40%)
   - Supported by: Smoker, wheezing, chronic cough
   - Missing: ABG, chest X-ray
   → Action: Order CXR, ABG, start bronchodilators

3. 🏆 PNEUMONIA (Low probability: 10-20%)
   - Supported by: Fever, cough
   - Against: No focal findings on exam
   → Action: Consider if CXR shows consolidation
```

**Files to create:**
- `server/services/consult_safety_checker.py` — Medication safety, red flag detection
- `server/services/consult_guideline_checker.py` — Guideline compliance checklists
- `server/services/consult_differential.py` — Differential diagnosis probability assessment

### Phase 5: Config & Performance Optimization

**Goal:** Ensure the system is responsive and configurable.

#### 5.1 Parallel Execution
All RAG queries and web search should run in parallel:
```python
async def generate_consult_comment_parallel(note_text, cfg):
    # Build queries
    diff_query = build_differential_query(note_text)
    guideline_query = build_guideline_query(note_text)
    safety_query = build_safety_query(note_text)
    
    # Run everything in parallel
    tasks = [
        _rag_query(diff_query, cfg, top_k=8),
        _rag_query(guideline_query, cfg, top_k=6),
        _rag_query(safety_query, cfg, top_k=6),
        searx_search(diff_query, limit=6),
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # Combine results
    diff_rag, guideline_rag, safety_rag, web_results = results
    combined_context = combine_contexts([diff_rag, guideline_rag, safety_rag, web_results])
    
    # Generate comment with combined evidence
    comment = await llm.generate(prompt, combined_context)
    return comment
```

#### 5.2 Configurable Strategy
Allow clinicians to choose depth:
```json
"consult_comment_strategy": "comprehensive",  // "quick" | "comprehensive"
"consult_comment_depth": {
    "quick": {
        "rag_queries": ["differential"],
        "web_search": true,
        "max_context_words": 3200,
        "max_tokens": 4096,
        "timeout_ms": 30000
    },
    "comprehensive": {
        "rag_queries": ["differential", "guideline", "safety"],
        "web_search": true,
        "max_context_words": 4800,
        "max_tokens": 8196,
        "timeout_ms": 60000
    }
}
```

#### 5.3 Caching
Cache consult comments for the same note to avoid regenerating:
```python
_consult_comment_cache = {}  # gen_id -> comment (with TTL)

async def get_consult_comment(gen_id, force=False):
    if not force and gen_id in _consult_comment_cache:
        cached = _consult_comment_cache[gen_id]
        if time.time() - cached["timestamp"] < 3600:  # 1 hour
            return cached["comment"]
    
    comment = await generate_consult_comment(gen_id)
    _consult_comment_cache[gen_id] = {
        "comment": comment,
        "timestamp": time.time()
    }
    return comment
```

### Phase 6: Clinical Validation & Testing

**Goal:** Ensure the improved system actually works in practice.

#### 6.1 Test Cases
Build a library of clinical scenarios to test:
```
Test 1: Chest pain patient
- Input: HPI with chest pain, tachycardia, risk factors
- Expected: PE in differential, red flag for missing ECG/CXR
- Current behavior: Echoes "chest pain is common"

Test 2: Diabetic patient
- Input: HbA1c elevated, no statin in meds
- Expected: Statin recommendation per ADA guidelines
- Current behavior: No comment on secondary prevention

Test 3: Antibiotic prescription
- Input: Cephalexin for UTI, penicillin allergy
- Expected: Cross-reactivity warning (cephalosporins + PCN allergy)
- Current behavior: No safety check

Test 4: Elderly patient
- Input: 82-year-old on multiple meds
- Expected: Beers Criteria check, dosing adjustments
- Current behavior: No age-specific guidance
```

#### 6.2 A/B Testing
Run old vs new system side-by-side on a set of cases:
- Measure: "Actionable insights per comment"
- Measure: "Guideline gaps identified"
- Measure: "Safety alerts caught"
- Measure: "Clinician satisfaction" (simple rating)

### Estimated Effort

| Phase | Effort | Files | Risk |
|-------|--------|-------|------|
| Phase 1: Multi-source evidence | 2-3 days | 3 new files, 2 modified | Low — uses existing RAG + SearXNG |
| Phase 2: Redesigned prompt | 1-2 days | 1 modified | Low — prompt engineering |
| Phase 3: Frontend UI | 2-3 days | 2 modified | Medium — new rendering logic |
| Phase 4: Enhanced analysis | 3-4 days | 3 new files | Medium — new logic, needs validation |
| Phase 5: Config & performance | 1 day | 1 modified | Low — optimization |
| Phase 6: Validation & testing | 2-3 days | Test suite | Low — validation |
| **Total** | **~12-16 days** | **9 new, 5 modified** | **Medium** |

### Recommendations for Implementation Order

1. **Start with Phase 1** — Add web search, broaden the query. This alone will improve the evidence base significantly without changing the UI.
2. **Phase 2** — Improve the prompt to force critical analysis. Low risk, high impact.
3. **Phase 3** — Render the structured output in the UI. Makes it actionable.
4. **Phase 4** — Add safety checks and guideline compliance. This is the "killer feature."
5. **Phase 5 & 6** — Optimize performance and validate.

### Key Risks

1. **Token budget** — Combining RAG + Web evidence may exceed context limits. Mitigation: Use smarter summarization of evidence.
2. **Prompt hallucination** — LLM might generate false recommendations. Mitigation: Require explicit evidence citation for every claim, confidence levels.
3. **Performance** — Multiple RAG queries + web search will be slower. Mitigation: Parallel execution, caching, configurable depth.
4. **Clinical liability** — Wrong recommendations could harm patients. Mitigation: Clear disclaimer, confidence levels, "this is advisory, not diagnostic."
