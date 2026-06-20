# NOCA Bootstrap

This document describes the current bootstrap flow for running the `web` app,
the `arena` app, the `rating` worker, the `aiassistant` worker, and the `autojudge`
worker from a fresh clone.

The repo supports two real startup modes:

- local development: run `web`, `arena`, `rating`, `aiassistant`, and `autojudge` directly on the host
- container runtime: run `web`, `arena`, `rating`, `aiassistant`, and `autojudge` from Docker images

For day-to-day development, the intended workflow is host-run `web`, `arena`,
`rating`, `aiassistant`, and `autojudge`, with PostgreSQL and Valkey running in Docker
containers and accessed via the host network.

Scripts are organized by ownership:

- shared startup and maintenance scripts stay at `scripts/`
- web-specific scripts live under `scripts/web/`
- arena-specific scripts live under `scripts/arena/`
- autojudge-specific scripts live under `scripts/autojudge/`

---

## Quick Start

### Local development

1. Install dependencies:

```bash
uv sync --extra dev
uv lock
```

2. Copy and adjust the environment file:

```bash
cp .env.example .env
```

3. Fetch shared vendor assets (needed by both web and arena):

```bash
uv run python scripts/fetch_assets.py
```

4. Create and start the PostgreSQL and Valkey compose file:

```bash
# Start from the sample and keep only postgres + valkey.
cp docker-compose.yml.sample docker-compose-db-valkey.yml
# Then edit the file:
# - remove caddy, web, and autojudge
# - add ports:
#   postgres: - 5432:5432
#   valkey:   - 6379:6379
wsl docker compose -f docker-compose-db-valkey.yml up -d
```

5. Run migrations, create the web UberAdmin, and seed languages:

```bash
uv run alembic upgrade head
uv run python scripts/web/create_uberadmin.py
uv run python scripts/bootstrap_languages.py
```

6. Seed the Arena initial user and categories:

```bash
uv run python scripts/arena/seed_arena_user.py \
    --fullname "Your Name" \
    --email you@example.com \
    --enabled \
    --email-confirmed \
    --role ARENA_ADMIN
uv run python scripts/arena/upsert_arena_categories.py categories-en.txt (or categories-pt.txt)
```

7. Start the web app:

```bash
uv run noca-web
```

8. Build the judge images if they are not present locally:

```bash
./containers/build.sh gcc-c17 gcc-cpp23 python3 java javascript kotlin fpc-pascal go rust c-sharp
```

9. Start the worker:

```bash
uv run noca-autojudge
```

10. Start the Arena app:

```bash
uv run noca-arena
```

11. Start the rating worker:

```bash
uv run noca-rating
```

12. Start the AI review worker (optional; requires `NOCA_AI_OPENAI_API_KEY` or user-supplied keys):

```bash
uv run noca-aiassistant
```

### Full container runtime

```bash
wsl docker compose -f docker-compose.yml.sample up --build
```

That path uses the container entrypoints described below.

---

## Local Development Bootstrap

### Shared prerequisites

The host-run `web` and `autojudge` processes both read `.env` from the repo root
through `pydantic-settings`.

Prerequisites:

- Python 3.14+ with [uv](https://docs.astral.sh/uv/) installed
- Docker
- On WSL2, prefix all `docker` commands with `wsl`
- Run commands below from the repo root

Minimum requirements:

- `NOCA_DB_*` must point to PostgreSQL
- `NOCA_VALKEY_*` must point to Valkey
- `NOCA_WEB_PROBLEM_STATEMENT_DIR` must exist and be readable/writable by `web`
- `NOCA_PROBLEM_TESTCASE_DIR` must exist and be readable/writable by `web` and `arena`, and readable by `autojudge`. Web problems are stored under `<root>/contest/`, Arena problems under `<root>/arena/` (subdirs created on demand).

Typical dev values:

```env
NOCA_DB_SERVER=127.0.0.1
NOCA_DB_PORT=5432
NOCA_VALKEY_SERVER=127.0.0.1
NOCA_VALKEY_PORT=6379
NOCA_WEB_PROBLEM_STATEMENT_DIR=.docker/problem_statements
NOCA_PROBLEM_TESTCASE_DIR=.docker/problem_test_cases
NOCA_VALKEY_USER=
NOCA_VALKEY_PASSWORD=
```

Important variable guidance:

| Variable | Notes |
|---|---|
| `NOCA_DB_USER` | PostgreSQL username, typically `noca` in development |
| `NOCA_DB_PASSWORD` | Must match the password stored in the PostgreSQL data volume |
| `NOCA_DB_SERVER` | Use `127.0.0.1` on WSL2; avoid `localhost` because `asyncpg` may prefer IPv6 first |
| `NOCA_DB_NAME` | Database name, typically `noca` |
| `NOCA_DATA_ROOT` | Root for local data directories, for example `.docker` |
| `NOCA_WEB_PROBLEM_STATEMENT_DIR` | Usually `<NOCA_DATA_ROOT>/problem_statements` |
| `NOCA_PROBLEM_TESTCASE_DIR` | Usually `<NOCA_DATA_ROOT>/problem_test_cases`; shared root for web, arena, and autojudge (subdirs `contest/` and `arena/`) |
| `NOCA_VALKEY_USER` | Leave empty in development when using the local container without auth |
| `NOCA_VALKEY_PASSWORD` | Leave empty in development when using the local container without auth |

Example alignment with `NOCA_DATA_ROOT=.docker`:

```env
NOCA_WEB_PROBLEM_STATEMENT_DIR=.docker/problem_statements
NOCA_PROBLEM_TESTCASE_DIR=.docker/problem_test_cases
```

If you change `NOCA_DB_PASSWORD` after PostgreSQL has already initialized its
data volume, the container credentials do not update automatically. Reset the
password inside PostgreSQL or recreate the volume.

### Web bootstrap on the host

Run:

```bash
uv run noca-web
```

What happens:

1. `web.main:main()` starts Uvicorn on `0.0.0.0:8000`.
2. Uvicorn parses `X-Forwarded-*` headers only from trusted proxies configured in `NOCA_FORWARDED_ALLOW_IPS`.
3. Reload is enabled automatically when `NOCA_ENVIRONMENT=development`.
4. During FastAPI lifespan startup, the app:
   - configures logging
   - opens the async SQLAlchemy engine and session factory
   - starts the Valkey runtime
   - initializes JWT/auth/image services
   - builds the Jinja environment
   - optionally starts the clarification/task reapers

Important notes:

- `web` does not run migrations automatically in host-run development
- `web` does not seed languages automatically in host-run development
- shared static vendor assets must already exist under `shared/static/vendor`
- if running behind a reverse proxy, configure both sides:
  - proxy must forward `X-Forwarded-Proto`, `X-Forwarded-Host`, and `X-Forwarded-For`
  - `NOCA_FORWARDED_ALLOW_IPS` must include only trusted proxy IPs/CIDRs
  - `NOCA_WEB_URL_BASE` is still recommended for stable absolute links in emails/reports

That is why the normal dev bootstrap is:

```bash
uv run python scripts/fetch_assets.py
uv run alembic upgrade head
uv run python scripts/web/create_uberadmin.py
uv run python scripts/bootstrap_languages.py
uv run noca-web
```

### Autojudge bootstrap on the host

Run:

```bash
uv run noca-autojudge
```

What the worker requires before startup:

- reachable PostgreSQL
- reachable Valkey
- migrated schema
- seeded languages in the database
- readable `NOCA_PROBLEM_TESTCASE_DIR` (reads `<root>/contest/` and `<root>/arena/`)
- access to the Docker daemon
- local judge images for every active language

What happens during worker startup:

1. `autojudge.worker:main()` configures logging and starts the async worker.
2. The worker connects to PostgreSQL and Valkey.
3. It loads the language registry from the database.
4. It verifies that every required compile/run image is already present locally.
5. It starts the fixed-width worker loops, reaper loop, and heartbeat loop.

If the judge images are missing, startup aborts with a message like:

```text
Missing required judge images. Worker startup aborted.
```

Build them with `containers/build.sh` before retrying.

### Judge validation scripts

After rebuilding judge images or changing sandbox/runtime behavior, run the two
operator scripts for different purposes:

```bash
uv run scripts/autojudge/probe_sandbox_limits.py
uv run scripts/autojudge/smoke_test_judge.py
```

Use them differently:

- `probe_sandbox_limits.py` is the fast sandbox baseline. It uses small C probes through the real judge path to verify core enforcement such as `network=none`, TLE, stdout OLE, generic file-growth `--fsize`, expected `/tmp` writability inside the sandbox, blocked writes to unmounted paths, read-only system directories, memory, and PID/process limits.
- `probe_sandbox_limits.py` includes both enforcement-assertion probes and contestant-visible probes. Some checks intentionally print `BLOCKED` and exit `0`, so they expect `AC` when the sandbox correctly denies the operation. Companion probes intentionally do not handle the denial and therefore expect `RE`.
- `smoke_test_judge.py` is the per-language integration check. It uses the sample solutions and test cases to verify compile/run images, runtime command wiring, and language-specific compatibility across all supported languages.

### Rating worker bootstrap on the host

Run:

```bash
uv run noca-rating
```

What the worker requires before startup:

- reachable PostgreSQL
- reachable Valkey
- migrated schema

What happens during worker startup:

1. `rating.worker:main()` configures logging and starts the async worker.
2. The worker connects to PostgreSQL and Valkey.
3. It starts the three sequential rating recomputation loops (problem → user → affiliation).

### AI assistant bootstrap on the host

Run:

```bash
uv run noca-aiassistant
```

What the worker requires before startup:

- reachable PostgreSQL
- reachable Valkey
- migrated schema
- AI provider API credentials (`NOCA_AI_OPENAI_API_KEY` or similar)

What happens during worker startup:

1. `aiassistant.worker:main()` configures logging and starts the async worker.
2. The worker connects to PostgreSQL and Valkey.
3. It starts three concurrent async loops: the dequeue loop (online reviews),
   the stale-job reaper loop, and the batch poller loop (platform-key reviews).

### Host-run worker checklist:

- Valkey is reachable (`NOCA_VALKEY_*`)
- Schema is migrated (`alembic upgrade head`)
- Docker socket is reachable from the host user (for `autojudge` only)
- `docker images` shows the `noca/judge-...` images expected by the language registry (for `autojudge` only)
- `NOCA_JUDGE_DOCKER_BASE_URL` matches the daemon you want to use (for `autojudge` only)
- `NOCA_JUDGE_DOCKER_NETWORK=none` unless you are intentionally debugging (for `autojudge` only)
- AI API credentials are set (for `aiassistant` only)

### Pytest integration markers

The default test command runs every collected test:

```bash
uv run pytest
```

That includes tests that exercise real infrastructure paths.

Use markers to target or exclude them:

```bash
# Tests that exercise the real SQL database engine fixtures
uv run pytest -m real_db

# Tests that exercise a real Valkey instance
uv run pytest -m real_valkey

# Everything except infrastructure-backed tests
uv run pytest -m "not real_db and not real_valkey"
```

Notes:

- `real_db` tests use the SQLAlchemy test engine/session fixtures.
- `real_valkey` tests require reachable Valkey (`NOCA_VALKEY_*`).
- When Valkey is unavailable, Valkey-backed tests are skipped.

### Startup verification

Check all host-run terminals for errors after startup.

Common issues:

- `password authentication failed`: `.env` does not match the PostgreSQL password stored in the container volume
- `invalid username-password pair or user is disabled`: `NOCA_VALKEY_USER` or `NOCA_VALKEY_PASSWORD` is set, but the local dev Valkey container runs without auth
- `ModuleNotFoundError`: `uv sync` was not run, or the editable install is stale
- connection refused on `127.0.0.1:5432` or `127.0.0.1:6379`: PostgreSQL or Valkey is not running
- missing static assets: run `uv run python scripts/fetch_assets.py`
- `Directory '...' is not readable/writable`: the problem data directories do not exist or have the wrong permissions
- `Missing required judge images`: build the local judge images before starting `noca-autojudge`
- Docker permission errors from autojudge: the host user cannot access the Docker daemon
- AI assistant startup failure: verify `NOCA_AI_OPENAI_API_KEY` or your AI provider credentials are set

---

## Container Bootstrap

The container runtime is slightly different because `web`, `arena`,
`rating`, `aiassistant`, and `autojudge` have entrypoint scripts.

### Web container bootstrap

`containers/webapp/entrypoint.sh` performs this sequence:

1. wait for PostgreSQL
2. wait for Valkey
3. run `scripts/run_migrations.py`
4. optionally run `scripts/web/create_uberadmin.py` when `NOCA_WEB_UBERADMIN_USERNAME` is set
5. optionally run `scripts/bootstrap_languages.py` when `NOCA_SEED_LANGUAGES=true`
6. start `noca-web` if no explicit command was provided

Operational consequence:

- schema migration is serialized by a PostgreSQL advisory lock, so any runtime
  container may request `alembic upgrade head` safely during startup
- the web container can also seed languages and bootstrap the first UberAdmin

### Arena container bootstrap

`containers/arena/entrypoint.sh` performs this sequence:

1. wait for PostgreSQL
2. wait for Valkey
3. run `scripts/run_migrations.py`
4. optionally run `scripts/bootstrap_languages.py` when `NOCA_SEED_LANGUAGES=true`
5. start `noca-arena` if no explicit command was provided

### Autojudge container bootstrap

`containers/autojudge/entrypoint.sh` performs this sequence:

1. wait for PostgreSQL TCP reachability
2. wait for Valkey TCP reachability
3. run `scripts/run_migrations.py`
4. exec the worker command

### Rating container bootstrap

`containers/rating/entrypoint.sh` performs this sequence:

1. wait for PostgreSQL TCP reachability
2. wait for Valkey TCP reachability
3. run `scripts/run_migrations.py`
4. exec the worker command

### AI assistant container bootstrap

`containers/aiassistant/entrypoint.sh` performs this sequence:

1. wait for PostgreSQL TCP reachability
2. wait for Valkey TCP reachability
3. run `scripts/run_migrations.py`
4. exec the worker command

Operational consequence:

- concurrent `web`, `arena`, `rating`, `aiassistant`, and `autojudge` startup is safe
  because only one container holds the migration advisory lock at a time
- language seeding is available in the web and arena containers, controlled by
  `NOCA_SEED_LANGUAGES=true`

---

## Recommended Commands

### Development

```bash
uv sync --extra dev
uv run python scripts/fetch_assets.py
wsl docker compose -f docker-compose-db-valkey.yml up -d
uv run alembic upgrade head
uv run python scripts/web/create_uberadmin.py
uv run python scripts/bootstrap_languages.py
uv run python scripts/arena/seed_arena_user.py --fullname "Your Name" --email you@example.com --enabled --email-confirmed --role ARENA_ADMIN
uv run python scripts/arena/upsert_arena_categories.py categories-en.txt (or categories-pt.txt)
uv run noca-web
uv run noca-arena
uv run noca-rating
uv run noca-aiassistant
uv run noca-autojudge
```

### Build judge images

```bash
./containers/build.sh gcc-c17 gcc-cpp23 python3 java javascript kotlin fpc-pascal go rust c-sharp
```

### Full stack containers

```bash
wsl docker compose -f docker-compose.yml.sample up --build
```

For reverse-proxy deployments, set `NOCA_FORWARDED_ALLOW_IPS` to the address/CIDR of the proxy that reaches the `web` service. Avoid `*` unless the network is fully private and clients cannot access `web` directly.

---

## Common Failure Modes

- `ModuleNotFoundError`: `uv sync` has not been run, or the editable install is stale
- `password authentication failed`: `.env` does not match the PostgreSQL container state
- Valkey auth errors in development: clear `NOCA_VALKEY_USER` and `NOCA_VALKEY_PASSWORD`
- missing static assets: run `uv run python scripts/fetch_assets.py`
- `Directory '...' is not readable/writable`: the problem data directories do not exist or have the wrong permissions
- `Missing required judge images`: build the local judge images before starting `noca-autojudge`
- Docker permission errors from autojudge: the host user cannot access the Docker daemon

---

## Versioning

All six workspace members derive their version from the root `pyproject.toml` via
Hatchling's `regex` version source. There is one place to bump the version:

```toml
# pyproject.toml (root)
[project]
version = "9.1.0"
```

Each member's `pyproject.toml` declares `dynamic = ["version"]` and reads it with:

```toml
[tool.hatch.version]
source = "regex"
path = "../pyproject.toml"
pattern = '(?m)^version = "(?P<version>[^"]+)"'
```

After changing the version, force-reinstall the workspace packages to refresh their
installed metadata (a plain `uv sync` skips rebuilding editable packages when only the
version changes):

```bash
uv sync --all-packages --reinstall-package noca-shared --reinstall-package noca-web \
  --reinstall-package noca-arena --reinstall-package noca-autojudge \
  --reinstall-package noca-rating --reinstall-package noca-aiassistant
```

---

## Source Of Truth

The current bootstrap behavior is defined in:

- `web/main.py` for host-run web startup
- `arena/main.py` for host-run arena startup
- `rating/worker.py` for host-run rating worker startup
- `aiassistant/worker.py` for host-run AI assistant startup
- `autojudge/worker.py` for host-run autojudge worker startup
- `containers/webapp/entrypoint.sh` for containerized web bootstrap
- `containers/arena/entrypoint.sh` for containerized arena bootstrap
- `containers/rating/entrypoint.sh` for containerized rating worker bootstrap
- `containers/aiassistant/entrypoint.sh` for containerized AI assistant bootstrap
- `containers/autojudge/entrypoint.sh` for containerized autojudge worker bootstrap
- `CONFIG.md` for environment variable definitions
