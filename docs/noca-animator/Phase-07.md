# Phase 07: Stream live contest events over SSE

This session adds one resilient verdict subscriber per animator process and a
fan-out service for browser SSE clients. Clients receive change notifications,
timer ticks, and heartbeats while PostgreSQL snapshots remain authoritative.

## Source-plan coverage

This phase implements unified-plan sections 3.1 and 3.2. It delivers backend
streaming only; visual rank and cell transitions belong to the next phase.

## Dependencies

Complete [Phase 06](Phase-06.md) first. The stream will signal clients to
refresh the existing snapshot contract.

## Session scope

Limit this session to Valkey subscription, in-process fan-out, SSE routing,
lifespan integration, backpressure, reconnect behavior, and tests.

## Required preflight

Complete these checks before editing code:

1. Read `shared.queue_schema.VerdictEvent`,
   `ValkeyRuntime.iter_verdict_events()`, and existing Web and Arena SSE tests.
2. Verify the pinned FastAPI version's native `fastapi.sse` API in official
   documentation and check PyPI for maintained alternatives only if native SSE
   lacks a required capability.
3. Inspect how current services stop background tasks during FastAPI lifespan
   shutdown.
4. Decide queue bounds and overflow semantics before implementing fan-out.

## Event contract

Expose `GET /animator/c/{slug}/events` for enabled contests with these events:

- `verdict` identifies a relevant finalized judgment without leaking logs.
- `scoreboard_refresh` tells the client to fetch an authoritative snapshot.
- `timer_tick` carries server contest timing at a bounded cadence.
- A comment heartbeat is sent every 15 seconds when no data event is emitted.

## Implementation tasks

Implement streaming in this order:

1. Create `animator/services/event_stream_service.py` with one background
   subscriber task per animator process and bounded per-client queues.
2. Filter events by `contest_id`. Treat legacy events with no contest ID as an
   optional global refresh signal only if a safe, bounded fallback can resolve
   their contest from PostgreSQL.
3. Coalesce refresh signals when a client queue is full instead of allowing
   unbounded memory growth.
4. Restart the Valkey iterator with bounded exponential backoff after
   recoverable disconnects, and stop promptly when lifespan shutdown begins.
5. Use `EventSourceResponse` and typed `ServerSentEvent` values. Detect browser
   disconnects and unregister queues in `finally`.
6. Emit monotonically increasing event IDs when practical so reconnect behavior
   can be tested. Do not promise replay until a durable event log exists.
7. Add the service to animator lifespan startup and shutdown.
8. Add unit and route tests for filtering, heartbeat, timer ticks, multiple
   clients, slow-client coalescing, disconnect cleanup, Valkey interruption, and
   disabled-contest `404` behavior.
9. Update `animator/docs/ROUTES.md` and `animator/docs/SERVICES.md`.

## Validation

Run the focused streaming checks:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest \
  tests/animator/test_event_stream_service.py \
  tests/animator/test_events_route.py -q
UV_CACHE_DIR=/tmp/uv-cache uv run mypy animator shared
UV_CACHE_DIR=/tmp/uv-cache uv run ruff check animator tests/animator
UV_CACHE_DIR=/tmp/uv-cache uv run ruff format --check animator tests/animator
```

Use a manual SSE client to confirm heartbeat delivery and clean termination when
the animator process stops.

## Completion criteria

This phase is complete when one Valkey subscription safely fans out relevant
events, slow or disconnected clients cannot leak resources, SSE uses typed
native FastAPI primitives, and PostgreSQL remains the snapshot source of truth.

## Next phase

Continue with [Phase 08](Phase-08.md), which turns refresh notifications into
visible rank and problem-cell transitions.
