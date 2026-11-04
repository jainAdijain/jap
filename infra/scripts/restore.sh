#!/usr/bin/env sh
# Restores a backup made by backup.sh: infra/scripts/restore.sh data/backups/<file>.sql.gz
set -eu
COMPOSE="${COMPOSE:-docker compose -f infra/compose.yaml}"
FILE="${1:?Usage: restore.sh data/backups/<file>.sql.gz}"
printf 'This replaces the current database with %s. Type yes to continue: ' "$FILE"
read -r answer
[ "$answer" = "yes" ] || { echo "Cancelled."; exit 1; }
gunzip -c "$FILE" | $COMPOSE exec -T db sh -c 'psql -q -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
echo "Restored $FILE"
