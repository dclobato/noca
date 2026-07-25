# Phase 10: Implement the reveal state machine

This session implements the pure bottom-up reveal algorithm. Every command
returns a new reversible state and newly derived views, while the official
shared scoreboard function remains the only ranking implementation.

## Source-plan coverage

This phase completes unified-plan section 5.2, including step, back, reset,
jump-to-team, site filtering, and medal assignment.

## Dependencies

Complete [Phase 09](Phase-09.md) first so session state and projection inputs
are stable.

## Session scope

Limit this session to pure reveal-engine behavior and exhaustive unit tests. Do
not add Valkey, HTTP routes, or UI.

## Required preflight

Complete these checks before editing code:

1. Re-read the unified plan's step algorithm and the Phase 09 ceremony fixture.
2. Inspect shared scoreboard tie behavior and deterministic team ordering.
3. Search PyPI for state-machine libraries. Record why explicit pure functions
   are easier to audit and reverse for this small transition graph.
4. Define command behavior for idle, revealing, and done phases before coding.

## Transition contract

Create `animator/services/reveal_engine.py` with pure operations that return a
new state plus derived views:

- `start()` establishes focus and changes idle to revealing or done.
- `step()` reveals exactly one relevant frozen submission.
- `back()` removes exactly one ID from `reveal_log`.
- `reset()` empties the log and returns to idle.
- `jump_team()` repeatedly uses the same `step()` transition until the target
  becomes focused.

## Implementation tasks

Implement the state machine in this order:

1. Identify relevant pending runs as those for currently unsolved problems with
   IDs in the frozen universe but outside the reveal log.
2. Select the bottom-most team in the current standings that has a relevant run.
   Use standings order, not rank number alone, to resolve tied ranks.
3. Within that team, select the first problem by label and its oldest relevant
   submission by `(timestamp_seconds, created_at, id)`.
4. Append one ID, rebuild standings through `compute_icpc()`, and recalculate
   focus and phase.
5. Ignore later frozen submissions for a problem after that problem becomes
   solved.
6. Implement exact `back()` with one `pop`; recompute phase and focus from the
   shortened log rather than storing inverse score mutations.
7. Make `reset()` deterministic and idempotent.
8. Validate jump targets belong to the session scope. Bound the loop by the
   frozen-universe length and return a clear domain error when the target can
   never become focused.
9. Assign medals from the current scoped rank using inclusive gold, silver, and
   bronze cutoffs.
10. Add property-style or parameterized tests for transition invariants,
    including `back(step(state)) == state` for all non-terminal fixture states.
11. Add a golden ceremony test that compares every intermediate ranking with a
    direct `compute_icpc()` call over the same revealed set.
12. Update `animator/docs/SERVICES.md`.

## Validation

Run the focused state-machine checks:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest \
  tests/animator/test_reveal_engine.py -q
UV_CACHE_DIR=/tmp/uv-cache uv run mypy animator shared
UV_CACHE_DIR=/tmp/uv-cache uv run ruff check animator tests/animator
UV_CACHE_DIR=/tmp/uv-cache uv run ruff format --check animator tests/animator
```

The test matrix must include PE acceptance, CE penalty, tied rankings, a first
solver, a site ceremony, a global ceremony, and a problem solved before freeze.

## Completion criteria

This phase is complete when every command is deterministic and reversible,
bottom-up focus matches the specified ceremony behavior, irrelevant runs are
skipped, medals follow scoped rank, and no score logic is duplicated.

## Next phase

Continue with [Phase 11](Phase-11.md), which persists transitions and publishes
derived reveal projections through Valkey.
