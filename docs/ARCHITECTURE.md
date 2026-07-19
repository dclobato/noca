# NOCA Architecture Overview

This document summarizes the main system design and application architecture of NOCA.
For detailed runtime behavior, module internals, coordination flows, security, and
operational consequences, see [ARCHITECTURE_RUNTIME.md](ARCHITECTURE_RUNTIME.md).

Related references:
- [ARCHITECTURE_RUNTIME.md](ARCHITECTURE_RUNTIME.md) for detailed runtime architecture and operational constraints
- [autojudge/docs/AUTOJUDGE_INFRA.md](../autojudge/docs/AUTOJUDGE_INFRA.md) for worker isolation, queue protocol, and container execution details
- [DATA_FLOW_FROM_SUBMISSION_TO_VERDICT.md](DATA_FLOW_FROM_SUBMISSION_TO_VERDICT.md) for the submission lifecycle
- [CUSTOM_VALIDATOR.md](CUSTOM_VALIDATOR.md) for interactive custom validators: authoring, exit codes, and which limits apply
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

NOCA is split into six main runtime modules:

- `web/`: the FastAPI application that serves HTML pages, handles authentication, enforces authorization, manages contests/problems/users, and creates judging work
- `autojudge/`: the asynchronous judge worker that consumes queued judgments, compiles and runs submissions inside containers, and writes results back
- `arena/`: the FastAPI application that serves the Arena platform, with its own user identity domain, OTP-protected accounts, and login history
- `rating/`: the single-replica Arena rating worker that periodically recomputes problem difficulty, user scores, and affiliation ratings, and publishes the next-cycle timestamp to Valkey for the Arena footer
- `aiassistant/`: the Arena AI review worker that dequeues AI review jobs,
  uses the OpenAI Responses API for user-key reviews, uses the OpenAI Batch API
  for platform-key reviews, and stores feedback in the database
- `healthmonitor/`: the public health-monitoring FastAPI server that probes the
  other five modules through their Valkey worker-presence keys and renders an
  environment status page plus a 30-day uptime heatmap dashboard

Those modules are intentionally separated. The web app owns contest-admin workflows; the autojudge owns untrusted-code execution and verdict production; the arena owns public participant registration and authentication; the rating worker owns periodic rating recomputation cycles so they run exactly once regardless of how many Arena replicas are deployed; the aiassistant worker owns external AI provider calls and cost recording; the health monitor owns availability observation and uptime history without participating in any business workflow.

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

- **web**: FastAPI server with async database and Valkey connections, serving HTTP requests (port 8000)
- **autojudge**: Independent async worker process with fixed-width concurrency, processing judge jobs
- **arena**: FastAPI server with async database and Valkey connections, serving the Arena platform (port 8001)
- **rating**: Independent single-replica async worker running the Arena rating recomputation loops
- **aiassistant**: Independent async worker dequeuing AI review jobs from
  Valkey, calling the OpenAI Responses API for online user-key reviews, and
  polling OpenAI Batch API jobs for platform-key reviews
- **healthmonitor**: FastAPI server with a Valkey connection only (no database),
  serving the public status and uptime dashboards (port 8002)
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

Each runtime module has its own `pyproject.toml`, build metadata, dependency list, and console script. The runtime entrypoints are:

- `uv run noca-web`
- `uv run noca-arena`
- `uv run noca-autojudge`
- `uv run noca-rating`
- `uv run noca-aiassistant`
- `uv run noca-healthmonitor`

The module packages use Hatchling `dev-mode-dirs = [".."]` and `packages = ["."]`
so workspace installs are true live editable installs. Console scripts resolve
`web`, `arena`, `shared`, `autojudge`, `rating`, `aiassistant`, and
`healthmonitor` from the repository workspace rather than copied package
directories in the virtual environment.

The runtime packages depend on `noca-shared` through the uv workspace source
mapping. This keeps shared schema and service contracts importable without
turning the root project into an installable Python package.

## 4. Data model and schema ownership

PostgreSQL is the system of record. The web, arena, and autojudge modules access
it through SQLAlchemy with async drivers.

Schema ownership is centralized in `shared/db_schema/`:

- `shared/db_schema/` (package) defines the physical SQLAlchemy Core tables
- `web/database.py` binds the web ORM base metadata to that shared metadata
- `arena/database.py` binds the arena ORM base metadata to that shared metadata
- `autojudge/db/` package uses the same shared tables directly through SQLAlchemy Core queries

Alembic migrations target `shared.db_schema.metadata`, so the migration environment
is independent of the `web`, `arena`, and `autojudge` runtime modules. In containers,
each runtime may request migrations during startup via `scripts/run_migrations.py`;
PostgreSQL advisory locking serializes concurrent `alembic upgrade head` attempts.

The web module adds application-specific ORM behavior on top of the shared tables:
relationships, computed properties, hybrid properties, model hooks, and invariants.
The autojudge intentionally does not depend on those ORM hooks. It reads and writes
only the shared schema plus its own focused worker-side data access layer.

Contest-scoped programming language availability is stored in the `contest_languages`
junction table. The web layer uses `get_contest_languages(session, contest)` as the
authoritative query for contest-scoped language lists; the autojudge continues to use
all active languages from the registry.

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
Platform-key jobs first insert a durable `arena_ai_batch_jobs` row, then the
batch poller stores the result after OpenAI completes the batch.

The worker runs four async loops in one deployment unit: the dequeue loop, the
stale-job reaper, the batch poller, and the reconciler. The reaper uses
`ai:queue:inflight:times` to recover queue jobs that were dispatched but not
cleaned up. The batch poller reads non-terminal `arena_ai_batch_jobs` rows,
retrieves OpenAI batch status, stores completed review output in
`arena_submission_ai_reviews`, creates Arena notifications, clears failed retry
flags, and deletes uploaded OpenAI files after terminal states. At the top of each
batch poll cycle a stale-batch detector locally expires batch jobs whose
`submitted_at` is older than `NOCA_AI_BATCH_STALE_HOURS`: in one transaction per
submission it atomically claims the row, refunds the consumed platform credit,
clears `submit_to_ai`, notifies the user, and finalizes the row as `expired`, then
best-effort cancels the OpenAI batch and deletes its files.

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

The healthmonitor module is a standalone FastAPI server (port 8002) with no
database access and no authentication — both of its pages are public. It reads
the Valkey worker-presence keys published by all other runtime modules (the
`web` and `arena` HTTP servers publish presence from their lifespans exactly
like the workers do, under the presence-only `WorkerClass.WEB` / `ARENA`
classes) and serves:

- `/` — the environment status page: one Available/Unavailable/Unknown card per
  service, read live at request time
- `/dashboard` — the uptime dashboard: the same live statuses plus a 30-day
  heatmap per service (60 slots of 12 hours, colored from green at 100% slot
  uptime to red at 70% or below)

A prober loop records one up/down sample per service every
`NOCA_HEALTHMON_PROBE_INTERVAL` seconds into per-slot `up`/`total` hashes
(`noca:healthmon:stats:{service}:{slot_epoch}`), skipping cycles while Valkey
is unreachable so monitor-side outages never count against the services. A
reaper loop deletes slots older than `NOCA_HEALTHMON_RETENTION_DAYS`; slot keys
also carry a TTL as a safety net. The presence-only classes never appear in the
Arena admin dashboard or pause machinery.

### `shared/`

The shared module defines cross-runtime contracts: SQLAlchemy Core schema, enums,
queue payloads, language registry helpers, logging, Valkey services, locks,
scoreboard cache support, email delivery, safe outbound network helpers, image
processing, and other services reused by more than one runtime module.

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

See [CUSTOM_VALIDATOR.md](CUSTOM_VALIDATOR.md) for the authoring workflow, the
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

## 6. Summary

NOCA is a six-process contest platform:

- `web` manages contest and business workflows (port 8000)
- `autojudge` manages sandboxed compilation and execution
- `arena` manages the public Arena participant platform (port 8001)
- `rating` manages the single-replica Arena rating recomputation cycles
- `aiassistant` manages the Arena AI code review pipeline (OpenAI Responses API
  and Batch API)
- `healthmonitor` manages the public availability dashboards (port 8002)
- `shared` defines the common contract between them

The architecture is built around separation of concerns, a shared PostgreSQL schema
contract, lightweight Valkey coordination, shared problem/testcase storage,
server-rendered FastAPI pages with flash-based feedback, role-based access control
with strict contest scoping, and background reapers for asynchronous processing.
