#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

ARG APP_BASE_REF=noca/app-base:latest
FROM ${APP_BASE_REF}

RUN mkdir -p /data/problem_test_cases

RUN --mount=type=cache,target=/tmp/uv-cache \
    uv sync --frozen --no-dev --package noca-autojudge --no-install-workspace

COPY shared /app/shared
COPY autojudge /app/autojudge
COPY migrations /app/migrations
COPY scripts/run_migrations.py /app/scripts/run_migrations.py
COPY containers/autojudge/entrypoint.sh /app/entrypoint.sh

RUN chmod +x /app/entrypoint.sh

RUN --mount=type=cache,target=/tmp/uv-cache \
    uv sync --frozen --no-dev --package noca-autojudge

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["python", "-m", "autojudge.worker"]
