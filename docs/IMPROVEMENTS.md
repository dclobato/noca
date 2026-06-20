# Future improvements

This document tracks improvements that are useful but aren't required for the
current implementation.

## Scale Arena submission heatmap aggregation

The Arena rating cycle computes user submission heatmaps from submissions in
the previous 52 weeks. The bulk snapshot approach is suitable for thousands of
users at ordinary submission volumes, but its cost grows with the number of
submissions in the time window.

If the yearly submission volume grows enough to make the periodic aggregation
expensive:

- Store incremental daily submission counts per user instead of rescanning the
  complete 52-week window during every rating cycle.
- Update the daily count when a submission is created.
- Build each user's heatmap snapshot from the compact daily-count table.
- Keep snapshot replacement and upserts batched in a single transaction.
- Measure the current and proposed queries with
  `EXPLAIN (ANALYZE, BUFFERS)` against production-like data before migrating.

Consider this improvement when heatmap computation materially increases the
rating-cycle duration, database I/O, or lock contention.
