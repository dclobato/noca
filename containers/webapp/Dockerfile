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

RUN mkdir -p /data/problem_statements /data/problem_test_cases

RUN --mount=type=cache,target=/tmp/uv-cache \
    uv sync --frozen --no-dev --package noca-web --no-install-workspace

COPY shared /app/shared
COPY web /app/web
COPY migrations /app/migrations
COPY scripts/__init__.py /app/scripts/__init__.py
COPY scripts/backfill_web_tc_sizes.py /app/scripts/backfill_web_tc_sizes.py
COPY scripts/backfill_login_geolocation.py /app/scripts/backfill_login_geolocation.py
COPY scripts/migrate_arena_tc_to_fs.py /app/scripts/migrate_arena_tc_to_fs.py
COPY scripts/web/__init__.py /app/scripts/web/__init__.py
COPY scripts/web/create_uberadmin.py /app/scripts/web/create_uberadmin.py
COPY scripts/web/backfill_job_contest_id.py /app/scripts/web/backfill_job_contest_id.py
COPY scripts/web/cleanup_problem_files.py /app/scripts/web/cleanup_problem_files.py
COPY scripts/web/valkey_queue_status.py /app/scripts/web/valkey_queue_status.py
COPY scripts/bootstrap_languages.py /app/scripts/bootstrap_languages.py
COPY scripts/run_migrations.py /app/scripts/run_migrations.py
COPY scripts/secrets_config.py /app/scripts/secrets_config.py
COPY containers/webapp/entrypoint.sh /app/entrypoint.sh

RUN chmod +x /app/entrypoint.sh

RUN --mount=type=cache,target=/tmp/uv-cache \
    uv sync --frozen --no-dev --package noca-web

COPY --from=assets /app/shared/static/vendor   /app/shared/static/vendor
COPY --from=assets /app/shared/static/webfonts /app/shared/static/webfonts

# Documentary only: EXPOSE cannot follow a runtime env var. When NOCA_WEB_PORT is
# overridden, the container still listens on that port and Caddy follows it.
EXPOSE 8000

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["python", "-m", "web.main"]
