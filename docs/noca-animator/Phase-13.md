# Phase 13: Add reveal spectator APIs and team photos

This session provides read-only ceremony endpoints for projector clients and the
photo response needed by the team modal. Spectators can recover current state
after joining late or missing a Valkey publication.

## Source-plan coverage

This phase implements the backend portion of unified-plan sections 5.5 and
5.5.1, including the photo endpoint that Phase 6.1 anticipates.

## Dependencies

Complete [Phase 12](Phase-12.md) first. Spectator APIs read the same durable
state created by authenticated controls.

## Session scope

Limit this session to spectator page routing, public reveal-state JSON, reveal
SSE, scoped photo delivery, caching, fallbacks, tests, and documentation. The
full projector and controller interfaces belong to Phase 14.

## Required preflight

Complete these checks before editing code:

1. Read `web/routes/user_media.py`, `web/models/users.py`, and
   `shared/db_schema/users.py` without importing the Web implementations.
2. Inspect current image response cache, ETag, MIME, and placeholder patterns.
3. Verify native FastAPI byte-response and SSE behavior in official docs. Search
   PyPI only if current primitives cannot satisfy safe media delivery.
4. Define how a public client selects `global` or a site ID and how every team
   lookup is constrained to that selected session scope.

## Public contract

Add these enabled-contest operations:

- `GET /animator/c/{slug}/ceremony` returns the spectator HTML shell.
- `GET /animator/c/{slug}/reveal/state` returns the latest projection for a
  validated global or site scope.
- `GET /animator/c/{slug}/reveal/events` streams projection changes for that
  same scope.
- `GET /animator/c/{slug}/teams/{team_id}/photo` serves a full photo, then an
  avatar fallback, then an animator placeholder.

Scope selection must use a validated query value or path component, never an
operator token.

## Implementation tasks

Implement spectator support in this order:

1. Add a scope dependency that accepts `global` or a site belonging to the
   contest and produces the canonical Valkey scope.
2. Add a public state endpoint that loads durable state and derives or returns
   the latest safe projection. Define a clear not-started response.
3. Add a reveal SSE endpoint backed by `iter_revelation_events()`, with a
   15-second heartbeat and disconnect cleanup.
4. On connection, have the browser fetch current state before relying on new
   events; pub/sub remains non-replayable.
5. Query the requested team with `RoleEnum.TEAM`, the current contest ID, and
   the selected site when applicable.
6. Decode stored base64 defensively. Return the stored full photo, then stored
   avatar, then a checked-in static placeholder with the correct MIME type.
7. Build an ETag from media kind and `dta_foto`, honor `If-None-Match`, and set
   a suitable public `Cache-Control`. Don't include base64 data in JSON
   endpoints.
8. Add tests for scope validation, late join, heartbeat, missed-event recovery,
   photo/avatar/placeholder selection, invalid base64, MIME type, ETag `304`,
   disabled contest, wrong contest, and wrong site.
9. Update `animator/docs/ROUTES.md` and `animator/docs/SERVICES.md`.

## Validation

Run the focused spectator and media checks:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest \
  tests/animator/test_reveal_public_routes.py \
  tests/animator/test_team_photo_route.py -q
UV_CACHE_DIR=/tmp/uv-cache uv run mypy animator shared
UV_CACHE_DIR=/tmp/uv-cache uv run ruff check animator tests/animator
UV_CACHE_DIR=/tmp/uv-cache uv run ruff format --check animator tests/animator
```

Manually request a stored photo twice and confirm the second conditional request
returns `304` with no response body.

## Completion criteria

This phase is complete when spectators can join or recover a ceremony without a
secret, scope cannot leak another site's teams, reveal SSE cleans up correctly,
and team-photo responses always produce a valid image or safe `404` policy.

## Next phase

Continue with [Phase 14](Phase-14.md), which builds the projector and operator
interfaces on these contracts.
