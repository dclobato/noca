#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

# noca/app-base
#
# Internal build artifact — NOT a runtime contract image.
# Shared base for webapp, arena, autojudge, rating, aiassistant, mailer, healthmonitor,
# and animator service images.
# Holds only package-independent layers; the per-service uv sync
# and source COPY steps remain in each service Dockerfile.
#
# Built with repo root as context.
#
# Digest-pinned for reproducible builds (same policy as the judge images).

FROM python:3.14-slim-trixie@sha256:cad9a2c871761c413caa6fdd6441c783451e740a48aaeba60ae62a8b53525ef6

LABEL org.opencontainers.image.source="https://github.com/dclobato/noca"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_CACHE_DIR=/tmp/uv-cache \
    UV_HTTP_TIMEOUT=180 \
    PYTHONPATH=/app \
    PATH="/app/.venv/bin:${PATH}" \
    PUID=1000 \
    PGID=100

RUN apt-get -o Acquire::Retries=3 update \
    && apt-get -o Acquire::Retries=3 install -y --no-install-recommends \
        bash ca-certificates curl git util-linux \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

WORKDIR /app

COPY pyproject.toml uv.lock alembic.ini /app/

RUN mkdir -p /tmp/uv-cache /app/shared /app/web /app/arena /app/autojudge \
    /app/rating /app/aiassistant /app/mailer /app/healthmonitor /app/animator /app/scripts

COPY shared/pyproject.toml /app/shared/pyproject.toml
COPY web/pyproject.toml /app/web/pyproject.toml
COPY arena/pyproject.toml /app/arena/pyproject.toml
COPY autojudge/pyproject.toml /app/autojudge/pyproject.toml
COPY rating/pyproject.toml /app/rating/pyproject.toml
COPY aiassistant/pyproject.toml /app/aiassistant/pyproject.toml
COPY mailer/pyproject.toml /app/mailer/pyproject.toml
COPY healthmonitor/pyproject.toml /app/healthmonitor/pyproject.toml
COPY animator/pyproject.toml /app/animator/pyproject.toml
