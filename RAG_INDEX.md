# CNG RAG System — Complete Source Index

**Generated:** 2026-03-17  
**Root:** `/mnt/c/project-root/RAG/`  
**Purpose:** Medical knowledge RAG pipeline for clinical note generation (CNG). Ingests clinical guidelines, PubMed articles, PMC papers, ClinicalTrials, and FDA drug labels; chunks/embeds them; stores in ChromaDB; serves hybrid BM25 + dense search via FastAPI.

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [API Server — `query_api.py`](#query_apipy)
3. [Retrieval — `retriever.py`](#retrieverpy)
4. [Vector Store — `store.py`](#storepy)
5. [Vector Store Manager — `vector_store_manager.py`](#vector_store_managerpy)
6. [Embedder — `embedder.py`](#embedderpy)
7. [Embed Chunks — `embed_chunks.py`](#embed_chunkspy)
8. [Chunker — `chunker.py`](#chunkerpy)
9. [Chunking Pipeline — `chunking_pipeline.py`](#chunking_pipelinepy)
10. [Composer — `composer.py`](#composerpy)
11. [BM25 Index — `bm25_index.py`](#bm25_indexpy)
12. [Fetch Sources — `fetch_sources.py`](#fetch_sourcespy)
13. [Guidelines Fetcher — `guidelines_fetcher.py`](#guidelines_fetcherpy)
14. [PMC Fetcher — `pmc_fetcher.py`](#pmc_fetcherpy)
15. [Process Clinical Corpus — `process_clinical_corpus.py`](#process_clinical_corpuspy)
16. [Log Utils — `log_utils.py`](#log_utilspy)
17. [Metrics — `metrics.py`](#metricspy)
18. [Sources Config — `sources_config.py`](#sources_configpy)
19. [Utils Meta — `utils_meta.py`](#utils_metapy)
20. [Version Manager — `version_manager.py`](#version_managerpy)
21. [Update Index — `update_index.py`](#update_indexpy)
22. [Summarize Recent Updates — `summarize_recent_updates.py`](#summarize_recent_updatespy)
23. [Test — `test.py`](#testpy)
24. [RAG Client — `server/services/rag_client.py`](#serverservicesrag_clientpy)
25. [Scripts](#scripts)
    - [`scripts/fetch_aasm_pdfs.py`](#scriptsfetch_aasm_pdfspy)
    - [`scripts/fetch_site_pdfs.py`](#scriptsfetch_site_pdfspy)
    - [`scripts/import_dpd.py`](#scriptsimport_dpd.py)
    - [`scripts/import_local_pdfs.py`](#scriptsimport_local_pdfspy)
    - [`scripts/import_spl.py`](#scriptsimport_splpy)
    - [`scripts/print_site_pages_to_pdf.py`](#scriptsprint_site_pages_to_pdfpy)
    - [`scripts/process_spl_drugs.py`](#scriptsprocess_spl_drugspy)
26. [Config Files](#config-files)
    - [`settings.yaml`](#settingsyaml)
    - [`sources_config.yaml`](#sources_configyaml)
    - [`requirements.txt`](#requirementstxt)
27. [Data Flow Summary](#data-flow-summary)
28. [Dead Code / Legacy / Unused Files](#dead-code--legacy--unused-files)
29. [Dependency Map](#dependency-map)

---

## Architecture Overview

```
[Fetch Layer]       [Corpus Layer]           [Index Layer]        [Serve Layer]
fetch_sources.py    process_clinical         embed_chunks.py      query_api.py
guidelines_fetcher  _corpus.py (enrich)      ──> embeddings/      ──> /query endpoint
pmc_fetcher.py      ──> clean_corpus/        vector_store_        rag_client.py
                    version_manager.py        manager.py           (server integration)
                    ──> current_corpus/       update_index.py
                    chunking_pipeline.py     ──> chroma_store/
                    ──> chunks/              ──> bm25_index.json
scripts/import_*   [Supporting]
                    chunker.py (legacy)
                    embedder.py (model singleton)
                    store.py (chroma client)
                    retriever.py (search impl)
                    bm25_index.py (BM25 cache)
                    metrics.py (perf metrics)
                    log_utils.py (fetch logs)
                    utils_meta.py (normalization)
                    sources_config.py (domain config)
                    composer.py (output formatting)
```

---

## `query_api.py`

**Purpose:** FastAPI RAG server. Exposes `/query` and `/health` endpoints. Implements hybrid retrieval (dense cosine + BM25 + keyword overlap) with optional RRF, caching, filtering, and result summarization.

**Key Dependencies:** `store`, `embedder`, `bm25_index`, `metrics`, `utils_meta`

### Classes

#### `QueryRequest(BaseModel)`
Pydantic input model for `/query`.
- `query: str` — natural language query
- `top_k: int = 6` — number of results (1–50)
- `specialty: Optional[str]` — exact-match metadata filter
- `date_from: Optional[str]` — ISO date lower bound (post-filter)
- `date_to: Optional[str]` — ISO date upper bound
- `include_keywords: Optional[List[str]]` — require any keyword in result text

#### `Hit(BaseModel)`
Output result model: `id, text, metadata, score, summary`.

#### `QueryResponse(BaseModel)`
Full response: `results, used_filters, context, references, refs, meta`.

### Module-Level Functions

| Function | Signature | Description |
|---|---|---|
| `_tokens` | `(s: str) -> List[str]` | Regex tokenizer (alphanum), lowercase |
| `extract_keywords` | `(text, extra, min_len, max_terms) -> List[str]` | Extract top content keywords, ignoring stopwords |
| `summarize_chunk` | `(text, query_kws, target_words=160) -> str` | Lightweight extractive sentence summarizer using keyword overlap |
| `_parse_date_any` | `(s: str) -> Tuple[int,int,int]` | Parse date strings from multiple formats; returns (y,m,d) |
| `_query_cache_key` | `(req, corpus_version) -> Tuple` | Build stable cache key from request params |
| `_get_cached_hits` | `(key) -> Optional[List]` | Thread-safe LRU cache lookup |
| `_store_cached_hits` | `(key, hits)` | Thread-safe LRU cache store (max 128 entries) |
| `_load_settings` | `() -> Dict` | `@lru_cache` — reads `settings.yaml` |
| `_get_embedder` | `() -> Embedder` | `@lru_cache` — singleton Embedder instance |
| `_get_collection` | `()` | `@lru_cache` — singleton Chroma collection |
| `_where_for` | `(request) -> Dict` | Build Chroma `where` filter dict from specialty |
| `_text_date` | `(meta) -> str` | Extract best date string from metadata |
| `_date_ok` | `(meta, dfrom, dto) -> bool` | Post-filter: check if metadata date is in range |
| `_keywords_ok` | `(text, kws) -> bool` | Post-filter: check if text contains any keyword |
| `_cosine` | `(a, b) -> float` | Cosine similarity (defined but **not used** — dead code) |
| `hybrid_search_filtered` | `(req, metrics, cfg) -> List[Dict]` | **Core retrieval**: embed query → vector search → BM25 → hybrid score → filters → RRF/sort → summarize |
| `_summarize_hits` | `(hits) -> Dict` | Produce aggregates (score stats, years, specialties) for logging |
| `_package` | `(hits, query, used_filters) -> Tuple[str, List, Dict]` | Build LLM-ready context block + references list |

### API Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Returns `{"status": "ok"}` or `{"status": "error", "detail": ...}` |
| `POST` | `/query` | Full RAG query — returns `QueryResponse` |

#### `POST /query` Behavior
1. Check LRU cache for this query+params combination
2. If cache miss: embed query, vector search Chroma, BM25 score, hybrid score + recency/authority boost
3. Post-filter by date, keywords
4. Optional RRF reranking
5. Dedupe per doc (`max_chunks_per_doc` from settings)
6. Summarize long chunks
7. Build context block and references list
8. Log metrics to CSV

### Scoring Details
- `hybrid_score = cosine_sim * (1 - lambda) + bm25_norm * lambda` (lambda=0.10 default)
- `final_score = hybrid * (1 - 0.15) + keyword_overlap * 0.15`
- Recency boost: +0.025 for year ≥ 2022, +0.01 for year ≥ 2018
- Authority boost: +0.02 if source contains "guideline", "thoracic", "chest", "nice", "idsa", etc.

### Dead/Commented-Out Code
- `_cosine()` function is defined but **never called** in `query_api.py` (duplicate of `retriever.py` version)
- `from contextlib import nullcontext` and `from dataclasses import asdict` are imported but `asdict` is unused

---

## `retriever.py`

**Purpose:** Core search module implementing hybrid retrieval with parallel BM25 + vector search, LRU cache, and minimum similarity filtering.

**Key Dependencies:** `bm25_index`, `metrics`

### Constants
- `MIN_SIM = 0.15` — minimum hybrid score threshold
- `HYBRID_LAMBDA = 0.20` — BM25 weight (20% lexical, 80% semantic)
- `_CACHE_MAX = 128` — LRU cache size

### Functions

| Function | Signature | Description |
|---|---|---|
| `_cosine` | `(a, b) -> float` | Cosine similarity (defined but **not called** here) |
| `_measure` | `(name, metrics) -> contextmanager` | Wraps metrics.measure or nullcontext |
| `_cache_key` | `(query, k, corpus_version) -> Tuple` | Stable cache key |
| `_get_cached` | `(key, metrics) -> Optional[List]` | LRU cache lookup; records cache_hit counter |
| `_store_cache` | `(key, hits)` | LRU store with eviction |
| `search` | `(col, embedder, query, k, metrics, corpus_version, use_cache) -> List[Dict]` | **Main search function**: parallel BM25 + vector search via ThreadPoolExecutor, hybrid score, MIN_SIM filter, sort, cache |

### Notes
- Uses `ThreadPoolExecutor(max_workers=2)` to parallelize BM25 and vector search
- Returns hits as `{"text", "metadata", "score", "id"}`
- **Not called by `query_api.py`** — `query_api.py` implements its own inline retrieval. This module is used by `vector_store_manager.py` for CLI search and by the now-superseded path.

---

## `store.py`

**Purpose:** Thread-safe singleton factory for ChromaDB `PersistentClient` and collection handles.

**Key Dependencies:** `chromadb`

### Module-Level State
- `_CLIENT`, `_CLIENT_PATH`, `_CLIENT_LOCK` — singleton client with lock
- `_COLLECTION_CACHE`, `_COLLECTION_LOCK` — per-(client, name) collection cache

### Functions

| Function | Signature | Description |
|---|---|---|
| `get_client` | `(persist_directory: str)` | Returns or creates `chromadb.PersistentClient` (threadsafe); sets `BM25_PERSIST_DIR` env var; creates dir |
| `get_collection` | `(client, name="medical_rag")` | Returns or creates a Chroma collection with cosine HNSW space; cached per (client_id, name) |

---

## `vector_store_manager.py`

**Purpose:** CLI tool and library for ingesting precomputed embeddings into ChromaDB, counting vectors, and running hybrid search. Supports JSONL, NPZ, and portable (npy+jsonl) embedding formats.

**Key Dependencies:** `store`, `utils_meta`, `retriever`, `embedder`, `bm25_index`

### Standalone Functions

| Function | Signature | Description |
|---|---|---|
| `_load_jsonl` | `(fp: Path) -> Tuple[...]` | Load embeddings from JSONL (id, text, embedding, metadata per line) |
| `_load_npz` | `(fp: Path) -> Tuple[...]` | Load embeddings from NPZ archive |
| `_load_portable_dir` | `(dir_path, chunk_dir) -> Tuple[...]` | Load portable embedding set (embeddings.npy + ids.jsonl + metadatas.jsonl); reconstructs text from chunk JSONL files |
| `_ensure_core_meta` | `(meta: Dict)` | Fill defaults for specialty, timestamp, source, doc_id, chunk_index, chunk_count, word_count |

### Classes

#### `ManagerConfig`
Dataclass: `persist_directory`, `collection_name`, `embedding_model`.

#### `VectorStoreManager`
Main manager class.

| Method | Signature | Description |
|---|---|---|
| `__init__` | `(cfg: ManagerConfig)` | Initialize client and collection |
| `ingest_dir` | `(emb_dir, reset, upsert, delete_where, chunk_dir) -> int` | Load all embeddings from dir; handles portable sets specially; batches into Chroma; warms BM25 |
| `_flush_batch` | `(ids, texts, embs, metas, upsert) -> int` | Sanitize metas and add/upsert to Chroma in batch |
| `count` | `() -> int` | Return total vector count in collection |
| `hybrid_search` | `(query, k, specialty, start, end) -> List[Dict]` | Search with post-filtering; wraps `retriever.search` |

### CLI Commands
- `ingest` — Load embeddings into Chroma from dir
- `count` — Print total count
- `search` — Hybrid search with optional filters (`--specialty`, `--start`, `--end`)

---

## `embedder.py`

**Purpose:** Thread-safe singleton wrapper around `sentence_transformers.SentenceTransformer`. Supports Matryoshka dimension truncation for Jina v5 and similar models.

**Key Dependencies:** `sentence_transformers`

### Module-Level State
- `_MODEL_CACHE` / `_MODEL_LOCK` — singleton model cache by name
- `MATRYOSHKA_DIM` — from env var `EMBEDDER_DIM`; set to None to disable truncation

### Functions

| Function | Signature | Description |
|---|---|---|
| `_pick_device` | `() -> str` | Auto-detect cuda/mps/cpu; overrideable by env `EMBEDDER_DEVICE` |
| `_get_model` | `(model_name: str) -> SentenceTransformer` | Load or return cached model |
| `_supports_matryoshka` | `(model_name: str) -> bool` | Check if model supports Matryoshka (Jina v5 or "matryoshka" in name) |
| `_encode_kwargs` | `(model_name: str) -> dict` | Build encode kwargs (normalize, convert_to_numpy, optional output_dim) |
| `_cached_single_embedding` | `(model_name, text) -> np.ndarray` | `@lru_cache(maxsize=512)` — single-text embedding cache |

### Classes

#### `Embedder`
| Method | Signature | Description |
|---|---|---|
| `__init__` | `(model_name: str)` | Load or retrieve model |
| `encode` | `(texts: List[str]) -> List[np.ndarray]` | Encode batch; uses lru_cache for single-text queries; returns float32 normalized arrays |

**Current model** (from `settings.yaml`): `avsolatorio/GIST-small-Embedding-v0`

---

## `embed_chunks.py`

**Purpose:** Offline batch embedder. Loads chunk JSONL files from `./chunks/`, encodes with Sentence-Transformers, saves portable artifacts (npy + jsonl) to `./embeddings/`, and optionally inserts into Chroma.

**Key Dependencies:** `embedder`, `store`, `utils_meta`

### Functions

| Function | Signature | Description |
|---|---|---|
| `iter_chunks` | `(chunk_dir: Path) -> Iterator[Tuple]` | Yield (id, text, metadata) from `*.chunks.jsonl` or `*.jsonl` |
| `encode_batches` | `(emb, texts, batch) -> np.ndarray` | Encode in batches; stacks into float32 array |
| `save_portable` | `(ids, metas, embs, out_dir, cfg)` | Save ids.jsonl, metadatas.jsonl, embeddings.npy, manifest.json |
| `upsert_chroma` | `(ids, metas, texts, embs, cfg)` | Add to Chroma collection (not upsert; may fail on dupes) |
| `main` | `()` | CLI: parse args, collect chunks, embed, save portable; optionally upsert to Chroma |

### CLI Args
- `--input ./chunks` — chunk dir
- `--output ./embeddings` — output dir
- `--model <name>` — override model
- `--batch 64` — batch size
- `--to-chroma` — also write to Chroma

### Dead Code
- `upsert_chroma()` is called only when `--to-chroma` is passed; uses `col.add()` not `col.upsert()` — naming is misleading

---

## `chunker.py`

**Purpose:** Early/legacy chunker for reading `.txt` corpus files. Provides simple character-based chunking with overlap, PHI redaction patterns, and basic metadata extraction from text headers.

**Key Dependencies:** `utils_meta`

### Classes

#### `Chunk(BaseModel)`
Fields: `id: str`, `text: str`, `metadata: Dict`

### Constants
- `PHI_PATTERNS` — list of regex patterns for SSN, phone, dates, MRN, naive full names

### Functions

| Function | Signature | Description |
|---|---|---|
| `deidentify` | `(text: str) -> str` | Apply PHI_PATTERNS redaction |
| `chunk_text` | `(doc_text, chunk_size=1800, overlap=200) -> List[str]` | Character-based sliding window chunker |
| `read_corpus` | `(corpus_dir: str) -> List[Chunk]` | Read `.txt` files from dir, deidentify, chunk, return Chunk list |
| `extract_metadata` | `(text: str) -> Dict` | Extract Source, Section, last_updated from first lines |
| `strip_header` | `(text: str) -> str` | Remove first 2 lines (where metadata was stashed) |

### Status: **LEGACY**
This file uses `.txt` input format and character-based chunking. The pipeline now uses `chunking_pipeline.py` with JSON/JSONL inputs and sentence-aware chunking. `chunker.py` may still be used for sample_corpus txt files written by `guidelines_fetcher.py --emit-txt`.

---

## `chunking_pipeline.py`

**Purpose:** Production chunker. Loads cleaned JSONL/JSON documents, splits into 100–300 word sentence-aware chunks with heading context, writes JSONL chunk files. Handles enumerated lists, headings, and small paragraph accumulation.

**Key Dependencies:** `utils_meta`

### Classes

#### `CleanDoc`
Dataclass: `id: str`, `text: str`, `metadata: Dict`

#### `Chunk`
Dataclass: `id: str`, `text: str`, `metadata: Dict`

### Functions

| Function | Signature | Description |
|---|---|---|
| `word_count` | `(s: str) -> int` | Split-based word count |
| `iter_input_records` | `(path: Path) -> Iterator[Dict]` | Yield records from `.jsonl` (one per line) or `.json` (array or single) |
| `normalize_record` | `(obj, fallback_id) -> CleanDoc` | Normalize record; pull metadata from nested or flat fields |
| `is_enumerated_block` | `(paragraph: str) -> bool` | True if ≥60% lines are bullet/number list items |
| `is_heading` | `(paragraph: str) -> bool` | True if short colon-ending or ALL-CAPS or title-case block |
| `split_sentences` | `(text: str) -> List[str]` | Lightweight sentence boundary split; merges stray short fragments |
| `paragraphs` | `(text: str) -> List[str]` | Split text on double newlines |
| `chunk_paragraph` | `(paragraph, target_min, target_max) -> List[str]` | Sentence-accumulating chunker with min/max word bounds |
| `chunk_document` | `(text, target_min, target_max) -> List[Tuple[str, Optional[str]]]` | Full document chunker: flush buffer, keep lists intact, attach headings |
| `build_chunk_text` | `(body, title, heading) -> str` | Prepend title/heading context to chunk body |
| `process_file` | `(path, output_dir, target_min, target_max) -> Tuple[int, int]` | Process one input file → one `*.chunks.jsonl` output file |
| `main` | `()` | CLI: glob input, call process_file for each, report totals |

### CLI Args
- `--input ./cleaned`
- `--output ./chunks`
- `--pattern *.jsonl`
- `--target_min 100`
- `--target_max 300`

---

## `composer.py`

**Purpose:** Generates a structured "INDEPENDENT OPINION (RAG)" consult comment from RAG hits. Intended as drop-in for `OPINION_TEMPLATE.format_map()` in the CNG notes flow.

**Key Dependencies:** `metrics`

### Functions

| Function | Signature | Description |
|---|---|---|
| `format_references` | `(snippets: List[Dict]) -> str` | Format `[1] source — title — section` reference list |
| `_best_lines` | `(text, limit_words=220) -> str` | Return first 2–3 sentences trimmed to ~220 words |
| `build_cited_opinion` | `(query, hits) -> str` | Build full structured consult comment with template sections and references. Records `coverage_hits` in metrics. |
| `compose_consult_comment` | `(query, hits) -> str` | Thin wrapper calling `build_cited_opinion`; this is the call-site entry point |

### Dead/Commented-Out Code
- Comment at top: `# from prompts import OPINION_TEMPLATE  # optional, not needed here` — indicates this replaced a prior template-based approach
- `_extracts` variable in `build_cited_opinion` is computed but **never used** in the output body

---

## `bm25_index.py`

**Purpose:** BM25 index with file-based persistence. Wraps `rank_bm25.BM25Okapi` with normalized scoring. Maintains a global cache to avoid rebuilds. Persists to `chroma_store/bm25_index.json`.

**Key Dependencies:** `rank_bm25`

### Module-Level State
- `_cache = {"ids", "docs", "bm25", "count"}` — global in-process cache

### Classes

#### `BM25Helper`
| Method | Signature | Description |
|---|---|---|
| `__init__` | `(docs: List[str])` | Tokenize docs, build BM25Okapi |
| `scores` | `(query: str) -> List[float]` | Get raw BM25 scores; updates `_max` |
| `normalize` | `(raw: float) -> float` | Divide by `_max` |

### Functions

| Function | Signature | Description |
|---|---|---|
| `_tokens` | `(s: str) -> List[str]` | Regex tokenizer |
| `_persist_path` | `() -> str` | Returns `$BM25_PERSIST_DIR/bm25_index.json` |
| `_load_persisted` | `() -> bool` | Load cache from JSON file; rebuild BM25Helper |
| `_persist` | `(ids, docs, helper)` | Save ids, docs, max_score to JSON |
| `warm_bm25` | `(col) -> Tuple[BM25Helper, List[str]]` | Fetch ALL docs from Chroma, build BM25, persist |
| `get_bm25` | `(col) -> Tuple[BM25Helper, List[str]]` | Return cached BM25; rebuild if empty/stale (count changed) |

---

## `fetch_sources.py`

**Purpose:** Automated weekly fetcher. Pulls from PubMed, ClinicalTrials.gov, OpenFDA, and DrugBank (stub). Applies inclusion/exclusion filters. Saves batches to `./raw_docs/`. Logs runs to `fetch_log.jsonl`. Supports ad-hoc or weekly-scheduled runs.

**Key Dependencies:** `sources_config`, `utils_meta`, `log_utils`; external `requests`, `schedule`

### Functions

| Function | Signature | Description |
|---|---|---|
| `_text_passes_filters` | `(text, cfg, domains) -> bool` | Apply include/exclude keyword filters from config |
| `_looks_like_letter` | `(title: str) -> bool` | Heuristic: drop letter-to-editor articles |
| `fetch_pubmed` | `(days, domains, cfg) -> List[Dict]` | ESearch + ESummary; filter by text; no abstract by default |
| `fetch_clinicaltrials` | `(days, domains, cfg) -> List[Dict]` | CTGov v2 API with pagination; filter by text |
| `fetch_openfda` | `(days, domains, cfg) -> List[Dict]` | OpenFDA drug label updates; filter by text |
| `fetch_drugbank` | `(days, domains, cfg) -> List[Dict]` | Stub (returns mock item unless `DRUGBANK_API_KEY` set) |
| `save_batch` | `(source_id, items) -> Optional[Path]` | Write batch JSON to `./raw_docs/` |
| `append_log` | `(entry: Dict)` | Call `append_recent_log` to add entry |
| `run_fetch` | `(domains, days) -> Dict` | Orchestrate all fetchers; return summary |
| `schedule_weekly` | `(domains)` | Schedule Monday 08:00 weekly run with `schedule` library |
| `main` | `()` | CLI entry point |

### CLI Args
- `--domains cardiology,pulmonology,...`
- `--days 7`
- `--weekly` — long-running scheduler mode

---

## `guidelines_fetcher.py`

**Purpose:** Crawls 15 medical society websites (ACP, IDSA, ACC/AHA, NICE, ADA, KDIGO, ATS, CHEST, ASCO, AAN, AGA, ACG, ASH, ESMO, WHO) to scrape and download guideline full-text (HTML or PDF). Writes JSON array output to `./raw_docs/`.

**Key Dependencies:** `log_utils`, `sources_config`; external `requests`, `BeautifulSoup`, optional `PyPDF2`, `pdfminer`

### Module-Level Constants
- `SOURCES` — list of 15 society dicts with name, index URL, domain
- `DEFAULT_UA` — user agent string
- `PDF_MAX_BYTES` — max PDF size (default 8MB; env `GUIDELINES_PDF_MAX_BYTES`)
- `USE_PDFMINER` — env flag `GUIDELINES_USE_PDFMINER`

### Functions

| Function | Signature | Description |
|---|---|---|
| `_make_session` | `() -> requests.Session` | Session with retry/backoff |
| `sleep_ms` | `(ms)` | Sleep milliseconds |
| `sha1` | `(s: str) -> str` | 16-char SHA1 of string |
| `find_date` | `(text: str)` | Regex best-effort date extraction from HTML |
| `get` | `(url, timeout, stream)` | GET with error suppression |
| `absolute_links` | `(base, links)` | Resolve and dedupe anchor hrefs |
| `looks_like_guideline` | `(url, text) -> bool` | Heuristic: guideline/statement/consensus in URL or text |
| `scrape_index_generic` | `(start_url, domain_filter, link_filter, timeout)` | Crawl index page and collect guideline links |
| `extract_html_text` | `(html: str) -> str` | Strip boilerplate (nav/footer/script); extract text from main content |
| `extract_pdf_text_bytes` | `(pdf_bytes: bytes) -> str` | Extract PDF text via PyPDF2 (primary) or pdfminer (secondary) |
| `fetch_fulltext_from_url` | `(url, timeout, prefer_pdf, pdf_max_bytes) -> Dict` | Fetch HTML or PDF from URL; optionally follow PDF link from HTML |
| `extract_title_and_date` | `(html, fallback_url) -> Tuple[str, str]` | Extract title and date from HTML meta/h1 |
| `crawl_source` | `(src, max_links, timeout, depth, prefer_pdf, pdf_max_bytes) -> Tuple[List, Dict]` | Crawl one society; collect up to max_links guideline pages |
| `collect_guideline_links` | `(start_url, domain, timeout, max_links, depth) -> List[str]` | BFS link discovery for guideline pages |
| `_extract_year` | `(s: str) -> int` | Extract year from string |
| `filter_by_year` | `(rows, years) -> List` | Filter by year cutoff |
| `_parse_date_any` | `(s: str) -> datetime` | Multi-format date parser |
| `filter_by_days` | `(rows, days) -> List` | Filter by recency cutoff |
| `_pediatric_re` | `() -> re.Pattern` | Pediatric population exclusion regex |
| `filter_rows` | `(rows, cfg) -> List` | Apply exclude keywords + pediatric filter + dedup by id |
| `write_json_array` | `(path, rows)` | Write JSON array to file |
| `maybe_emit_txt` | `(rows, outdir)` | Write `.txt` files with Source/Section/last_updated headers (for legacy `chunker.py`) |
| `main` | `()` | CLI entry |

### CLI Args
- `--emit-txt` — also write `sample_corpus/*.txt`
- `--out ./raw_docs`
- `--limit-per-source 80`
- `--timeout 45`
- `--years 0` / `--days 30`
- `--depth 2`
- `--fetch-pdf`
- `--pdf-max-mb 8`

---

## `pmc_fetcher.py`

**Purpose:** Harvests PMC Open Access and Selective Deposit article metadata for Internal Medicine topics. Two modes: OA Web Service (date-based, returns PMCID list) and OAI-PMH (publisher-based selective deposit). Filters for IM relevance and excludes pediatric content.

**Key Dependencies:** `log_utils`, `sources_config`; external `requests`

### Functions

| Function | Signature | Description |
|---|---|---|
| `_make_session` | `() -> requests.Session` | Session with retry |
| `_collect_im_keywords` | `(cfg) -> List[str]` | Collect IM domain keywords from sources_config |
| `_looks_im_relevant` | `(text, im_terms) -> bool` | True if any IM term found in text |
| `_is_pediatric` | `(text: str) -> bool` | Apply pediatric regex |
| `fetch_oa_subset` | `(since_iso, max_items) -> List[Dict]` | PMC OA Web Service XML parse; yields PMCID list |
| `harvest_selective` | `(publishers, years, page_max) -> List[Dict]` | OAI-PMH ListRecords with resumption tokens; parse JATS fields |
| `_list_sets` | `() -> List[Dict]` | OAI-PMH ListSets |
| `_match_publisher_sets` | `(publishers) -> List[str]` | Match setSpec by publisher name substring |
| `filter_im` | `(items, cfg) -> List[Dict]` | Filter by IM terms + exclude + pediatric |
| `dedupe_by_pmcid` | `(items) -> List[Dict]` | Deduplicate and normalize PMCIDs |
| `save_batch` | `(tag, items) -> Optional[Path]` | Write JSON to `./raw_docs/pmc_{tag}_{stamp}.json` |
| `append_log` | `(entry)` | Log entry via `log_utils` |
| `main` | `()` | CLI with `--oa-subset` / `--selective` mutually exclusive modes |

### CLI Args
- `--oa-subset` / `--selective` (mutually exclusive, required)
- `--years 5` / `--days 0`
- `--publishers "BMJ Publishing Group, Oxford University Press"`
- `--max 2000`

---

## `process_clinical_corpus.py`

**Purpose:** Enrichment/filtering step between raw fetch and chunking. For PubMed articles: fetches full abstract via EFetch, filters for result-bearing publications. For ClinicalTrials: keeps trials with posted results only. For PMC: fetches JATS full text via OAI-PMH. For guidelines: accepts only pages with recommendation cues.

**Key Dependencies:** `requests`, `lxml`

### Classes

#### `Rec`
Dataclass wrapping a raw dict with `source` and `id` properties.

### Constants
- `RESULT_CUES` — regex: results/conclusion/OR/HR/p<0.05/etc.
- `METHODS_ONLY_CUES` — regex: protocol/methods/pilot/objective (for context)
- `PREFERRED_PUBTYPES` — set of preferred publication types
- `EFETCH`, `ELINK`, `PMC_OA_BASE`, `PMC_OAI` — NCBI API URLs

### Functions

| Function | Signature | Description |
|---|---|---|
| `read_records` | `(path: Path) -> Iterator[Dict]` | Read JSONL or JSON records |
| `ensure_parent_dir` | `(out_path: Path)` | Make parent dirs |
| `write_jsonl` | `(items, out_path)` | Write JSONL output |
| `wc` | `(s: str) -> int` | Word count |
| `compact_ws` | `(s: str) -> str` | Collapse whitespace |
| `fetch_pubmed_xml` | `(pmid: str) -> Optional[etree._Element]` | EFetch XML for PMID |
| `parse_pubmed_article` | `(root) -> Dict` | Parse title, abstract, journal, year, pubtypes from XML |
| `elink_pmcid` | `(pmid: str) -> Optional[str]` | ELink pubmed→pmc for PMCID |
| `fetch_pmc_fulltext` | `(pmcid: str) -> Optional[str]` | OAI-PMH GetRecord; extract body text from JATS (deprecated path) |
| `pmcid_to_pmid` | `(pmcid: str) -> Optional[str]` | ELink pmc→pubmed |
| `fetch_pmc_fulltext_by_pmcid` | `(pmcid: str) -> Optional[str]` | OAI-PMH full text by PMCID (cleaner path) |
| `fetch_ctgov` | `(nct_id: str) -> Optional[Dict]` | CTGov v2 API fetch by NCT ID |
| `assemble_ctgov_text` | `(payload) -> Tuple[str, Dict, bool]` | Build readable text block from CTGov study JSON; return (text, extra_meta, has_results) |
| `accept_pubmed_abstract` | `(text, pubtypes) -> bool` | Accept if has RESULT_CUES or guideline-type |
| `accept_results_like_text` | `(text: str) -> bool` | Accept PMC full text if has result cues |
| `accept_guideline_text` | `(text: str) -> bool` | Accept if has recommendation cues and not a hub/index page |
| `handle_pubmed` | `(rec, fulltext) -> Optional[Dict]` | Fetch PubMed abstract; optionally upgrade to PMC full text |
| `handle_pmc` | `(rec, fulltext) -> Optional[Dict]` | Try PMC OA full text; fallback to PubMed abstract |
| `handle_guideline` | `(rec) -> Optional[Dict]` | Accept guideline with recommendation cues |
| `handle_ctgov` | `(rec) -> Optional[Dict]` | Fetch CTGov; accept only if has results |
| `process_stream` | `(records, allow_pubmed, allow_trials, fulltext) -> Iterator[Dict]` | Route records by source to correct handler |
| `main` | `()` | CLI: --in, --out, --no-trials, --no-pubmed, --fulltext |

### Dead/Noted Code
- `fetch_pmc_fulltext()` is an older path; `fetch_pmc_fulltext_by_pmcid()` is the cleaner version; both exist and `handle_pmc` uses the newer one

---

## `log_utils.py`

**Purpose:** Rolling log file manager for `fetch_log.jsonl`. Keeps entries within a configurable time window (default 7 days). Shared across all fetchers.

### Functions

| Function | Signature | Description |
|---|---|---|
| `_parse_timestamp` | `(obj: Dict) -> Optional[datetime]` | Extract and parse timestamp from log entry |
| `_should_keep` | `(entry, cutoff) -> bool` | True if entry is within window |
| `_iter_existing_lines` | `(path: Path)` | Read existing JSONL, tolerating parse errors |
| `append_recent_log` | `(entry, log_path, max_age_days=7)` | Trim old entries, append new entry, rewrite file |
| `load_recent_entries` | `(log_path, max_age_days=7) -> List[Dict]` | Return entries within window, sorted by timestamp |

---

## `metrics.py`

**Purpose:** Per-request metrics recorder. Records wall-time measurements for named phases (embed_query, vector_search, bm25_search, etc.) and counters (retrieved_k, score_mean, etc.). Persists to `logs/request_metrics.csv`.

### Classes

#### `RequestMetrics`
| Method | Signature | Description |
|---|---|---|
| `__init__` | `(query, top_k, request_id, log_path)` | Initialize with query, top_k; start timer |
| `activate` | `() -> contextmanager` | Bind to thread-local context var |
| `measure` | `(name: str) -> contextmanager` | Time a code block; accumulate |
| `set_measurement` | `(name, seconds)` | Set a measurement directly |
| `record_counter` | `(name, value)` | Store counter value |
| `increment_counter` | `(name, delta)` | Add to counter |
| `finish` | `() -> float` | Compute total elapsed |
| `to_row` | `() -> Dict` | Serialize to CSV row dict |
| `log` | `()` | Append row to `logs/request_metrics.csv` |
| `current` | `() -> Optional[RequestMetrics]` | Class method: get context-var bound instance |

### Module Functions

| Function | Signature | Description |
|---|---|---|
| `get_current_metrics` | `() -> Optional[RequestMetrics]` | Get thread-local metrics instance |
| `maybe_measure` | `(name: str) -> contextmanager` | Measure if metrics active, else nullcontext |

---

## `sources_config.py`

**Purpose:** Defines the medical domain scope, trusted sources, and include/exclude keyword filters for the RAG pipeline. Exports `get_config()` and can be run to write `sources_config.yaml`.

### Data

| Name | Type | Description |
|---|---|---|
| `SUPPORTED_DOMAINS` | `List[str]` | 16 medical domains |
| `TRUSTED_SOURCES` | `List[Source]` | 8 trusted source definitions (PubMed, WHO, CTGov, DrugBank, OpenFDA, NICE, ADA, ACC) |
| `GLOBAL_MESH_TERMS` | `List[str]` | 5 global MeSH terms |
| `GLOBAL_KEYWORDS` | `List[str]` | 18 global keywords |
| `DOMAIN_FILTERS_INCLUDE` | `Dict` | Per-domain MeSH + keyword lists (14 domains) |
| `GLOBAL_EXCLUDE_KEYWORDS` | `List[str]` | 19 exclusion keywords (billing, marketing, legal, etc.) |

### Classes

#### `Source`
Frozen dataclass: `id, name, base_url, type, notes`.

### Functions

| Function | Signature | Description |
|---|---|---|
| `get_config` | `() -> Dict` | Return the full config dict |
| `write_yaml` | `(path) -> Path` | Write config to YAML file |

### Notes
- `emergency_medicine` is in `SUPPORTED_DOMAINS` but has **no entry in `DOMAIN_FILTERS_INCLUDE`** — potential gap

---

## `utils_meta.py`

**Purpose:** Metadata normalization, sanitization, and quality counter utilities. Used by chunkers, embedders, and the query API.

### Functions

| Function | Signature | Description |
|---|---|---|
| `_to_primitive` | `(v: Any) -> Any` | Convert to str/int/float/bool/isoformat; lists → semicolon-joined |
| `flatten_meta` | `(d: Dict, prefix: str) -> Dict` | Flatten nested dicts with dot-notation keys |
| `sanitize_metas` | `(metas: list) -> list` | Flatten + primitives + align keys across all rows |
| `_extract_year` | `(*values) -> str` | Extract 4-digit year from any of the given values |
| `normalize_metadata_fields` | `(meta: Dict) -> Dict` | Ensure doc_id, publisher, group_id, guideline_year, doc_type, specialty, geography, version, doi, nid are present and normalized |
| `_tokenize` | `(text: str) -> List[str]` | Alphanum tokenizer, stopword-filtered |
| `_doc_id_from_meta` | `(meta: Dict) -> str` | Extract first non-empty id field |
| `gather_quality_counters` | `(hits, query, context_text) -> Dict` | Compute: retrieved_k, unique_docs, sources_diversity, coverage_hits, overlap_tokens, score stats, specialty scores, year_span |
| `normalize_whitespace` | `(text: str) -> str` | Collapse whitespace |
| `dedupe_and_normalize_hits` | `(hits, max_per_doc=2) -> List[Dict]` | Deduplicate by doc_id (allow up to max_per_doc chunks per doc); normalize text/summary whitespace |

---

## `version_manager.py`

**Purpose:** Manages document versioning and staleness for the corpus. Compares incoming documents against a persistent index by content hash (or title similarity when no ID). Archives outdated versions, deletes documents older than threshold (unless foundational). Maintains `./version_index.json` and `./current_corpus/`.

### Classes

#### `IndexEntry`
Dataclass: `key_src, key_id, title, date, hash, path, foundational`. Property `key` returns `(key_src, key_id)`.

### Functions

| Function | Signature | Description |
|---|---|---|
| `normalize_title` | `(title: str) -> str` | Lowercase alnum normalization |
| `content_hash` | `(item: Dict) -> str` | SHA1 of title+text+date+journal |
| `parse_date_any` | `(s: str) -> datetime` | Multi-format date parser |
| `make_key` | `(source, id_, title) -> Tuple` | (source, id) or (source, normalized_title) |
| `load_index` | `() -> Dict[str, IndexEntry]` | Load `version_index.json` |
| `save_index` | `(idx)` | Write `version_index.json` |
| `key_to_filename` | `(key) -> str` | Safe filename from key tuple |
| `add_or_update_doc` | `(item, idx, similarity, simulate) -> str` | Add/update/unchanged; archive old version on change |
| `delete_old_docs` | `(idx, age_years, foundational_field, simulate) -> List[str]` | Delete docs older than threshold (skip foundational) |
| `main` | `()` | CLI: --input, --simulate, --foundational-field, --similarity, --age-years, --max-updates |

---

## `update_index.py`

**Purpose:** Orchestration script to ingest new embeddings into Chroma, prune obsolete doc_ids (based on `./current_corpus/`), and create daily/weekly snapshots of the Chroma persistence directory.

**Key Dependencies:** `store`, `vector_store_manager`

### Functions

| Function | Signature | Description |
|---|---|---|
| `load_settings` | `() -> Dict` | Read `settings.yaml` |
| `col_counts` | `(col) -> int` | Count vectors in collection |
| `load_active_doc_ids` | `(current_dir: Path) -> Set[str]` | Read all `current_corpus/*.json` and collect `id` fields |
| `build_docid_index` | `(col) -> Tuple[Dict, Dict]` | Get all metadata from Chroma; build doc_id → chunk_ids and doc_id → count maps |
| `delete_doc_chunks` | `(col, doc_ids) -> int` | Delete all chunks where metadata.doc_id in list; returns deleted count |
| `snapshot_chroma` | `(persist_dir, mode, out_root) -> Optional[Path]` | Copy Chroma dir to `./snapshots/chroma/{YYYYMMDD or YYYY-W##}/` |
| `main` | `()` | CLI: ingest via VSM → prune obsolete → snapshot → report |

### CLI Args
- `--emb-dir ./embeddings`
- `--chunk-dir ./chunks`
- `--snapshots none|daily|weekly|both`
- `--no-prune`

---

## `summarize_recent_updates.py`

**Purpose:** Reads the most recent weekly fetch run from `fetch_log.jsonl`, iterates fetched documents, sends each to a local llama-server (`http://127.0.0.1:8081/completion`) for LLM summarization (2-sentence clinical summary), and caches results in `recent_updates.json`.

**Key Dependencies:** `requests`; Windows path `C:\RAG`

### Notable: **Code is duplicated** — the entire module body appears **twice** in the file (lines 1–~130 and again ~130–end). This is likely a copy-paste bug.

### Functions (first definition counts)

| Function | Signature | Description |
|---|---|---|
| `looks_like_letter` | `(title: str) -> bool` | Filter editorial letters |
| `load_fetch_entries` | `(path: Path) -> List[Dict]` | Read fetch_log.jsonl |
| `choose_weekly_entry` | `(entries) -> Dict` | Prefer entry with days≥7; else newest |
| `load_cache` | `(cache_path) -> Dict` | Load `recent_updates.json` or empty dict |
| `save_cache` | `(cache_path, data)` | Write `recent_updates.json` |
| `flatten_doc_text` | `(doc: Dict) -> str` | Concatenate text/abstract/summary fields; cap at MAX_TEXT_CHARS (2000) |
| `build_prompt` | `(title, content) -> str` | Build summarization prompt for llama-server |
| `call_llm` | `(prompt: str) -> str` | POST to `http://127.0.0.1:8081/completion`; parse response |
| `summarise_documents` | `(entry, cache, max_docs) -> Dict` | Iterate batch files; skip cached; call LLM; return summary dict |
| `main` | `(argv) -> int` | CLI: --max-docs; check cache freshness; run summarise_documents |

### Issues/Flags
- Hardcoded `RAG_ROOT = Path(r"C:\RAG")` — **Windows-only path**
- **Entire module body duplicated** — second import block at line ~130 redefines everything
- `LLMError` class defined **twice** (duplicate)
- Relies on locally running llama-server (not included in this repo)

---

## `test.py`

**Purpose:** Minimal one-time smoke test. Loads the `jinaai/jina-embeddings-v5-text-nano` model on CPU and prints `✓ Model loads`.

```python
from sentence_transformers import SentenceTransformer
model = SentenceTransformer('jinaai/jina-embeddings-v5-text-nano', device='cpu')
print('✓ Model loads')
```

**Status:** Not a proper test suite. Just a manual model check. **Not a pytest module.**

**Note:** The configured model in `settings.yaml` is `avsolatorio/GIST-small-Embedding-v0`, not the Jina model. This test may reflect a prior configuration.

---

## `server/services/rag_client.py`

**Purpose:** Convenience integration layer for the CNG server. Resolves the RAG root, imports `query_api` dynamically, and provides cleaned context for note generation.

**Key Dependencies:** `query_api` (dynamically imported)

### Functions

| Function | Signature | Description |
|---|---|---|
| `_rag_root` | `() -> Path` | Resolve RAG root: env `RAG_ROOT` → 2 levels up → `C:\RAG` fallback |
| `_ensure_rag_import` | `()` | Add RAG root to `sys.path` |
| `retrieve_context` | `(query, top_k=16, include_keywords, date_from) -> Tuple[str, List[Dict]]` | Call `hybrid_search_filtered` + `_package` directly (bypasses HTTP); returns (context_str, refs) |
| `clean_context` | `(s: str) -> str` | Post-process context: ftfy normalization, remove boilerplate lines (QUALITY OF EVIDENCE, AASM headers, etc.), strip citation brackets, fix hyphenated line breaks |
| `get_context` | `(query: str) -> Tuple[str, List[Dict], str]` | Convenience wrapper: retrieve with CPAP/sleep-apnea keywords, date_from=2018-01-01; returns (ctx_raw, refs, ctx_clean) |

### Notes
- `get_context()` has **hardcoded** `include_keywords` for PAP/CPAP — this is domain-specific (sleep medicine) and probably needs to be parameterized
- Imports `query_api` at call time, not module load time, to avoid circular imports and allow path injection

---

## Scripts

### `scripts/fetch_aasm_pdfs.py`

**Purpose:** Downloads guideline PDFs from the AASM (American Academy of Sleep Medicine) practice guidelines page. Parses HTML for PDF hrefs, handles members-only-resource redirect params.

| Function | Description |
|---|---|
| `sanitize_name(name)` | Clean filename |
| `extract_pdf_links(html, base)` | Find all `.pdf` hrefs; unwrap members-only-resource params; dedupe |
| `download_pdf(session, url, out_dir)` | Stream download; verify Content-Type |
| `main()` | Fetch `https://aasm.org/clinical-resources/practice-standards/practice-guidelines/`, extract and download |

**CLI:** `--out ./local_guidelines`

---

### `scripts/fetch_site_pdfs.py`

**Purpose:** Generic PDF crawler for guideline websites. BFS within domain/base path up to configurable depth. Downloads all discovered PDFs.

Default target: `https://thrombosiscanada.ca/hcp/practice/clinical_guides`

| Function | Description |
|---|---|
| `is_same_scope(url, base_netloc, base_path)` | Stay within domain + path prefix |
| `fetch_html(session, url, timeout)` | GET HTML |
| `discover_links(session, page_url)` | Return (pdf_links, page_links) |
| `sanitize_name(name)` | Clean filename |
| `choose_filename(url, out_dir)` | Avoid overwrite: add numeric suffix |
| `download_pdf(session, url, out_dir, timeout)` | Stream download PDF |
| `crawl_and_download(base_url, out_dir, max_pages, max_depth)` | Full BFS crawl |
| `main()` | CLI |

**CLI:** `--base URL`, `--out ./local_guidelines`, `--depth 3`, `--max-pages 800`

---

### `scripts/import_dpd.py`

**Purpose:** End-to-end importer for Health Canada Drug Product Database (DPD) tab/CSV files. Orchestrates: ETL (`etl/dpd_etl.py`) → version_manager → chunking_pipeline → embed_chunks → update_index.

| Function | Description |
|---|---|
| `run_py(script, *args)` | Run a Python subprocess; fail-fast on error |
| `main()` | Parse args; orchestrate 5-step pipeline via subprocess calls |

**CLI:** `--dir ./dpd_raw`, `--source-id dpd_ca`, `--dump-date YYYYMMDD`, `--out`, `--no-{register,chunk,embed,update}`

**Note:** Calls `etl/dpd_etl.py` — **this file is NOT present in the RAG directory**; it's expected in an `etl/` subdirectory which doesn't appear in the file listing. Potential missing dependency.

---

### `scripts/import_local_pdfs.py`

**Purpose:** End-to-end importer for local guideline PDF files. Extracts text via PyPDF2/pdfminer, writes cleaned JSON, then orchestrates version_manager → chunking_pipeline → embed_chunks → update_index.

| Function | Description |
|---|---|
| `extract_pdf_text(path)` | Try PyPDF2, fallback pdfminer |
| `file_date(path)` | Get file modification date as ISO string |
| `build_record(path, source_id, society)` | Build cleaned record dict from PDF |
| `write_json_array(objs, out_path)` | Write JSON array |
| `run_py(script, *args)` | Subprocess runner |
| `main()` | Glob PDFs, build records, run pipeline |

**CLI:** `--dir ./local_guidelines`, `--source-id guidelines_local`, `--society`, `--out`, `--no-{register,chunk,embed,update}`

---

### `scripts/import_spl.py`

**Purpose:** Manual importer for FDA Structured Product Labels (SPL drug XML). Calls `etl/spl_etl.py` (not in repo), then version_manager → chunking_pipeline → embed_chunks → update_index.

| Function | Description |
|---|---|
| `run_py(script, *args)` | Subprocess runner |
| `ensure_dir(path)` | Create dirs |
| `main()` | Parse args; run 5-step pipeline |

**CLI:** `--input-jsonl`, `--clean-json`, `--chunk-dir`, `--emb-dir`, `--skip-{register,chunk,embed,update}`

**Note:** Calls `etl/spl_etl.py` — **not present in repo**. Same gap as `import_dpd.py`.

---

### `scripts/print_site_pages_to_pdf.py`

**Purpose:** Playwright-based headless Chromium crawler. Prints guideline web pages to PDF. Useful for JavaScript SPAs where direct PDF download isn't possible. Supports interactive login with storage state persistence.

| Function | Description |
|---|---|
| `sanitize_name(s)` | Clean filename |
| `in_scope(url, base_netloc, base_path)` | Stay within domain/path |
| `extract_links(page, url)` | Get all hrefs via Playwright DOM evaluation |
| `is_candidate_detail(url, base_path)` | Heuristic: guideline detail page (not listing, not admin) |
| `dismiss_disclaimer(page)` | Try to click I Agree / Accept buttons |
| `crawl_and_print(base_url, out_dir, depth, max_pages, storage, interactive)` | Async BFS crawl and PDF print |
| `main()` | Sync CLI wrapper calling `asyncio.run` |

**CLI:** `--base URL`, `--out`, `--depth 2`, `--max-pages 200`, `--storage .playwright_storage.json`, `--interactive`

**Dependencies:** `playwright` (async API, Chromium)

---

### `scripts/process_spl_drugs.py`

**Purpose:** Parses FDA SPL (Structured Product Label) XML corpus. Extracts drug metadata and clinically relevant sections (adverse reactions, interactions, dosing, contraindications, etc.) into normalized JSONL. Does **not** push to RAG index — produces artifacts for review/integration via `import_spl.py`.

| Function | Description |
|---|---|
| `configure_logging(log_file, verbose)` | Set up file + optional stdout logging |
| `iter_xml_files(root)` | Yield all .xml files recursively |
| `clean_text(elem)` | Convert SPL XHTML to readable text (skip media elements) |
| `parse_sections(body)` | Find target LOINC-coded sections; extract text |
| `extract_label(xml_path)` | Parse one XML file into `LabelRecord` |
| `write_jsonl(records, output_path)` | Write `LabelRecord` list as JSONL |
| `build_inventory(root)` | Count file types in directory |
| `main()` | CLI: --input, --output, --log, --limit, --verbose |

**Classes:**
- `Section`: Dataclass `code, name, title, text`
- `LabelRecord`: Dataclass `source_path, set_id, version, effective_time, product_name, ndc_list, manufacturer, sections`

**CLI:** `--input /path/to/spl_xml`, `--output output.jsonl`, `--log process.log`, `--limit N`, `--verbose`

---

## Config Files

### `settings.yaml`

```yaml
persist_directory: "./chroma_store"
corpus_version: 1
embedding_model: "avsolatorio/GIST-small-Embedding-v0"

# Retrieval
dense_top_k: 16
final_top_k: 10
bm25_candidates: 50
hybrid_lambda: 0.10
keyword_overlap_lambda: 0.15
max_chunks_per_doc: 2
summarize_threshold_words: 700
summary_target_words: 220

# Optional reranking
use_rrf: true
rrf_k: 60
use_reranker: false

# Chunking (informational for external scripts)
chunk_size_chars: 1800
chunk_overlap_chars: 200
```

Key runtime settings:
- `corpus_version: 1` — used for cache invalidation in query API
- `use_rrf: true` — RRF reranking is enabled
- Model: `avsolatorio/GIST-small-Embedding-v0` (not Jina v5; Matryoshka not relevant for this model)

### `sources_config.yaml`

Programmatically generated from `sources_config.py`. Contains full domain/keyword/filter config. 16 medical domains, 8 trusted sources, global + per-domain inclusion filters, exclusion keywords.

### `requirements.txt`

Core dependencies:
```
fastapi>=0.115.0, uvicorn[standard]>=0.30.0, pydantic>=2.9.0
chromadb>=1.3.0, sentence-transformers>=3.0.0, rank-bm25>=0.2.2
PyYAML>=6.0.0, requests>=2.32.0, numpy>=2.1.0
schedule>=1.2.0, PyPDF2>=3.0.0, pdfminer.six>=20231228
beautifulsoup4>=4.12.0, lxml>=4.9.0, ftfy>=6.1.0, playwright>=1.44.0
tqdm>=4.66.0, rich>=13.7.1
```

---

## Data Flow Summary

### Weekly Update Cycle (automated)
```
fetch_sources.py → ./raw_docs/*.json
pmc_fetcher.py  → ./raw_docs/pmc_*.json
guidelines_fetcher.py → ./raw_docs/guidelines_*.json
  ↓
process_clinical_corpus.py → ./clean_corpus/*.jsonl (filter, enrich)
  ↓
version_manager.py → ./current_corpus/*.json + version_index.json
  ↓
chunking_pipeline.py → ./chunks/*.chunks.jsonl
  ↓
embed_chunks.py → ./embeddings/{run}/embeddings.npy + ids.jsonl + metadatas.jsonl
  ↓
update_index.py → chroma_store/ (upsert) + prune obsolete + snapshots/chroma/
  ↓
bm25_index.py warm → chroma_store/bm25_index.json
```

### Query Cycle (runtime)
```
HTTP POST /query (query_api.py)
  → check LRU cache
  → Embedder.encode(query)         [embedder.py]
  → col.query(dense n_results=64+) [store.py → chromadb]
  → BM25Helper.scores(query)       [bm25_index.py]
  → hybrid score + recency/authority boost
  → date/keyword post-filter
  → optional RRF rerank
  → dedupe (max 2 chunks/doc)      [utils_meta.py]
  → summarize long chunks
  → _package() → context + refs    [metrics.py]
  → return QueryResponse
```

### Manual Import Cycle
```
fetch_aasm_pdfs.py / fetch_site_pdfs.py / print_site_pages_to_pdf.py
  → ./local_guidelines/*.pdf
  ↓
import_local_pdfs.py
  → extract_pdf_text → clean JSON
  → version_manager.py
  → chunking_pipeline.py
  → embed_chunks.py
  → update_index.py
```

---

## Dead Code / Legacy / Unused Files

| File/Item | Status | Reason |
|---|---|---|
| `chunker.py` | **Legacy** | Replaced by `chunking_pipeline.py`. Reads `.txt` corpus; still used if `guidelines_fetcher.py --emit-txt` is used. |
| `_cosine()` in `query_api.py` | **Dead code** | Defined but never called |
| `_cosine()` in `retriever.py` | **Dead code** | Defined but never called in that file |
| `_extracts` in `composer.py:build_cited_opinion` | **Dead code** | Computed but never used in output |
| `from dataclasses import asdict` in `query_api.py` | **Unused import** | Imported but not used |
| `test.py` | **Stub/Obsolete** | Not a real test. Loads Jina model which is not the configured model. |
| `summarize_recent_updates.py` (second half) | **Duplicate code** | Entire module body appears twice; second block redefines all classes and functions |
| `etl/dpd_etl.py` | **Missing** | Called by `import_dpd.py`; not present in repo |
| `etl/spl_etl.py` | **Missing** | Called by `import_spl.py`; not present in repo |
| `server/services/rag_client.py:get_context()` | **Hardcoded** | `include_keywords` hardcoded for PAP/CPAP — needs parameterization |
| `summarize_recent_updates.py` `RAG_ROOT` | **Windows-only** | Hardcoded `C:\RAG` path |
| `retriever.py` | **Partially superseded** | `query_api.py` does its own inline hybrid retrieval. `retriever.py` is still used by `vector_store_manager.py` CLI search. |

---

## Dependency Map

```
query_api.py
  ├── store.py
  ├── embedder.py
  ├── bm25_index.py
  ├── metrics.py
  └── utils_meta.py

retriever.py
  ├── bm25_index.py
  └── metrics.py

vector_store_manager.py
  ├── store.py
  ├── utils_meta.py
  ├── retriever.py
  ├── embedder.py
  └── bm25_index.py

embed_chunks.py
  ├── embedder.py
  ├── store.py
  └── utils_meta.py

chunker.py
  └── utils_meta.py

chunking_pipeline.py
  └── utils_meta.py

composer.py
  └── metrics.py

fetch_sources.py
  ├── sources_config.py
  ├── utils_meta.py (imported, not substantively used)
  └── log_utils.py

guidelines_fetcher.py
  ├── log_utils.py
  └── sources_config.py (optional)

pmc_fetcher.py
  ├── log_utils.py
  └── sources_config.py (optional)

process_clinical_corpus.py
  └── (no local deps; uses requests + lxml only)

update_index.py
  ├── store.py
  └── vector_store_manager.py

summarize_recent_updates.py
  └── (no local deps; uses requests only)

server/services/rag_client.py
  └── query_api.py (dynamic import)

scripts/import_local_pdfs.py
  → subprocess calls: version_manager.py, chunking_pipeline.py,
    embed_chunks.py, update_index.py

scripts/import_dpd.py
  → subprocess calls: etl/dpd_etl.py (missing), version_manager.py,
    chunking_pipeline.py, embed_chunks.py, update_index.py

scripts/import_spl.py
  → subprocess calls: etl/spl_etl.py (missing), version_manager.py,
    chunking_pipeline.py, embed_chunks.py, update_index.py
```
