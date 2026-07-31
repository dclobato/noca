#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
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
    uv sync --frozen --no-dev --package noca-healthmonitor --no-install-workspace

COPY shared /app/shared
COPY healthmonitor /app/healthmonitor
COPY containers/healthmonitor/entrypoint.sh /app/entrypoint.sh

RUN chmod +x /app/entrypoint.sh

RUN --mount=type=cache,target=/tmp/uv-cache \
    uv sync --frozen --no-dev --package noca-healthmonitor

COPY --from=assets /app/shared/static/vendor   /app/shared/static/vendor
COPY --from=assets /app/shared/static/webfonts /app/shared/static/webfonts

# Documentary only: EXPOSE cannot follow a runtime env var. When NOCA_HEALTHMON_PORT is
# overridden, the container still listens on that port and Caddy follows it.
EXPOSE 8002

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["noca-healthmonitor"]
