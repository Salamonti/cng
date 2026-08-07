#!/usr/bin/env bash
# ops/deploy.sh -- codifies the same sequence used by hand all session:
# run both test suites as a gate, only touch running services if they pass,
# bump the service-worker cache name so clients actually pick up the new
# static assets, restart, then confirm with the health check.
#
# STEP 7 (H-10) hardening in this script:
#   1. STAGED DEPENDENCY SYNC + SNAPSHOT-DIFF GATE. The prod venv is never
#      mutated blind. A stable dependency snapshot is recorded, the intended
#      requirements resolution is diffed against it, and the operator sees
#      exactly what would change. A `--dry-run` (DEPLOY_DRY_RUN=1) mode
#      resolves the plan WITHOUT touching the prod venv (pip --dry-run).
#   2. POST-DEPLOY HEALTH GATE WITH AUTOMATIC ROLLBACK. If the health check
#      fails after restart, the venv is restored to the pre-deploy snapshot
#      (rollback.freeze), services are restarted, and health re-verified.
#
# This does NOT git pull. Production is the source of truth for this repo
# right now (commits land directly on this box) -- there is no separate
# build step that hands this script something newer than what's already
# on disk. Run this after you've made/committed your change, not instead
# of committing it.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO}"

# Optional test hooks: DEPLOY_DRY_RUN=1 -> resolve deps without mutating prod;
# DEPLOY_FORCE_HEALTH_FAIL=1 -> simulate a health-check failure to exercise rollback.
DRY_RUN="${DEPLOY_DRY_RUN:-0}"
FORCE_HEALTH_FAIL="${DEPLOY_FORCE_HEALTH_FAIL:-0}"
SNAP_DIR="${REPO}/ops/.deploy-snapshots"
mkdir -p "${SNAP_DIR}"

log() { echo "[$(date '+%H:%M:%S')] $*"; }

CNG="Clinical-Note-Generator"
VENV_PIP="${CNG}/.venv/bin/pip"

# PYTHONPATH must be cleared for every venv introspection/install call: the
# invoking shell (an agent's sandbox) can leak a PYTHONPATH that injects
# foreign dist-info (2026-08: Hermes' sandbox made `pip freeze` capture a
# phony `hermes-agent==0.20.0`, which then broke the venv rollback's resolver
# with a rich==15.0.0 conflict on restore). The snapshot must reflect only
# the real venv, and both the sync and the rollback must resolve only it.
freeze_prod() { (cd "${CNG}" && PYTHONPATH= .venv/bin/pip freeze --exclude-editable) ; }

# ---------------------------------------------------------------------------
# 1) STAGED DEPENDENCY SYNC + SNAPSHOT-DIFF GATE
# ---------------------------------------------------------------------------
KNOWN_GOOD="${SNAP_DIR}/known-good.freeze"
if [[ ! -f "${KNOWN_GOOD}" ]]; then
  freeze_prod > "${KNOWN_GOOD}"
  log "Recorded initial known-good dependency snapshot."
fi
CUR_SNAP="${SNAP_DIR}/rollback.freeze"
freeze_prod > "${CUR_SNAP}"
log "Snapshotted current venv state -> rollback.freeze ($(wc -l < "${CUR_SNAP}") pkgs)"

if [[ "${DRY_RUN}" == "1" ]]; then
  log "DRY-RUN: resolving dependency plan without touching the prod venv..."
  report="$(mktemp --suffix=.json)"
  if ! (cd "${CNG}" && .venv/bin/pip install --dry-run --report "${report}" -r requirements.txt >/dev/null 2>&1); then
    log "DRY-RUN: pip resolution failed (no prod change)."
    rm -f "${report}"
    exit 1
  fi
  # Extract name==version for every package the dry-run would install.
  "${CNG}/.venv/bin/python" - "${report}" "${CUR_SNAP}" <<'PY'
import json, sys
report_file, cur_file = sys.argv[1], sys.argv[2]
with open(report_file) as f:
    plan = json.load(f)
planned = {}
for op in plan.get("install", []):
    md = op.get("metadata", {})
    if md.get("name"):
        planned[md["name"].lower()] = f"{md['name']}=={md.get('version','?')}"
current = {}
with open(cur_file) as f:
    for line in f:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "==" in line:
            n, _, _ = line.partition("==")
            current[n.strip().lower()] = line
added = [v for k, v in planned.items() if k not in current]
changed = [f"{planned[k]} (was {current[k]})" for k in planned if k in current and current[k] != planned[k]]
print("Dry-run resolved dependency plan vs current prod snapshot:")
if not added and not changed:
    print("  No dependency changes.")
else:
    for a in sorted(added): print("  +", a)
    for c in sorted(changed): print("  ~", c)
PY
  rm -f "${report}"
  log "DRY-RUN complete. Prod venv untouched."
  exit 0
fi

log "Syncing ${CNG} Python dependencies..."
(cd "${CNG}" && PYTHONPATH= .venv/bin/pip install -q -r requirements.txt)
freeze_prod > "${SNAP_DIR}/candidate.freeze"
if ! diff -q "${CUR_SNAP}" "${SNAP_DIR}/candidate.freeze" >/dev/null 2>&1; then
  log "Dependency snapshot changed during this deploy:"
  diff "${CUR_SNAP}" "${SNAP_DIR}/candidate.freeze" | head -60 || true
else
  log "No dependency changes this deploy."
fi

log "Verifying spaCy de-id model is installed..."
(cd "${CNG}" && .venv/bin/python -c "import spacy; spacy.load('en_core_web_sm')" 2>/dev/null) \
  || (cd "${CNG}" && .venv/bin/python -m spacy download en_core_web_sm)

log "Running ${CNG} test suite..."
# Run tests with a clean PYTHONPATH so an ambient PYTHONPATH from the invoking
# shell (e.g. an agent's sandbox) cannot shadow the repo's `tools/` namespace
# package and spuriously fail the deploy gate. (2026-08 incident: Hermes'
# sandbox PYTHONPATH made `test_purge_expired_asr_segments` fail only when
# deployed from that shell.)
(cd "${CNG}" && PYTHONPATH= .venv/bin/python -m pytest server/tests -q)

log "Running RAG test suite..."
(cd RAG && PYTHONPATH= venv/bin/python -m pytest tests -q)

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

# Restart the three prod services. `reset-failed` is required because the
# rollback path restarts a service a second time within seconds of the
# deploy's own restart, which otherwise trips systemd's StartLimitBurst rate
# limiter ("Start request repeated too quickly" / start-limit-hit) and leaves
# the unit failed even though the app itself is fine.
_restart_services() {
  sudo systemctl reset-failed dreamcision-fastapi.service dreamcision-pchost.service dreamcision-rag.service 2>/dev/null || true
  sudo systemctl restart dreamcision-fastapi.service dreamcision-pchost.service dreamcision-rag.service
}

log "Restarting services..."
_restart_services

log "Waiting for services to settle..."
sleep 3

# ---------------------------------------------------------------------------
# 2) POST-DEPLOY HEALTH GATE WITH AUTOMATIC ROLLBACK
# ---------------------------------------------------------------------------
run_health() {
  # Test hook: fail only the FIRST health check (post-deploy), letting the
  # rollback re-check succeed so the full "rollback successful" path is
  # exercised. A state file makes it one-shot rather than failing forever.
  if [[ "${FORCE_HEALTH_FAIL}" == "1" ]]; then
    fail_file="${SNAP_DIR}/force-fail-once"
    if [[ ! -f "${fail_file}" ]]; then
      touch "${fail_file}"
      echo "INJECTED health failure" >&2
      return 1
    fi
  fi
  python3 ops/health_check.py
}

if run_health; then
  log "Health check passed."
  # Promote rollback.freeze to the new known-good snapshot.
  cp "${SNAP_DIR}/candidate.freeze" "${KNOWN_GOOD}"
  log "Deploy complete."
  exit 0
fi

log "HEALTH CHECK FAILED after deploy. Rolling back venv to pre-deploy snapshot..."
if ! (cd "${CNG}" && PYTHONPATH= .venv/bin/pip install -q -r "${SNAP_DIR}/rollback.freeze"); then
  log "ROLLBACK venv restore FAILED. Manual intervention required."
  exit 1
fi
log "Venv restored. Restarting services and re-checking health..."
_restart_services
sleep 3
if run_health; then
  log "Rollback successful: services healthy on pre-deploy dependencies."
  log "Deploy aborted (rolled back)."
  exit 0
fi
log "ROLLBACK health re-check FAILED. Manual intervention required."
exit 1
