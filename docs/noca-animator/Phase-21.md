# Phase 21: Prove end-to-end behavior and finalize documentation

This final session runs one complete live-scoreboard flow and both global and
site reveal ceremonies, closes only defects exposed by those flows, and performs
the full cross-module validation and documentation audit.

## Source-plan coverage

This phase implements unified-plan section 7.3 and verifies every phase-level
success criterion from the source document.

## Dependencies

Complete [Phase 20](Phase-20.md) first. This session validates the final
deployed and observable architecture rather than adding another feature layer.

## Session scope

Limit this session to end-to-end fixtures, integration tests, bounded fixes,
full validation, documentation consistency, and the final handoff record.

## Required preflight

Complete these checks before editing code:

1. Audit all tests under `tests/animator/` against the unified plan's minimum
   coverage list.
2. Build one deterministic ceremony fixture with multiple sites, ties,
   first-solves, PE/CE variations, post-freeze failures and accepts, photos,
   missing media, audio, and presentation profiles.
3. Confirm PostgreSQL and Valkey test isolation and identify which end-to-end
   tests need `real_db` or `real_valkey` markers.
4. Re-read every animator route, service, architecture, configuration, and
   deployment document before the final audit.

## End-to-end contract

The final test flow must prove these journeys:

- Bootstrap an enabled contest, render the live page, publish a verdict, receive
  SSE, refresh the snapshot, and display the authoritative new rank.
- Start and complete a global ceremony with step, back, jump, and reset.
- Start and complete a site ceremony without exposing another site's teams.
- Restart the animator mid-ceremony and continue from the same state.
- Join a projection late and recover current state without replayed pub/sub.
- Render medal changes, focus, pending cells, profile overrides, photo fallback,
  avatar fallback, and optional audio availability.

## Implementation tasks

Complete final verification in this order:

1. Add the end-to-end live test from bootstrap through verdict-driven rank
   update.
2. Add global and site reveal tests covering all commands and completion.
3. Add golden assertions that every intermediate reveal rank equals
   `compute_icpc()` over the same visible submission set.
4. Add regression assertions for exact `back(step(state))` reversibility,
   bottom-up order, focus retention, pending cells, medal cutoffs, enablement,
   credential scope, persistence, and late join.
5. Run a final `rg` audit proving animator doesn't import `web`, doesn't contain
   Maratona compatibility code, and doesn't log or serialize secret digests.
6. Run a source-size audit and split oversized files without changing behavior.
7. Verify all route, service, architecture, configuration, environment,
   container, health-monitor, and README documentation against code.
8. Fix only issues required to satisfy these end-to-end contracts, then rerun
   the smallest affected slice before the full suite.

## Validation

Run focused checks first, then complete repository validation with the required
long timeout:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/animator -q
UV_CACHE_DIR=/tmp/uv-cache uv run pytest -m real_valkey tests/animator -q
UV_CACHE_DIR=/tmp/uv-cache uv run mypy \
  web shared autojudge arena rating aiassistant healthmonitor animator
UV_CACHE_DIR=/tmp/uv-cache uv run ruff check .
UV_CACHE_DIR=/tmp/uv-cache uv run ruff format --check .
UV_CACHE_DIR=/tmp/uv-cache uv run pytest
docker compose -f docker-compose.yml.sample config --quiet
docker buildx bake --print animator
```

Build and smoke-test the animator image when the environment permits it. Verify
health, assets, snapshot JSON, live SSE, reveal SSE, media, metrics, and
presence through the configured proxy.

## Completion criteria

The animator module is complete when all unified-plan behavior is implemented,
live and reveal rankings share one score function, authorization can't cross
scope, state survives failures and concurrent replicas, media and UI fallbacks
work, telemetry is bounded and secret-safe, deployment is documented, and the
full repository suite passes.

## Final handoff

Record the implementation summary, migration IDs, configuration additions,
deployment commands, known operational limits, and rollback procedure in the
release or pull-request description. No later feature phase is required for the
scope defined by `PLANO_UNIFICADO.md`.
