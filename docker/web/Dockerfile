# FROM python:3.12-slim

# WORKDIR /app

# COPY ./requirements.txt /app/requirements.txt

# RUN pip install --no-cache-dir -r /app/requirements.txt

# COPY . /app

# # RUN python3 manage.py makemigrations && python3 manage.py migrate

# EXPOSE 8000

# Dockerfile

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Postgres 드라이버 준비
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# 앱 코드
COPY . /app

CMD ["bash", "/app/entrypoint.sh"]

