FROM python:3.13-slim
LABEL org.opencontainers.image.source=https://github.com/silvansan/vitrine

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# ffmpeg for conversion; rclone and pg_dump 16 (from the PostgreSQL apt repo, to match
# the postgres:16 container) for backups.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg rclone ca-certificates postgresql-common \
    && /usr/share/postgresql-common/pgdg/apt.postgresql.org.sh -y \
    && apt-get install -y --no-install-recommends postgresql-client-16 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chmod +x /app/entrypoint.sh

ENV MEDIA_ROOT=/data/media \
    STATIC_ROOT=/data/static \
    VITRINE_KEY_PATH=/data/keys/pairing_ed25519.pem \
    VITRINE_DOCS_DIR=/app/docs

EXPOSE 8000
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["web"]
