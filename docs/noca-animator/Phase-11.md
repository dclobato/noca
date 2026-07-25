# Phase 11: Persist and publish reveal sessions in Valkey

This session creates the durable session store required before HTTP controls are
exposed. State survives process restarts, writes are serialized per contest and
scope, and spectator projections are published after successful mutations.

## Source-plan coverage

This phase implements unified-plan section 5.4 and the initial persistence
requirements of section 7.1. Phase 18 later stress-tests and observes the same
store rather than replacing it.

## Dependencies

Complete [Phase 10](Phase-10.md) first so the store persists stable, versioned
state transitions.

## Session scope

Limit this session to Valkey constants and primitives, the reveal-session store,
atomic mutation support, publication, and focused unit and integration tests.

## Required preflight

Complete these checks before editing code:

1. Read `shared/services/valkey_service/runtime.py`, `constants.py`,
   `queue_ops.py`, `__init__.py`, and existing lock patterns.
2. Inspect real-Valkey test fixtures and reconnect/buffer semantics.
3. Search PyPI for distributed-lock libraries. Record whether the current Valkey
   client and a token-owned `SET NX` plus atomic release cover the requirement.
4. Define failure semantics for store unavailable, lock contention, malformed
   state, and publish failure before coding.

## Storage contract

Use these stable keys and channels:

- State key: `animator:reveal:{contest_id}:{scope}`.
- Scope value: the site ID or `global`.
- Event channel: `revelation:events:{contest_id}:{scope}`.
- Lock key: a namespaced derivative of the state key.

State must be written before its projection event is published. A publication
failure must not roll back valid state; clients can recover through state fetch.

## Implementation tasks

Implement persistence in this order:

1. Add versioned reveal event models to an appropriate shared schema module.
2. Extend `ValkeyRuntime` with typed publication and iteration methods for
   revelation channels, following verdict-channel reconnect and cleanup style.
3. Keep arbitrary channel strings constrained to validated contest and scope
   components; don't accept raw user-provided channel names.
4. Create `animator/services/reveal_session_store.py` with `load`, `save`,
   `delete` or reset support, and an atomic mutation context.
5. Serialize Pydantic state with an explicit schema version and reject corrupted
   or future-version payloads with a typed store error.
6. Calculate an initial TTL from contest end plus a validated
   `NOCA_ANIMATOR_REVEAL_TTL_MARGIN_SECONDS` setting. Refresh the TTL on every
   successful mutation, and document the setting in `docs/CONFIG.md`.
7. Use a random lock-owner token and atomic compare-and-delete release. Never
   delete another writer's lock.
8. Publish a typed projection event after `start`, `step`, `back`, `jump`, or
   `reset` state is durably saved.
9. Add fake-client tests and marked real-Valkey tests for round-trip, TTL,
   contention, lock ownership, malformed data, restart recovery, channel
   isolation, and save-before-publish ordering.
10. Update `docs/SHARED_SERVICES.md` and `animator/docs/SERVICES.md`.

## Validation

Run local tests first, then the real-Valkey slice when the service is available:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest \
  tests/animator/test_reveal_session_store.py \
  tests/shared/test_revelation_channel.py -q
UV_CACHE_DIR=/tmp/uv-cache uv run pytest -m real_valkey \
  tests/animator/test_reveal_session_store.py -q
UV_CACHE_DIR=/tmp/uv-cache uv run mypy animator shared
UV_CACHE_DIR=/tmp/uv-cache uv run ruff check animator shared tests
```

Kill and recreate the animator process between a save and load to confirm state
is independent of process memory.

## Completion criteria

This phase is complete when session state survives restarts, only one writer can
mutate a scope at a time, lock release is ownership-safe, events are isolated by
scope, and subscribers can recover from a missed publication by loading state.

## Next phase

Continue with [Phase 12](Phase-12.md), which exposes authenticated control
commands over HTTP.
