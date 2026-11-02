#!/usr/bin/env sh
# Writes <root>/.env for test or prod (make env-create): .env.example from that checkout, with
# every key in infra/env/<env>.env replaced, plus a fresh database password and Inngest keys.
# Never overwrites an existing .env.
# Usage: env-create.sh <test|prod> <root>
set -eu
ENV_NAME="${1:?Usage: env-create.sh <test|prod> <root>}"
ROOT="${2:?Usage: env-create.sh <test|prod> <root>}"
HERE="$(cd "$(dirname "$0")/../.." && pwd)"
OVERRIDES="$HERE/infra/env/$ENV_NAME.env"
OUT="$ROOT/.env"
[ -f "$OVERRIDES" ] || { echo "No $OVERRIDES" >&2; exit 1; }
if [ -f "$OUT" ]; then echo "$OUT already exists; left as it is."; exit 0; fi

PG_PASSWORD="$(openssl rand -hex 16)"
# Inngest wants an even-length hex signing key; the event key can be any string.
SIGNING_KEY="$(openssl rand -hex 32)"
EVENT_KEY="$(openssl rand -hex 16)"

TMP="$(mktemp)"
{
  grep -v '^[[:space:]]*#' "$OVERRIDES" | grep '='
  echo "POSTGRES_PASSWORD=$PG_PASSWORD"
  echo "DATABASE_URL=postgresql+psycopg://jap:$PG_PASSWORD@db:5432/jap"
  echo "INNGEST_SIGNING_KEY=$SIGNING_KEY"
  echo "INNGEST_EVENT_KEY=$EVENT_KEY"
} > "$TMP"

# Replace each key in place where .env.example has it; append the ones it doesn't.
awk -F= 'NR == FNR { v[$1] = $0; next }
  /^[A-Z_][A-Z0-9_]*=/ && ($1 in v) { print v[$1]; done[$1] = 1; next }
  { print }
  END { for (k in v) if (!(k in done)) print v[k] }' "$TMP" "$ROOT/.env.example" > "$OUT"
rm -f "$TMP"
chmod 600 "$OUT"
echo "Wrote $OUT. Add the OpenRouter key$( [ "$ENV_NAME" = prod ] && echo ' and the mailbox password' ) to it."
