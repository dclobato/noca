# Phase 20: Add animator observability

This session adds bounded Prometheus metrics and structured logs for the live
scoreboard and reveal runtime. It avoids high-cardinality labels and guarantees
that credentials, database secrets, and media never enter telemetry.

## Source-plan coverage

This phase implements unified-plan section 7.2.

## Dependencies

Complete [Phase 19](Phase-19.md) first so metrics describe the final recovery
and idempotency behavior.

## Session scope

Limit this session to metrics, logs, configuration, telemetry tests, and
observability documentation.

## Required preflight

Complete these checks before editing code:

1. Read `autojudge/metrics.py`, shared logging helpers, request-ID handling, and
   animator security logs.
2. Check PyPI for the current `prometheus-client` release and compare it with
   the repository pin before adding a direct animator dependency.
3. Define metric cardinality rules. Contest IDs, team IDs, site IDs, slugs,
   secrets, and client addresses must not become labels.
4. List every secret-bearing field and binary payload that log redaction tests
   must cover.

## Metrics contract

Add bounded metrics for these signals:

- Loaded or requested contests without contest-ID labels.
- Connected live and reveal SSE clients.
- Verdict and reveal events received, coalesced, dropped, and published.
- Active reveal sessions by global versus site scope only.
- Reveal command outcomes by operation and bounded result code.
- Snapshot, command, and store latency histograms.
- Valkey reconnects and malformed stored-state failures.

## Implementation tasks

Implement observability in this order:

1. Add `animator/metrics.py` following repository Prometheus conventions and
   expose metrics only when a validated animator setting enables them.
2. Instrument service boundaries rather than individual rows or teams.
3. Ensure SSE gauges decrement in `finally` for disconnects and cancellations.
4. Add structured logs for startup, contest load, SSE lifecycle, state mutation,
   recovery, and command authorization outcome.
5. Redact credentials, digests, bearer headers, database URLs, base64 fields,
   and media bytes from every log path and exception representation.
6. Add metric-value tests, bounded-label tests, disconnect gauge tests, and log
   redaction tests for both success and failure paths.
7. Add settings to `.env.full`, compose wiring, and `docs/CONFIG.md`.
8. Document metrics names, meanings, and label sets in animator service docs.

## Validation

Run focused telemetry checks:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest \
  tests/animator/test_metrics.py \
  tests/animator/test_logging.py -q
UV_CACHE_DIR=/tmp/uv-cache uv run mypy animator shared
UV_CACHE_DIR=/tmp/uv-cache uv run ruff check animator tests/animator
```

Scrape the metrics endpoint during a short live and reveal session. Confirm
gauges return to zero after clients leave and labels remain bounded.

## Completion criteria

This phase is complete when required operations are observable, metric labels
can't grow with contest or user data, SSE gauges don't leak, telemetry is
disabled or exposed according to configuration, and secret-redaction tests pass.

## Next phase

Continue with [Phase 21](Phase-21.md), which proves the complete animator module
through end-to-end ceremonies and final repository validation.
