#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

ARG APP_BASE_REF=noca/app-base:latest
FROM ${APP_BASE_REF}

LABEL org.opencontainers.image.source="https://github.com/dclobato/noca"

RUN --mount=type=cache,target=/tmp/uv-cache \
    uv sync --frozen --no-dev --package noca-rating --no-install-workspace

COPY shared /app/shared
COPY rating /app/rating
COPY migrations /app/migrations
COPY scripts/wait_for_migrations.py /app/scripts/wait_for_migrations.py
COPY containers/rating/entrypoint.sh /app/entrypoint.sh

RUN chmod +x /app/entrypoint.sh

RUN --mount=type=cache,target=/tmp/uv-cache \
    uv sync --frozen --no-dev --package noca-rating

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["noca-rating"]
