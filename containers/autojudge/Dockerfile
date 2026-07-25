#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

ARG APP_BASE_REF=noca/app-base:latest
FROM ${APP_BASE_REF}

LABEL org.opencontainers.image.source="https://github.com/dclobato/noca"

RUN mkdir -p /data/problem_test_cases

RUN --mount=type=cache,target=/tmp/uv-cache \
    uv sync --frozen --no-dev --package noca-autojudge --no-install-workspace

COPY shared /app/shared
COPY autojudge /app/autojudge
COPY migrations /app/migrations
COPY scripts/wait_for_migrations.py /app/scripts/wait_for_migrations.py
COPY scripts/autojudge/probe_sandbox_limits.py /app/scripts/autojudge/probe_sandbox_limits.py
COPY scripts/autojudge/smoke_test_judge.py /app/scripts/autojudge/smoke_test_judge.py
COPY containers/autojudge/entrypoint.sh /app/entrypoint.sh

RUN chmod +x /app/entrypoint.sh

RUN --mount=type=cache,target=/tmp/uv-cache \
    uv sync --frozen --no-dev --package noca-autojudge

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["python", "-m", "autojudge.worker"]
