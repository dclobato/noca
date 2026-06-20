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

RUN --mount=type=cache,target=/tmp/uv-cache \
    uv sync --frozen --no-dev --package noca-arena --no-install-workspace

COPY shared /app/shared
COPY arena /app/arena
COPY docs/legal /app/docs/legal
COPY migrations /app/migrations
COPY scripts/__init__.py /app/scripts/__init__.py
COPY scripts/arena/categories-en.txt /app/scripts/arena/categories-en.txt
COPY scripts/arena/categories-pt.txt /app/scripts/arena/categories-pt.txt
COPY scripts/arena/copy_legal_docs.py /app/scripts/arena/copy_legal_docs.py
COPY scripts/arena/seed_arena_user.py /app/scripts/arena/seed_arena_user.py
COPY scripts/arena/upsert_arena_categories.py /app/scripts/arena/upsert_arena_categories.py
COPY scripts/bootstrap_languages.py /app/scripts/bootstrap_languages.py
COPY scripts/run_migrations.py /app/scripts/run_migrations.py
COPY containers/arena/entrypoint.sh /app/entrypoint.sh

RUN python /app/scripts/arena/copy_legal_docs.py
RUN chmod +x /app/entrypoint.sh

RUN --mount=type=cache,target=/tmp/uv-cache \
    uv sync --frozen --no-dev --package noca-arena

COPY --from=assets /app/shared/static/vendor   /app/shared/static/vendor
COPY --from=assets /app/shared/static/webfonts /app/shared/static/webfonts

EXPOSE 8001

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["python", "-m", "arena.main"]
