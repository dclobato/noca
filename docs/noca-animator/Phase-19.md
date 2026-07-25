# Phase 19: Harden reveal recovery and concurrency

This session stress-tests and hardens the existing Valkey reveal store. It
focuses on restart recovery, multiple animator replicas, corrupt state, and
ambiguous client retries without adding presentation features.

## Source-plan coverage

This phase completes unified-plan section 7.1. It hardens the persistence first
implemented in Phase 11 rather than introducing a second store.

## Dependencies

Complete [Phase 18](Phase-18.md) first so concurrency tests can run against the
final process and deployment configuration.

## Session scope

Limit this session to failure injection, TTL validation, multi-replica safety,
command retry behavior, bounded fixes, tests, and store documentation.

## Required preflight

Complete these checks before editing code:

1. Audit `reveal_session_store.py`, control mutations, lock TTL, state TTL, and
   current real-Valkey tests.
2. Inspect current command request/response versions and identify which retries
   can apply a mutation twice.
3. Search PyPI for distributed-lock and idempotency libraries. Record why the
   existing Valkey primitives remain sufficient or justify a dependency.
4. Define failure injection for process death before save, after save but before
   publish, after publish, during lock release, and during client response.

## Recovery contract

The hardened store must provide these guarantees:

- Restarting any animator replica preserves the last durable reveal state.
- At most one writer mutates a contest/scope at a time.
- A lock is released only by its owner and expires after owner failure.
- State outlives the contest by the documented ceremony margin.
- Malformed or future-version state fails closed and is never silently reset.
- A retried command can't advance the same state twice.

## Implementation tasks

Harden the store in this order:

1. Add deterministic crash-point tests around lock, save, publish, and response
   boundaries.
2. Run two animator app instances against one Valkey and submit simultaneous
   commands for the same and different scopes.
3. Add request idempotency keys or optimistic expected-state versions to
   mutating control requests. Keep the contract explicit and bounded by TTL.
4. Persist enough command-result metadata to return the original result for a
   safe retry without storing credentials.
5. Validate state and lock TTL behavior with a contest that has ended and one
   that is still running.
6. Add corruption and future-schema fixtures and a documented operator recovery
   procedure that requires an explicit reset.
7. Verify missed publication recovery through the public state endpoint.
8. Update `animator/docs/SERVICES.md`, `animator/docs/ROUTES.md`, and
   `docs/CONFIG.md` for final retry and TTL behavior.

## Validation

Run focused fake and real-Valkey checks:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest \
  tests/animator/test_reveal_session_store.py \
  tests/animator/test_control_concurrency.py -q
UV_CACHE_DIR=/tmp/uv-cache uv run pytest -m real_valkey \
  tests/animator/test_control_concurrency.py -q
UV_CACHE_DIR=/tmp/uv-cache uv run mypy animator shared
```

Repeat the same concurrent test enough times to detect flaky lock or retry
behavior without turning the normal unit suite into a long stress test.

## Completion criteria

This phase is complete when restart and crash points preserve recoverable state,
simultaneous writers can't double-apply commands, retries are idempotent,
malformed state fails closed, and TTL behavior is verified against real Valkey.

## Next phase

Continue with [Phase 20](Phase-20.md), which adds bounded metrics and
secret-safe structured operational logs.
