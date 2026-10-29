#!/usr/bin/env sh
# Dumps the database to data/backups/jap-YYYYmmdd-HHMMSS.sql.gz (make backup).
set -eu
COMPOSE="${COMPOSE:-docker compose -f infra/compose.yaml}"
mkdir -p data/backups
OUT="data/backups/jap-$(date +%Y%m%d-%H%M%S).sql.gz"
$COMPOSE exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists' | gzip > "$OUT"
echo "Backup written to $OUT"
