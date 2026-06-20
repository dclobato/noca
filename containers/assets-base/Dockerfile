#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

# noca/assets-base
#
# Internal build artifact — NOT a runtime contract image.
# Downloads all shared frontend vendor assets (vendor/ + webfonts/) once per
# platform build; webapp and arena import them via COPY --from=assets.
#
# Built with repo root as context.

ARG APP_BASE_REF=noca/app-base:latest
FROM ${APP_BASE_REF}

RUN --mount=type=cache,target=/tmp/uv-cache \
    uv sync --frozen --no-dev --package noca-shared --no-install-workspace

COPY shared /app/shared
COPY scripts/__init__.py /app/scripts/__init__.py
COPY scripts/fetch_assets.py /app/scripts/fetch_assets.py
COPY scripts/asset_manifest.py /app/scripts/asset_manifest.py

RUN --mount=type=cache,target=/tmp/uv-cache \
    uv sync --frozen --no-dev --package noca-shared

RUN /app/.venv/bin/python scripts/fetch_assets.py
