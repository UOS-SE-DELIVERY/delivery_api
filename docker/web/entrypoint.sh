#!/usr/bin/env bash
set -e

cd /app

python manage.py makemigrations --noinput || true
python manage.py migrate --noinput

SEED_FILE="${SEED_FILE:-apps/catalog/catalog_seed.json}"
LOAD_SEED="${LOAD_SEED:-1}"

if [ "$LOAD_SEED" = "1" ] && [ -f "$SEED_FILE" ]; then
    python manage.py loaddata "$SEED_FILE" || echo "[seed] loaddata 실패"
fi

python manage.py runserver 0.0.0.0:8000