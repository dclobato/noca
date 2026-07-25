# Phase 12: Expose authenticated reveal control APIs

This session exposes the reveal state machine through contest-scoped HTTP APIs.
Every command enforces contest enablement, the global control kill switch,
credential scope, and per-session serialization.

## Source-plan coverage

This phase implements unified-plan section 5.3. It strengthens the source design
by keeping operator credentials out of query strings and access logs.

## Dependencies

Complete [Phase 11](Phase-11.md) first. No mutating endpoint may exist before
durable, serialized state writes are available.

## Session scope

Limit this session to control dependencies, Pydantic request/response models,
HTTP routes, authorization, error mapping, tests, and route documentation.

## Required preflight

Complete these checks before editing code:

1. Read the shared animator access service, enabled-contest dependency, reveal
   engine, and reveal-session store.
2. Inspect existing bearer-header parsing and security-event logging patterns.
3. Verify FastAPI's current `HTTPBearer` and `Annotated` dependency behavior in
   official documentation. Search PyPI only if an unmet authentication need
   remains.
4. Define status codes for disabled control, invalid credentials, invalid state,
   lock contention, missing sessions, and invalid target teams.

## API contract

Create these operations under `/animator/c/{slug}/control`:

- `POST /start-reveal` with optional `site_id`.
- `POST /step`.
- `POST /back`.
- `POST /reset`.
- `POST /jump-team` with `team_id`.
- `GET /state`.

Use `Authorization: Bearer <operator-token>` for every operation. Never accept a
credential in a URL or log its value.

## Authorization contract

Apply authorization in this order:

1. Return `404` when the contest doesn't exist or animator is disabled.
2. Return `404` for all control routes when
   `NOCA_ANIMATOR_ENABLE_CONTROL=false`, so deployment configuration isn't
   disclosed.
3. Resolve the bearer token through the shared digest service.
4. Authorize a site token only for its exact site scope.
5. Authorize a global token only for global scope.
6. Use the stored session scope for commands after `start-reveal`; never trust a
   caller-supplied scope on each command.

## Implementation tasks

Implement control in this order:

1. Create `animator/routes/control.py` with a router-level prefix and reusable
   `Annotated` dependencies.
2. Add typed request and response models. Return safe projection views, not raw
   database records or secret metadata.
3. Make start idempotency explicit: reject an active session with `409` unless a
   reset or explicit restart contract is used.
4. Run every mutation under the Phase 11 per-scope lock, then save and publish
   exactly once.
5. Map domain validation to `400` or `409`, invalid authorization to `403`, and
   temporary lock contention or store unavailability to a retryable response.
6. Add structured security logs for valid and invalid control attempts without
   recording tokens or digests.
7. Register the router in `animator/main.py`.
8. Add route tests for every command and state transition, site/global scope
   crossing, disabled contest, disabled control, malformed bearer headers,
   contention, restart recovery, and secret redaction in logs.
9. Update `animator/docs/ROUTES.md`.

## Validation

Run the focused control checks:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest \
  tests/animator/test_control_routes.py \
  tests/animator/test_reveal_session_store.py -q
UV_CACHE_DIR=/tmp/uv-cache uv run mypy animator shared
UV_CACHE_DIR=/tmp/uv-cache uv run ruff check animator tests/animator
UV_CACHE_DIR=/tmp/uv-cache uv run ruff format --check animator tests/animator
```

Search test logs and HTTP locations for sample operator tokens. None may appear
outside a request authorization header or one-time generation response.

## Completion criteria

This phase is complete when every reveal command is durable, scope-correct,
kill-switch protected, and safely typed; invalid tokens reveal no credential
metadata; and secrets never appear in URLs or logs.

## Next phase

Continue with [Phase 13](Phase-13.md), which adds spectator state streaming and
the team-photo endpoint required by the reveal projection.
