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
    uv sync --frozen --no-dev --package noca-arena --no-install-workspace

COPY shared /app/shared
COPY arena /app/arena
COPY docs/legal /app/docs/legal
COPY migrations /app/migrations
COPY scripts/__init__.py                        /app/scripts/__init__.py
COPY scripts/arena/backfill_statement_language.py /app/scripts/arena/backfill_statement_language.py
COPY scripts/arena/categorias-simples-en.txt    /app/scripts/arena/categorias-simples-en.txt
COPY scripts/arena/categorias-simples-pt.txt    /app/scripts/arena/categorias-simples-pt.txt
COPY scripts/arena/copy_legal_docs.py           /app/scripts/arena/copy_legal_docs.py
COPY scripts/arena/create_arena_admin.py        /app/scripts/arena/create_arena_admin.py
COPY scripts/arena/seed_arena_user.py           /app/scripts/arena/seed_arena_user.py
COPY scripts/arena/upsert_arena_affiliation.py  /app/scripts/arena/upsert_arena_affiliation.py
COPY scripts/arena/upsert_arena_categories.py   /app/scripts/arena/upsert_arena_categories.py
COPY scripts/backfill_email_reputation.py       /app/scripts/backfill_email_reputation.py
COPY scripts/backfill_login_geolocation.py      /app/scripts/backfill_login_geolocation.py
COPY scripts/bootstrap_languages.py             /app/scripts/bootstrap_languages.py
COPY scripts/migrate_arena_tc_to_fs.py          /app/scripts/migrate_arena_tc_to_fs.py
COPY scripts/run_migrations.py                  /app/scripts/run_migrations.py
COPY scripts/secrets_config.py                  /app/scripts/secrets_config.py
COPY containers/arena/entrypoint.sh /app/entrypoint.sh

RUN python /app/scripts/arena/copy_legal_docs.py
RUN chmod +x /app/entrypoint.sh

RUN --mount=type=cache,target=/tmp/uv-cache \
    uv sync --frozen --no-dev --package noca-arena

COPY --from=assets /app/shared/static/vendor   /app/shared/static/vendor
COPY --from=assets /app/shared/static/webfonts /app/shared/static/webfonts

# Documentary only: EXPOSE cannot follow a runtime env var. When NOCA_ARENA_PORT is
# overridden, the container still listens on that port and Caddy follows it.
EXPOSE 8001

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["python", "-m", "arena.main"]
