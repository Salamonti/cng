#!/usr/bin/env bash
set -euo pipefail

# Usage:
#   ./scripts/searx_search.sh "query" [limit]
#   ./scripts/searx_search.sh --json "query"

JSON_FLAG=""
if [[ "${1:-}" == "--json" ]]; then
  JSON_FLAG="--json"
  shift
fi

QUERY="${1:-}"
LIMIT="${2:-5}"

if [[ -z "$QUERY" ]]; then
  echo "Usage: $0 [--json] \"query\" [limit]" >&2
  exit 1
fi

if [[ -n "$JSON_FLAG" ]]; then
  python3 "$(dirname "$0")/searx_search.py" --json "$QUERY"
else
  python3 "$(dirname "$0")/searx_search.py" "$QUERY" "$LIMIT"
fi
