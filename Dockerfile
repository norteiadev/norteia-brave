# Backend image (dev) — FastAPI API + Celery worker/beat share this image.
# uv-managed venv baked at /app/.venv; docker-compose bind-mounts the live source over
# /app and keeps /app/.venv in a named volume (seeded from this build) so host and
# container deps never clash. The service command (uvicorn/celery/alembic) is set per
# service in docker-compose.yml.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# build tools for any sdist-only deps; psycopg[binary] bundles libpq (no libpq-dev needed).
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential curl \
 && rm -rf /var/lib/apt/lists/*

# uv (static binary) from the official image.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Deps first (cached layer): install into /app/.venv without the project itself.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project

# Project source (shadowed at runtime by the compose bind-mount; kept so the image is
# runnable standalone and so the .venv named volume seeds from a complete build).
COPY . .
RUN uv sync --frozen

EXPOSE 8000

# Default command = the API (overridden by worker/beat/migrate services in compose).
CMD ["/app/.venv/bin/uvicorn", "brave.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
