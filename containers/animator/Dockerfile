#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

ARG ASSETS_BASE_REF=noca/assets-base:latest
ARG ASSETS_PLATFORM=linux/amd64
ARG APP_BASE_REF=noca/app-base:latest

FROM --platform=${ASSETS_PLATFORM} ${ASSETS_BASE_REF} AS assets

FROM ${APP_BASE_REF}

LABEL org.opencontainers.image.source="https://github.com/dclobato/noca"

RUN --mount=type=cache,target=/tmp/uv-cache \
    uv sync --frozen --no-dev --package noca-animator --no-install-workspace

COPY shared /app/shared
COPY animator /app/animator
COPY migrations /app/migrations
COPY scripts/wait_for_migrations.py /app/scripts/wait_for_migrations.py
COPY containers/animator/entrypoint.sh /app/entrypoint.sh

RUN chmod +x /app/entrypoint.sh

RUN --mount=type=cache,target=/tmp/uv-cache \
    uv sync --frozen --no-dev --package noca-animator

COPY --from=assets /app/shared/static/vendor /app/shared/static/vendor
COPY --from=assets /app/shared/static/webfonts /app/shared/static/webfonts

# Documentary only: EXPOSE cannot follow a runtime env var. When NOCA_ANIMATOR_PORT is
# overridden, the container still listens on that port and Caddy follows it.
EXPOSE 8003

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["noca-animator"]
