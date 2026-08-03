# Evidence-Based Q&A Improvement Plan

**Created:** 2026-06-30
**Status:** Planning
**Author:** Steve (userver)

---

## Current State (Baseline)

### RAG Index
- **ChromaDB:** `/opt/dreamcision/RAG/chroma_store/` (~1GB SQLite + embeddings)
- **Embedding model:** `avsolatorio/GIST-small-Embedding-v0` (768 dims)
- **BM25:** `/opt/dreamcision/RAG/chroma_store/bm25_index.json` (~54MB)
- **Sources indexed:**
  - GOLD Guidelines (local PDFs): ~770 chunks, **has full text** ✓
  - Guidelines (crawled): 135 items, many hit login walls ("Login" as title)
  - PMC OA: 997 items, **metadata only** ✗ (no text)
  - PubMed: 26 items, **metadata only** ✗ (`abstr = ""` in code)
  - ClinicalTrials: 386 items, **metadata only** ✗
  - OpenFDA: 2 items, **metadata only** ✗
  - DrugBank: stub (no API key)

### Web Search (SearXNG)
- **URL:** `http://127.0.0.1:8083/search`
- **Snippet length:** 500 chars max (hardcoded in `qa_web_search.py`)
- **Domain filter:** Strict allowlist (pubmed, nejm, jama, lancet, bmj, etc.)
- **Engines:** Google works; Brave/DuckDuckGo/Startpage hit CAPTCHA
- **No full content extraction** — only snippets

### QA Chat Flow
```
User question → deidentify → RAG query (:8007) + SearXNG search (parallel)
→ Build prompt with rag_ctx + web_ctx → LLM → answer + sources
```

### Evidence Quality Check
```python
evidence_chars = len(rag_ctx) + sum(len(w.get("snippet")) for w in web_items)
weak_evidence = evidence_chars < 260  # TRIVIAL threshold
```

### Key Files
```
/opt/dreamcision/RAG/
  fetch_sources.py          # PubMed, ClinicalTrials, OpenFDA, DrugBank
  guidelines_fetcher.py     # Society guideline crawler (BeautifulSoup + PDF)
  pmc_fetcher.py            # PMC OA metadata fetcher
  chunking_pipeline.py      # Document → chunks (100-300 words)
  embed_chunks.py           # Chunks → embeddings (GIST-small)
  update_index.py           # Upsert to ChromaDB, prune stale docs
  query_api.py              # RAG query API (:8007)
  retriever.py              # Hybrid search (dense + BM25)
  settings.yaml             # Config (chunk_size, hybrid_lambda, etc.)

/opt/dreamcision/Clinical-Note-Generator/
  server/services/qa_web_search.py    # SearXNG integration
  server/services/rag_http_client.py  # RAG HTTP client
  server/routes/qa_chat.py            # QA chat endpoint
  server/core/qa_rag/helpers.py       # RAG rewrite helpers
```

---

## Phase 1: Fix the RAG Index

**Goal:** Make PubMed, PMC, and ClinicalTrials fetch actual text content, not just metadata.

**Risk:** ZERO to running app. Batch scripts only. No restart needed.

### Step 1.1: PubMed — Add Abstract Fetching

**File:** `/opt/dreamcision/RAG/fetch_sources.py`

**Current code (line 134):**
```python
abstr = ""  # ESummary is title-oriented; leave empty or follow with EFetch if needed
```

**Fix:**
1. After `fetch_pubmed()` gets IDs via ESearch and metadata via ESummary, add EFetch call:
   ```python
   # After getting IDs from esearch
   efetch = f"{base}/efetch.fcgi?db=pubmed&retmode=xml&id={','.join(ids)}&tool=rag-pipeline&email=eissa.islam@gmail.com"
   r = requests.get.efetch, timeout=60)
   r.raise_for_status()
   # Parse XML to extract <AbstractText>
   from xml.etree import ElementTree as ET
   root = ET.fromstring(r.text)
   for article in root.findall('.//PubmedArticle'):
       abst = article.find('.//Abstract/AbstractText')
       abstract_text = abst.text if abst is not None else ""
   ```
2. Add `abstract` field to the output dict:
   ```python
   items.append({
       "title": title,
       "source": "pubmed",
       "id": pid,
       "date": pubdate,
       "journal": journal,
       "link": f"https://pubmed.ncbi.nlm.nih.gov/{pid}/",
       "abstract": abstract_text,  # NEW
   })
   ```
3. Update `chunking_pipeline.py` to recognize `abstract` as a text field (alongside `text`, `content`, `body`).

**Testing:**
```bash
cd /opt/dreamcision/RAG
python3 fetch_sources.py --domains pulmonology --days 7
# Check raw_docs/pubmed_*.json for "abstract" field with real text
```

**Rollback:** Revert `fetch_sources.py` to previous version. No impact on existing index.

**Time estimate:** 1-2 hrs

---

### Step 1.2: PMC — Add Full-Text Download

**File:** `/opt/dreamcision/RAG/pmc_fetcher.py`

**Current:** Fetches metadata via OAI-PMH, no text.

**Fix:**
1. After getting PMC IDs, download full-text XML:
   ```python
   # For each PMC ID
   url = f"https://www.ncbi.nlm.nih.gov/pmc/utils/oa/oa_tool.py?id={pmc_id}"
   # Or use the OA Web Service API
   url = f"https://www.ncbi.nlm.nih.gov/research/bionlp/RESTful/pmcoa_search/{pmc_id}/xml"
   ```
2. Parse XML to extract `<p>` tags from `<sec>` sections:
   ```python
   from xml.etree import ElementTree as ET
   root = ET.fromstring(xml_text)
   text_parts = []
   for p in root.findall('.//body//p'):
       text_parts.append(p.text or "")
   full_text = " ".join(text_parts)
   ```
3. Add `text` field to output:
   ```python
   items.append({
       "title": title,
       "source": "pmc",
       "id": pmc_id,
       "text": full_text,  # NEW
       ...
   })
   ```

**Alternative (simpler):** Use the PMC OA Subset API which provides downloadable XML files:
```python
# Download the OA subset manifest
manifest = requests.get("https://www.ncbi.nlm.nih.gov/pmc/tools/oa/oa_subsets.xml")
# Parse to get URLs to full-text XML
```

**Testing:**
```bash
cd /opt/dreamcision/RAG
python3 pmc_fetcher.py --oa-subset --years 1 --limit 5
# Check raw_docs/pmc_oa_*.json for "text" field with real content
```

**Rollback:** Revert `pmc_fetcher.py`. No impact on existing index.

**Time estimate:** 2-3 hrs

---

### Step 1.3: ClinicalTrials — Add Study Descriptions

**File:** `/opt/dreamcision/RAG/fetch_sources.py`

**Current:** Title + conditions only.

**Fix:**
1. After getting study IDs from the list API, fetch full record:
   ```python
   # v2 API record endpoint
   url = f"https://clinicaltrials.gov/api/v2/studies/{nct_id}?format=json"
   r = requests.get(url, timeout=30)
   data = r.json()
   # Extract description
   desc = data.get('study', {}).get('description', '')
   conditions = data.get('study', {}).get('conditions', [])
   interventions = data.get('study', {}).get('interventions', [])
   ```
2. Add `description` field:
   ```python
   items.append({
       "title": title,
       "source": "clinicaltrials",
       "id": nct_id,
       "description": desc,  # NEW
       ...
   })
   ```

**Testing:**
```bash
cd /opt/dreamcision/RAG
python3 fetch_sources.py --domains pulmonology --days 7
# Check raw_docs/clinicaltrials_*.json for "description" field
```

**Rollback:** Revert `fetch_sources.py`. No impact.

**Time estimate:** 1 hr

---

### Step 1.4: Guidelines — Fix Login Walls

**File:** `/opt/dreamcision/RAG/guidelines_fetcher.py`

**Current:** Crawls society sites, many return login pages.

**Fix:**
1. Add detection for login walls:
   ```python
   def _is_login_page(html: str) -> bool:
       lower = html.lower()
       return any(k in lower for k in [
           'login', 'sign in', 'authentication required',
           'please log in', 'access denied', 'subscribe'
       ])
   ```
2. Skip login pages, log them:
   ```python
   if _is_login_page(html):
       logging.warning(f"Login wall detected: {url}")
       continue
   ```
3. Prefer PDF downloads when available (already supported via PyPDF2/pdfminer).
4. Add `robots.txt` respect to avoid being blocked.

**Testing:**
```bash
cd /opt/dreamcision/RAG
python3 guidelines_fetcher.py --limit-per-source 10
# Check fetch_log.jsonl for login wall warnings
# Check raw_docs/guidelines_*.json for meaningful titles (not "Login")
```

**Rollback:** Revert `guidelines_fetcher.py`. No impact.

**Time estimate:** 2 hrs

---

### Step 1.5: Re-index Everything

**After all fetchers are fixed:**

1. Run all fetchers:
   ```bash
   cd /opt/dreamcision/RAG
   python3 fetch_sources.py --domains pulmonology,cardiology,endocrinology --days 30
   python3 pmc_fetcher.py --oa-subset --years 2
   python3 guidelines_fetcher.py
   ```

2. Run chunking pipeline:
   ```bash
   python3 chunking_pipeline.py --input ./raw_docs --output ./chunks
   ```

3. Run embedding:
   ```bash
   python3 embed_chunks.py --chunk-dir ./chunks --emb-dir ./embeddings
   ```

4. Update ChromaDB index:
   ```bash
   python3 update_index.py --emb-dir ./embeddings --chunk-dir ./chunks --snapshots both
   ```

5. Verify:
   ```bash
   curl -s -X POST http://127.0.0.1:8007/query \
     -H "Content-Type: application/json" \
     -d '{"query": "COPD exacerbation treatment", "top_k": 3}' | python3 -m json.tool
   # Check that results have substantial text content
   ```

**Time estimate:** 1 hr (plus ~30 min for indexing to complete)

---

## Phase 2: Web Content Extraction

**Goal:** After SearXNG returns URLs, extract full article content instead of 500-char snippets.

**Risk:** MEDIUM — adds latency to QA responses. Mitigated by async extraction + timeout + fallback.

### Step 2.1: Install trafilatura

```bash
cd /opt/dreamcision/Clinical-Note-Generator
source .venv/bin/activate
pip install trafilatura
```

**Time estimate:** 5 min

---

### Step 2.2: Add Content Extraction Function

**File:** `/opt/dreamcision/Clinical-Note-Generator/server/services/qa_web_search.py`

**New function:**
```python
import trafilatura

async def extract_web_content(urls: List[str], timeout: int = 5) -> List[Dict[str, Any]]:
    """Extract full article content from URLs using trafilatura."""
    results = []
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
        for url in urls[:6]:  # Limit to 6 URLs
            try:
                downloaded = await asyncio.get_event_loop().run_in_executor(
                    None, trafilatura.fetch_url, url
                )
                if downloaded:
                    text = trafilatura.extract(
                        downloaded,
                        include_comments=False,
                        include_tables=True,
                        wisdom=False,
                    )
                    if text and len(text.strip()) > 200:
                        results.append({
                            "url": url,
                            "text": text.strip()[:8000],  # Cap at 8K chars
                            "source": "web_extracted",
                        })
            except Exception:
                continue
    return results
```

**Time estimate:** 1 hr

---

### Step 2.3: Integrate into QA Chat Flow

**File:** `/opt/dreamcision/Clinical-Note-Generator/server/routes/qa_chat.py`

**Current flow (line 312-315):**
```python
rag_task = asyncio.create_task(_rag_query(deid_q, cfg))
web_task = asyncio.create_task(searx_search(deid_q, limit=int(cfg.get("qa_chat_web_k", 6))))
rag_ctx, rag_refs = await rag_task
web_items = await web_task
```

**New flow:**
```python
rag_task = asyncio.create_task(_rag_query(deid_q, cfg))
web_task = asyncio.create_task(searx_search(deid_q, limit=int(cfg.get("qa_chat_web_k", 6))))
rag_ctx, rag_refs = await rag_task
web_items = await web_task

# NEW: Extract full content from top web results
web_urls = [w.get("url") for w in web_items[:3] if w.get("url")]
extracted_content = []
if web_urls:
    from server.services.qa_web_search import extract_web_content
    extracted_content = await extract_web_content(web_urls, timeout=5)

# Combine snippets + extracted content
combined_web = web_items + extracted_content
```

**Time estimate:** 1 hr

---

### Step 2.4: Add Fallback & Timeout

**Safety measures:**
1. If extraction takes >5 seconds per URL, skip it and use snippet
2. If extraction fails entirely, fall back to current snippet behavior
3. Add config flag to disable extraction: `qa_web_extract_enabled: true/false`

**Config addition:**
```json
{
  "qa_web_extract_enabled": true,
  "qa_web_extract_timeout": 5,
  "qa_web_extract_max_urls": 3
}
```

**Time estimate:** 30 min

---

### Step 2.5: Test & Verify

```bash
# Test extraction function
cd /opt/dreamcision/Clinical-Note-Generator
source .venv/bin/activate
python3 -c "
import asyncio
from server.services.qa_web_search import extract_web_content
urls = ['https://www.ncbi.nlm.nih.gov/pmc/articles/PMC12345678/']
result = asyncio.run(extract_web_content(urls))
print(f'Extracted {len(result)} articles')
for r in result:
    print(f'  {r[\"url\"]}: {len(r[\"text\"])} chars')
"
```

**Rollback:** Set `qa_web_extract_enabled: false` in config. No code change needed.

**Time estimate:** 30 min

---

## Phase 3: Evidence Quality & Attribution

**Goal:** Make the system actually use retrieved evidence, not fall back to training knowledge.

**Risk:** LOW — config and prompt changes only.

### Step 3.1: Raise Evidence Threshold

**File:** `/opt/dreamcision/Clinical-Note-Generator/server/routes/qa_chat.py`

**Current (line 318):**
```python
weak_evidence = evidence_chars < int(cfg.get("qa_chat_min_evidence_chars", 260))
```

**New:**
```python
weak_evidence = evidence_chars < int(cfg.get("qa_chat_min_evidence_chars", 2000))
```

**Config addition:**
```json
{
  "qa_chat_min_evidence_chars": 2000
}
```

**Time estimate:** 10 min

---

### Step 3.2: Add Source Tiering

**File:** `/opt/dreamcision/Clinical-Note-Generator/server/routes/qa_chat.py`

**New function:**
```python
def _classify_source_tier(ref: Dict[str, Any]) -> int:
    """Classify evidence source into tiers.
    Tier 1: Guidelines (GOLD, ATS/ERS, ACP)
    Tier 2: RCTs / Meta-analyses (PubMed/PMC)
    Tier 3: Review articles
    Tier 4: Opinion / Expert consensus / Web
    """
    md = ref.get("metadata", {}) if isinstance(ref, dict) else {}
    source = (md.get("source") or "").lower()
    evidence_level = (md.get("evidence_level") or "").lower()
    pubtypes = (md.get("pubtypes") or "").lower()

    if evidence_level == "guideline" or source in ("guidelines_local", "guidelines"):
        return 1
    if "randomized" in pubtypes or "meta-analysis" in pubtypes or "clinical trial" in pubtypes:
        return 2
    if "review" in pubtypes or "review article" in pubtypes:
        return 3
    return 4
```

**Time estimate:** 30 min

---

### Step 3.3: Update Prompt to Require Evidence

**File:** `/opt/dreamcision/Clinical-Note-Generator/server/routes/qa_chat.py`

**Current prompt (line 363-368):**
```
Evidence policy:
- Local RAG snippets and web snippets below are SUPPLEMENTARY. They are not the only allowed content.
- For management, dosing, drug choice, algorithms, and stepwise care: give the full detail a clinician would expect...
```

**New prompt:**
```
Evidence policy:
- You MUST base your answer on the retrieved evidence below.
- If the evidence is insufficient to answer the question, state what is missing rather than guessing.
- Do NOT fall back to general training knowledge when specific evidence is requested.
- Cite specific sources when making claims (e.g., "According to GOLD 2026...").
- If no relevant evidence is retrieved, say: "The available evidence does not cover this specific question."
```

**Time estimate:** 30 min

---

### Step 3.4: Add Evidence Summary in Response

**File:** `/opt/dreamcision/Clinical-Note-Generator/server/routes/qa_chat.py`

**New field in QAChatResponse:**
```python
class QAChatResponse(BaseModel):
    answer: str
    summary: str
    sources: List[Dict[str, Any]]
    deid_counts: Dict[str, int]
    web_results_count: int = 0
    rag_results_count: int = 0
    used_knowledge_fallback: bool = False
    evidence_max_year: Optional[int] = None
    evidence_tiers: Dict[str, int] = {}  # NEW: {"tier1": 2, "tier2": 1, ...}
```

**Time estimate:** 20 min

---

### Step 3.5: Test & Verify

```bash
# Test with a clinical question
curl -s -X POST http://127.0.0.1:8000/qa/chat \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <token>" \
  -d '{"message": "What is the first-line treatment for COPD exacerbation?", "session_id": "test"}' | python3 -m json.tool
```

**Rollback:** Revert prompt changes and config. No data loss.

**Time estimate:** 30 min

---

## Phase 4: Structured Evidence Display (UI)

**Goal:** Show the doctor which evidence supports which claim.

**Risk:** LOW — frontend-only changes.

### Step 4.1: Add Citation Markers to Answer

**File:** `/opt/dreamcision/Clinical-Note-Generator/server/routes/qa_chat.py`

**Update prompt to instruct model to use citation markers:**
```
When citing evidence, use superscript numbers ¹ ² ³ that correspond to the source list.
Example: "Antibiotics should be given during COPD exacerbations¹."
```

**Time estimate:** 20 min

---

### Step 4.2: Update Frontend to Show Sources with Tiers

**File:** `/opt/dreamcision/PCHost/web/js/workspace_app.js`

**Current:** Shows sources as a flat list.

**New:** Group sources by tier, show tier labels:
```javascript
function renderSources(sources) {
  const tiers = {
    1: { label: "Guidelines", color: "#2ecc71" },
    2: { label: "Clinical Trials", color: "#3498db" },
    3: { label: "Reviews", color: "#f39c12" },
    4: { label: "Other", color: "#95a5a6" }
  };
  // Group and render
}
```

**Time estimate:** 2-3 hrs

---

### Step 4.3: Add Evidence Quality Banner

**File:** `/opt/dreamcision/PCHost/web/js/workspace_app.js`

**New banner:**
```
Evidence Quality: 2 guidelines, 1 RCT, 1 review (Strong)
```

**Time estimate:** 1 hr

---

### Step 4.4: Test & Verify

```bash
# Open the app and test Q&A
# Verify sources are grouped by tier
# Verify evidence quality banner shows correctly
```

**Rollback:** Revert frontend changes. No data loss.

**Time estimate:** 30 min

---

## Phase 5: SearXNG Engine Fix

**Goal:** Make SearXNG return more results by fixing engine configuration.

**Risk:** ZERO — config only.

### Step 5.1: Configure SearXNG Settings

**File:** SearXNG settings.yaml (likely `/etc/searxng/settings.yaml` or similar)

**Changes:**
1. Set Google as primary engine
2. Add PubMed as an engine
3. Add Scholar as an engine
4. Disable problematic engines (Brave, DuckDuckGo if CAPTCHA persists)

```yaml
engines:
  - name: google
    disabled: false
  - name: pubmed
    disabled: false
  - name: scholar
    disabled: false
  - name: brave
    disabled: true  # Too many requests
  - name: duckduckgo
    disabled: true  # CAPTCHA
  - name: startpage
    disabled: true  # CAPTCHA
```

**Time estimate:** 30 min

---

### Step 5.2: Test & Verify

```bash
curl -s "http://127.0.0.1:8083/search?q=COPD+exacerbation&format=json" | python3 -m json.tool
# Check that results are returned and include pubmed/scholar results
```

**Rollback:** Revert settings.yaml. No impact.

**Time estimate:** 15 min

---

## Rollback Plan

| Phase | Rollback Method | Time |
|---|---|---|
| 1 | Revert fetcher scripts to previous version | 5 min |
| 2 | Set `qa_web_extract_enabled: false` | 1 min |
| 3 | Revert prompt + config changes | 5 min |
| 4 | Revert frontend changes | 5 min |
| 5 | Revert SearXNG settings.yaml | 1 min |

---

## Completion Criteria

**Phase 1 complete when:**
- PubMed items have `abstract` field with real text
- PMC items have `text` field with full article content
- ClinicalTrials items have `description` field
- Guidelines fetcher skips login walls
- RAG index updated with new content
- Query test returns substantial text content

**Phase 2 complete when:**
- trafilatura installed and working
- `extract_web_content()` function added
- QA chat flow integrates extraction
- Fallback works (extraction disabled = current behavior)
- QA response latency acceptable (<30 seconds)

**Phase 3 complete when:**
- Evidence threshold raised to 2000 chars
- Source tiering implemented
- Prompt requires evidence-based answers
- Evidence summary in response
- QA test shows evidence-based answers

**Phase 4 complete when:**
- Citation markers in answers
- Sources grouped by tier in UI
- Evidence quality banner shows
- UI test passes

**Phase 5 complete when:**
- SearXNG returns results from pubmed/scholar
- No CAPTCHA/rate limit errors
- Search test passes

---

## Notes

- **PEP 668:** Use venv for pip installs (`source .venv/bin/activate`)
- **Python:** `python3.11.15` (no pip module), `pip→python3.12` (mismatch)
- **RAG service:** Port 8007
- **FastAPI app:** Port 8000
- **SearXNG:** Port 8083
- **ChromaDB:** `/opt/dreamcision/RAG/chroma_store/`
- **Embedding model:** `avsolatorio/GIST-small-Embedding-v0` (768 dims)
- **BM25:** `/opt/dreamcision/RAG/chroma_store/bm25_index.json`
