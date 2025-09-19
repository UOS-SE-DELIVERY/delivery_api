#!/usr/bin/env bash
set -e
python - <<'PY'
import os, time
import psycopg
dsn = f"dbname={os.environ.get('POSTGRES_DB','mrdinner')} user={os.environ.get('POSTGRES_USER','mrdinner')} password={os.environ.get('POSTGRES_PASSWORD','mrdinner')} host={os.environ.get('POSTGRES_HOST','db')} port={os.environ.get('POSTGRES_PORT','5432')}"
for _ in range(60):
    try:
        with psycopg.connect(dsn, connect_timeout=3) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                break
    except Exception:
        time.sleep(1)
else:
    raise SystemExit("DB not ready")
PY
cd /app
python manage.py makemigrations --noinput || true
python manage.py migrate --noinput
python manage.py runserver 0.0.0.0:8000
