# Phase 01: Add animator site and access-control schema

This session adds the durable database contract needed to gate animator pages,
configure site medals, and authorize global or site-scoped reveal operators. It
does not add UI or animator routes.

## Source-plan coverage

This phase implements unified-plan sections 4.1, 4.2, and 5.0. It intentionally
places the gate before public animator routes so disabled contests are never
exposed during the staged implementation.

## Dependencies

Complete [Phase 00](Phase-00.md) first. The schema work is otherwise independent
of the live-scoreboard implementation.

## Session scope

Limit this session to SQLAlchemy table metadata, Web ORM mappings, one Alembic
migration, and schema tests. Defer services and administration UI.

## Required preflight

Complete these checks before editing code:

1. Inspect `shared/db_schema/contest.py`, `shared/db_schema/__init__.py`,
   `web/models/contest.py`, and `web/models/site.py`.
2. Inspect the current Alembic head and the most recent migration style.
3. Search PyPI for secret-storage packages. Record the result, but don't add a
   dependency when Python's `secrets`, `hashlib`, and `hmac` cover the required
   high-entropy token workflow.
4. Confirm that these tables are low-churn reference/configuration data. Record
   why they don't need custom per-table autovacuum tuning.

## Schema contract

Add the following database behavior:

- Add non-null `contests.animator_enabled` with Python and server defaults of
  `false`.
- Add `gold_cutoff`, `silver_cutoff`, `bronze_cutoff`, and nullable `style` to
  `sites`.
- Enforce positive, ordered cutoffs: gold is less than or equal to silver, and
  silver is less than or equal to bronze.
- Create `site_secrets` with `id`, `contest_id`, nullable `site_id`,
  `secret_digest`, `label`, `created_at`, and `updated_at`.
- Treat `site_id = NULL` as contest-global authorization.
- Use a composite foreign key from `(contest_id, site_id)` to
  `(sites.contest_id, sites.id)` so a secret cannot reference a site from a
  different contest.
- Store only a fixed-length digest. The plaintext token must exist only in the
  create operation's return value.
- Enforce uniqueness on `(contest_id, secret_digest)` and add indexes needed by
  contest and site lookups.

## Implementation tasks

Implement the database change in this order:

1. Extend `shared/db_schema/contest.py` and export the new table from
   `shared/db_schema/__init__.py`.
2. Extend the `Contest` and `Site` Web mappings and add a small `SiteSecret` ORM
   mapping in a focused file.
3. Add one Alembic revision after the current head. Its `upgrade()` must add the
   columns and table, while `downgrade()` must remove them in dependency-safe
   order.
4. Use server defaults during migration so existing rows remain valid. Remove
   temporary migration-only defaults only if that matches current repository
   convention.
5. Add schema and migration-boundary tests, including cross-contest FK rejection
   and cutoff constraint rejection.

## Validation

Run schema-focused checks without running the full suite:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest \
  tests/test_db_schema.py tests/test_run_migrations.py -q
UV_CACHE_DIR=/tmp/uv-cache uv run mypy shared web
UV_CACHE_DIR=/tmp/uv-cache uv run ruff check shared web tests
UV_CACHE_DIR=/tmp/uv-cache uv run ruff format --check shared web tests
```

If migration tests use a different current file, select the smallest existing
test slice that exercises upgrade and metadata parity.

## Completion criteria

This phase is complete when old contests migrate safely, disabled is the
default, site medal cutoffs are valid, global and site-scoped digest rows are
representable, and no plaintext operator token is stored.

## Next phase

Continue with [Phase 02](Phase-02.md), which adds shared access-control behavior
and Web-facing site configuration services.
