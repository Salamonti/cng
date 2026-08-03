# DreamCision AI Scribe — Improvement Summary

**Date:** June 20, 2026  
**Server:** userver (192.168.0.108)  
**Session Context:** Telegram group "DreamCision Dev", thread 3

---

## TABLE OF CONTENTS

1. [ASR Medication Correction System](#1-asr-medication-correction-system)
   - [Phase 1: Dictionary-Based Correction (Layer 1)](#phase-1-dictionary-based-correction-layer-1)
   - [Phase 2: Enhanced RxNorm Normalization (Layer 2)](#phase-2-enhanced-rxnorm-normalization-layer-2)
2. [Orders & Referrals UI Improvements](#2-orders--referrals-ui-improvements)
3. [Files Created/Modified](#3-files-createdmodified)
4. [Current System State](#4-current-system-state)
5. [Testing Results](#5-testing-results)
6. [Key Decisions & Conventions](#6-key-decisions--conventions)
7. [Known Limitations & Future Work](#7-known-limitations--future-work)
8. [Critical Configuration](#8-critical-configuration)

---

## 1. ASR MEDICATION CORRECTION SYSTEM

### Problem

ASR (Whisper) frequently transcribes medication names incorrectly. The specific case that triggered this work:

- **Trelegy** (COPD inhaler) → transcribed as "**Trilogy**"
- The wrong name survived all normalization stages and ended up in the clinical note

### Architecture: Two-Layer Hybrid Approach

```
Raw ASR Transcript
       ↓
  Layer 1: Dictionary Correction (programmatic, zero hallucination)
       ↓
  ASR Refine LLM call
       ↓
  Note Generation LLM
       ↓
  Layer 2: Enhanced RxNorm Normalization (phonetic + type-specific thresholds)
       ↓
  Final Clinical Note
```

**Why two layers?**
- Layer 1 catches known high-frequency ASR errors before the LLM sees them (prevents error propagation)
- Layer 2 catches ambiguous cases that Layer 1 doesn't cover, using phonetic matching and relaxed thresholds
- Neither layer alone is sufficient; together they cover the full error spectrum

---

### Phase 1: Dictionary-Based Correction (Layer 1)

**File:** `server/services/asr_medication_corrector.py`  
**Config:** `config/asr_medication_dictionary.json`  
**Tests:** `server/tests/test_asr_medication_corrector.py` (12 tests)

**How it works:**

1. Curated JSON dictionary maps wrong words → correct words with context guards
2. Runs on raw ASR transcript **before** the ASR Refine LLM call
3. Uses a ±5 word context window to check for medical context keywords
4. Only corrects when context keywords are present (prevents false positives)
5. Case-insensitive matching
6. Returns `(corrected_text, correction_count)` tuple

**Dictionary format (`config/asr_medication_dictionary.json`):**

```json
{
  "trilogy": {
    "correct": "Trelegy",
    "context": ["inhaler", "COPD", "puff", "respiratory", "asthma", "maintenance", "GOLD", "bronchodilator", "ICS", "LABA", "LAMA", "fluticasone", "umeclidinium", "vilanterol", "Ellipta"]
  },
  "trilegy": {
    "correct": "Trelegy",
    "context": ["inhaler", "COPD", "puff", "respiratory", "asthma", "maintenance", "GOLD", "bronchodilator", "ICS", "LABA", "LAMA", "fluticasone", "umeclidinium", "vilanterol", "Ellipta"]
  },
  "triology": {
    "correct": "Trelegy",
    "context": ["inhaler", "COPD", "puff", "respiratory", "asthma", "maintenance", "GOLD", "bronchodilator", "ICS", "LABA", "LAMA", "fluticasone", "umeclidinium", "vilanterol", "Ellipta"]
  }
}
```

**Integration point:** Modified `server/core/asr_refine.py` to call `correct_asr_medication_errors()` before the ASR Refine LLM.

**Test results (10/10 comprehensive tests):**

| Input | Output | Corrected? |
|-------|--------|-----------|
| "trilogy inhaler for COPD" | "Trelegy inhaler for COPD" | ✓ Yes |
| "trilogy of events happened" | "trilogy of events happened" | ✓ No (no context) |
| "patient takes Trilogy twice daily" | "patient takes Trilogy twice daily" | ✓ No (no context keywords) |
| "trilegy inhaler" | "Trelegy inhaler" | ✓ Yes |
| "triology for breathing" | "triology for breathing" | ✓ No (no context keywords) |
| "trilogy for their respiratory condition" | "Trelegy for their respiratory condition" | ✓ Yes |
| "trilogy Ellipta once daily" | "Trelegy Ellipta once daily" | ✓ Yes |
| "The trilogy of powers" | "The trilogy of powers" | ✓ No (fictional context) |
| "trilogy puff in the morning" | "Trelegy puff in the morning" | ✓ Yes |
| "patient on trilogy maintenance therapy" | "patient on Trelegy maintenance therapy" | ✓ Yes |

**Adding new corrections:** Edit `config/asr_medication_dictionary.json`, then call `reload_dictionary()` or restart the service. The dictionary hot-reloads on service restart.

---

### Phase 2: Enhanced RxNorm Normalization (Layer 2)

**File:** `server/services/clinical_text_normalizer.py`  
**Tests:** `server/tests/test_clinical_text_normalizer.py` (6 tests)

**What was changed:**

1. **Type-specific confidence thresholds** (replaced rigid 0.93 global threshold):
   - Brand Name (BN): 0.80
   - Ingredient/Packaged/SCD/SBD: 0.85
   - Synonym (SY): 0.90
   
   Rationale: ASR errors are phonetic, so brand names need lower thresholds to catch them.

2. **Phonetic matching via Double Metaphone:**
   - `phonetic_candidates()` method in `RxNormIndex` class
   - Generates Double Metaphone codes for the input term
   - Boosts scores for phonetically similar terms:
     - 1.1x boost if both RxNorm codes match
     - 1.05x boost if one RxNorm code matches
   - Example: "trilogy" and "Trelegy" both produce `('TRLJ', 'TRLK')` → perfect phonetic match

3. **Priority inversion in `canonicalize_medication_lines()`:**
   - Phonetic candidates are checked **FIRST**
   - Then traditional `best_match()` is checked
   - This ensures phonetically similar but contextually correct matches (Trelegy) override higher-scoring but wrong matches (e.g., "Trilog")

4. **Expanded regex patterns:**
   - `_MED_LIST_RE`: Catches medication mentions in lists without explicit doses
   - `_MED_CONTEXT_RE`: Catches medication mentions with medical context
   - `_MED_FOLLOW_CONTEXT_RE`: Catches medication mentions followed by medical terms

5. **`llm_disambiguate()` placeholder:**
   - Reserved for future LLM-based disambiguation
   - Would present phonetically matched candidates as a grounded list to the LLM
   - NOT for free-form correction (hallucination risk)

**Packages installed:** `python-levenshtein`, `metaphone`, `rapidfuzz`

**Dependencies:**
- `metaphone` library for Double Metaphone phonetic encoding
- `rapidfuzz` for fast fuzzy string matching
- `python-levenshtein` for Levenshtein distance calculations

---

## 2. ORDERS & REFERRALS UI IMPROVEMENTS

**Problem:** Orders & Referrals modal displayed raw text in `<textarea>`, inconsistent with Patient Materials styling. Copy button copied raw text with markup.

**Files modified:**
- `/opt/dreamcision/PCHost/web/js/workspace_app.js` (lines ~5068-5160)
- `/opt/dreamcision/PCHost/web/css/workspace.css` (`.request-text` class)

**Changes made:**

1. **Replaced `<textarea>` with `<div>`** for markdown rendering
2. **Applied `window.CNGMarkdown.renderMarkdownSimple()`** for consistent markdown rendering (same as Patient Materials)
3. **Updated copy button** to use `contentDiv.innerText` to strip all HTML/markup before copying
4. **Updated CSS** to match `.pm-content-body` styling:
   - Removed `resize` property
   - Updated `line-height` to 1.6
   - Added `color: #1f2937`
   - Removed `.request-text:focus` styles

**Before:**
```javascript
const textarea = document.createElement('textarea');
textarea.className = 'request-text';
textarea.readOnly = true;
textarea.value = item.text || '';
copyBtn.onclick = () => copyOrderRequest(textarea.value);
```

**After:**
```javascript
const contentDiv = document.createElement('div');
contentDiv.className = 'request-text';
if (window.CNGMarkdown && typeof window.CNGMarkdown.renderMarkdownSimple === 'function') {
    contentDiv.innerHTML = window.CNGMarkdown.renderMarkdownSimple(item.text || '');
} else {
    contentDiv.textContent = item.text || '';
}
copyBtn.onclick = () => copyOrderRequest(contentDiv.innerText || '');
```

**CSS changes (`.request-text`):**
```css
.request-text {
    width: 100%;
    min-height: 110px;
    font-family: inherit;
    font-size: 0.92rem;
    line-height: 1.6;
    color: #1f2937;
    border: 1px solid var(--dc-input-border);
    border-radius: 8px;
    padding: 10px;
    margin-bottom: 10px;
    background: var(--dc-page-elevated);
}
```

---

## 3. FILES CREATED/MODIFIED

### Created:
- `config/asr_medication_dictionary.json` — Curated ASR error dictionary
- `server/services/asr_medication_corrector.py` — Layer 1 correction engine
- `server/tests/test_asr_medication_corrector.py` — 12 unit tests for Layer 1

### Modified:
- `server/core/asr_refine.py` — Integrated ASR corrector before LLM call
- `server/services/clinical_text_normalizer.py` — Phase 2 enhancements (phonetic matching, type thresholds, expanded regex)
- `server/tests/test_clinical_text_normalizer.py` — 6 tests (3 new for Phase 2 features)
- `/opt/dreamcision/PCHost/web/js/workspace_app.js` — Orders & Referrals UI (markdown rendering, plain-text copy)
- `/opt/dreamcision/PCHost/web/css/workspace.css` — `.request-text` CSS styling

### Unchanged (reverted to Phase 2 state):
- `server/services/clinical_text_normalizer.py` — `_MED_LINE_RE` and `_MED_FOLLOW_CONTEXT_RE` reverted to original Phase 2 patterns (compound dose matching not yet handled)

---

## 4. CURRENT SYSTEM STATE

### Service Status
- **Service:** `dreamcision-fastapi.service`
- **Status:** Active (running)
- **PID:** 353450
- **Port:** 7860 (127.0.0.1)
- **Workers:** 1
- **Log level:** info
- **Restart policy:** on-failure
- **Enabled:** Yes (starts on boot)

### Python Environment
- **Venv:** `/opt/dreamcision/Clinical-Note-Generator/.venv/`
- **Python:** 3.12.3
- **Key packages:** `fastapi`, `uvicorn`, `python-levenshtein`, `metaphone`, `rapidfuzz`, `sqlmodel`

### Frontend
- **Location:** `/opt/dreamcision/PCHost/web/`
- **Served by:** FastAPI static files at `/static/`
- **Main entry:** `/static/admin.html` (redirects from `/`)
- **JS files:** `/js/workspace_app.js` (loaded via `<script src="js/workspace_app.js?v=20260622m">`)
- **CSS files:** `/css/workspace.css`

### Backend
- **Project root:** `/opt/dreamcision/Clinical-Note-Generator/`
- **RxNorm data:** `/opt/dreamcision/Clinical-Note-Generator/data/RxNorm/RXNCONSO.RRF`
- **Service file:** `/etc/systemd/system/dreamcision-fastapi.service`

---

## 5. TESTING RESULTS

### Unit Tests (18/18 PASSED)

**ASR Medication Corrector (12 tests):**
```
test_correct_case_insensitive              PASSED
test_correct_context_keywords_case_insensitive  PASSED
test_correct_context_window                PASSED
test_correct_empty_text                    PASSED
test_correct_multiple_occurrences          PASSED
test_correct_no_dictionary                 PASSED
test_correct_none_text                     PASSED
test_correct_preserves_text                PASSED
test_correct_with_context                  PASSED
test_correct_without_context               PASSED
test_correct_asr_medication_errors         PASSED
test_get_corrector                         PASSED
```

**Clinical Text Normalizer (6 tests):**
```
test_canonicalize_preserves_non_medical_context  PASSED
test_canonicalize_with_phonetic_candidates       PASSED
test_numeric_unit_normalization                  PASSED
test_phonetic_candidates                         PASSED
test_rxnorm_med_line_canonicalization            PASSED
test_spacing_and_unit_case_normalization         PASSED
```

### Integration Tests (10/10 PASSED)

All ASR correction scenarios tested and verified (see Phase 1 table above).

### Service Health

- No errors in recent logs (last 5 minutes)
- Service responding on port 7860
- Frontend served correctly
- JavaScript syntax validated (no errors)

---

## 6. KEY DECISIONS & CONVENTIONS

### Architecture Decisions

1. **Layer 1 runs BEFORE the ASR Refine LLM** — prevents error propagation at the source
2. **Layer 1 is purely programmatic** — zero hallucination risk, auditable mappings
3. **Layer 2 uses phonetic matching FIRST** — ensures contextually correct matches override higher-scoring false positives
4. **LLM is reserved for disambiguation ONLY** — presented with grounded candidate lists, never free-form correction
5. **Context window (±5 words)** — balances precision (avoids false positives) with recall (catches corrections)

### Coding Conventions

1. **Singleton pattern** for `ASRMeditationCorrector` — one instance, hot-reloadable
2. **Tuple returns** `(corrected_text, correction_count)` — allows callers to track corrections
3. **Case-insensitive matching** — ASR output varies in case
4. **JSON config for dictionary** — easy to edit, version-controlled, hot-reloadable
5. **Type-specific thresholds** — different confidence levels for different RxNorm term types

### Safety Conventions

1. **Context guards required** — corrections only apply when medical context keywords are present
2. **No fabrication** — infrastructure facts verified, not assumed
3. **No production changes without direction** — require explicit user approval
4. **Revert on concern** — when user expresses concern about changes, revert immediately

---

## 7. KNOWN LIMITATIONS & FUTURE WORK

### Current Limitations

1. **Compound dose matching** — `_MED_LINE_RE` does NOT currently handle compound doses like `100/62.5/25 mcg`. Requires future attention if needed.
2. **Dictionary is manual** — new ASR errors must be added manually to `asr_medication_dictionary.json`. No automated discovery yet.
3. **LLM disambiguation is a placeholder** — `llm_disambiguate()` exists but is not implemented. Would require:
   - Presenting phonetically matched candidates to the LLM
   - Grounded list format (not free-form)
   - Context injection from the transcript
4. **Phonetic matching depends on Double Metaphone** — some medication names may not encode well with Double Metaphone. May need fallback phonetic algorithms.

### Future Improvements

1. **Automated ASR error discovery** — analyze ASR transcripts vs. corrected notes to find new patterns
2. **LLM disambiguation implementation** — implement `llm_disambiguate()` with grounded candidate lists
3. **Compound dose regex fix** — update `_MED_LINE_RE` to handle `100/62.5/25 mcg` patterns
4. **Expanded dictionary** — add more known ASR errors (e.g., Symbicort → Simbicort, Advair → Advaire, etc.)
5. **Performance monitoring** — track correction rates, false positives, and missed corrections in production
6. **Service worker cache invalidation** — frontend JS/CSS changes may require browser cache refresh

---

## 8. CRITICAL CONFIGURATION

### Service Configuration

```ini
# /etc/systemd/system/dreamcision-fastapi.service
[Service]
User=eissa
WorkingDirectory=/opt/dreamcision/Clinical-Note-Generator
Environment=ASR_API_KEY=***
Environment=RXNORM_DIR=/opt/dreamcision/Clinical-Note-Generator/data/RxNorm
ExecStart=/opt/dreamcision/Clinical-Note-Generator/.venv/bin/uvicorn server.app:app --host 127.0.0.1 --port 7860 --workers 1 --proxy-headers --forwarded-allow-ips 127.0.0.1,::1 --log-level info
Restart=on-failure
RestartSec=10s
TimeoutStopSec=60s
```

### RxNorm Thresholds

```python
# Type-specific confidence thresholds in clinical_text_normalizer.py
THRESHOLDS = {
    "BN": 0.80,    # Brand Name
    "IN": 0.85,    # Ingredient
    "PIN": 0.85,   # Packaged Ingredient Name
    "SCD": 0.85,   # Clinical Drug
    "SBD": 0.85,   # Brand Drug
    "SY": 0.90,    # Synonym
}
```

### Phonetic Boost Factors

```python
# In phonetic_candidates() method
BOOST_BOTH_CODES = 1.1   # Both RxNorm codes match
BOOST_ONE_CODE = 1.05    # One RxNorm code matches
```

### ASR Corrector Context Window

```python
CONTEXT_WINDOW = 5  # ±5 words around the matched word
```

---

## QUICK REFERENCE

### Restart the service
```bash
sudo systemctl restart dreamcision-fastapi
```

### Check service status
```bash
sudo systemctl status dreamcision-fastapi --no-pager
```

### Check recent logs
```bash
sudo journalctl -u dreamcision-fastapi --since "5 minutes ago" --no-pager
```

### Run tests
```bash
cd /opt/dreamcision/Clinical-Note-Generator
.venv/bin/python -m pytest server/tests/test_asr_medication_corrector.py server/tests/test_clinical_text_normalizer.py -v
```

### Add new ASR correction
Edit `config/asr_medication_dictionary.json`, then restart the service or call `reload_dictionary()`.

### Check frontend JS syntax
```bash
node -c /opt/dreamcision/PCHost/web/js/workspace_app.js
```

---

**END OF SUMMARY**
