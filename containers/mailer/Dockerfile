#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

ARG APP_BASE_REF=noca/app-base:latest
FROM ${APP_BASE_REF}

LABEL org.opencontainers.image.source="https://github.com/dclobato/noca"

RUN --mount=type=cache,target=/tmp/uv-cache \
    uv sync --frozen --no-dev --package noca-mailer --no-install-workspace

COPY shared /app/shared
COPY mailer /app/mailer
COPY migrations /app/migrations
COPY scripts/__init__.py /app/scripts/__init__.py
COPY scripts/wait_for_migrations.py /app/scripts/wait_for_migrations.py
COPY containers/mailer/entrypoint.sh /app/entrypoint.sh

RUN chmod +x /app/entrypoint.sh

RUN --mount=type=cache,target=/tmp/uv-cache \
    uv sync --frozen --no-dev --package noca-mailer

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["noca-mailer"]
