FROM postgres:18-bookworm

HEALTHCHECK \
  --interval=5s \
  --timeout=5s \
  --retries=5 \
  CMD pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB" || exit 1