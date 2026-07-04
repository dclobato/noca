#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

# noca/app-base
#
# Internal build artifact — NOT a runtime contract image.
# Shared base for webapp, arena, autojudge, rating, and aiassistant service images.
# Holds only package-independent layers; the per-service uv sync
# and source COPY steps remain in each service Dockerfile.
#
# Built with repo root as context.

FROM python:3.14-slim-bookworm

LABEL org.opencontainers.image.source="https://github.com/dclobato/noca"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_CACHE_DIR=/tmp/uv-cache \
    UV_HTTP_TIMEOUT=180 \
    PYTHONPATH=/app \
    PATH="/app/.venv/bin:${PATH}" \
    PUID=1000 \
    PGID=100

RUN apt-get update \
    && apt-get install -y --no-install-recommends bash ca-certificates curl git util-linux \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

WORKDIR /app

COPY pyproject.toml uv.lock alembic.ini /app/

RUN mkdir -p /tmp/uv-cache /app/shared /app/web /app/arena /app/autojudge /app/rating /app/aiassistant /app/scripts

COPY shared/pyproject.toml /app/shared/pyproject.toml
COPY web/pyproject.toml /app/web/pyproject.toml
COPY arena/pyproject.toml /app/arena/pyproject.toml
COPY autojudge/pyproject.toml /app/autojudge/pyproject.toml
COPY rating/pyproject.toml /app/rating/pyproject.toml
COPY aiassistant/pyproject.toml /app/aiassistant/pyproject.toml
