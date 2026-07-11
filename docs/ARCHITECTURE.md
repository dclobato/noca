# NOCA Architecture Overview

This document summarizes the main system design and application architecture of NOCA.
For detailed runtime behavior, module internals, coordination flows, security, and
operational consequences, see [ARCHITECTURE_RUNTIME.md](ARCHITECTURE_RUNTIME.md).

Related references:
- [ARCHITECTURE_RUNTIME.md](ARCHITECTURE_RUNTIME.md) for detailed runtime architecture and operational constraints
- [autojudge/docs/AUTOJUDGE_INFRA.md](../autojudge/docs/AUTOJUDGE_INFRA.md) for worker isolation, queue protocol, and container execution details
- [DATA_FLOW_FROM_SUBMISSION_TO_VERDICT.md](DATA_FLOW_FROM_SUBMISSION_TO_VERDICT.md) for the submission lifecycle
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

NOCA is split into five main runtime modules:

- `web/`: the FastAPI application that serves HTML pages, handles authentication, enforces authorization, manages contests/problems/users, and creates judging work
- `autojudge/`: the asynchronous judge worker that consumes queued judgments, compiles and runs submissions inside containers, and writes results back
- `arena/`: the FastAPI application that serves the Arena platform, with its own user identity domain, OTP-protected accounts, and login history
- `rating/`: the single-replica Arena rating worker that periodically recomputes problem difficulty, user scores, and affiliation ratings, and publishes the next-cycle timestamp to Valkey for the Arena footer
- `aiassistant/`: the Arena AI review worker that dequeues AI review jobs,
  uses the OpenAI Responses API for user-key reviews, uses the OpenAI Batch API
  for platform-key reviews, and stores feedback in the database

Those modules are intentionally separated. The web app owns contest-admin workflows; the autojudge owns untrusted-code execution and verdict production; the arena owns public participant registration and authentication; the rating worker owns periodic rating recomputation cycles so they run exactly once regardless of how many Arena replicas are deployed; the aiassistant worker owns external AI provider calls and cost recording.

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

Each runtime module has its own `pyproject.toml`, build metadata, dependency list, and console script. The runtime entrypoints are:

- `uv run noca-web`
- `uv run noca-arena`
- `uv run noca-autojudge`
- `uv run noca-rating`
- `uv run noca-aiassistant`

The module packages use Hatchling `dev-mode-dirs = [".."]` and `packages = ["."]`
so workspace installs are true live editable installs. Console scripts resolve
`web`, `arena`, `shared`, `autojudge`, `rating`, and `aiassistant` from the
repository workspace rather than copied package directories in the virtual environment.

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

Cross-module security auditing shares a single `security_events` table (owned by
`shared.services.security_events`) rather than per-domain audit tables. Each row
snapshots both the opaque `actor_user_id` and a human-readable `actor_label` (the
actor's login, e.g. email/username) captured at event time, so the admin viewers
can name who originated an `auth_*` / `parental_*` event without a lookup that could
break on account rename or deletion. Both the
Web and Arena HTTP processes append to it: authentication failures, throttle
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
`request.client.host` — raw `X-Forwarded-For` is never trusted.

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

### `shared/`

The shared module defines cross-runtime contracts: SQLAlchemy Core schema, enums,
queue payloads, language registry helpers, logging, Valkey services, locks,
scoreboard cache support, email delivery, safe outbound network helpers, image
processing, and other services reused by more than one runtime module.

## 6. Summary

NOCA is a five-process contest platform:

- `web` manages contest and business workflows (port 8000)
- `autojudge` manages sandboxed compilation and execution
- `arena` manages the public Arena participant platform (port 8001)
- `rating` manages the single-replica Arena rating recomputation cycles
- `aiassistant` manages the Arena AI code review pipeline (OpenAI Responses API
  and Batch API)
- `shared` defines the common contract between them

The architecture is built around separation of concerns, a shared PostgreSQL schema
contract, lightweight Valkey coordination, shared problem/testcase storage,
server-rendered FastAPI pages with flash-based feedback, role-based access control
with strict contest scoping, and background reapers for asynchronous processing.
