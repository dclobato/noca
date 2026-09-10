# NOCA Rating Module Architecture

This document describes the `rating/` module: the single-replica worker that
recomputes Arena problem difficulty, user scores, and affiliation ratings,
precomputes the statistics snapshots Arena reads, and awards gamification
badges. It covers what the worker owns, the badge ledger, and how difficulty is
stored, displayed, and seeded from the author's estimate. Read
[ARCHITECTURE.md](ARCHITECTURE.md) first for why these cycles run in exactly
one process.

Related references:
- [ARCHITECTURE.md](ARCHITECTURE.md) for the system overview and the boundary between modules
- [ARENA_BADGES.md](ARENA_BADGES.md) for the badge catalogue and rules
- [SHARED_SERVICES.md](SHARED_SERVICES.md) for `arena_badges`, `arena_problem_stats`, and the difficulty display helpers
- [ARCHITECTURE_ARENA.md](ARCHITECTURE_ARENA.md) for the Arena surfaces that read what this worker writes

## Responsibilities

The rating module is a standalone single-replica worker. It owns Arena problem,
user, and affiliation rating recomputation cycles. The affiliation cycle also
stores the sum of ranking-visible members' precomputed solved-problem counts.
The worker publishes scheduler metadata to Valkey so all Arena replicas can show
consistent footer and help-page timing.
It also runs an independent per-problem statistics loop (`run_problem_stats_loop`,
on its own `NOCA_RATING_STATS_INTERVAL` timer) that precomputes the JSON snapshots
read by the Arena problem statistics page
(`shared.services.arena_problem_stats`) — rebuilt in bounded per-problem batches
over a streamed submission cursor, so the worker's peak memory tracks the busiest
batch rather than the whole submission history — and a parallel per-user statistics loop
(`run_user_stats_loop`, sharing the same
`NOCA_RATING_STATS_INTERVAL` timer) that precomputes the verdict and language
distribution snapshots stored in `arena_user_statistics` and read by the Arena
public profile page. A third independent loop (`run_badge_assignment_loop`, on its
own `NOCA_RATING_BADGE_INTERVAL`
timer) awards Arena gamification badges from submission and catalogue state
(`shared.services.arena_badges`): each cycle runs a cheap incremental pass bounded by a
watermark, and periodically a full reconciliation pass re-evaluates all relevant
history so dynamic badges (CLEAN_CODE and ROCK_CRACKER) and late data stay correct.
`NOCA_RATING_COMPUTE_ON_STARTUP` makes that startup cycle a full reconciliation
rather than an incremental pass, on the same reasoning as the rating and
statistics loops it also applies to: a restart is normally a deploy, the one
moment when the award rules themselves may have changed and the ledger has to be
re-derived rather than merely extended. Without it a deploy waits up to
`NOCA_RATING_BADGE_RECONCILE_INTERVAL` for rules it has already shipped.

## Badges

Arena gamification adds the `arena_user_badges` table to the shared schema: the
set of badges each Arena user currently holds (`ArenaBadge` enum), when each row
was awarded (`awarded_at`), and what earned it (`submission_id`). A unique
`(user_id, badge)` constraint permits at most one current row. The rating
worker's badge-assignment loop (`shared.services.arena_badges`) owns every write;
the Arena ORM exposes the ledger through `ArenaUser.badges`, and each row's
awarding submission through `ArenaUserBadge.submission`. Streak badges are backed
by the `arena_users.current_streak` / `longest_streak` / `last_ac_date` columns
the loop recomputes, and the loop tracks its incremental watermark plus last full
reconciliation in the singleton `arena_badge_cycle_state` table.

The table is **derived, not accumulated**. `submission_id` is `NOT NULL` with an
`ON DELETE CASCADE` FK to `arena_submissions`, so no row can name nothing and
deleting a submission deletes the badges that named it. Every rule family returns
its complete qualifying set as a `(user, badge) -> submission` mapping, and
`shared.services.arena_badge_writer` turns that mapping into the minimum set of
statements that makes the table match it: insert what is missing, re-anchor a
surviving row whose canonical submission moved, revoke what nothing derives. Only
the full-reconcile pass may revoke or re-anchor — an incremental pass sees a
subset of history and would delete every badge it did not look at — and a pass
over an unchanged database issues no statement at all. Because it is now rewritten
rather than appended to, the table carries per-table autovacuum storage parameters
(migration `202609100002`) and an index on `submission_id` so the cascade does not
scan.

See [Which submission earned a badge](ARENA_BADGES.md#which-submission-earned-a-badge)
for what each rule names and how a badge with several possible witnesses picks
one, and [Badges are reconciled, not
accumulated](ARENA_BADGES.md#badges-are-reconciled-not-accumulated) for the
revocation triggers. Badge families cover per-submission recovery, solve streaks,
distinct solved-problem counts, distinct-language counts per problem, first-solver
and problem-set hand-in positions, latest on-time problem-set solves after
deadlines, non-AC bursts, unbroken distinct-AC runs, and dynamic low-solve-rate
problem solves.

ROCK_CRACKER derives its solve rate directly from authoritative data rather than
`arena_problem_ratings`: distinct raw submitters are attempted users, current
`arena_problem_solvers` rows are solved users, and both exclude the problem
owner. Incremental cycles evaluate complete populations for problems named by
either their AC or non-AC event batches and only award. Full cycles aggregate
the whole catalogue and derive the complete qualifying set, so users outside it
are revoked. A surviving row keeps its identity and `awarded_at` but is
re-anchored to the earliest-solved problem that qualifies now, since the anchor
is current evidence rather than a record of the original award; a revoked badge
that is earned again receives a fresh row and timestamp.

## Problem difficulty

Difficulty is computed over the users who **currently** hold an Accepted
submission. `rate_all_problems()` starts each problem by rewriting
`arena_problem_ratings.solved_users` and `total_tries_before_solve` from
`arena_problem_solvers` (`_recompute_stats_for_problem`), and the judge keeps
that table in step with the live verdicts on every Arena judgment that finishes
with a verdict or `FAILED`, so a
rejudge that withdraws an AC removes the solver and the next cycle recomputes
difficulty without them. Those two counters are therefore a batch-derived cache
of the solver rows rather than an independently maintained tally, and the judge
writes neither of them: an absent solver row does not mean a first solve, since
a withdrawn AC deletes the row and leaves the counters alone, so a later AC for
the same pair would credit the user twice. Between cycles the two counters
therefore lag the solver rows. The only surface reading them in the meantime is
the AC-rate percentage on the problem browse list; the displayed difficulty is
written solely by this cycle and does not move until it runs.


The rating worker's problem-difficulty cycle (`rate_all_problems()`) ends by
snapshotting a 20-bin histogram of the catalogue's current difficulty
distribution into the singleton `arena_rating_cycle_state` table, read by the
Arena `/help/rating` page to render a current-distribution chart without an
aggregate query at request time. A difficulty is stored for every problem, but
it is **displayed** — and counted in that histogram — only once the problem has
at least `MIN_ATTEMPTS_FOR_DISPLAY` (5) unique attempters
(`shared/services/arena_difficulty_display.py`). Below that the Bayesian prior
pins the value to the centre of the scale, so a bare `5.0` would mean both
"medium" and "unknown"; every Arena surface instead renders the shared
`DifficultyDisplay` value, which shows a dash labelled "Not enough data yet",
and the histogram records how many problems it left out. The gate keys on the
attempter count alone: an author's estimate is a prior, not evidence.

That estimate is `arena_problems.expected_difficulty` (nullable integer on the
internal 1–100 scale, entered in the editor as one of five worded anchors —
Introductory 15, Easy 30, Standard 50, Challenging 70, Hard 85 — and carried by
the problem package as an additive optional key). The rating worker uses it as
the **mean** of the Bayesian solve-rate prior, inverted through the display
pipeline so a never-attempted problem rates exactly at its declaration; the
prior's weight is unchanged, so evidence overrides the estimate at the same
rate it overrides the flat 0.5 prior, and above the display threshold only the
measured value is ever shown. Below it, a problem with an estimate renders as
`7.0?` whose accessible label names it an estimate, so a reader can always tell
"we measured this" from "the author thinks this". Low-churn reference data on
the server-wide autovacuum defaults.

The anchors are the editor's *vocabulary*, not the column's constraint: an
imported package may carry any value in `[1, 100]`. Such a value gets an
"Imported value" option of its own in the editor's select, and the save parser
accepts exactly the value already stored alongside the anchors. Without both
halves the browser would fall back to the first option — "No estimate" — and a
Save of an unrelated field would silently discard the author's estimate.
