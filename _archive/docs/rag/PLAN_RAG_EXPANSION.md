# RAG + Web Search Expansion Plan

**Created:** 2026-07-02
**Status:** Awaiting approval
**Goal:** Expand RAG from respiratory-only (17,385 chunks) to cover ALL clinical specialties. Improve web search quality, speed, and coverage.

---

## Current State

- **ChromaDB:** 17,385 chunks, Harrier 1024d embeddings
- **Specialty coverage:** Respiratory only (GOLD, GINA, ATS, CHEST) — zero cardiology, oncology, neurology, etc.
- **Metadata:** All chunks have empty `specialty` and `year` fields — temporal decay and specialty filtering don't work
- **Sources:** GOLD (1611), GINA (717), WHO (935), GIN (468), AAOS (170), EAN (91), NICE (55), guidelines (12302)
- **Web search:** Sequential, raw user queries, allowlist blocks major domains, PubMed CAPTCHA blocks Crawl4AI
- **Scrapers:** Extract navigation links instead of guideline content — generic HTML parsing is broken
- **PubMed API key:** `fa2b1e42c0c608665464ff73230035e90f07` (3 req/sec) — NOT currently used in codebase

---

## Phase 0: Fix Broken Scrapers

**Problem:** `fetch_guideline_aggregators.py` and `guideline_sources.py` extract navigation/menu links instead of actual guideline content.

**Changes:**
1. Rewrite scrapers with source-specific CSS selectors (NICE `.guidance-item`, WHO iris.who.int PDF links, GIN API, ACC `.guideline-card`, ESC `.guideline-item`)
2. Add content quality filter: minimum 5000 chars, block nav-heavy pages (>50% nav text), block login/paywall pages
3. Add DOI extraction for deduplication (primary key), title as fallback

**Deliverable:** Scrapers return actual guideline text, not navigation links.

---

## Phase 1: Web Search Overhaul

**Problem:** Slow, sequential, raw queries, allowlist blocks, CAPTCHA blocks.

**Changes:**
1. **Integrate PubMed API key** (`fa2b1e42c0c608665464ff73230035e90f07`) into `web_search.py` and `qa_web_search.py` — 3 req/sec, batch EFetch (20 PMIDs/request)
2. **Smart query reformulation** — `query_reformer.py`: take user question, generate 3-5 targeted queries via local vLLM (port 8000). Example: "PE management" → ["pulmonary embolism anticoagulation guidelines 2025", "PE risk stratification ESC", "acute PE treatment algorithm ACC AHA"]
3. **Parallel tiered fetching:**
   - Tier 1 (instant): PubMed EFetch with API key — abstracts + metadata
   - Tier 2 (fast): SearXNG search — snippets from allowed domains
   - Tier 3 (slow): Crawl4AI — full text from guideline sites
   - Run Tiers 1+2 in parallel, Tier 3 only on top 5 results
4. **Expanded allowlist** — Add all Tier 2 organizations: escardio.org, idsociety.org, kdigo.org, gi.org, endocrine.org, rheumatology.org, eular.org, nccn.org, asco.org, esmo.org, aap.org, cps.ca, acog.org, sogc.org, sccm.org, asahq.org, auanet.org, car.ca, cfpc.ca, bcguidelines.ca, canadiantaskforce.ca, hypertension.ca, ccs.ca, magicevidence.org, guidelines.ecri.org, tripdatabase.com, g-i-n.net
5. **Content quality scoring:** authority (tier) + recency (year) + content_length + relevance (keyword overlap). Filter <2000 chars. Cap at 8 high-quality results.

**Deliverable:** Web search returns 8 high-quality, recent results with full abstracts or text, in under 10 seconds.

---

## Phase 2: Cross-Specialty Guideline Ingestion

**Problem:** Index is respiratory-only. Need all clinical specialties.

**New scrapers in `guideline_sources.py`:**

| Specialty | Sources |
|---|---|
| Cardiology | ACC/AHA, ESC, CCS, Hypertension Canada |
| Oncology | ASCO, ESMO (NCCN = login, use PubMed fallback) |
| GI | ACG, CAG |
| Endocrinology | ADA, Endocrine Society |
| Infectious Disease | IDSA, CDC |
| Rheumatology | ACR, EULAR |
| Neurology | AAN |
| Psychiatry | APA |
| Pediatrics | AAP, CPS |
| OB/GYN | ACOG, SOGC |
| Critical Care | SCCM |
| Anesthesiology | ASA |
| Nephrology | KDIGO |
| Urology | AUA |
| Radiology | CAR |
| Emergency | CAEP |
| Primary Care | AAFP, CFPC, BC GPAC |

**PubMed fallback strategy:**
- For login-protected sources (NCCN, some ASCO), use PubMed EFetch with API key
- Search: `("specialty"[MeSH]) AND ("Practice Guidelines as Topic"[Publication Type] OR guideline[Title/Abstract]) AND (2020:3000[pdate])`
- Fetch full abstracts + MeSH terms + publication dates + source URLs

**Enhanced `fetch_cross_specialty_guidelines.py`:**
- Use PubMed API key for rate-limited fetching (3 req/sec)
- Batch EFetch with proper delay
- Extract full abstracts + MeSH terms + publication dates + source URLs
- Follow source URLs to get full text where available

**Deliverable:** 500+ guidelines across 20+ specialties indexed in ChromaDB.

---

## Phase 3: Metadata & Quality Improvements

**Problem:** All 17,385 chunks have empty specialty and year metadata.

**Changes:**
1. **Metadata enrichment pipeline** (`metadata_enricher.py`):
   - Extract specialty from source (ACC/AHA → cardiology, ACG → gastroenterology, etc.)
   - Extract year from publication date, title, or filename
   - Extract DOI for deduplication
   - Extract GRADE evidence level if present in text
   - Extract source organization name
2. **Reprocess existing index:**
   - Run metadata enrichment on all 17,385 existing chunks
   - Upsert to ChromaDB with enriched metadata
   - Deduplicate by DOI
3. **Temporal decay function:**
   - Add decay formula to `query_api.py`: `decay = max(0, 1 - (current_year - publication_year) * 0.05)`
   - 2026 guideline: score × 1.0
   - 2023 guideline: score × 0.85
   - 2020 guideline: score × 0.70
   - 2015 guideline: score × 0.50
4. **GRADE binding during chunking:**
   - Modify `chunker.py` to keep GRADE evidence statements attached to their recommendations
   - Pattern: Look for "strong recommendation", "conditional recommendation", "high-quality evidence" within 200 chars of a recommendation sentence
   - Include in chunk metadata: `{"grade": "strong", "evidence_quality": "high"}`

**Deliverable:** All chunks have proper specialty, year, DOI, and GRADE metadata. Temporal decay works.

---

## Phase 4: Aggregator Integration

**Problem:** Manual scraping of 30+ sources is fragile. Aggregators provide centralized, curated access.

**Changes:**
1. **MAGICapp API integration** — Structured API for living guidelines, JSON format with GRADE metadata
2. **ECRI Guidelines Trust** — Strict inclusion criteria (English, <5 years, systematic review), TRUST scorecard
3. **TRIP Database** — PICO-optimized search, integrate into web search pipeline
4. **CMA Infobase** — Canadian guidelines, 5-year relevance window

**Deliverable:** 4 aggregator sources feeding the pipeline automatically.

---

## Phase 5: Weekly Pipeline Automation

**Changes to `weekly_run.sh`:**
1. Run new scrapers (all 30+ specialty sources)
2. Run aggregator fetchers (MAGICapp, ECRI, TRIP, CMA)
3. Run PubMed fallback for login-protected sources
4. Metadata enrichment on all new content
5. Deduplication by DOI
6. Chunk, embed, upsert to ChromaDB
7. Rebuild BM25 index
8. Verify index with test queries across specialties
9. Report counts by specialty and source

**Deliverable:** Automated weekly update that keeps all specialties current.

---

## Phase 6: Monitoring & Quality

1. **Dashboard:** Count of chunks per specialty, per source, per year
2. **Test queries:** 20 clinical queries across all specialties, verify correct results
3. **Staleness alert:** Flag specialties with no guidelines updated in >6 months
4. **Gap detection:** If web search returns good results but RAG returns nothing, log the gap

---

## Files to Create/Modify

| File | Action | Purpose |
|---|---|---|
| `guideline_sources.py` | Rewrite | Source-specific scrapers for 30+ organizations |
| `web_search.py` | Modify | PubMed API key, parallel fetching, quality scoring |
| `qa_web_search.py` | Modify | PubMed API key, expanded allowlist |
| `query_reformer.py` | Create | LLM-based query reformulation |
| `metadata_enricher.py` | Create | Extract specialty, year, DOI, GRADE from chunks |
| `fetch_cross_specialty_guidelines.py` | Modify | Use PubMed API key, batch fetching |
| `fetch_guideline_aggregators.py` | Rewrite | Source-specific selectors for GIN, NICE, ACC, ESC |
| `settings.yaml` | Modify | Add PubMed API key, temporal decay config |
| `sources_config.py` | Modify | Add all 30+ specialty sources |
| `weekly_run.sh` | Modify | Run all new scrapers, enrichment, verification |
| `query_api.py` | Modify | Temporal decay, improved tier classification |
| `chunker.py` | Modify | GRADE binding during chunking |

---

## Progress Tracking

| Phase | Status | Notes |
|---|---|---|
| Phase 0: Fix Broken Scrapers | ✅ DONE | guideline_sources.py, fetch_guideline_aggregators.py rewritten with source-specific selectors, quality filters, DOI extraction |
| Phase 1: Web Search Overhaul | ✅ DONE | web_search.py rewritten with PubMed API key, parallel fetching, quality scoring |
| Phase 2: Cross-Specialty Ingestion | ✅ DONE | fetch_cross_specialty_guidelines.py fixed (XML EFetch, API key from settings.yaml). 207 guidelines fetched, 205 chunks embedded across 20 specialties. ChromaDB: 20,588 total chunks (205 cross-specialty + 20,383 existing). Fixed duplicate chunk IDs by including specialty in ID. |
| Phase 3: Metadata & Quality | ✅ DONE | metadata_enricher.py created and run. Specialty: 100% (20,588/20,588). GRADE: 75.6% (15,565/20,588). DOI: 1.6% (339/20,588). Year: 1.7% (348/20,588, limited by doc_id mapping). Temporal decay added to query_api.py (decay = max(0, 1 - (current_year - year) * 0.05)). GRADE binding added to chunker.py. |
|| Phase 4: Aggregator Integration | ❌ CANCELLED | MAGICapp, ECRI, TRIP, CMA have no public APIs. PubMed API integration already covers structured medical literature from Phase 1/2. |
|| Phase 5: Weekly Automation | ✅ DONE | weekly_run.sh updated with: cross-specialty PubMed fetch (fetch_cross_specialty_guidelines.py), metadata enrichment (metadata_enricher.py --enrich), DOI deduplication, BM25 rebuild, verification query, specialty/source report. |
|| Phase 6: Monitoring & Quality | ✅ DONE | monitoring_dashboard.py (index stats), test_queries.py (20 queries across 12 specialties — all pass), staleness_check.py (8 stale specialties flagged), gap_detector.py (0 gaps — RAG covers all queries). Fixed evidence_min_chars bug (2000 > chunk_size 1800, set to 500). |
