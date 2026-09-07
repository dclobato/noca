# NOCA Architecture Overview

This document is the entry point to NOCA's architecture. It describes the
runtime modules, what each one owns, how they communicate across the
infrastructure boundary, how the code is laid out as a uv workspace, and who
owns the database schema. The design decisions that belong to a single module
live in that module's own architecture document, listed in
[section 5](#5-per-module-architecture-documents).

Related references:
- [ARCHITECTURE_RUNTIME.md](ARCHITECTURE_RUNTIME.md) for detailed runtime architecture and operational constraints
- [autojudge/docs/AUTOJUDGE_INFRA.md](../autojudge/docs/AUTOJUDGE_INFRA.md) for worker isolation, queue protocol, and container execution details
- [DATA_FLOW_FROM_SUBMISSION_TO_VERDICT.md](DATA_FLOW_FROM_SUBMISSION_TO_VERDICT.md) for the submission lifecycle
- [CONTEST_BACKUP_FORMAT.md](CONTEST_BACKUP_FORMAT.md) for the contest
  backup/restore ZIP format (version 6, the only version restored) and fidelity notes
- [Interactive validator guide](custom-validator/INTERACTIVE_VALIDATOR.md) for
  authoring, exit codes, and applicable limits
- [Output checker validator rationale](custom-validator/OUTPUT_CHECKER_VALIDATOR.md)
  for the planned non-interactive output-checker strategy
- [FASTAPI_FLASH.md](FASTAPI_FLASH.md) for the flash-message pattern used in the web and arena modules
- [web/docs/ROUTES.md](../web/docs/ROUTES.md) and [web/docs/SERVICES.md](../web/docs/SERVICES.md) for web-layer responsibilities
- [SHARED_SERVICES.md](SHARED_SERVICES.md) for cross-module shared services (email, network, image, Valkey, locks)
- [VALKEY_CACHING.md](VALKEY_CACHING.md) for the Valkey entries written to avoid expensive recomputation

## 1. High-level design

NOCA is split into nine main runtime modules:

- `web/`: the FastAPI application that serves HTML pages, handles authentication, enforces authorization, manages contests/problems/users, and creates judging work
- `autojudge/`: the asynchronous judge worker that consumes queued judgments, compiles and runs submissions inside containers, and writes results back
- `arena/`: the FastAPI application that serves the Arena platform, with its own user identity domain, OTP-protected accounts, and login history
- `rating/`: the single-replica Arena rating worker that periodically recomputes problem difficulty, user scores, and affiliation ratings, and publishes the next-cycle timestamp to Valkey for the Arena footer
- `aiassistant/`: the Arena AI review worker that dequeues AI review jobs,
  uses the OpenAI Responses API for user-key reviews, uses the OpenAI Batch API
  for platform-key reviews, and stores feedback in the database
- `mailer/`: the single-replica outbound-email worker that drains the Valkey
  mail queue the Web and Arena processes fill with fully rendered messages and
  delivers them through the configured provider at the deployment's own pace
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
external AI provider calls and cost recording; the mailer owns every
conversation with the email provider, so no HTTP process blocks on SMTP and the
deployment's sending rate is set in exactly one place; the health monitor owns
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

Outbound email crosses the same boundary, and the `mailer` worker is the only
process in NOCA that talks to a mail provider. The Web and Arena HTTP processes
hold no SMTP settings at all: every email is rendered in the process that
decided to send it and handed, fully formed, to the shared `EmailService`,
which charges the acting user's per-actor budget and pushes a `MailJob` onto the
Valkey mail queue (`mail:queue:*`, `mail:job:{id}` with a TTL). The worker
delivers it at the deployment's own pace, with retries -- or, with sending
disabled, logs it, which is what a development install runs. That enqueue is
the one Valkey write that is never buffered through an outage: a rendered
message has no database row to recover from, so an unreachable queue is
reported to the caller as "not sent" rather than accepted. Because a deployment
without a mailer would accept mail it never delivers, Web and Arena refuse to
start until a mailer has published its presence (`wait_for_mailer`, bounded by
`NOCA_STARTUP_TIMEOUT_SECONDS`); that is a startup dependency only -- a send
never checks mailer liveness, so a mailer restart refuses no request. See
[SHARED_SERVICES.md](SHARED_SERVICES.md) for the contract and the budget's
fail-open rule.

User online-presence (the green dot on avatars) lives entirely on the Valkey
side of the boundary: the shared `user_presence` service writes a short-TTL live
key per user and reads presence in batch, best-effort with no database writes, so
a Valkey outage simply shows everyone as offline.

Authenticated worker pause/resume follows the boundary too. The Arena admin can
pause or resume the queue-consuming workers (autojudge, aiassistant, mailer;
rating is always-on) from the dashboard. PostgreSQL is the authoritative, monotonic source
of truth (`arena_worker_pause_state`); the Arena route commits the pause-state
bump and an `arena_worker_command_audit` row before publishing a signed
`HMAC-SHA256` command over Valkey. That command is only an authenticated *nudge*:
each worker derives its paused state solely from committed PG rows and treats the
command as a trigger to reconcile now, so a replayed, forged, raced, or
undelivered command can never advance state on its own (workers reconcile from PG
every poll and at startup). See [SHARED_SERVICES.md](SHARED_SERVICES.md) for the
trust and ordering model.

Long-lived Server-Sent-Events connections are bounded across the same boundary.
Every SSE route in Web, Arena, and the animator holds a slot in a Valkey-backed
connection lease (`shared/services/sse_connection_limit.py`): one gauge per
module bucket and client IP, plus one per authenticated user on the Web runs
stream and both Arena streams, taken before the handler runs and released when
the client disconnects, with a TTL that the open connection renews so only a
crashed process can leak a slot. The lease fails open -- a Valkey outage never
refuses a stream -- and the animator adds a per-process ceiling that needs no
Valkey at all. See [SHARED_SERVICES.md](SHARED_SERVICES.md).

Runtime isolation:

- **web**: FastAPI server with async database and Valkey connections, serving HTTP requests (default port 8000)
- **autojudge**: Independent async worker process with fixed-width concurrency, processing judge jobs
- **arena**: FastAPI server with async database and Valkey connections, serving the Arena platform (default port 8001)
- **rating**: Independent single-replica async worker running the Arena rating recomputation loops
- **aiassistant**: Independent async worker dequeuing AI review jobs from
  Valkey, calling the OpenAI Responses API for online user-key reviews, and
  polling OpenAI Batch API jobs for platform-key reviews
- **mailer**: Independent single-replica async worker draining the Valkey mail
  queue and delivering through SMTP off any request path; pausable from the
  Arena dashboard like the other queue consumers
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
- `noca-mailer` from `mailer/`

Each runtime module has its own `pyproject.toml`, build metadata, dependency list, and console script. The runtime entrypoints are:

- `uv run noca-web`
- `uv run noca-arena`
- `uv run noca-autojudge`
- `uv run noca-rating`
- `uv run noca-aiassistant`
- `uv run noca-healthmonitor`
- `uv run noca-animator`
- `uv run noca-mailer`

The module packages use Hatchling `dev-mode-dirs = [".."]` and `packages = ["."]`
so workspace installs are true live editable installs. Console scripts resolve
`web`, `arena`, `shared`, `autojudge`, `rating`, `aiassistant`,
`healthmonitor`, `animator`, and `mailer` from the repository workspace rather than copied
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
`mailer`, `animator`) are pure schema consumers:
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

Table-level decisions are documented with the module that owns the tables and
the reasoning behind them:

- The cross-domain problem model (stored validation strategy, the two
  generation counters, editorials, test-case storage, packages, and the
  import and edit durability rules), the platform announcement board,
  `security_events`, and the HTTP hardening every server installs are in
  [ARCHITECTURE_SHARED.md](ARCHITECTURE_SHARED.md).
- Contest-side tables and workflows (`users_media`, `contest_languages`,
  clarification read state, problem-set release, contest backups, permanent
  contest removal, and the non-scoring solution-test tables) are in
  [ARCHITECTURE_WEB.md](ARCHITECTURE_WEB.md).
- Arena identity, parental consent, the age shield, avatars, signup
  reputation, Google identities, required announcements, and the worker
  pause tables are in [ARCHITECTURE_ARENA.md](ARCHITECTURE_ARENA.md).
- Badges, the difficulty histogram, and the author's difficulty estimate are
  in [ARCHITECTURE_RATING.md](ARCHITECTURE_RATING.md).
- The animator gate, medal cutoffs, and operator secrets are in
  [ARCHITECTURE_ANIMATOR.md](ARCHITECTURE_ANIMATOR.md).

## 5. Per-module architecture documents

Every runtime module, and the `shared/` contract layer, has an architecture
document of its own. This overview is the place for anything that spans
modules -- the boundary, the workspace, schema ownership -- and a decision that
belongs to one module belongs in that module's document, so a change to how a
module works updates the module document and a change to how modules relate
updates this one.

- [ARCHITECTURE_WEB.md](ARCHITECTURE_WEB.md) -- the contest administration and
  participant application: authentication, team session binding, contest-side
  tables, problem-set release, backups, permanent removal, and non-scoring
  solution tests
- [ARCHITECTURE_AUTOJUDGE.md](ARCHITECTURE_AUTOJUDGE.md) -- the sandboxed judge
  worker and where its execution details are documented
- [ARCHITECTURE_ARENA.md](ARCHITECTURE_ARENA.md) -- the public Arena platform:
  default-deny access, identity and the age shield, parental consent, Google
  sign-in, required announcements, and the worker pause tables
- [ARCHITECTURE_RATING.md](ARCHITECTURE_RATING.md) -- the single-replica rating
  worker, badges, and problem difficulty
- [ARCHITECTURE_AIASSISTANT.md](ARCHITECTURE_AIASSISTANT.md) -- the AI review
  worker and its batch, reaper, and reconciler loops
- [ARCHITECTURE_MAILER.md](ARCHITECTURE_MAILER.md) -- the outbound-email worker
  and the mail queue it drains
- [ARCHITECTURE_HEALTHMONITOR.md](ARCHITECTURE_HEALTHMONITOR.md) -- the public
  uptime dashboard, its prober, and its amplification guards
- [ARCHITECTURE_ANIMATOR.md](ARCHITECTURE_ANIMATOR.md) -- the live scoreboard
  and reveal ceremony: feeds, event stream, ceremony state, and operator control
- [ARCHITECTURE_LANDINGPAGE.md](ARCHITECTURE_LANDINGPAGE.md) -- the Caddy-served
  environment entry point
- [ARCHITECTURE_SHARED.md](ARCHITECTURE_SHARED.md) -- the contracts both domains
  share: the problem model and its durability rules, interactive validators and
  sample interactions, the announcement board, security auditing, and the HTTP
  hardening every server installs

## 6. Summary

NOCA is a nine-process contest platform:

- `web` manages contest and business workflows (default port 8000)
- `autojudge` manages sandboxed compilation and execution
- `arena` manages the public Arena participant platform (default port 8001)
- `rating` manages the single-replica Arena rating recomputation cycles
- `aiassistant` manages the Arena AI code review pipeline (OpenAI Responses API
  and Batch API)
- `mailer` manages outbound email delivery, at the deployment's pace, off every request path
- `healthmonitor` manages the public uptime dashboard (default port 8002)
- `animator` manages the public live scoreboard and reveal presentation (default port 8003)
- `landingpage` serves the environment entry point and public module links
  (default internal port 8080)
- `shared` defines the common contract between them

The architecture is built around separation of concerns, a shared PostgreSQL schema
contract, lightweight Valkey coordination, shared problem/testcase storage,
server-rendered FastAPI pages with flash-based feedback, role-based access control
with strict contest scoping, and background reapers for asynchronous processing.
