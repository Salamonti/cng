#!/bin/bash
# DreamCision RAG Weekly Guideline Update Script
# Runs weekly to fetch new guidelines, merge, chunk, embed, and index
#
# DEPRECATED (2026-07-14): This is a thin wrapper that only runs guideline_pipeline.py.
# The canonical weekly orchestration script is weekly_run.sh, which includes:
#   - All specialty sources (30+) + GIN + PubMed cross-specialty
#   - Full text fetching, metadata enrichment, DOI deduplication
#   - BM25 rebuild, index verification, specialty/source report
# Keep this file for backward compatibility; do not delete.

set -euo pipefail

cd /opt/dreamcision/RAG
source .venv/bin/activate

echo "=== DreamCision Weekly Update ==="
echo "Started at: $(date)"

# Run the guideline pipeline
python3 guideline_pipeline.py --merge --chunk --embed --index

echo "=== Weekly Update Complete ==="
echo "Finished at: $(date)"
