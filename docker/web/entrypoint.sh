#!/usr/bin/env bash
# set -e

# cd /app

# python manage.py makemigrations --noinput || true
# python manage.py migrate --noinput

# SEED_FILE="${SEED_FILE:-apps/catalog/catalog_seed.json}"
# LOAD_SEED="${LOAD_SEED:-1}"

# if [ "$LOAD_SEED" = "1" ] && [ -f "$SEED_FILE" ]; then
#     python manage.py loaddata "$SEED_FILE" || echo "[seed] loaddata 실패"
# fi

# python manage.py migrate --noinput

# python manage.py runserver 0.0.0.0:8000

set -e

cd /app

# 마이그 없는 앱(promotion 등)의 테이블을 즉시 생성 + 나머지는 정상 migrate
python manage.py migrate --noinput --run-syncdb

SEED_FILE="${SEED_FILE:-apps/catalog/catalog_seed.json}"
if [ "${LOAD_SEED:-1}" = "1" ] && [ -f "$SEED_FILE" ]; then
  python manage.py loaddata "$SEED_FILE" || echo "[seed] loaddata 실패"
fi

python manage.py runserver 0.0.0.0:8000
