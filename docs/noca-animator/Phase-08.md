# Phase 08: Animate live scoreboard updates

This session connects the presentation page to SSE and adds deterministic visual
transitions for ranking and problem-cell changes. It also adds polling fallback
and recovery behavior for unreliable event connections.

## Source-plan coverage

This phase implements unified-plan section 3.3 and completes the live-scoreboard
milestone before reveal work begins.

## Dependencies

Complete [Phase 07](Phase-07.md) first so the frontend consumes a stable event
stream and snapshot API.

## Session scope

Limit this session to client-side state, refresh coalescing, animations,
connection status, accessibility, and frontend-focused tests.

## Required preflight

Complete these checks before editing code:

1. Read the Phase 06 renderer and all existing shared live-event scripts.
2. Inspect reusable CSS transitions and reduced-motion rules in shared and Web
   stylesheets.
3. Search PyPI and the asset registry for a maintained animation dependency.
   Record why the FLIP technique and CSS transitions are sufficient unless a
   real unmet requirement is found.
4. Define expected behavior for bursty verdicts, reconnects, stale responses,
   and browser tabs returning from the background.

## Client behavior

The live page must provide these behaviors:

- Open one `EventSource` after initial data loads.
- Coalesce bursts into one in-flight snapshot refresh and one queued refresh.
- Ignore an older snapshot that completes after a newer request.
- Animate rank movement and changed problem cells.
- Disable motion while preserving state highlights for reduced-motion users.
- Switch to configured polling fallback while SSE is unavailable.
- Display a non-blocking live, reconnecting, or polling status.

## Implementation tasks

Implement the client behavior in this order:

1. Split rendering, snapshot comparison, connection management, and animation
   into focused functions or files that stay within source-size guidance.
2. Key row state by `team_id` and cells by `(team_id, problem_id)` rather than
   display text.
3. Use a FLIP-style rank transition or an equally small vanilla-JavaScript
   approach that doesn't reorder focus unexpectedly.
4. Add distinct, time-limited classes for rank-up, rank-down, newly solved,
   changed attempts, pending, and first-balloon changes.
5. Honor server snapshot order as authoritative after every update.
6. Start polling after repeated SSE failures, stop polling after SSE recovers,
   and use `NOCA_ANIMATOR_POLL_FALLBACK_SECONDS` from page configuration.
7. Refresh immediately after `visibilitychange` returns the page to foreground.
8. Add browser-independent JavaScript tests if the repository has a current
   harness; otherwise add focused DOM-contract tests plus a documented manual
   verification script.
9. Update service or route documentation only if contracts changed.

## Validation

Run the focused backend/template regression checks:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/animator -q
uv run djlint animator/template/animator.html --check
UV_CACHE_DIR=/tmp/uv-cache uv run mypy animator shared
UV_CACHE_DIR=/tmp/uv-cache uv run ruff check animator tests/animator
```

In a browser, publish several verdict events quickly and confirm refresh
coalescing, correct final order, reconnect status, polling fallback, and reduced
motion.

## Completion criteria

This phase is complete when the live scoreboard converges on authoritative
snapshots under event bursts and disconnects, visual changes are clear but
accessible, and no event path can create overlapping unbounded requests.

## Next phase

Continue with [Phase 09](Phase-09.md), which defines the reveal session model
and builds its frozen submission universe.
