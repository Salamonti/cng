#!/usr/bin/env bash
set -euo pipefail

# Usage: ./scripts/searx_search.sh "query" [limit]
QUERY="${1:-}"
LIMIT="${2:-5}"

if [[ -z "$QUERY" ]]; then
  echo "Usage: $0 \"query\" [limit]" >&2
  exit 1
fi

python3 "$(dirname "$0")/searx_search.py" "$QUERY" "$LIMIT"
