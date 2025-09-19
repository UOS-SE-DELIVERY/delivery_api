# Refactored Django Layout

```
  .env.example
  apps/
  config/
    config/__init__.py
    config/asgi.py
    config/settings.py
    config/urls.py
    config/wsgi.py
  docker/
    docker/db/
      docker/db/initdb/
        docker/db/initdb/00_extensions.sql
    docker/docker-compose.yml
    docker/web/
      docker/web/Dockerfile
      docker/web/entrypoint.sh
  manage.py
  requirements.txt
```

**How to run**

```
cd docker
cp ../.env.example ../.env
docker compose up -d --build
```

If you had custom settings/apps, update `config/settings.py` accordingly.
