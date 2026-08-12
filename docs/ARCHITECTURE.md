# NOCA Architecture Overview

This document summarizes the main system design and application architecture of NOCA.
For detailed runtime behavior, module internals, coordination flows, security, and
operational consequences, see [ARCHITECTURE_RUNTIME.md](ARCHITECTURE_RUNTIME.md).

Related references:
- [ARCHITECTURE_RUNTIME.md](ARCHITECTURE_RUNTIME.md) for detailed runtime architecture and operational constraints
- [autojudge/docs/AUTOJUDGE_INFRA.md](../autojudge/docs/AUTOJUDGE_INFRA.md) for worker isolation, queue protocol, and container execution details
- [DATA_FLOW_FROM_SUBMISSION_TO_VERDICT.md](DATA_FLOW_FROM_SUBMISSION_TO_VERDICT.md) for the submission lifecycle
- [CONTEST_BACKUP_FORMAT.md](CONTEST_BACKUP_FORMAT.md) for the contest
  backup/restore ZIP format and fidelity notes
- [Interactive validator guide](custom-validator/INTERACTIVE_VALIDATOR.md) for
  authoring, exit codes, and applicable limits
- [Output checker validator rationale](custom-validator/OUTPUT_CHECKER_VALIDATOR.md)
  for the planned non-interactive output-checker strategy
- [FASTAPI_FLASH.md](FASTAPI_FLASH.md) for the flash-message pattern used in the web and arena modules
- [web/docs/ROUTES.md](../web/docs/ROUTES.md) and [web/docs/SERVICES.md](../web/docs/SERVICES.md) for web-layer responsibilities
- [SHARED_SERVICES.md](SHARED_SERVICES.md) for cross-module shared services (email, network, image, Valkey, locks)

## Table of Contents

- [1. High-level design](#1-high-level-design)
- [2. Main architectural boundary](#2-main-architectural-boundary)
- [3. uv workspace and package layout](#3-uv-workspace-and-package-layout)
- [4. Data model and schema ownership](#4-data-model-and-schema-ownership)
- [5. Module summaries](#5-module-summaries)
- [6. Summary](#6-summary)

## 1. High-level design

NOCA is split into eight main runtime modules:

- `web/`: the FastAPI application that serves HTML pages, handles authentication, enforces authorization, manages contests/problems/users, and creates judging work
- `autojudge/`: the asynchronous judge worker that consumes queued judgments, compiles and runs submissions inside containers, and writes results back
- `arena/`: the FastAPI application that serves the Arena platform, with its own user identity domain, OTP-protected accounts, and login history
- `rating/`: the single-replica Arena rating worker that periodically recomputes problem difficulty, user scores, and affiliation ratings, and publishes the next-cycle timestamp to Valkey for the Arena footer
- `aiassistant/`: the Arena AI review worker that dequeues AI review jobs,
  uses the OpenAI Responses API for user-key reviews, uses the OpenAI Batch API
  for platform-key reviews, and stores feedback in the database
- `healthmonitor/`: the public health-monitoring FastAPI server that probes the
  other modules through their Valkey worker-presence keys and renders a 30-day
  uptime heatmap dashboard with live per-service statuses
- `animator/`: the standalone FastAPI presentation runtime (default port 8003) that reads
  PostgreSQL and Valkey directly to serve a live scoreboard and post-freeze reveal
  ceremony; it reuses the shared scoreboard projection and never imports `web`
- `landingpage/`: a standalone Caddy-served entry point (default internal port
  8080) that presents the NOCA environment and links to the configured Web,
  Arena, Animator, and Health Monitor deployments without using Python,
  PostgreSQL, or Valkey

Those modules are intentionally separated. The web app owns contest-admin
workflows; the autojudge owns untrusted-code execution and verdict production;
the arena owns public participant registration and authentication; the rating
worker owns periodic rating recomputation cycles so they run exactly once
regardless of how many Arena replicas are deployed; the aiassistant worker owns
external AI provider calls and cost recording; the health monitor owns
availability observation and uptime history without participating in any
business workflow; the animator owns public scoreboard and reveal presentation,
reading the shared schema directly through SQLAlchemy Core; the landing page
owns only public navigation into an environment and has no data-plane access.

Between them there is one important shared module:

- `shared/`: cross-module source of truth for database schema, enums, queue payloads, language registry helpers, logging, and shared services

## 2. Main architectural boundary

`web`, `autojudge`, and `arena` do not call each other directly through Python imports or HTTP APIs. They collaborate through infrastructure boundaries:

- PostgreSQL
- Valkey
- Shared filesystem directories

This keeps the judge isolated from the web process and makes each side independently scalable and easier to harden.
Durable Arena notifications follow this same boundary: worker-side producers
insert rows in PostgreSQL through shared schema and service helpers, and the
Arena HTTP process owns user-facing display and read state.

User online-presence (the green dot on avatars) lives entirely on the Valkey
side of the boundary: the shared `user_presence` service writes a short-TTL live
key per user and reads presence in batch, best-effort with no database writes, so
a Valkey outage simply shows everyone as offline.

Authenticated worker pause/resume follows the boundary too. The Arena admin can
pause or resume the queue-consuming workers (autojudge, aiassistant; rating is
always-on) from the dashboard. PostgreSQL is the authoritative, monotonic source
of truth (`arena_worker_pause_state`); the Arena route commits the pause-state
bump and an `arena_worker_command_audit` row before publishing a signed
`HMAC-SHA256` command over Valkey. That command is only an authenticated *nudge*:
each worker derives its paused state solely from committed PG rows and treats the
command as a trigger to reconcile now, so a replayed, forged, raced, or
undelivered command can never advance state on its own (workers reconcile from PG
every poll and at startup). See [SHARED_SERVICES.md](SHARED_SERVICES.md) for the
trust and ordering model.

Runtime isolation:

- **web**: FastAPI server with async database and Valkey connections, serving HTTP requests (default port 8000)
- **autojudge**: Independent async worker process with fixed-width concurrency, processing judge jobs
- **arena**: FastAPI server with async database and Valkey connections, serving the Arena platform (default port 8001)
- **rating**: Independent single-replica async worker running the Arena rating recomputation loops
- **aiassistant**: Independent async worker dequeuing AI review jobs from
  Valkey, calling the OpenAI Responses API for online user-key reviews, and
  polling OpenAI Batch API jobs for platform-key reviews
- **healthmonitor**: FastAPI server with a Valkey connection only (no database),
  serving the public uptime dashboard (default port 8002)
- **animator**: FastAPI server with async database (SQLAlchemy Core) and Valkey
  connections, serving the public live scoreboard and reveal presentation (default port 8003).
  Publishes a presence-only `WorkerClass.ANIMATOR` heartbeat from its lifespan, so
  the health monitor shows it as its own service
- **landingpage**: Caddy static-file and template server with no database,
  Valkey, Python, or Node.js runtime (default internal port 8080)
- No Python imports between modules; all communication goes through infrastructure

## 3. uv workspace and package layout

The repository root is a non-package uv workspace defined by `pyproject.toml`.
It provides shared development tooling and resolves these workspace packages:

- `noca-shared` from `shared/`
- `noca-web` from `web/`
- `noca-arena` from `arena/`
- `noca-autojudge` from `autojudge/`
- `noca-rating` from `rating/`
- `noca-aiassistant` from `aiassistant/`
- `noca-healthmonitor` from `healthmonitor/`
- `noca-animator` from `animator/`

Each runtime module has its own `pyproject.toml`, build metadata, dependency list, and console script. The runtime entrypoints are:

- `uv run noca-web`
- `uv run noca-arena`
- `uv run noca-autojudge`
- `uv run noca-rating`
- `uv run noca-aiassistant`
- `uv run noca-healthmonitor`
- `uv run noca-animator`

The module packages use Hatchling `dev-mode-dirs = [".."]` and `packages = ["."]`
so workspace installs are true live editable installs. Console scripts resolve
`web`, `arena`, `shared`, `autojudge`, `rating`, `aiassistant`,
`healthmonitor`, and `animator` from the repository workspace rather than copied
package directories in the virtual environment.

The runtime packages depend on `noca-shared` through the uv workspace source
mapping. This keeps shared schema and service contracts importable without
turning the root project into an installable Python package.

The `landingpage/` module is intentionally not a workspace package. Its
container copies the Caddy configuration, the HTML page and its static assets,
the startup validator, and the two product illustrations owned by `web/` and
`arena/`. It also imports the shared font stylesheet and the Public Sans, Inter,
and IBM Plex Mono files from the internal `assets-base` build stage, so the
landing page renders in the same typefaces as every other NOCA surface without
depending on a CDN. It runs no Python and no Node.js.

## 4. Data model and schema ownership

PostgreSQL is the system of record. The web, arena, and autojudge modules access
it through SQLAlchemy with async drivers.

Schema ownership is centralized in `shared/db_schema/`:

- `shared/db_schema/` (package) defines the physical SQLAlchemy Core tables
- `web/database.py` binds the web ORM base metadata to that shared metadata
- `arena/database.py` binds the arena ORM base metadata to that shared metadata
- `autojudge/db/` package uses the same shared tables directly through SQLAlchemy Core queries

Alembic migrations target `shared.db_schema.metadata`, so the migration environment
is independent of the `web`, `arena`, and `autojudge` runtime modules. Schema
stewardship is limited to the independently-deployable HTTP front doors: the `web`
and `arena` container entrypoints run `scripts/run_migrations.py` (`alembic upgrade
head` under a PostgreSQL advisory lock that serializes concurrent attempts), so a
Web-only or Arena-only install can still bring the schema to head on its own. The
worker and presentation containers (`autojudge`, `rating`, `aiassistant`,
`animator`) are pure schema consumers:
their entrypoints instead run `scripts/wait_for_migrations.py`, which blocks until
`alembic_version` is at or ahead of the head revision the worker image expects (a
revision the image does not recognize counts as "ahead", i.e. newer than the
worker). This keeps a mismatched worker image from driving the schema during a
rolling deploy. The optional `NOCA_WAIT_FOR_MIGRATIONS_TIMEOUT` (seconds, default
300) bounds that wait.

The web module adds application-specific ORM behavior on top of the shared tables:
relationships, computed properties, hybrid properties, model hooks, and invariants.
The autojudge intentionally does not depend on those ORM hooks. It reads and writes
only the shared schema plus its own focused worker-side data access layer.

Contest-user photo and audio payloads live in `users_media`, keyed one-to-one by
`user_id` with cascade deletion from `users`. The table keeps the original photo,
derived avatar, MIME metadata, update timestamps, and an optional MP3, OGG, or WAV
clip. Web routes load this row explicitly so normal authentication and user-list
queries do not fetch multi-megabyte base64 payloads. The animator module reads the
same shared table for team presentation media.

Contest-scoped programming language availability is stored in the `contest_languages`
junction table. The web layer uses `get_contest_languages(session, contest)` as the
authoritative query for contest-scoped language lists; the autojudge continues to use
all active languages from the registry.

The animator module (public reveal/scoreboard presentation) is gated per contest by
`contests.animator_enabled` (`Boolean`, default and server default `false`, NOT NULL): only
enabled contests may expose animator snapshot/events/reveal, so a disabled contest is never
reachable by guessing its slug. The existing `sites` table carries the reveal ceremony's
per-site medal configuration — `gold_cutoff`, `silver_cutoff`, `bronze_cutoff` (positive and
ordered by the `ck_sites_medal_cutoffs_ordered` CHECK). Reveal
operators authenticate with a token whose fixed-length digest lives in `site_secrets`
(the plaintext token is never stored); a row with `site_id` set authorizes one site, while
`site_id = NULL` is a contest-global control secret. A composite foreign key
`(contest_id, site_id) → (sites.contest_id, sites.id)` keeps a secret from referencing a site
in another contest, and `(contest_id, secret_digest)` is unique. These three tables are
low-churn configuration data and use the server-wide autovacuum defaults.

Medals also exist at the **contest** level, for the global scope that has no site:
`contests.global_gold_cutoff`, `global_silver_cutoff`, and `global_bronze_cutoff` are nullable
and **all-or-nothing** — either all three are NULL (no global medals, the pre-existing
behavior, so nothing needed backfilling) or all three are set, positive, and ordered.
`ck_contests_global_medal_cutoffs` enforces exactly that, asserting `IS NOT NULL` explicitly on
its configured branch because a CHECK rejects only FALSE and would otherwise let a partial
triple such as `(1, 2, NULL)` through as UNKNOWN. Both scopes' cutoffs are edited on the same
Web admin form (`POST /c/{slug}/admin/animator/medals`) and validated in one transaction.

The cutoffs reach the animator in two places. The `/snapshot` feed bands each standing row with
a `medal` field computed from the cutoffs of the *requested* scope — the site's for a site
scope, the contest's global triple for `global` — so medals render on the live scoreboard in
both scopes. A reveal ceremony instead **snapshots** its cutoffs into
`RevealSessionState.medal_cutoffs` when the session is created, exactly as site cutoffs already
behaved: a settings change cannot reshuffle bands under an operator mid-ceremony, and adopting
one is an explicit `start-reveal` with `restart=true` (**Start over**, which the control panel
therefore also offers on an idle stored session). Both surfaces derive their band from the same
shared `medal_band_for_rank`, so they cannot disagree. The persisted state version is
deliberately **not** bumped: the change is forward-compatible, since a global ceremony recorded
before global medals existed simply carries `medal_cutoffs=None` and shows no medals. Rolling
back with a configured global ceremony in flight is recovered the same way every unreadable
payload is — `start-reveal` with `restart=true`.

Problem test-case content (both Web and Arena) lives on a single shared filesystem mount
configured by `NOCA_PROBLEM_TESTCASE_DIR`, namespaced by identity domain:
`<root>/contest/<problem_id>/NNN.in|out` for Web and `<root>/arena/<problem_id>/NNN.in|out`
for Arena. The database keeps only metadata and the normalized (LF) on-disk byte sizes
(`test_cases.input_size_bytes` / `output_size_bytes` and the mirrored Arena
`arena_test_cases.input_size_bytes` / `output_size_bytes`); the former Arena
`input_content` / `output_content` text columns were dropped. Inline (textarea) editing is
gated to cases where both sides are ≤ `MAX_INLINE_TESTCASE_BYTES` (10 KB); larger cases are
edited offline via a single-case ZIP download/replace round-trip. The autojudge reads test
files directly from the appropriate domain subdirectory.

Problem **packages** — the import/export ZIP both domains speak — are owned end to end by
`shared/services/problem_package/`, which is the single place every format decision is made:
integer coercion, null semantics, string widths, UTF-8, image resolution, archive safety, and the
export field set. Both domain importers consume one frozen `ProblemPackage` and are left with only
category resolution, target-language availability, ORM row construction, and lifecycle queueing.
Neither direction ever holds an archive in RAM: an upload is spooled to disk in bounded chunks and
opened once, and an export is written to a temporary path the route streams and deletes.

That subsystem also owns the ordering between an import's filesystem writes and its transaction.
Artifacts are prepared in hidden siblings of their final locations (so promotion is a
same-filesystem rename), promoted, and only then committed; a failed commit deletes exactly what
was promoted. A guarded, `fsync`'d journal per import makes a crash *between* promotion and commit
recoverable, and is reconciled at Web and Arena startup as well as before each import — with every
journal-supplied path re-validated against its configured root before anything is deleted. See
[SHARED_SERVICES.md](SHARED_SERVICES.md) and
[PROBLEM_PACKAGE_FORMAT.md](PROBLEM_PACKAGE_FORMAT.md).

Both problem tables make `output_limit_in_bytes` **NOT NULL** (server default 65536): a problem
always states an output limit, and `NOCA_JUDGE_OUTPUT_LIMIT_BYTES` is a hard global ceiling applied
as `min(problem_limit, global_limit)` rather than a fallback for a missing value. The *per-language*
`problem_language_limits.output_limit_in_bytes` deliberately stays nullable, where NULL means
"inherit the problem's limit" — which is exactly what the judge's `coalesce(per-language, problem)`
computes. Field widths are unified across the two domains (`title` 256, `author` 256, `notes` 512,
`source` 256, `license` 256, `image_caption` 512) so a value that survives on one side survives a
round trip through the other.

Permanent inactive-contest removal coordinates all three infrastructure
boundaries synchronously. Web locks and rechecks the inactive contest row,
collects its database and runtime identifiers, and requires strict Valkey
cleanup before changing PostgreSQL or files. It then moves each problem's PDF,
Markdown, and test-case directory into guarded same-filesystem quarantine and
deletes the contest graph in one PostgreSQL transaction. The transaction also
adds one warning-level `contest_deleted` security event containing only the
acting UberAdmin ID and deleted contest ID. A pre-commit failure rolls back the
database and restores the quarantine; a successful commit erases it. Global
languages, global problem categories, UberAdmins, unrelated contests, and
existing security events remain outside the deletion graph.

Authenticated worker pause/resume adds two Arena-owned tables to the shared schema:
`arena_worker_pause_state` (authoritative `paused`/`paused_by` plus a monotonic
per-worker, nonnegative `generation`) and `arena_worker_command_audit` (one row
per issued pause/resume attempt, including rejected and malformed worker-class
requests). The Arena route writes them; the autojudge and aiassistant workers
read pause state through
`shared/services/worker_pause_state.py`.

Arena gamification adds the `arena_user_badges` table to the shared schema: an
append-only ledger of which badge each Arena user has earned (`ArenaBadge` enum)
and when (`awarded_at`), with a unique `(user_id, badge)` constraint so a badge is
awarded to a user at most once. The award logic that inserts rows is owned by the
rating worker's badge-assignment loop (`shared.services.arena_badges`); the Arena
ORM exposes the ledger through `ArenaUser.badges`. Streak badges are backed by the
`arena_users.current_streak` / `longest_streak` / `last_ac_date` columns the loop
recomputes, and the loop tracks its incremental watermark plus last full
reconciliation in the singleton `arena_badge_cycle_state` table. Badge families
cover per-submission recovery, solve streaks, distinct solved-problem counts,
distinct-language counts per problem, first-solver and problem-set hand-in
positions, latest on-time problem-set solves after deadlines, non-AC bursts,
unbroken distinct-AC runs, and dynamic low-solve-rate problem solves.

The rating worker's problem-difficulty cycle (`rate_all_problems()`) ends by
snapshotting a 20-bin histogram of the catalogue's current difficulty
distribution into the singleton `arena_rating_cycle_state` table, read by the
Arena `/help/rating` page to render a current-distribution chart without an
aggregate query at request time.

Arena signup reputation adds the `arena_user_reputation` table to the shared schema:
one row per Arena user (unique `user_id` FK) holding the client IP captured at signup
plus the IPQualityScore IP and email reputation reports (fraud scores as columns and the
full signals as JSON). The signup IP is recorded for every account even when the
IPQualityScore integration is disabled, so `scripts/backfill_email_reputation.py` can
later score both the email and any recorded signup IP. The Arena HTTP process owns the
writes (a post-signup background task through `arena.services.signup_reputation_service`,
which also emails every `ARENA_ADMIN` a report); the Arena admin user profile reads the
snapshot on its Reputation tab.

Cross-module security auditing shares a single `security_events` table (owned by
`shared.services.security_events`) rather than per-domain audit tables. Each row
snapshots both the opaque `actor_user_id` and a human-readable `actor_label` (the
actor's login, e.g. email/username) captured at event time, so the admin viewers
can name who originated an `auth_*` / `parental_*` event without a lookup that could
break on account rename or deletion. Rows also store `client_ip`, `source_port`,
and `request_id` when the ASGI server or a trusted reverse proxy provides those
values. The Caddy deployment example overwrites `X-Request-ID` with Caddy's
per-request UUID so `security_events.request_id` can be correlated with Caddy
access logs. Both the Web and Arena HTTP processes append to it: authentication
failures, throttle
lockouts, existing-account signup attempts, and — through
`shared.services.admin_audit` (`event_type="admin_action"`) — destructive and
privilege admin actions, all committed in the same transaction as the mutation
they describe. Arena admins view the log at `/admin/dashboard/security-events`;
uberadmins view and filter it at `/uberadmin/security-events`. Retention is a
shared `security_events_reaper` loop that each HTTP runtime runs over its own
module ownership set — Web prunes `module=web`, Arena prunes `module in
(arena, aiassistant)` — so an independently deployed Web-only or Arena-only site
still cleans up exactly its own rows older than
`NOCA_SECURITY_EVENTS_RETENTION_DAYS`. Failed
logins deliberately land here (not in `login_history`/`arena_login_history`,
which stay success-only device history) because the log must record attempts
against non-existent accounts that cannot satisfy a login-history user FK.

CSRF protection relies on `SameSite=Lax` session cookies rather than
per-request CSRF tokens; this is a documented accepted risk given the
server-rendered, same-site POST forms. Session and auth cookies are marked
`Secure` whenever `NOCA_COOKIE_SECURE` is set (mandatory in production), and the
trusted client IP for throttling/auditing is taken from the proxy-corrected
`request.client.host` — raw `X-Forwarded-For` is never trusted. The client
source port comes from `request.client.port` unless `NOCA_SOURCE_PORT_HEADER`
names a reverse-proxy-managed header. The request identifier comes from
`X-Request-ID`; deployments must only trust it when the reverse proxy strips
incoming values and sets its own ID.

Error responses are owned by the same shared module across all four HTTP modules
(`shared.error_handlers`). Generic failures the router or validator produced --
`404`, `405`, and every `RequestValidationError` -- answer a neutral
`{"error": <code>}` body rather than FastAPI's `{"detail": ...}` and Pydantic's
error array, which otherwise name the stack, disclose internal parameter names,
and echo the caller's input back. Anything an application authored keeps its
`detail` verbatim, and statuses outside `{404, 405, 422}` are untouched, so the
animator control panel still reads its refusal messages out of `payload.detail`.
Because every handler derives its response from the status code alone and never
from the cause, the animator's requirement that an unknown slug, a disabled
contest, and the control kill switch stay indistinguishable holds for the neutral
body exactly as it did for the framework default.

Integer request parameters are bounded through `shared.http_params` (`DbId`,
`PageNumber`). PostgreSQL `integer` is 32-bit while Python integers are unbounded,
so an unbounded parameter reaches a query and raises
`asyncpg.DataError: value out of int32 range`, which surfaces as a misleading
`503` and logs a full SQL statement. Row primary keys are `String(36)` UUIDs, so
this applies to the natural-number columns -- `arena_number`, ordinals, page
numbers, and the problem limit fields -- not to the `str`-typed id parameters.
Route *paths* use the `dbid` convertor (`{arena_number:dbid}`) rather than
Starlette's built-in `int`, whose `int(value)` call raises inside `Route.matches()`
for a path longer than CPython's 4300-digit limit -- before any handler, so it
escaped the error handlers as an unauthenticated `500`. Both rules are enforced by
`tests/shared/test_route_int_bounds.py` against the real registered routes.

Browser security headers are owned by one shared middleware
(`shared.services.security_headers`) that **all four** HTTP modules install — web,
arena, animator, and healthmonitor — so a public page cannot ship without CSP,
HSTS, `nosniff`, framing, referrer, and permissions policy. The two cookieless
modules (animator, healthmonitor) derive `hsts_enabled` from `ENVIRONMENT` alone
rather than from `COOKIE_SECURE`. `NOCA_SECURITY_HEADERS_ENABLED` and
`NOCA_CSP_REPORT_ONLY` are deliberately unprefixed so one setting governs every
module at once. The sample Caddyfile re-applies a subset of the same headers at
the edge with set-only-if-absent semantics as defense in depth, and strips
`Server` and `Via` so the origin stack is not named to clients; see
[CONFIG.md](CONFIG.md) for the two Caddy operator pitfalls involved.

## 5. Module summaries

### `web/`

The web module is the server-rendered contest administration and participant-facing
FastAPI app. It owns authentication and authorization, contest management, problem
management, Auto-Limit profiling requests, contest-scoped user management,
clarifications, staff task queues, submission lifecycle actions, scoreboards, and
chief-judge workflows.

### `autojudge/`

The autojudge module is a separate async worker process. It owns queued submission
and profiling jobs, Docker container pool management, compilation, isolate-based
execution, result persistence, stale in-flight recovery, startup and periodic
reconciliation of non-terminal jobs missing from the queue (recovering jobs lost
between a producer's DB commit and its follow-up enqueue), zombie container
cleanup, and worker heartbeat health monitoring.

### `arena/`

The arena module is the public-facing FastAPI application for Arena users. It owns
Arena signup and login, OTP-protected accounts, login history, LGPD age-gate handling,
Arena submissions, classes (teacher-owned groups with dated membership history and a
self-service registration-request workflow), problem sets (teacher-owned, scheduled
problem collections within a class; students may opt a submission into a set at submit
time to make it visible to the teacher, and post-deadline rating snapshots freeze each
student's AC totals), and Arena-specific user identity separate from contest users and
uberadmins.

Every Arena problem records the natural language of its statement in
`arena_problems.statement_language` (`pt`, `en`, or `es`; nullable while unknown), which
both problem lists expose as a filter and the problem package carries as the optional
`statement_language` key. The value is normally derived rather than typed: the author may
leave the form on "detect automatically", and `arena.services.statement_language_service`
detects it with `lingua`. An explicit choice that disagrees with detection is refused until
the author confirms it, and an import that stated no language is flagged for the importer to
verify. Rows predating the column are filled by
`scripts/arena/backfill_statement_language.py`, which only ever writes rows whose language is
still NULL.

Arena access is **default-deny**: a single global FastAPI dependency
(`arena.dependencies.access_control.enforce_arena_authentication`, registered on
the app in `arena/main.py`) requires a valid logged-in session for every route
except a small public allowlist — `/dashboard`, the `/problems` list (the problem
*detail* page and sub-resources stay protected), `/legal/*`, `/help/*`, the
entire `/auth/*` namespace (login, signup, and the other pre-login flows), `/`,
`/health`, and the root favicon assets. The gate reads only
`request.state.validated_token` (populated by `ArenaAuthMiddleware`, no database
I/O) and raises `HTTPException(401)`, which the Arena exception handler turns into
a login redirect (HTML), an `HX-Redirect` (HTMX), or a plain 401 (API). Per-route
`get_current_arena_user` / `require_arena_*` dependencies still apply full
database gating and role checks on top of the gate. New routes are therefore
protected automatically unless their path is added to the allowlist.

### `rating/`

The rating module is a standalone single-replica worker. It owns Arena problem,
user, and affiliation rating recomputation cycles and publishes scheduler metadata
to Valkey so all Arena replicas can show consistent footer and help-page timing.
It also runs an independent per-problem statistics loop (`run_problem_stats_loop`,
on its own `NOCA_RATING_STATS_INTERVAL` timer) that precomputes the JSON snapshots
read by the Arena problem statistics page (`shared.services.arena_stats`), and a
parallel per-user statistics loop (`run_user_stats_loop`, sharing the same
`NOCA_RATING_STATS_INTERVAL` timer) that precomputes the verdict and language
distribution snapshots stored in `arena_user_statistics` and read by the Arena
public profile page. A third independent loop (`run_badge_assignment_loop`, on its
own `NOCA_RATING_BADGE_INTERVAL`
timer) awards Arena gamification badges from Accepted submissions
(`shared.services.arena_badges`): each cycle runs a cheap incremental pass bounded by a
watermark, and periodically a full reconciliation pass re-evaluates all Accepted history
so dynamic badges (CLEAN_CODE) and late data stay correct.

### `aiassistant/`

The aiassistant module is a standalone async worker. It owns dequeuing Arena AI
review jobs from the Valkey `ai:queue:pending` list, calling the OpenAI Responses
API when the submitting user has a personal `ai_api_key`, and submitting an
OpenAI Batch API job when the worker falls back to the platform key configured
via `NOCA_AI_OPENAI_API_KEY`. Online user-key jobs store the AI review immediately.
Platform-key jobs first insert a durable `arena_ai_batch_jobs` row with
`local_status='staged'`, without calling OpenAI. The batch flusher periodically
collects all staged rows into one multi-item OpenAI batch, and the batch poller
stores each result after OpenAI completes that batch.

The worker runs the dequeue loop, stale-job reaper, batch flusher, batch poller,
reconciler, and worker-presence loop in one deployment unit. It also runs the
signed command loop when a worker command secret is configured. The reaper uses
`ai:queue:inflight:times` to recover queue jobs that were dispatched but not
cleaned up. The batch flusher wakes every five batch-poll intervals, or when
triggered. It submits all staged jobs as one OpenAI batch. The batch poller
reads non-terminal `arena_ai_batch_jobs` rows, retrieves OpenAI batch status,
stores completed review output in `arena_submission_ai_reviews`, creates Arena
notifications, clears failed retry flags, and deletes uploaded OpenAI files
after terminal states. At the top of each batch poll cycle, a stale-batch
detector locally expires batch jobs whose `submitted_at` is older than
`NOCA_AI_BATCH_STALE_HOURS`: in one transaction per submission it atomically
claims the row, refunds the consumed platform credit, clears `submit_to_ai`,
notifies the user, and finalizes the row as `expired`, then best-effort cancels
the OpenAI batch and deletes its files.

After any poll cycle that completes a batch, the worker derives turnaround
statistics from the 100 most recent successful platform-key reviews and stores
one persistent JSON value at `ai:batch:turnaround:stats`. The value is an
optional cache that Arena reads for the AI credits dashboard and platform-credit
review confirmation modal. Missing or invalid cache data produces an explicit
unavailable state; PostgreSQL remains authoritative, and a Valkey write failure
does not affect completed reviews.

The reconciler is the database-driven safety net for the request route's dual
write: because
`arena_submissions.submit_to_ai=True` is committed to PostgreSQL before the job
is pushed to Valkey, a crash between the two leaves a flagged submission with no
queue job. The reconciler periodically finds such submissions (flagged, no
review row, no active batch job, older than a grace window) that have no live
pending/inflight queue presence and re-enqueues them.

### `healthmonitor/`

The healthmonitor module is a standalone FastAPI server (default port 8002) with no
database access and no authentication — its single page is public. It reads
the Valkey worker-presence keys published by all other runtime modules (the
`web`, `arena`, and `animator` HTTP servers publish presence from their
lifespans exactly like the workers do, under the presence-only
`WorkerClass.WEB` / `ARENA` / `ANIMATOR` classes) and serves:

- `/` — the uptime dashboard: the live Available/Unavailable/Unknown status of
  every monitored service plus an ECharts 30-day heatmap per service (60 slots
  of 12 hours, colored from green at 100% slot uptime to red at 70% or below)
- `/refresh` — the HTMX dashboard fragment, polled every 30 seconds while
  automatic refresh is active; the browser preserves card expansion and focus
  while replacing the live status and chart containers
- `/uptime.json` — the JSON source for all six heatmaps; the browser fetches it
  after initial load and every HTMX refresh, then recreates the ECharts
  instances and their accessible data-table fallbacks

A prober loop records one up/down sample per service every
`NOCA_HEALTHMON_PROBE_INTERVAL` seconds into per-slot `up`/`total` hashes
(`noca:healthmon:stats:{service}:{slot_epoch}`), skipping cycles while Valkey
is unreachable so monitor-side outages never count against the services. A
reaper loop deletes slots older than `NOCA_HEALTHMON_RETENTION_DAYS`; slot keys
also carry a TTL as a safety net. That same loop also prunes worker-presence
records of workers unseen for more than 7 days, bounding the durable
`noca:worker-presence:<class>:seen` and `:last-jobs` hashes every module writes
(live markers already expire on their own). Because the health monitor is an
optional deployment, every presence-publishing module runs the same shared pass
once at shutdown, so growth stays bounded in a Web-only or Arena-only install
too. The presence-only classes never appear in the Arena admin dashboard or
pause machinery.

### `animator/`

The animator module is a standalone FastAPI presentation runtime (default port
8003) that owns the public live scoreboard and post-freeze reveal ceremony. It
reads PostgreSQL (through SQLAlchemy Core over the shared schema) and Valkey
directly across the same infrastructure boundary the other runtimes use, and
reuses the shared scoreboard projection so its standings always agree with the
official scoreboard. It defines no ORM mappings and never imports `web`.
Configuration is isolated under the `NOCA_ANIMATOR_*` prefix
(`NOCA_ANIMATOR_HOST`, `NOCA_ANIMATOR_PORT`, `NOCA_ANIMATOR_POLL_FALLBACK_SECONDS`,
`NOCA_ANIMATOR_ENABLE_CONTROL`, `NOCA_ANIMATOR_BRAND_NAME`). The reveal control
endpoints are gated per contest by `contests.animator_enabled` and can be
disabled process-wide with the `NOCA_ANIMATOR_ENABLE_CONTROL` kill-switch.

The animator exposes a presentation launcher at `GET /c/{slug}/`.
It lists a prominent global scope and every contest site, with links to each
scope's animated scoreboard, reveal projector, and reveal controller. The
scoreboard shell lives at `GET /c/{slug}/scoreboard?scope=...`.

The animator exposes a read-only public feed for one enabled contest:
`GET /c/{slug}/meta` (contest identity, problem labels and balloon
colors, start/end/freeze timing, freeze state, and per-site medal-cutoff
summaries only) and `GET /c/{slug}/snapshot?scope=...` (a shared
`ScoreboardSnapshot` plus a server-generated refresh version). The snapshot
defaults to the global scope; a validated site scope filters teams and
submissions before scoring. Animator loads `release_scoreboard_after_end` into
its immutable contest record: post-freeze submissions stay hidden while the
contest runs and after an unreleased end, while an ended, released contest
scores every final result and reports `is_frozen=false`, matching Web. The
final presentation keeps the existing **Ended** timer state, hides the
connection badge, and starts neither SSE nor polling. Both feeds resolve the
contest through a single non-enumerating `get_enabled_contest` dependency, so a
missing slug and an `animator_enabled=false` contest are indistinguishable
(identical `404`). The
feed reads the shared schema through SQLAlchemy Core in
`animator/services/contest_feed_service.py`, loads only `RoleEnum.TEAM` users,
selects the effective judgment deterministically (prefer `DONE`, then latest
`created_at`, then id), and delegates all scoring to the shared `compute_icpc`
so the animator standings agree with the official scoreboard.

The animator also streams live change notifications over Server-Sent Events at
`GET /c/{slug}/events` (native FastAPI `EventSourceResponse` / typed
`ServerSentEvent`). A single `AnimatorEventStream` per process subscribes to the
shared `judge:results` and `judge:submissions` Valkey channels and fans out
`verdict`, `submission`, `scoreboard_refresh`, and `timer_tick` events to bounded
per-client queues; FastAPI adds a native 15 s idle-only comment heartbeat. Verdict
events are filtered by `contest_id` (legacy events without one are dropped) and
redacted while a contest is frozen; `submission` events (a new-submission nudge)
are filtered by `contest_id` and suppressed entirely while frozen. Per-client queues
coalesce to a single pending refresh on overflow, and the stream carries no logs —
PostgreSQL snapshots stay authoritative, so clients refetch `/snapshot` on
`scoreboard_refresh` and on `submission`. The `/snapshot` response carries a
freeze-safe `pending_submissions` array that is the authoritative source for the
pending list; `judge:submissions` and `judge:results` are **not** mutually ordered,
so a verdict arriving before its submission signal is reconciled by the next
snapshot.

The reveal ceremony is a **bottom-up sweep over every row** of the standings, not
only the rows holding frozen runs. One `step` either reveals a single frozen run
on the cursor's row or, when that row has nothing left, moves the highlight up
one row; `back` undoes either kind. The cursor is a *screen position*: when a
reveal lifts a team past others, the cursor holds its row and the team now on it
comes into focus, while the lifted team is met again as the sweep climbs toward
it. A single sweep therefore still resolves every relevant run, because revealing
can only improve a team's score and the teams it overtakes fall at most onto the
cursor's own row. The whole history is one ordered trail of steps
(`RevealSessionState.step_log`), from which both the revealed set and the cursor
are derived — which is what keeps `back()` an exact `pop` now that a step need
not reveal anything. That trail is `state_version=3`; an older payload —
version 1, recorded before the cursor existed, or version 2, recorded before
command receipts did — is refused rather than replayed, and the operator recovers
with `start-reveal` + `restart=true`.

The post-freeze reveal ceremony persists its state in Valkey rather than process
memory, so a ceremony survives a restart and can only be mutated by one writer per
contest+scope at a time. `animator/services/reveal_session_store.py` serializes each
mutation under a token-owned lock at `animator:reveal:lock:{contest_id}:{scope}` and
writes the `RevealSessionState` at `animator:reveal:{contest_id}:{scope}` through a
Lua **fenced** write (state persisted only while the lock still holds the writer's
token, so an expired-then-reacquired lease can never clobber the new owner). State
is saved before a `RevealStateChangedEvent` invalidation nudge is published on
`revelation:events:{contest_id}:{scope}`; the event carries only metadata, so
spectators refetch authoritative state and a missed nudge is harmless. The key TTL
is contest end plus `NOCA_ANIMATOR_REVEAL_TTL_MARGIN_SECONDS`, refreshed on every
mutation.

That durability answers "did the ceremony survive?", but not "did my command
apply?" — a `503` or a dropped connection can arrive *after* the fenced save
committed, and re-sending a `step` in front of an audience is not an acceptable
way to find out. Each mutating control command therefore accepts an optional
`Idempotency-Key` header naming one *attempt*. The ceremony state carries a
bounded ring of the last eight applied keys, written by the same fenced save and
expiring with the same key, and a retry is resolved inside the scope lock before
the engine runs: the most recent key is **replayed** (the stored state is
re-projected and returned, with no save and no publish, because that state
already is the command's result), an older or differently-used key is a stated
`409`, and an unknown key applies normally. `restart=true` discards the ring with
the rest of the old state. Without a key a command is applied exactly as sent,
which is why the operator panel attaches a fresh one to every attempt and why an
ambiguous outcome without one still requires reloading state instead of
retrying.

Spectators watch that ceremony through a **credential-free** public feed under
`GET /c/{slug}/ceremony`, `/reveal/state`, and `/reveal/events`, plus
the scoped team photo at `GET /c/{slug}/teams/{team_id}/photo`. A
ceremony is selected by a validated `?scope=` query value — `global` or a site id
of that contest — which grants nothing and is resolved *after* the enabled-contest
gate, so an unknown site, another contest's site, and garbage all answer the same
bare `404` an unknown slug does (a pattern-validated parameter would answer `422`
first and prove the slug resolved). `/reveal/state` returns the *same*
`RevealProjectionResponse` the control API does, produced by the same
`control_service.load_projection`, wrapped in an envelope whose `has_session`
flag distinguishes "no ceremony yet" from an `idle` one; `/reveal/events` streams
only invalidation nudges, preceded by one `reveal_ready` event emitted **after**
the Valkey subscription is live: reconciling there rather than on
`EventSource.onopen` (which fires when the response headers are written, possibly
before the subscribe) closes the window in which a publication would reach
neither the client's fetch nor its subscription — pub/sub has no replay. The
client's rule is therefore *fetch state, then subscribe*, refetching on every
nudge and every `reveal_ready`, and retrying a failed state request with backoff
so a transient failure cannot strand a projector. Team photos fall back photo → avatar → checked-in placeholder, honor
`users_media.com_foto`, re-verify stored bytes by actually decoding them under
explicit pixel limits (serving the validated format, never the stored MIME
claim, so a truncated blob falls through instead of rendering broken), and are
conditional on an `ETag` derived from the media kind and `dta_foto`.

The projector page itself (`GET /c/{slug}/ceremony`) renders the frozen
standings, the focused team, medal bands, and pending cells, and opens a single
reusable Bootstrap modal on a team name showing that team's photo and playing its
optional audio clip. The clip comes from `GET /c/{slug}/teams/{team_id}/audio`,
which shares the photo route's scoped lookup and serves the MIME type **sniffed
from the bytes** rather than the stored `audio_mime` claim; a missing,
undecodable, or unrecognizable payload is a `404` (never a `500`), so the modal
hides its player instead of rendering a broken one. Playback is started inside the
user activation of the team click, and the three outcomes are distinguished
explicitly: playing, refused by the browser's autoplay policy (native controls
stay, with a status hint), or unusable media (player hidden). Closing the modal
runs an idempotent teardown that stops playback *and* aborts any in-flight media
download.

Operators drive the ceremony from `GET /c/{slug}/control?scope=...`, a
credential-free HTML shell gated by the same `animator_enabled` → kill-switch
order as the command API and deliberately excluded from the control audit stream
(fetching a page is not a command attempt). The **operator token lives in
JavaScript memory only**: it is typed into a password field that is cleared on
capture, sent solely as an `Authorization: Bearer` header, and never written to a
URL, request body, cookie, `localStorage`, or `sessionStorage` — so a reload
requires re-entry. The panel builds its ceremony selector from the public `/meta`
feed, and the launcher's validated scope preselects the intended ceremony,
because `start-reveal` requires a `site_id` exactly equal to the token's own
scope. It also distinguishes *stated* refusals (`400/403/404/409/422`, which
changed nothing and re-enable the controls at once) from *ambiguous* outcomes (a
network failure or any `5xx`, including the store's `503`, which can arrive after
a fenced save committed): an ambiguous outcome keeps the commands disabled until
authoritative state is reloaded, so a failed `step` can never be replayed into a
double reveal.

Operators drive that ceremony through the authenticated control API under
`GET|POST /c/{slug}/control/*` (`start-reveal`, `step`, `back`, `reset`,
`jump-team`, `state`). Three gates apply in a fixed order: the per-contest
`animator_enabled` gate, then the process-wide `NOCA_ANIMATOR_ENABLE_CONTROL`
kill switch, then an `Authorization: Bearer` operator token resolved through
`shared.services.animator_access_service` — the first two answer the same bare
`404` an unknown slug does, so neither can be used to probe the others, and
every credential failure is one generic `403`. Every attempt, accepted or
rejected, is audited in one token-free structured log line. A site token authorizes exactly
its own site and a global token (`site_secrets.site_id IS NULL`) exactly the
global ceremony; only `start-reveal` reads a `site_id`, and only to require exact
equality with the token's own scope. Every later command derives its scope from
the credential and the stored session, so no request body can redirect a
ceremony. Each successful mutation is exactly one fenced save plus one nudge,
performed inside the per-scope lock; responses carry a projection (counts, phase,
focus, derived team views), never the persisted `reveal_log` or
`frozen_submission_ids`. See
[animator/docs/ROUTES.md](../animator/docs/ROUTES.md) and
[animator/docs/SERVICES.md](../animator/docs/SERVICES.md).

### `landingpage/`

The landing page is a standalone Caddy static site (default internal port 8080)
that serves one public page: an overview of the deployment and links into the
Contest, Arena, Animator, and Health Monitor instances running in it. It has no
Python package, no application framework, no database or Valkey connection, and
no background loop, which is why it stays outside the `uv` workspace.

Its only runtime input is configuration. Caddy's template middleware renders the
four `NOCA_LANDINGPAGE_*_URL` values and `NOCA_LANDINGPAGE_VERSION` into the page
on each request and HTML-escapes them; `containers/landingpage/entrypoint.sh`
rejects a missing, relative, or whitespace-bearing URL and a missing, oversized,
or whitespace-bearing version tag before Caddy starts, so a misconfigured
deployment fails rather than publishing broken navigation. The version is
configuration rather than something discovered at runtime precisely because there
is no application behind the page to ask.

The module sits outside every infrastructure boundary in section 2. It reaches
neither PostgreSQL, Valkey, nor another module, and its
`Content-Security-Policy` enforces that from the browser side as well: it begins
at `default-src 'none'`, allows only same-origin styles, scripts, fonts, and
images, and sets `connect-src 'none'`. A consequence worth stating explicitly is
that the page cannot display live service status, by construction; it links to
the Health Monitor instead.

Because it publishes no Valkey worker-presence heartbeat — there is no
`WorkerClass` member for it, unlike the `WEB`, `ARENA`, and `ANIMATOR`
presence-only classes — the landing page deliberately does **not** appear as a
service on the health monitor's status and uptime dashboards. Its own liveness is
the dependency-free `/health` route, for container and load-balancer probes only.

The image is the official Caddy image plus static files. A build-only stage
supplies the shared NOCA webfonts, so the page uses the same typefaces as every
other surface without a CDN, and the Contest, Arena, and Animator illustrations
are copied from the modules that own them rather than duplicated into this one.
Development uses `landingpage/serve_dev.py`, a stand-in for Caddy that renders
the same template calls and sends the same headers; it never ships in the image.
See [landingpage/README.md](../landingpage/README.md),
[landingpage/docs/ROUTES.md](../landingpage/docs/ROUTES.md), and
[landingpage/docs/SERVICES.md](../landingpage/docs/SERVICES.md).

### `shared/`

The shared module defines cross-runtime contracts: SQLAlchemy Core schema, enums,
queue payloads, language registry helpers, logging, Valkey services, locks,
scoreboard cache support, email delivery, safe outbound network helpers, image
processing, and other services reused by more than one runtime module.

## Non-scoring solution tests

JUDGE, ADMIN, and UBERADMIN actors can run a candidate solution against any problem
in an active contest through the real compiler, sandbox, limits, test cases, and
custom validator, and see a verdict — with zero effect on the competition.

The runs live in their **own** tables (`solution_test_runs`,
`solution_test_case_results`) rather than behind an `is_test` flag on
`submissions`. A flag would make correctness depend on every present and future
consumer remembering `WHERE is_test = false`, and a single missed filter silently
corrupts standings. Separate tables make leakage into standings, balloons, Runs,
reports, feeds, and exports **structurally impossible** rather than test-enforced.
The worker enforces the same boundary by signature: `process_solution_test_job`
takes no Valkey handle, so it cannot publish a verdict event, invalidate the
scoreboard cache, or create a balloon task.

Execution semantics mirror real submissions rather than profiling: an ordinary
problem runs *every* test case and aggregates the verdict, while an interactive
problem stops at the first case that does not end `AC` — the custom validator
replay's own contract. Interactive attempts are routed to the solution-test tables
by an `attempt_target` axis independent of `domain`, since a solution test always
runs against a *contest* problem.

Each ordinary case-result row snapshots the executed problem input, expected
output, and submission output for later diagnosis. Each value is capped at 10
KB, including an explicit truncation marker, so one run cannot duplicate
unbounded test data in PostgreSQL. Interactive runs retain their transcript
instead because they have no static expected output.

Runs reuse `JudgmentStatus` so the autojudge state machine is reused verbatim.
They share the contestant queues and the `priority=contest.is_running` rule, are
retained for the life of the contest and purged only by permanent contest removal,
and are **excluded from contest backups** exactly as Auto-Limit profiling runs are.

## Custom interactive validators

Contest and Arena problems can stage a UTF-8 custom validator revision. The
application commits the candidate before placing a compile-validation job on
the profiling-priority Autojudge queue. A candidate token makes delayed results
safe: the worker updates or promotes a revision only while its token still
matches.

A successful candidate becomes the active `VALID` revision. A failed
replacement retains bounded compiler diagnostics and leaves an older active
revision available.

The validator is **parametrized by test-case input**: one container pair judges a
whole submission, and the judge replays the conversation once per test case,
writing that case's input to the validator's stdin before the two sides talk. The
first case that does not end `AC` stops the iteration and its verdict is the
submission's. Consequently an interactive problem's test cases carry **input and
explanation only** — no expected output — and its packages, ZIPs and downloads
ship `.in` files alone.

Every test case of an interactive problem is **secret**, because a bare input
reveals a secret without showing what to do with it. The public examples come from
**sample interactions** instead (below). The invariant for a problem with a
configured validator is therefore *zero public test cases and at least one secret
one*: staging a validator demotes any existing public case, the sample toggle is
refused while a validator is configured, and no edit path may remove the last
secret case. The "at least one secret case" half is a gate rather than a write
barrier — Arena creation legitimately stages a validator on a brand-new disabled
draft with no cases yet — so it is enforced at the Arena enable gate and at both
submission paths, where the problem becomes visible or judgeable.

## Sample interactions

A problem with a custom validator cannot show sample test cases meaningfully: what a
contestant needs to see is the *conversation* their program will have. Such a problem
therefore carries up to five **sample interactions** (`problem_sample_interactions` /
`arena_sample_interactions`), each an author-written transcript plus an optional
explanation, ordered by `ordinal`.

Transcripts are stored in the same JSON shape the judge records for a real interactive
attempt, so one shared partial (`_partials/transcript_table.html`) renders both the
authored examples on the problem page and the recorded attempts on the submission page.
Authors write them as plain text with the strict prefixes `> ` (validator → contestant)
and `< ` (contestant → validator); `shared/services/sample_interactions.py` owns the
parser, the package I/O, and the invariant helper, with no ORM dependency.

Removing a validator leaves the interactions with nothing to illustrate, so the removal
endpoints require an exact `keep_interactions=true|false` — a strict string, not a
`bool`, so that FastAPI cannot coerce `1`/`on`/`yes` into a choice between hiding data
and destroying it. `true` sets a nullable `hidden_at` (the same soft-hide pattern
`clarifications` uses), which keeps the rows out of every UI and out of exports; staging
a validator again clears it and the interactions resurface. Hidden rows still count
against the five-interaction cap, so an un-hide can never overflow it.

Packages carry them as `interaction/NNN.interaction` (the raw transcript JSON) and
`interaction/NNN.explain` (plain text). A package with no validator has its
`interaction/` members dropped on import; a validator package with none warns the
importer that the problem shows no examples.

See the
[interactive validator guide](custom-validator/INTERACTIVE_VALIDATOR.md) for
the authoring workflow, the
exit-code-to-verdict mapping, and which problem limits are enforced by the judge
versus by the validator.

Each interactive attempt is recorded in `submission_interactive_attempts` /
`arena_submission_interactive_attempts`, tagged with the `test_case_ordinal` it
belongs to. Its `transcript` JSON column holds the ordered, line-split
conversation between the contestant and the validator (the judge relays every
byte, so it observes protocol order); the case's own input is not part of it. A
judgment retains only the last executed case's attempts, so the surviving rows are
the conversation that decided the submission. Both frontends render them from one
shared partial — and only for a non-`AC` verdict, since an accepted submission has
no failing round to explain.

The per-case watchdog attributes a line-protocol stall only when a complete
message identifies the side that failed to reply. A validator message followed
by contestant silence becomes contestant `TLE`; a contestant message followed
by validator silence remains an internal failure and retries once. A partial
message or no message remains ambiguous and internal. When a complete validator
message establishes contestant silence, that stall can never disable the Arena
problem by being mistaken for a validator failure.

## 6. Summary

NOCA is an eight-process contest platform:

- `web` manages contest and business workflows (default port 8000)
- `autojudge` manages sandboxed compilation and execution
- `arena` manages the public Arena participant platform (default port 8001)
- `rating` manages the single-replica Arena rating recomputation cycles
- `aiassistant` manages the Arena AI code review pipeline (OpenAI Responses API
  and Batch API)
- `healthmonitor` manages the public uptime dashboard (default port 8002)
- `animator` manages the public live scoreboard and reveal presentation (default port 8003)
- `landingpage` serves the environment entry point and public module links
  (default internal port 8080)
- `shared` defines the common contract between them

The architecture is built around separation of concerns, a shared PostgreSQL schema
contract, lightweight Valkey coordination, shared problem/testcase storage,
server-rendered FastAPI pages with flash-based feedback, role-based access control
with strict contest scoping, and background reapers for asynchronous processing.
