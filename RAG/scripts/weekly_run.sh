#!/usr/bin/env bash
# weekly_run.sh — full RAG pipeline (Linux). Mirrors scripts/weekly_run.ps1.
set -euo pipefail

DAYS_BACK="${DAYS_BACK:-7}"
PMC_DAYS="${PMC_DAYS:-30}"
PMC_MAX="${PMC_MAX:-2000}"
GUIDE_DAYS="${GUIDE_DAYS:-30}"
GUIDE_LIMIT="${GUIDE_LIMIT:-120}"
PDF_MAX_MB="${PDF_MAX_MB:-8}"
SUM_MAX="${SUM_MAX:-800}"
SKIP_SUMMARIZE="${SKIP_SUMMARIZE:-0}"
STRICT_SUMMARIZE="${STRICT_SUMMARIZE:-0}"
EMBEDDER_DEVICE="${EMBEDDER_DEVICE:-cuda}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO}"

export RAG_ROOT="${REPO}"
export EMBEDDER_DEVICE

PYTHON="${RAG_PYTHON:-${REPO}/venv/bin/python}"
if [[ ! -x "${PYTHON}" ]]; then
  PYTHON="${REPO}/.venv/bin/python"
fi
if [[ ! -x "${PYTHON}" ]]; then
  echo "ERROR: RAG venv not found at ${REPO}/venv or ${REPO}/.venv" >&2
  exit 1
fi

stamp="$(date +%Y%m%d_%H%M%S)"
run_dir="${REPO}/runs/${stamp}"
mkdir -p "${run_dir}" "${REPO}/raw_docs" "${REPO}/clean_corpus" "${REPO}/chunks" "${REPO}/embeddings"

log_step() { echo "[$(date +%H:%M:%S)] $*"; }

run_tool() {
  local name="$1"
  shift
  local log="${run_dir}/${name}.log"
  log_step "Running ${name}"
  echo "  run: ${PYTHON} $*"
  if ! "${PYTHON}" "$@" >"${log}" 2>&1; then
    echo "---- Tail of ${name}.log ----" >&2
    tail -n 50 "${log}" >&2 || true
    echo "Full log: ${log}" >&2
    exit 1
  fi
}

run_tool_optional() {
  local name="$1"
  shift
  local log="${run_dir}/${name}.log"
  log_step "Running ${name} (optional)"
  echo "  run: ${PYTHON} $*"
  if ! "${PYTHON}" "$@" >"${log}" 2>&1; then
    echo "---- WARNING: ${name} failed (optional) ----" >&2
    tail -n 40 "${log}" >&2 || true
    echo "Full log: ${log}" >&2
  fi
}

log_step "Using RAG venv: ${PYTHON}"
log_step "Run logs: ${run_dir}"

# --- Clean up old run directories to prevent unbounded growth ---
# Every invocation creates a new timestamped runs/<stamp>/ directory of
# per-tool logs that nothing else ever cleans up. Keep the trailing
# RUN_RETENTION_DAYS worth (default ~8 weekly runs).
RUN_RETENTION_DAYS="${RUN_RETENTION_DAYS:-60}"
old_run_count=0
while IFS= read -r old_dir; do
  rm -rf "${old_dir}"
  ((old_run_count++)) || true
done < <(find "${REPO}/runs" -mindepth 1 -maxdepth 1 -type d -mtime "+${RUN_RETENTION_DAYS}" 2>/dev/null)
if [[ ${old_run_count} -gt 0 ]]; then
  log_step "Cleaned up ${old_run_count} run dir(s) older than ${RUN_RETENTION_DAYS} days"
fi

run_tool fetch_sources fetch_sources.py --days "${DAYS_BACK}"
run_tool pmc_fetcher pmc_fetcher.py --oa-subset --days "${PMC_DAYS}" --max "${PMC_MAX}"
run_tool guidelines_fetcher guidelines_fetcher.py \
  --days "${GUIDE_DAYS}" \
  --limit-per-source "${GUIDE_LIMIT}" \
  --depth 2 \
  --timeout 45 \
  --fetch-pdf \
  --pdf-max-mb "${PDF_MAX_MB}"

# P3-3: this used to run AFTER the raw_files scan/chunk/embed/index steps
# below (was near the metadata-enrichment block, well past update_index).
# That meant cross_specialty.jsonl -- 207 guidelines/week, confirmed --
# never made it into the SAME run's corpus: the raw_files scan below had
# already completed by the time this wrote fresh output, so it sat until
# the following week's scan, and was only picked up then if this run's
# write time still happened to fall within that later scan's DAYS_BACK
# mtime window -- otherwise silently dropped, not just delayed. Moved
# ahead of the raw_files scan so its output is included in this same run.
run_tool_optional cross_specialty fetch_cross_specialty_guidelines.py --specialty all --output ./raw_docs/cross_specialty.jsonl

# --- Clean up old timestamped raw_docs to prevent duplication ---
# Each weekly run creates new timestamped files (pubmed_YYYYMMDD_HHMMSS.json, etc.).
# Old files from previous weeks accumulate and get re-processed every time,
# multiplying chunk counts by 5-6x. Keep only the latest N per source type.
KEEP_COUNT=1  # number of latest files to keep per pattern
for pattern in guidelines_*.json guidelines_*.processed.jsonl clinicaltrials_*.json pmc_oa_*.json pubmed_*.json openfda_*.json cross_specialty_*.jsonl; do
  latest=$(ls -1t "${REPO}/raw_docs"/${pattern} 2>/dev/null | head -1 || true)
  if [[ -n "${latest}" ]]; then
    old_count=0
    while IFS= read -r old_file; do
      rm -v "${old_file}"
      ((old_count++)) || true
    done < <(ls -1t "${REPO}/raw_docs"/${pattern} 2>/dev/null | tail -n +$((KEEP_COUNT + 1)) || true)
    if [[ ${old_count} -gt 0 ]]; then
      log_step "Cleaned up ${old_count} old snapshot(s) matching ${pattern}"
    fi
  fi
done

raw_dir="${REPO}/raw_docs"
clean_dir="${REPO}/clean_corpus"
mkdir -p "${clean_dir}"

# Find raw docs modified in last DAYS_BACK days.
# EXCLUDE *.processed.jsonl files — those are guideline snapshots from the fetcher
# that should NOT be double-processed by process_clinical_corpus.
mapfile -t raw_files < <(find "${raw_dir}" -maxdepth 1 -type f \( -name '*.json' -o -name '*.jsonl' \) -not -name '*.processed.jsonl' -mtime "-${DAYS_BACK}" 2>/dev/null | sort)
if [[ ${#raw_files[@]} -eq 0 ]]; then
  echo "WARNING: No raw_docs modified in last ${DAYS_BACK} days; processing ALL *.json / *.jsonl (excluding .processed.jsonl snapshots)" >&2
  mapfile -t raw_files < <(find "${raw_dir}" -maxdepth 1 -type f \( -name '*.json' -o -name '*.jsonl' \) -not -name '*.processed.jsonl' 2>/dev/null | sort)
fi

if [[ ${#raw_files[@]} -eq 0 ]]; then
  echo "ERROR: No raw_docs/*.json or *.jsonl to process" >&2
  exit 1
fi

for f in "${raw_files[@]}"; do
  base="$(basename "${f}")"
  stem="${base%.*}"
  out="${clean_dir}/${stem}.processed.jsonl"
  run_tool "process_clinical_corpus_${stem}" process_clinical_corpus.py --in "${f}" --out "${out}" --fulltext
done

run_tool chunking_pipeline chunking_pipeline.py --input ./clean_corpus --pattern '*.processed.jsonl' --output ./chunks
run_tool embed_chunks embed_chunks.py --input ./chunks --output ./embeddings --batch 64
run_tool update_index update_index.py --emb-dir ./embeddings --chunk-dir ./chunks --snapshots both

# Metadata enrichment (specialty, year, DOI, GRADE)
run_tool_optional metadata_enrich metadata_enricher.py --enrich

# DOI deduplication
run_tool_optional doi_dedup doi_dedup.py

# Rebuild BM25 index
run_tool_optional bm25_rebuild rebuild_bm25.py

# Verify the index
run_tool_optional verify_index verify_index.py

# Specialty/source report
run_tool_optional report pipeline_report.py

# Phase 6: Monitoring & Quality
# Dashboard — comprehensive index statistics
run_tool_optional dashboard monitoring_dashboard.py

# Staleness check — flag specialties with outdated guidelines
run_tool_optional staleness_check staleness_check.py

# Test queries — verify RAG returns results across specialties
run_tool_optional test_queries test_queries.py

# Gap detection — web search has results but RAG doesn't
run_tool_optional gap_detection gap_detector.py

if [[ "${SKIP_SUMMARIZE}" == "1" ]]; then
  log_step "Skipping summarize_recent_updates.py (SKIP_SUMMARIZE=1)"
else
  sum_args=(summarize_recent_updates.py --max-docs "${SUM_MAX}")
  if [[ "${STRICT_SUMMARIZE}" == "1" ]]; then
    run_tool summarize_updates "${sum_args[@]}"
  else
    run_tool_optional summarize_updates "${sum_args[@]}"
  fi
fi

# Bump corpus_version so the live query service's result caches (which key
# on it) actually invalidate now that the index has changed -- see
# bump_corpus_version.py for why this was previously a silent no-op.
run_tool bump_corpus_version bump_corpus_version.py

log_step "Weekly run completed. Logs in ${run_dir}"
