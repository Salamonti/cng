#!/usr/bin/env bash
# ops/deploy.sh -- codifies the same sequence used by hand all session:
# run both test suites as a gate, only touch running services if they pass,
# bump the service-worker cache name so clients actually pick up the new
# static assets, restart, then confirm with the health check.
#
# This does NOT git pull. Production is the source of truth for this repo
# right now (commits land directly on this box) -- there is no separate
# build step that hands this script something newer than what's already
# on disk. Run this after you've made/committed your change, not instead
# of committing it.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO}"

log() { echo "[$(date '+%H:%M:%S')] $*"; }

# De-id incident (2026-08): spacy>=3.7.0 sat in requirements.txt for an
# unknown amount of time without ever actually being installed -- nothing
# in this script (or anywhere else) ran `pip install -r requirements.txt`
# after it was added, so the NER de-id backstop silently no-oped on 100%
# of records the whole time. Syncing dependencies here, as part of every
# deploy, closes that class of gap for good instead of just this one
# package.
log "Syncing Clinical-Note-Generator Python dependencies..."
(cd Clinical-Note-Generator && .venv/bin/pip install -q -r requirements.txt)

log "Verifying spaCy de-id model is installed..."
(cd Clinical-Note-Generator && .venv/bin/python -c "import spacy; spacy.load('en_core_web_sm')" 2>/dev/null) \
  || (cd Clinical-Note-Generator && .venv/bin/python -m spacy download en_core_web_sm)

log "Running Clinical-Note-Generator test suite..."
(cd Clinical-Note-Generator && .venv/bin/python -m pytest server/tests -q)

log "Running RAG test suite..."
(cd RAG && venv/bin/python -m pytest tests -q)

log "Running PCHost test suite..."
(cd PCHost && npm test --silent)

log "All test suites passed."

SW_FILE="PCHost/web/service_worker.js"
SHA="$(git rev-parse --short HEAD)"
NEW_CACHE_NAME="dreamcision-pwa-${SHA}"
OLD_CACHE_NAME="$(grep -oP "(?<=const CACHE_NAME = ')[^']+" "${SW_FILE}")"
if [[ "${OLD_CACHE_NAME}" != "${NEW_CACHE_NAME}" ]]; then
  sed -i "s/const CACHE_NAME = '${OLD_CACHE_NAME}';/const CACHE_NAME = '${NEW_CACHE_NAME}';/" "${SW_FILE}"
  log "Bumped service worker cache name: ${OLD_CACHE_NAME} -> ${NEW_CACHE_NAME}"
  git add "${SW_FILE}"
  git commit -m "deploy: bump service worker cache to ${SHA}" --quiet
else
  log "Service worker cache name already matches HEAD (${NEW_CACHE_NAME}); nothing to bump."
fi

log "Restarting services..."
sudo systemctl restart dreamcision-fastapi.service dreamcision-pchost.service dreamcision-rag.service

log "Waiting for services to settle..."
sleep 3

log "Running health check..."
python3 ops/health_check.py

log "Deploy complete."
