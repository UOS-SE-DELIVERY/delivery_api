#!/usr/bin/env bash
set -e

# DB 대기
until python - <<'PY'
import os, psycopg2, time
dsn = f"dbname={os.environ['POSTGRES_DB']} user={os.environ['POSTGRES_USER']} password={os.environ['POSTGRES_PASSWORD']} host=db"
for _ in range(30):
    try: psycopg2.connect(dsn).close(); break
    except Exception: time.sleep(1)
else: raise SystemExit("DB not ready")
PY
do sleep 1; done

# 스키마 적용(원본은 migrations)
python manage.py migrate --noinput

# (옵션) 시드 로딩
# python manage.py loaddata apps/catalog/fixtures/serving_styles.json
# python manage.py loaddata apps/catalog/fixtures/dinner_types.json

# 앱 실행