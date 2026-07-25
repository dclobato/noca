# Phase 05: Build contest metadata and scoreboard snapshot feeds

This session gives the animator an authoritative, read-only PostgreSQL feed for
one enabled contest. At completion, clients can fetch contest metadata and a
shared-scoreboard snapshot, but no presentation page or live stream exists yet.

## Source-plan coverage

This phase implements unified-plan sections 2.2 and 2.3 and applies the
`animator_enabled` gate defined in sections 4.1 and 5.0.

## Dependencies

Complete [Phase 04](Phase-04.md) first so the standalone process and shared
score projection are available.

## Session scope

Limit this session to read models, SQLAlchemy Core queries, JSON response
models, snapshot/meta routes, tests, and animator route/service documentation.

## Required preflight

Complete these checks before editing code:

1. Read `web/services/scoreboard/service.py`, the shared table metadata for
   contests, users, problems, submissions, and judgments, and the new shared
   scoreboard projection.
2. Inspect contest slug lookup and 404 patterns without importing their Web
   implementation.
3. Search PyPI for an ICPC scoreboard or projection library. Record why the
   Phase 00 shared implementation remains the required source of truth.
4. Review query plans and indexes for contest-scoped team, problem, submission,
   and judgment reads.

## Feed contract

Add these enabled-contest endpoints:

- `GET /animator/c/{slug}/meta` returns contest identity, problem labels and
  balloon colors, start/end timing, freeze state, and site summary data.
- `GET /animator/c/{slug}/snapshot` returns a typed `ScoreboardSnapshot` plus a
  server-generated version or timestamp suitable for client refreshes.

Both endpoints return `404` when the slug doesn't exist or
`animator_enabled=false`. The response must not reveal which condition failed.

## Implementation tasks

Implement the feed in this order:

1. Add animator-local immutable query records for contest, team, problem,
   submission, and judgment inputs. Keep them structurally compatible with the
   shared `compute_icpc()` contract.
2. Create `animator/services/contest_feed_service.py` using SQLAlchemy Core.
3. Load only `RoleEnum.TEAM` users for the target contest, order problems and
   submissions deterministically, and join judgments without N+1 queries.
4. Derive the freeze cutoff from `stop_updating_scoreboard` in seconds and call
   the shared score function. Do not copy scoring rules.
5. Create typed public response models that expose only presentation-safe data.
6. Add `animator/routes/public.py` with a router-level contest prefix and one
   operation per function.
7. Add a reusable enabled-contest dependency so all later public, media, event,
   and reveal routes share the same non-enumerating `404` behavior.
8. Add unit and route tests for enabled/disabled contests, missing slugs, empty
   contests, freeze visibility, PE/CE settings, ordering, and query count.
9. Create `animator/docs/ROUTES.md` and `animator/docs/SERVICES.md`.

## Validation

Run the focused feed checks:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest \
  tests/animator/test_contest_feed_service.py \
  tests/animator/test_public_routes.py -q
UV_CACHE_DIR=/tmp/uv-cache uv run mypy animator shared
UV_CACHE_DIR=/tmp/uv-cache uv run ruff check animator tests/animator
UV_CACHE_DIR=/tmp/uv-cache uv run ruff format --check animator tests/animator
```

Search animator source for imports beginning with `web.`; there must be none.

## Completion criteria

This phase is complete when enabled contests return score-compatible metadata
and snapshots, disabled and unknown contests are indistinguishable, query count
is bounded, responses are typed, and the routes and service are documented.

## Next phase

Continue with [Phase 06](Phase-06.md), which renders the first
projector-oriented live scoreboard page from these feeds.
