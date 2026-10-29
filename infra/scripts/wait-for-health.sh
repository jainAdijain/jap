#!/usr/bin/env sh
# Waits until GET /api/health answers, then prints each check.
# Usage: wait-for-health.sh [url] [timeout_seconds]
set -eu
URL="${1:-http://127.0.0.1:8000/api/health}"
TIMEOUT="${2:-180}"

printf 'Waiting for %s ' "$URL"
i=0
until body=$(curl -fsS "$URL" 2>/dev/null); do
  i=$((i + 2))
  if [ "$i" -ge "$TIMEOUT" ]; then
    echo; echo "API did not answer within ${TIMEOUT}s. Try: make logs" >&2
    exit 1
  fi
  printf '.'; sleep 2
done
echo " up"

echo "$body" | python3 -c '
import json, sys
h = json.load(sys.stdin)
for name, c in h["checks"].items():
    state = "OK " if c["ok"] else "NOT"
    print("  %-9s %s  %s" % (name, state, c["detail"]))
'
