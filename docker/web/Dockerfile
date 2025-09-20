FROM python:3.12-slim

WORKDIR /app

COPY ./requirements.txt /app/requirements.txt

RUN pip install --no-cache-dir -r /app/requirements.txt

COPY . /app

# RUN python3 manage.py makemigrations && python3 manage.py migrate

EXPOSE 8000
