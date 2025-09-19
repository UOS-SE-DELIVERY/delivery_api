#!usr/bin/env bash
set -euo pipefail
DB="${POSTGRES_DB:-mrdinner}"
USER="${POSTGRES_USER:-mrdinner}"
HOST="${POSTGRES_HOST:-127.0.0.1}"
OUT="docs/sql/schema.sql"

pg_dump -s -h "$HOST" -U "$USER" "$DB" > "$OUT"
echo "wrote $OUT"
