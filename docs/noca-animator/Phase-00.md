# Phase 00: Extract the shared scoreboard projection

This session creates the stable scoring boundary that every later animator
phase uses. At completion, Web behavior remains unchanged, but scoreboard DTOs,
serialization, and ICPC calculation live in `shared/` without importing `web`.

## Source-plan coverage

This phase implements sections 1.1 through 1.4 of the
[unified implementation plan](PLANO_UNIFICADO.md). It also establishes the
architectural rule that the animator cannot import Web models or services.

## Dependencies

This is the first implementation phase. Start from a clean understanding of the
current worktree, and preserve unrelated changes.

## Session scope

Keep the session limited to the shared scoring extraction and the Web adapter.
Do not create the `animator/` package, add routes, or change score semantics.

## Required preflight

Complete these checks before editing code:

1. Read `web/services/scoreboard/models.py`, `computation.py`,
   `serialization.py`, and `service.py`.
2. Read `tests/web/test_scoreboard.py` and all direct imports of the current
   scoreboard package.
3. Search PyPI for a maintained scoreboard or ICPC ranking library. Record that
   decision in the session notes. Prefer the existing NOCA implementation
   because it carries contest-specific `accept_pe`, `ce_adds_penalty`, freeze,
   and first-balloon rules.
4. Run the focused scoreboard tests once to establish a baseline.

## Implementation tasks

Implement the extraction in this order:

1. Create `shared/services/scoreboard_projection.py` with the NOCA copyright
   header and Google-style docstrings.
2. Move `ProblemResult`, `TeamStanding`, `ScoreboardSnapshot`,
   `ordinal_to_label`, `compute_icpc`, and snapshot serialization into the new
   shared module.
3. Define structural input protocols or immutable value records in `shared/` so
   `compute_icpc` doesn't type-import `web.models`. Use `Sequence` and `Mapping`
   inputs where covariance is needed.
4. Preserve the existing sort order, tied-rank behavior, first-balloon logic,
   freeze predicate (`timestamp_seconds > freeze_at_seconds`), pending-cell
   behavior, and verdict rules exactly.
5. Convert `web/services/scoreboard/` into a query, cache, and adaptation layer.
   Re-export old public imports temporarily if repository callers still use
   them, but make `shared` the only implementation owner.
6. Add `tests/shared/test_scoreboard_projection.py` with pure unit tests for
   normal scoring, freeze visibility, ties, pending cells, PE handling, CE
   penalty handling, and deterministic submission ordering.
7. Update Web tests only where imports moved; don't weaken existing assertions.
8. Document the new shared service in `docs/SHARED_SERVICES.md`.

## Validation

Run focused checks that prove both the new boundary and regression safety:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest \
  tests/shared/test_scoreboard_projection.py tests/web/test_scoreboard.py -q
UV_CACHE_DIR=/tmp/uv-cache uv run mypy shared web
UV_CACHE_DIR=/tmp/uv-cache uv run ruff check shared web tests
UV_CACHE_DIR=/tmp/uv-cache uv run ruff format --check shared web tests
```

Also run `rg "from web" shared/services/scoreboard_projection.py`; it must
return no matches.

## Completion criteria

This phase is complete when one shared implementation produces every scoreboard
result, Web behavior and cache serialization remain compatible, focused tests
pass, and `shared/` has no dependency on `web/`.

## Next phase

Continue with [Phase 01](Phase-01.md), which adds the animator enablement, site
medal settings, and operator-secret schema.
