# Phase 04: Scaffold the standalone animator runtime

This session creates a runnable `noca-animator` FastAPI process with isolated
configuration, PostgreSQL and Valkey lifecycle management, static/template
mounts, and a health endpoint. It does not load contest data yet.

## Source-plan coverage

This phase implements unified-plan sections 0.1 through 0.3 and 2.1. It follows
the current seven-member workspace and adds animator as the eighth member.

## Dependencies

Complete [Phase 03](Phase-03.md) first. The runtime can then enforce the schema
and admin-owned enablement contract from its first public feature.

## Session scope

Limit this session to package structure, process bootstrap, configuration,
dependency wiring, health behavior, and baseline tests and documentation.

## Required preflight

Complete these checks before editing code:

1. Inspect `healthmonitor/main.py`, `web/main.py`, `web/database.py`, and the
   package-level `pyproject.toml` files.
2. Inspect current startup-wait helpers, logging setup, static mounts, template
   loaders, and health-route tests.
3. Check PyPI and official project documentation for the currently pinned
   FastAPI, Uvicorn, Jinja2, SQLAlchemy, and Valkey packages. Add only direct
   runtime dependencies that `animator` imports.
4. Choose an unused default development port and verify it against the sample
   compose and documented runtimes.

## Package contract

Create this initial structure:

```text
animator/
├── __init__.py
├── pyproject.toml
├── config.py
├── database.py
├── dependencies.py
├── main.py
├── routes/
│   ├── __init__.py
│   └── health.py
├── services/__init__.py
├── static/css/animator.css
├── static/js/animator.js
└── template/_base.html
```

## Implementation tasks

Implement the scaffold in this order:

1. Add `animator` to workspace members and sources, Ruff first-party modules,
   and CI mypy targets.
2. Create `animator/pyproject.toml` with `noca-animator =
   "animator.main:main"`, the standard Hatchling workspace layout, and direct
   dependencies.
3. Define validated `NOCA_ANIMATOR_HOST`, `NOCA_ANIMATOR_PORT`,
   `NOCA_ANIMATOR_POLL_FALLBACK_SECONDS`, and
   `NOCA_ANIMATOR_ENABLE_CONTROL` settings alongside shared database, Valkey,
   environment, log-level, and startup-timeout settings.
4. Use SQLAlchemy Core and an async engine. Do not define animator ORM mappings
   or import Web's declarative base.
5. Implement a FastAPI lifespan that configures logging, waits for PostgreSQL
   and Valkey, starts `ValkeyRuntime`, initializes templates, and closes every
   resource on shutdown.
6. Add reusable `Annotated` database and runtime dependencies.
7. Add `GET /health` with readiness behavior consistent with other runtimes.
8. Mount animator and required shared static assets with existing cache-aware
   static-file classes.
9. Add package, configuration, lifespan, and health-route tests under
   `tests/animator/`.
10. Update `docs/ARCHITECTURE.md`, `docs/CONFIG.md`, and `README.md` with the
    new runtime and development command. Do not add container deployment yet.

## Validation

Run focused runtime checks:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv sync --all-packages
UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/animator -q
UV_CACHE_DIR=/tmp/uv-cache uv run mypy animator shared
UV_CACHE_DIR=/tmp/uv-cache uv run ruff check animator tests/animator
UV_CACHE_DIR=/tmp/uv-cache uv run ruff format --check animator tests/animator
```

Start `uv run noca-animator` against the development services and confirm the
health endpoint responds, then stop it cleanly.

## Completion criteria

This phase is complete when the workspace installs the new package, the process
starts and shuts down cleanly, health reflects dependency readiness,
configuration is documented, and no animator code imports `web`.

## Next phase

Continue with [Phase 05](Phase-05.md), which adds contest metadata and snapshot
feeds behind the enablement gate.
