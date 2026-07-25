# Phase 09: Model reveal sessions and build frozen projections

This session defines the minimal, reversible reveal state and constructs the
initial frozen universe for a global or site-scoped ceremony. It computes views
from the shared scoreboard function but does not advance reveal steps yet.

## Source-plan coverage

This phase implements unified-plan section 5.1 and the initialization and
projection portions of section 5.2.

## Dependencies

Complete [Phase 08](Phase-08.md) first. The live scoreboard milestone must
remain stable before reveal state is introduced.

## Session scope

Limit this session to Pydantic domain models, database loading, deterministic
frozen-universe construction, derived views, and pure tests.

## Required preflight

Complete these checks before editing code:

1. Read the reveal definitions in `PLANO_UNIFICADO.md` again, along with the
   shared scoreboard projection and animator contest-feed records.
2. Inspect the exact nullable behavior of `submissions.timestamp_seconds` and
   the current scoreboard convention for legacy rows.
3. Search PyPI for event-sourcing or state-machine libraries. Record why an
   immutable Pydantic state plus a short ordered ID log is sufficient here.
4. Build a small ceremony fixture on paper that includes ties, multiple pending
   runs, PE/CE configuration, a solved problem with later runs, and two sites.

## State contract

Create `animator/models/reveal_session.py` with these concepts:

- `RevealSessionState` stores contest ID, optional site ID and name, phase,
  optional medal cutoffs, ordered `frozen_submission_ids`, `reveal_log`, and
  optional focused team ID.
- `TeamRevealView` stores current rank, score totals, optional medal, site name,
  and problem views.
- `ProblemRevealView` stores solved state, penalizing attempts, solve time,
  pending-frozen state, and first-solver state.
- State serialization is versioned so later store migrations can reject or
  upgrade incompatible payloads safely.

## Implementation tasks

Implement initialization in this order:

1. Add immutable Pydantic models with field validation and no ellipsis defaults.
2. Create a reveal data loader that reuses or extracts common Core query helpers
   from `contest_feed_service.py` without duplicating SQL.
3. Filter site ceremonies by explicit `users.site_id`; global ceremonies include
   every contest team.
4. Define frozen submissions with the exact scoreboard predicate
   `timestamp_seconds > freeze_at_seconds` and deterministic order
   `(timestamp_seconds, created_at, id)`.
5. Initialize `reveal_log=[]`, `phase="idle"`, and the appropriate medal
   cutoffs. Validate that the requested site belongs to the contest.
6. Derive current standings by calling `compute_icpc()` over pre-freeze
   submissions plus IDs in `reveal_log`, with unrevealed submissions omitted.
7. Derive `pending_frozen` independently for each team/problem pair.
8. Add pure tests for serialization, site filtering, global scope, ordering,
   legacy timestamp handling, derived views, and score equality with the shared
   projection.
9. Document the new models and loader in `animator/docs/SERVICES.md`.

## Validation

Run the focused reveal-model checks:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest \
  tests/animator/test_reveal_models.py \
  tests/animator/test_reveal_projection.py -q
UV_CACHE_DIR=/tmp/uv-cache uv run mypy animator shared
UV_CACHE_DIR=/tmp/uv-cache uv run ruff check animator tests/animator
UV_CACHE_DIR=/tmp/uv-cache uv run ruff format --check animator tests/animator
```

Assert in tests that no derived ranking, attempt count, or penalty is serialized
as mutable session state.

## Completion criteria

This phase is complete when global and site-scoped sessions initialize
deterministically, state contains only the reversible reveal log plus identity
metadata, and every derived score matches `compute_icpc()`.

## Next phase

Continue with [Phase 10](Phase-10.md), which implements bottom-up reveal steps,
backtracking, reset, jump-to-team, and medal assignment.
