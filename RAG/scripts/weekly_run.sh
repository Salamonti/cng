# C:\RAG\scripts\weekly_run.sh
#!/usr/bin/env bash
set -euo pipefail

# Linux weekly pipeline for RAG
# Steps: fetch -> process -> chunk -> embed -> update_index
# Args: DAYS_BACK [default 7] PMC_DAYS [default 30] GUIDELINE_DAYS [default 30] PDF_MAX_MB [default 8]

DAYS_BACK=${1:-7}
PMC_DAYS=${2:-30}
GUIDE_DAYS=${3:-30}
PDF_MAX_MB=${4:-8}

cd "$(dirname "$0")/.."
REPO_DIR="$(pwd)"

# Python command (prefer venv if present)
if [ -x "$REPO_DIR/.venv/bin/python" ]; then
  PY="$REPO_DIR/.venv/bin/python"
else
  PY="python3"
fi

STAMP=$(date +%Y%m%d_%H%M%S)
RUN_DIR="$REPO_DIR/runs/$STAMP"
mkdir -p "$RUN_DIR"

log() { echo "[$(date +%H:%M:%S)] $*"; }
run_tool() {
  local name="$1"; shift
  local logf="$RUN_DIR/${name}.log"
  log "Running $name"
  set +e
  "$PY" "$@" >"$logf" 2>&1
  local code=$?
  set -e
  if [ $code -ne 0 ]; then
    echo "Step '$name' failed (exit $code). See $logf" >&2
    exit $code
  fi
}

# 1) Fetch sources
run_tool fetch_sources fetch_sources.py --days "$DAYS_BACK"
# PMC OA subset
run_tool pmc_fetcher pmc_fetcher.py --oa-subset --days "$PMC_DAYS" --max 2000
# Guidelines crawl
run_tool guidelines_fetcher guidelines_fetcher.py --days "$GUIDE_DAYS" --limit-per-source 120 --depth 2 --timeout 45 --fetch-pdf --pdf-max-mb "$PDF_MAX_MB"

# 2) Process clinical corpus (only new files since DAYS_BACK)
RAW_DIR="$REPO_DIR/raw_docs"
CLEAN_DIR="$REPO_DIR/clean_corpus"
mkdir -p "$CLEAN_DIR"

if command -v find >/dev/null 2>&1; then
  while IFS= read -r -d '' f; do
    base=$(basename "$f")
    out="$CLEAN_DIR/${base%.json*}.processed.jsonl"
    run_tool "process_${base%.*}" process_clinical_corpus.py --in "$f" --out "$out" --fulltext
  done < <(find "$RAW_DIR" -type f \( -name '*.json' -o -name '*.jsonl' \) -mtime -"$DAYS_BACK" -print0)
fi

# 3) Chunk
run_tool chunking_pipeline chunking_pipeline.py --input ./clean_corpus --pattern '*.processed.jsonl' --output ./chunks

# 4) Embed
run_tool embed_chunks embed_chunks.py --input ./chunks --output ./embeddings --batch 64

# 5) Update index (ingest + prune + snapshot)
run_tool update_index update_index.py --emb-dir ./embeddings --chunk-dir ./chunks --snapshots both

log "Weekly run completed. Logs in $RUN_DIR"

