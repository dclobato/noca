# Phase 16: Store and merge team presentation profiles

This session adds optional per-contest presentation metadata and merges it into
the team feed. Existing user and media values remain the fallback, so a profile
is never required to run a ceremony.

## Status: optional (backlog), not on the required path

This phase is **deferred to the backlog** and isn't a prerequisite for any later
phase. The required sequence goes from [Phase 14](Phase-14.md) straight to
[Phase 18](Phase-18.md). It also depends on [Phase 15](Phase-15.md), which is
deferred for its own reasons.

Three arguments put this work behind a concrete requirement:

- No surface consumes an institution name, short name, theme color, or
  `media_json`. Building the table, migration, merge service, and validation now
  would add infrastructure with no visible behavior.
- `media_json` is an untyped blob in a module whose response models are
  deliberately typed, non-enumerating, and free of stored payloads.
- Every `users` row is already contest-scoped (`users.contest_id`, unique per
  `(contest_id, username)`), so "different profiles in different contests" needs
  no second table, and `display_name` largely restates `users.fullname`.

For most contests, institution branding belongs to the venue rather than the
team, and `sites` already models venues. Implement this phase only once
institution, short name, or color has a defined visual use — and prefer one
nullable stage-name column plus site-level branding over a general profile table
if that turns out to cover the requirement.

## Source-plan coverage

This phase implements the schema and animator-service portions of unified-plan
sections 6.2 and 6.3. The Web write interface belongs to Phase 17.

## Dependencies

Complete [Phase 15](Phase-15.md) first so the fallback team-data contract is
stable before optional overrides are introduced.

## Session scope

Limit this session to one low-churn profile table and migration, shared schema,
the animator merge service, tests, and animator/shared documentation.

## Required preflight

Complete these checks before editing code:

1. Read shared user schema, the Phase 15 team feed, and current animator service
   source-size totals.
2. Inspect current JSON/JSONB column conventions and color validation patterns.
3. Search PyPI for profile or branding libraries. Record why Pydantic,
   SQLAlchemy, and the existing service stack cover this metadata model.
4. Analyze the table's write pattern. Record that administrator-edited
   reference rows are low churn and don't need custom autovacuum parameters.

## Data contract

Create `contest_team_profiles` with one row per contest/team and these fields:

- `contest_id` and `team_id` as the composite primary key.
- Optional `display_name`, `institution_name`, and
  `institution_short_name`.
- Optional validated `theme_color`.
- Optional `media_json` for small presentation metadata only.
- Standard creation and update timestamps.

Do not duplicate photo, avatar, or audio blobs. Add the composite user key
needed for a database-enforced same-contest team foreign key if current metadata
lacks one.

## Implementation tasks

Implement profile storage in this order:

1. Add the shared table metadata and export it from
   `shared/db_schema/__init__.py`.
2. Add one Alembic migration with complete upgrade and downgrade behavior,
   same-contest referential integrity, and cascade deletion.
3. Create `animator/services/team_profile_service.py` using SQLAlchemy Core.
4. Merge profile overrides with `users`, `sites`, and `users_media`; preserve
   the Phase 15 response shape and add optional institution, short name, color,
   and presentation metadata.
5. Validate color as a conservative CSS color representation, limit all
   strings, and allow only documented keys and value types in `media_json`.
6. Add migration, service, merge-fallback, invalid-value, cross-contest, and
   cascade-deletion tests.
7. Update `animator/docs/SERVICES.md`, `docs/SHARED_SERVICES.md`, and
   `docs/ARCHITECTURE.md`.

## Validation

Run focused profile checks:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest \
  tests/animator/test_team_profile_service.py \
  tests/test_db_schema.py -q
UV_CACHE_DIR=/tmp/uv-cache uv run mypy animator shared
UV_CACHE_DIR=/tmp/uv-cache uv run ruff check animator shared tests
UV_CACHE_DIR=/tmp/uv-cache uv run ruff format --check animator shared tests
```

Query a team with no profile, a partial profile, and a complete profile to prove
that fallback order is stable and media blobs aren't duplicated.

## Completion criteria

This phase is complete when the schema enforces contest/team ownership,
animator responses merge optional overrides deterministically, media remains in
`users_media`, and invalid or cross-contest records are rejected.

## Next phase

Continue with [Phase 17](Phase-17.md), which adds the administrator write path
for presentation profiles.
