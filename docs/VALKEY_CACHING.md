# Valkey caching in NOCA

This document catalogues every Valkey entry NOCA writes **to avoid an expensive
computation**, and names the routine that writes it. It exists because Valkey
carries several kinds of state — queues, locks, presence, rate limits, caches —
and only the last kind is safe to drop, shorten, lengthen, or recompute on a
whim. If you are tuning a hot page or hunting a stale read, start here.

Three questions decide whether an entry belongs on this list:

- Would recomputing the value produce the same answer? A cache says yes; a lock
  or a rate-limit counter says no.
- Is losing the entry harmless? Every cache below falls back to computing the
  value again, and every one of them treats a Valkey failure as a miss.
- Does the entry exist to make something faster, rather than correct?

Entries that fail those tests are listed in
[What is not a cache](#what-is-not-a-cache), so this catalogue is not mistaken
for an inventory of all Valkey writes.

Related references:

- [ARCHITECTURE.md](ARCHITECTURE.md) for the module boundary Valkey sits on
- [SHARED_SERVICES.md](SHARED_SERVICES.md) for the shared service contracts
- [CONFIG.md](CONFIG.md) for the environment variables named below

## Cache inventory at a glance

Each row is one Valkey key family. The TTL column gives the lifetime the writing
routine sets; "none" means the entry lives until something deletes it.

| Cache | Key | TTL | Written by |
| --- | --- | --- | --- |
| Scoreboard snapshot, admin view | `noca:scoreboard:<contest_id>:full` | 5 s | `web/services/scoreboard/service.py` |
| Scoreboard snapshot, public view | `noca:scoreboard:<contest_id>:public` | 180 s | `web/services/scoreboard/service.py` |
| Scoreboard snapshot, frozen | `noca:scoreboard:<contest_id>:frozen` | none | `web/services/scoreboard/service.py` |
| Scoreboard snapshot, final | `noca:scoreboard:<contest_id>:final` | none | `web/services/scoreboard/service.py` |
| Scoreboard display decoration | `noca:scoreboard:display:<contest_id>` | 5 s | `web/services/scoreboard_display_cache.py` |
| Contest report aggregate | `noca:web:contest-report:v1:<contest_id>:<generation>:<site-or-all>` | 600 s | `web/services/contest_report_cache.py` |
| Reverse-geocoded cell | `noca:geocode:cache:<lat>:<lon>` | `NOCA_ARENA_GEOCODE_CACHE_TTL_SECONDS` (86400 s) | `arena/services/geocode_service.py` |
| AI batch turnaround statistics | `ai:batch:turnaround:stats` | none | `aiassistant/turnaround_stats.py` |
| Rating cycle metadata | `arena:rating:next_update`, `arena:rating:interval_text`, `arena:rating:affiliation_factor` | `NOCA_RATING_INTERVAL` + 600 s | `rating/worker.py` |
| Uptime slot counters | `noca:healthmon:stats:<worker_class>:<slot>` | (`NOCA_HEALTHMON_RETENTION_DAYS` + 1) days | `healthmonitor/services/uptime_stats.py` |

## Scoreboard snapshots

The ICPC standings projection is the most expensive computation in NOCA: it
loads every team, problem, submission, and judgment for a contest and replays
them into a ranking. During a live contest it is also the most requested page
there is, so it is cached in four variants that differ in who may see what.

`ScoreboardService.get_cached_or_compute` in `web/services/scoreboard/service.py`
owns the first three, and `get_or_compute_final` owns the fourth. The keys come
from `shared/services/scoreboard_cache.py`, which is shared so the purge path
and the invalidation path build the same strings the writer does.

- **`:full`** holds the admin and judge view, where nothing is hidden. Its TTL
  is 5 seconds, because a judge acting on a verdict expects to see the effect
  almost at once.
- **`:public`** holds the team and public view. Its TTL is 180 seconds, since
  the public page tolerates being a little behind and the longer window absorbs
  far more of the poll traffic.
- **`:frozen`** holds the public view captured when the scoreboard first froze,
  and carries **no TTL**. Ordinary invalidation deliberately does not touch it:
  the whole point of the freeze is that new verdicts must not move the standings
  the audience sees. It survives until the admin releases the final standings or
  changes `stop_updating_scoreboard`.
- **`:final`** holds the permanently released final standings, also with no TTL.
  Writing it deletes the frozen entry, because the freeze window has ended.

Invalidation is explicit. `invalidate_scoreboard_cache` in
`shared/services/scoreboard_cache.py` deletes the `:full` and `:public` entries
only. Report-aware callers use `invalidate_contest_result_caches`, which also
rotates the contest report generation, wherever a verdict or a limit changes
both projections:

- `web/routes/contest_submissions_review.py`
- `web/routes/contest_runs_review.py`
- `web/routes/contest_admin_problem_limits.py`

<!-- prettier-ignore -->
> [!IMPORTANT]
> The `:frozen` and `:final` entries have no expiry, so a bug that writes a
> wrong value there persists until something deletes it. Contest purge
> (`shared/services/valkey_service/contest_purge.py`) removes all four keys.

## Scoreboard display decoration

The snapshot is only half of what the scoreboard page renders. The page also
needs each team's display name, the site that team competes at, and the
contest's site list for the filter control. Those two queries — a team scan with
an eager site load, plus a site select — used to run on *every* hit, including
hits that the snapshot cache already served.

`get_scoreboard_display_data` in `web/services/scoreboard_display_cache.py`
caches them together under `noca:scoreboard:display:<contest_id>` for 5 seconds.
With both caches warm, a scoreboard render does no database work at all.

Two design points are worth keeping in mind before you change it:

- The data lives in its own entry rather than as extra fields on the snapshot,
  because `ScoreboardSnapshot` is the shared projection the animator also reads
  (`shared/services/scoreboard_projection.py`). Widening it for one page's
  template would change a contract two modules deploy against.
- The TTL matches the tightest scoreboard TTL rather than the public one, so a
  roster edit appears as fast as the standings it decorates.

Staleness here is harmless by construction: the template falls back to the
standing's own `team_fullname` for a name it cannot find, and a team missing
from the site map is a team the snapshot has not published yet either.

Unlike the four snapshot keys, this entry is **not** in the contest purge set in
`shared/services/valkey_service/contest_purge.py`. Its 5-second TTL makes that
harmless in practice, since a purged contest's entry expires almost at once.

## Contest report aggregates

The reports page aggregates every contest submission into highlights,
cross-tables, distributions, and chart series. That work grows with the contest,
while the resulting presentation data is independent of the admin or judge who
opened the page.

`get_contest_report_page_data` in
`web/services/contest_report_cache.py` caches the JSON-compatible report and
static chart data for 600 seconds. The request-bound actor and template data,
site validation, and the running contest's live elapsed-minute marker remain
outside the entry. The optional site filter gets its own cache scope.

The data key includes the payload version and a per-contest generation token:
`noca:web:contest-report:v1:<contest_id>:<generation>:<site-or-all>`. A report-
relevant commit rotates
`noca:web:contest-report:generation:<contest_id>` instead of scanning and
deleting every site-scoped data key. A computation already in flight may finish
under the old generation, but no later request can read it. Old data entries
expire naturally after 600 seconds.

Generation rotation follows committed changes that affect the projection:

- new submissions;
- verdict finalization, confirmation, override, and rejudge;
- problem creation, import, editing, removal, and reordering;
- team creation, batch import, removal, display-name change, and site change;
- site, contest allowed-language selection, and report-relevant scoring changes;
- manual contest start and end-time changes.

The TTL is a recovery ceiling rather than the normal freshness policy. After a
successful generation rotation, the next request computes fresh data. If an
invalidation is missed during a Valkey outage, the old entry can survive only
until its 600-second expiry. Reads, writes, and generation rotation are
best-effort; an unavailable or malformed entry falls back to PostgreSQL.

`SingleFlightCache` coalesces simultaneous misses for the same generation and
site inside one Web process. Separate replicas may each rebuild once after an
invalidation. Contest purge removes the generation key; expired data keys need
no wildcard scan.

## Reverse-geocoded cells

`_write_cache` in `arena/services/geocode_service.py` stores the result of a
reverse-geocode lookup under a key built from the coordinates rounded to three
decimal places, so nearby users share one entry. This is the one cache in NOCA
that avoids an **external HTTP call** rather than a database query, which makes
it the most valuable entry per byte: the provider is rate-limited and slow, and
NOCA pairs the cache with a pacing gate (`noca:geocode:pace`) and a budget
counter (`noca:geocode:budget`).

Negative results are cached too. A coordinate the provider cannot resolve
resolves no better on the next attempt, and caching the failure is what stops a
bad coordinate from burning the budget repeatedly.

A cache write never fails a detection: the writer swallows connection, timeout,
and Valkey errors and logs a warning.

## AI batch turnaround statistics

Arena shows how long AI reviews are currently taking. Computing that means
aggregating over recent review rows, which is not work a page render should do.

`refresh_batch_turnaround_stats` in `aiassistant/turnaround_stats.py` runs the
aggregation inside the AI assistant worker and publishes the result to
`ai:batch:turnaround:stats`. The entry has no TTL because the worker refreshes
it every cycle and deletes it outright when no qualifying reviews exist, so an
absent key means "nothing to show" rather than "expired." Arena reads it through
`arena/services/ai_turnaround_stats_service.py` and never runs the aggregation
itself.

## Rating cycle metadata

The Arena footer shows when ratings next update, how often they update, and the
affiliation factor in force. Deriving those from the rating tables on every page
render would be wasteful, and only the single-replica rating worker knows them
authoritatively.

`_make_next_update_callback` and `_publish_rating_metadata` in
`rating/worker.py` write three keys — `arena:rating:next_update`,
`arena:rating:interval_text`, and `arena:rating:affiliation_factor` — each with
a TTL of the rating interval plus 600 seconds. The generous margin means a
worker that is late by one cycle does not blank the footer, while a worker that
is gone for good lets the values lapse instead of showing a stale promise
forever. The worker deletes all three on clean shutdown.

## Uptime slot counters

The health monitor dashboard renders a 30-day heatmap. Storing one row per probe
and aggregating them per request would grow without bound and cost more every
day, so the prober aggregates as it goes.

`record_probe` in `healthmonitor/services/uptime_stats.py` increments an `up`
and a `total` field in a hash keyed by service and 12-hour slot
(`SLOT_SECONDS = 43_200`), using a Lua script so the increment and the TTL are
applied atomically. The TTL is the retention window plus one day, so expiry
alone keeps the key space bounded even if the reaper never runs.

Reading the heatmap is then one pipelined `hmget_many` over the
`SLOTS_PER_WINDOW` (60) precomputed slots per service, rather than a scan of
probe history.

This is a rollup cache rather than a pure one: the underlying probes are not
retained, so a lost slot is lost history, not a recomputable value. It is listed
here because its entire purpose is to make the dashboard cheap.

## Caches that are not in Valkey

Several NOCA caches serve the same goal without touching Valkey. Knowing they
exist prevents a fruitless search for a key that was never written.

- **`shared/services/single_flight_cache.py`** is a process-local keyed TTL
  cache with per-key single-flight: concurrent misses on one key share a single
  build. It is deliberately process-local, so a multi-replica deployment builds
  once per replica, which is still bounded and needs no shared state. Its users
  are `healthmonitor/services/uptime_cache.py` (the `/uptime.json` payload),
  `animator/services/feed_cache.py` (scoreboard snapshot, contest meta, and
  reveal dataset, with TTLs from `NOCA_ANIMATOR_SNAPSHOT_CACHE_SECONDS`,
  `NOCA_ANIMATOR_SNAPSHOT_CACHE_ENDED_SECONDS`, and
  `NOCA_ANIMATOR_META_CACHE_SECONDS`), and
  `arena/services/required_announcement_cache.py` (a 30-second answer to
  "does any required announcement exist").
- **`shared/services/problem_export_cache.py`** caches built problem packages on
  the filesystem, validated against a generation-stamped sidecar rather than a
  clock.
- **`arena/main.py`** keeps the online-user count in `app.state`, refreshed by
  `_online_users_count_poller` on the presence heartbeat cadence, because
  aggregating the presence sorted set on every footer render is too costly. The
  poller retains the last valid count during a Valkey outage so the footer never
  shows a misleading zero.

## What is not a cache

Every other Valkey write in NOCA exists for correctness, coordination, or
protection. Do not shorten, lengthen, or drop these entries as if they were
cache tuning knobs.

<details>
<summary>Valkey writes that are not caches</summary>

- **Job queues and job hashes** — `shared/services/valkey_service/queue_ops.py`
  for the judge, AI review, and mail pipelines.
- **Distributed locks** — `shared/services/lock_service.py` and
  `autojudge/dispatch.py`.
- **Token revocation** — `shared/services/token_revocation.py`.
- **Worker presence, heartbeats, and signed pause commands** —
  `shared/services/valkey_service/worker_presence.py` and
  `shared/services/valkey_service/worker_commands.py`.
- **User and contest presence markers** — `shared/services/user_presence.py`,
  `web/services/contest_presence.py`.
- **Rate limits, cooldowns, and budgets** —
  `shared/services/auth_rate_limit.py`,
  `shared/services/request_rate_limit.py`,
  `shared/services/health_rate_limit.py`,
  `shared/services/email_budget.py`,
  `shared/services/rejudge_cooldown.py`.
- **SSE connection leases** — `shared/services/sse_connection_limit.py`.
- **Reveal ceremony state and projection pub/sub** —
  `shared/services/valkey_service/revelation.py`.

</details>

## Conventions a new cache must follow

Follow these rules when you add a Valkey cache, so the next reader can trust
this catalogue.

1. Build the key through a named helper, not an inline f-string, and keep the
   helper next to the reader and the writer that share it.
2. Treat every read and write as best-effort. Catch recoverable Valkey errors,
   log a warning, and fall back to computing the value. A cache must never be
   able to fail a request.
3. Give the entry a TTL unless you can name what deletes it. If you choose no
   TTL, say why in the module docstring, as the frozen and final scoreboards do.
4. Add the key to `shared/services/valkey_service/contest_purge.py` if it is
   scoped to a contest, so removing a contest removes its runtime state.
5. Add a row to the [cache inventory](#cache-inventory-at-a-glance) and a
   section here, and document any new configuration variable in
   [CONFIG.md](CONFIG.md) and the matching environment template.
