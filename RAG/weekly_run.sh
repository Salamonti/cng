#!/bin/bash
# weekly_run.sh - Weekly RAG pipeline update
# Run by systemd timer on userver
# Updates guidelines, chunks, embeds, and indexes to ChromaDB
#
# Phase 5: Weekly Pipeline Automation
# - All specialty sources (30+)
# - Cross-specialty PubMed fallback
# - Metadata enrichment on new content
# - DOI deduplication
# - BM25 rebuild
# - Verification + specialty/source report

set -euo pipefail

# Configuration
RAG_DIR="/opt/dreamcision/RAG"
LOG_FILE="/opt/dreamcision/RAG/logs/weekly_run_$(date +%Y%m%d_%H%M%S).log"
VENV_DIR="${RAG_DIR}/.venv"

# Create logs directory
mkdir -p "${RAG_DIR}/logs"

# Log function
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "${LOG_FILE}"
}

log "=== Weekly RAG Pipeline Start ==="
log "Working directory: ${RAG_DIR}"

# Activate virtual environment
source "${VENV_DIR}/bin/activate"
log "Virtual environment activated"

# Change to RAG directory
cd "${RAG_DIR}"

# Capture initial chunk count for reporting
INITIAL_COUNT=$(python3 -c "
import chromadb
from chromadb.config import Settings
client = chromadb.PersistentClient(path='./chroma_store', settings=Settings(anonymized_telemetry=False))
col = client.get_or_create_collection(name='medical_rag')
print(col.count())
")
log "Initial ChromaDB chunk count: ${INITIAL_COUNT}"

# ============================================
# Step 1: Fetch guidelines from direct sources
# ============================================
log "Step 1: Fetching guidelines from all direct sources..."
python3 guideline_sources.py --source all --output ./raw_docs/guidelines_all.jsonl 2>&1 | tee -a "${LOG_FILE}"
log "Direct source guidelines fetched"

# ============================================
# Step 2: Fetch GIN guidelines
# ============================================
log "Step 2: Fetching GIN guidelines..."
python3 fetch_gin_guidelines.py --language English --status Published --output ./raw_docs/guidelines_gin.jsonl 2>&1 | tee -a "${LOG_FILE}"
log "GIN guidelines fetched"

# ============================================
# Step 3: Fetch cross-specialty guidelines via PubMed
# ============================================
log "Step 3: Fetching cross-specialty guidelines from PubMed..."
python3 fetch_cross_specialty_guidelines.py --specialty all --output ./raw_docs/cross_specialty.jsonl 2>&1 | tee -a "${LOG_FILE}"
log "Cross-specialty guidelines fetched"

# ============================================
# Step 4: Fetch full text for guidelines
# ============================================
log "Step 4: Fetching full text for guidelines..."
python3 fetch_full_text.py --input ./raw_docs/guidelines_all.jsonl --output ./raw_docs/guidelines_all_fulltext.jsonl 2>&1 | tee -a "${LOG_FILE}"
log "Full text fetched"

# ============================================
# Step 5: Run guideline pipeline (merge, chunk, embed, index)
# ============================================
log "Step 5: Running guideline pipeline..."
python3 guideline_pipeline.py --merge --chunk --embed 2>&1 | tee -a "${LOG_FILE}"
log "Guideline pipeline completed"

# ============================================
# Step 6: Metadata enrichment on all chunks
# ============================================
log "Step 6: Running metadata enrichment..."
python3 metadata_enricher.py --enrich 2>&1 | tee -a "${LOG_FILE}"
log "Metadata enrichment completed"

# ============================================
# Step 7: DOI deduplication
# ============================================
log "Step 7: Running DOI deduplication..."
python3 -c "
import sys
sys.path.insert(0, '.')
import chromadb
from chromadb.config import Settings
from collections import defaultdict

client = chromadb.PersistentClient(path='./chroma_store', settings=Settings(anonymized_telemetry=False))
col = client.get_or_create_collection(name='medical_rag')

# Get all chunks with metadata
total = col.count()
batch_size = 1000
deleted = 0

# Group by (doc_id, DOI) -- NOT DOI alone. Two unrelated documents can
# legitimately share a DOI-looking value; scoping by doc_id too means we
# only collapse duplicate chunks within the same source document, not
# across different ones. Matches doi_dedup.py's standalone logic.
doi_groups = defaultdict(list)
for i in range(0, total, batch_size):
    res = col.get(limit=batch_size, offset=i, include=['metadatas', 'documents'])
    for j, meta in enumerate(res['metadatas']):
        if meta and 'doi' in meta and meta['doi']:
            doi = meta['doi'].strip()
            if doi and doi != 'N/A':
                doc_id = meta.get('doc_id', res['ids'][j].split('_chunk_')[0] if '_chunk_' in res['ids'][j] else 'unknown')
                doi_groups[(doc_id, doi)].append(res['ids'][j])

# For each (doc_id, DOI) group with duplicates, delete all but the first
for (doc_id, doi), ids in doi_groups.items():
    if len(ids) > 1:
        to_delete = ids[1:]
        col.delete(ids=to_delete)
        deleted += len(to_delete)
        print(f'  doc_id {doc_id} DOI {doi}: kept 1, removed {len(to_delete)} duplicates')

final = col.count()
print(f'Deduplication complete: removed {deleted} duplicates, {final} chunks remaining')
" 2>&1 | tee -a "${LOG_FILE}"
log "DOI deduplication completed"

# ============================================
# Step 8: Rebuild BM25 index
# ============================================
log "Step 8: Rebuilding BM25 index..."
python3 -c "
import sys
sys.path.insert(0, '.')
from bm25_index import warm_bm25
from store import get_client, get_collection

client = get_client('./chroma_store')
col = get_collection(client)
print(f'Collection size: {col.count()}')

helper, ids = warm_bm25(col)
print(f'BM25 index rebuilt with {len(ids)} documents')
" 2>&1 | tee -a "${LOG_FILE}"
log "BM25 index rebuilt"

# ============================================
# Step 9: Verify the index
# ============================================
log "Step 9: Verifying the index..."
python3 -c "
from sentence_transformers import SentenceTransformer
import chromadb
from chromadb.config import Settings

model = SentenceTransformer('microsoft/harrier-oss-v1-0.6b')
qvec = model.encode(['pulmonary embolism'])[0]

client = chromadb.PersistentClient(path='./chroma_store', settings=Settings(anonymized_telemetry=False))
col = client.get_or_create_collection(name='medical_rag')

results = col.query(
    query_embeddings=[qvec.tolist()],
    n_results=3,
    include=['documents', 'metadatas']
)

print(f'Verification query returned {len(results[\"ids\"][0])} results')
for i, (id_, meta) in enumerate(zip(results['ids'][0], results['metadatas'][0])):
    print(f'  {i+1}. {id_[:50]}')
    if meta:
        source = meta.get('source', 'N/A')
        print(f'     Source: {source}')
" 2>&1 | tee -a "${LOG_FILE}"
log "Index verified"

# ============================================
python3 -c "
import sys
sys.path.insert(0, '.')
import chromadb
from chromadb.config import Settings
from collections import defaultdict, Counter

client = chromadb.PersistentClient(path='./chroma_store', settings=Settings(anonymized_telemetry=False))
col = client.get_or_create_collection(name='medical_rag')

total = col.count()
specialty_counts = Counter()
source_counts = Counter()
grade_counts = Counter()
year_counts = Counter()

batch_size = 1000
for i in range(0, total, batch_size):
    res = col.get(offset=i, limit=batch_size, include=['metadatas'])
    for meta in res['metadatas']:
        if not meta:
            continue
        specialty_counts[meta.get('specialty', 'unknown')] += 1
        source_counts[meta.get('source', 'unknown')] += 1
        grade_counts[meta.get('grade', 'N/A')] += 1
        year = meta.get('year', 'N/A')
        if year and year != 'N/A':
            year_counts[str(year)] += 1

print(f'\n=== Weekly Pipeline Report ===')
print(f'Total chunks: {total}')
print(f'\n--- By Specialty ---')
for spec, cnt in specialty_counts.most_common():
    print(f'  {spec}: {cnt}')

print(f'\n--- By Source (top 20) ---')
for src, cnt in source_counts.most_common(20):
    print(f'  {src}: {cnt}')

print(f'\n--- By GRADE ---')
for g, cnt in grade_counts.most_common():
    print(f'  {g}: {cnt}')

print(f'\n--- By Year (top 10) ---')
for y, cnt in year_counts.most_common(10):
    print(f'  {y}: {cnt}')
" 2>&1 | tee -a "${LOG_FILE}"
log "Report generated"

log "=== Weekly RAG Pipeline Complete ==="
log "Log file: ${LOG_FILE}"