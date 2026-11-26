FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    GATEWAY_LOG_FORMAT=json \
    GATEWAY_DATABASE_URL=sqlite+aiosqlite:////data/gateway.db

WORKDIR /app

# Dependencies and source first, so these layers are cached until the code changes.
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir ".[postgres]"

COPY alembic.ini ./
COPY alembic ./alembic
COPY docker-entrypoint.sh ./
RUN chmod +x docker-entrypoint.sh \
    && useradd --create-home app \
    && mkdir /data \
    && chown app /data

USER app
EXPOSE 8000
VOLUME /data

HEALTHCHECK --interval=30s --timeout=3s --start-period=15s \
  CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('PORT', '8000'))"]

ENTRYPOINT ["./docker-entrypoint.sh"]
