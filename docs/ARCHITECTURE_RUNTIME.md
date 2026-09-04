# NOCA Runtime Architecture Reference

This document is the detailed runtime companion to [ARCHITECTURE.md](ARCHITECTURE.md).
Use the overview first for system boundaries, package layout, and schema ownership;
use this file for module internals, coordination flows, judging behavior, RBAC,
background processing, security, and operational consequences.

Related references:
- [ARCHITECTURE.md](ARCHITECTURE.md) for the concise system overview
- [autojudge/docs/AUTOJUDGE_INFRA.md](../autojudge/docs/AUTOJUDGE_INFRA.md) for worker isolation, queue protocol, and container execution details
- [DATA_FLOW_FROM_SUBMISSION_TO_VERDICT.md](DATA_FLOW_FROM_SUBMISSION_TO_VERDICT.md) for the submission lifecycle
- [FASTAPI_FLASH.md](FASTAPI_FLASH.md) for the flash-message pattern used in the web and arena modules
- [web/docs/ROUTES.md](../web/docs/ROUTES.md) and [web/docs/SERVICES.md](../web/docs/SERVICES.md) for web-layer responsibilities
- [SHARED_SERVICES.md](SHARED_SERVICES.md) for cross-module shared services (email, network, image, Valkey, locks)

## Table of Contents

- [1. Module responsibilities](#1-module-responsibilities)
- [2. Communication model between `web` and `autojudge`](#2-communication-model-between-web-and-autojudge)
- [3. Judging architecture](#3-judging-architecture)
- [4. Web messaging and UI flow](#4-web-messaging-and-ui-flow)
- [5. Identity, contest scoping, and RBAC](#5-identity-contest-scoping-and-rbac)
- [6. Background processing and reapers](#6-background-processing-and-reapers)
- [7. Security and middleware](#7-security-and-middleware)
- [8. Important design consequences](#8-important-design-consequences)
- [9. Summary](#9-summary)

## 1. Module responsibilities

### `web/`

The web module is a server-rendered FastAPI application with these main layers:

**Core application (`web/*.py`)**:
- `main.py`: FastAPI application factory with lifespan management, route mounting, and service initialization
- `config.py`: Configuration management with environment variables
- `database.py`: SQLAlchemy async session factory for web module consumption
- `dependencies.py`: FastAPI dependencies for authentication and contest scoping
- `healthcheck.py`: container healthcheck entrypoint for heartbeat

**Routes (`web/routes/`)**:
- Focused route modules handling auth, contest dashboards, scoreboard, runs, submissions, admin, reports, profile, assets, and health checks
- Request handling, redirects, template rendering, form parsing, and route-level authorization
- Each functional area is separated into focused modules (e.g., `contest_admin_problem.py`, `contest_submissions.py`)

**Services (`web/services/`):**
- Business-logic modules reused across routes
- Key services: `authentication_service.py`, `email_service.py`, `judging_service/`, `contest_service/`, `problem_service/`, `clarification_service/`, `task_service/`, `submission_service.py`, `scoreboard/`
- Reaper services and Valkey integration for background processing and live UI updates

**Email service (`app.state.email_service`):**
- Registered during lifespan startup via `EmailConfig.from_settings(settings)` → `EmailService(config, logger)`.
- The email stack is canonical under `shared/services/`: `email_service.py` (config and orchestration), `email_validation.py` (`EmailValidationService`), `email_models.py` (message/result dataclasses and the shared `build_rfc5322_address` helper), and `email_providers.py` (provider contract and concrete implementations).
- Provider is selected exclusively through `NOCA_SEND_EMAIL` and `NOCA_EMAIL_PROVIDER`; when `SEND_EMAIL=false` or provider is `mock`, `MockProvider` is used regardless.
- To add a new provider, implement `EmailProvider` in `shared/services/email_providers.py`, add provider-specific settings to `web/config.py` with validation in `validate_email_settings`, and map the new provider in `EmailConfig.create_provider()`.

**Models (`web/models/`):**
- `contest.py`, `submission.py`, `problem.py`, `users.py`, `clarification.py` with ORM behavior
- ORM mapping with relationships, computed properties, hybrid properties, model hooks, and invariants

**Middleware (`web/middleware/`):**
- Session middleware configured in main.py

**Templates (`web/template/`):**
- Jinja2 templates organized by subdirectory: `auth/` (login pages), `contest/` (participant-facing pages), `admin/` (contest admin tools with `problems/`, `users/`, and `clarifications/` subfolders), `profile/` (user self-service), `uberadmin/` (uber admin UI), `submissions/` (review UI), `email/` (email templates)
- Shared layout and macros live at the root: `_base.html`, `_macros.html`, `_contest_macros.html`, `_timing_timeline.html`
- Flash message integration via `fastapi_flash`

**Static assets:**
- App-specific CSS, JavaScript, and image assets live under `web/static/`.
- Shared offline vendor assets and webfonts live under `shared/static/` and are
  mounted by both the web and arena applications.
- Country flag SVGs are generated into `shared/static/vendor/img/flags/` by
  `scripts/fetch_assets.py`, which downloads a pinned commit of
  `hampusborgos/country-flags` (SHA recorded in `[tool.assets]` of the root
  `pyproject.toml`). They are served at `/static/vendor/img/flags/{code}.svg` by
  the `static_vendor` mount in the arena app.
- Circular Brazilian national and UF flag SVGs are generated into
  `shared/static/vendor/img/state-flags/` from the pinned
  `pierrelapalu/icones-bandeiras-br-uf` archive. The national flag is `BR.svg`,
  and each UF uses its uppercase two-letter code. The shared `static_vendor`
  mount serves them at `/static/vendor/img/state-flags/{code}.svg`.
- The `/static/css` and `/static/js` mounts (both the app-specific and
  `shared-*` variants, in web, arena, animator, and healthmonitor) use
  `shared.static_files.RevalidatedStaticFiles` instead of plain `StaticFiles`,
  stamping every response with `Cache-Control: no-cache`. The top-level bundles
  (`contest.css`, `arena.css`) are requested with a `?v={app_version}`
  cache-busting query, but the CSS files they pull in via `@import` -- 40-odd of
  them, including the `common.css` that owns the rendered-Markdown rules -- and
  the shared JS files referenced from templates are not individually versioned.
  A browser therefore refetches the top-level bundle, sees the same import URLs,
  and reuses whatever it already had underneath. `no-cache` requires it to
  revalidate first; `StaticFiles` answers an unchanged file with a bodiless
  `304`, so the cost is a header exchange rather than a transfer. This replaced
  a bounded `max-age=300`, which still left a five-minute window in which a
  deployed page rendered against the previous release's stylesheet -- the
  failure looks like table borders and heading gaps disappearing after an
  upgrade. `no-cache` is not `no-store`: caching is allowed, checking first is
  mandatory. `tests/shared/test_static_files_cache.py` pins both the directive
  and the `304`.

It owns:

- **Authentication and authorization**: JWT-based auth with contest-scoped roles (UBERADMIN, ADMIN, JUDGE, STAFF, TEAM, USER)
- **Contest management**: Full CRUD operations for contests, with timing-derived states such as upcoming, running, frozen-scoreboard, and past
- **Problem management**: Problem creation, editing, import/export, test case management, and statement handling
- **Problem profiling**: Auto-Limit profiling runs backed by PostgreSQL records plus a dedicated Valkey profiling queue
- **User management**: Contest-scoped user accounts, site assignment, batch import, role assignments, and profile/photo handling
- **Clarification workflow**: Request, answer, and visibility control for team clarifications
- **Task queue management**: BALLOON, PRINT, and SOS tasks with staff acquisition and processing
- **Submission lifecycle**: Upload, queue to autojudge, review-lock acquisition, human confirmations, rejudging, and manual overrides
- **Scoreboard**: Live, frozen, and released-final scoreboards with Valkey-backed caching
- **Chief judge workflow**: Special role for verdict overrides and final decisions (shared with ADMIN — see [ROUTES.md § Permission Model](../web/docs/ROUTES.md#permission-model))

### `autojudge/`

The autojudge module is a separate async worker process that owns:

**Core worker (`worker.py`):**
- Process bootstrap, resource lifecycle, and graceful shutdown (`run_worker` / `main`)
- Fixed-width concurrency: one async consumer loop per slot (`_worker_loop`)
- Optional startup sync of canonical judge image refs from `NOCA_JUDGE_IMAGE_*` settings into the
  database-backed language registry before Docker preflight
- Re-exports `dispatch.py` and `reconcile.py` entry points (`_dispatch_job`, `_process_job`,
  `_reconcile_queue_state`, `_reconcile_loop`) so existing import paths keep working

**Job dispatch (`dispatch.py`):**
- Routes one dequeued job to its pipeline by `JobKind`, owns the idempotency lock (`dispatch_job`)
- Lock-and-run web-submission entry point used directly by tests (`process_job`)
- On failure, persists a terminal FAILED verdict via a single kind-aware helper
  (`_persist_job_failure`), handling the web→arena `LookupError` fallback

**Queue reconciliation (`reconcile.py`):**
- Startup and periodic reconciliation (`reconcile_queue_state` / `reconcile_loop`):
  re-enqueues non-terminal jobs (QUEUED/DISPATCHED/JUDGING) missing from the
  Valkey queue, recovering jobs lost between a producer's DB commit and its
  follow-up enqueue without waiting for a worker restart
- Rebuilds *missing* queue state only, decided from queue membership rather than
  database status. A conservative snapshot skips known queued work, while every
  repair or enqueue uses an atomic current-state transition. Inflight jobs are
  left to the reaper, locked jobs are left alone, and an already-queued job is
  never enqueued twice
- The five job kinds (submission, profiling, solution test, Arena submission,
  custom-validator validation) share one parameterized rebuild loop driven by a
  per-kind spec

**Valkey decoding (`valkey_decode.py`):**
- `decode_valkey_scalar`, `hash_requeue_count` — normalize raw `bytes` queue values

**Heartbeat (`heartbeat.py`):**
- Heartbeat file management for health monitoring
- `touch_heartbeat_file`, `remove_heartbeat_file`, `heartbeat_loop`
- Compatibility export of `worker_id`, which is centrally implemented in `worker_identity.py`

**Worker identity (`worker_identity.py`):**
- Shared worker identity generation used by the main worker loop and Docker container labels
- `worker_id`
- Process-scoped, not attempt-scoped: two concurrent attempts at the same run (a reaper
  requeue overtaking a slow-but-alive attempt) normally share it. Ownership of a run is
  therefore tracked by the per-attempt `attempt_token` stamped at dispatch on all four
  worker-owned run tables (`submission_judgments`, `arena_submission_judgments`,
  `profiling_runs`, `solution_test_runs`), not by `worker_id` — see
  [AUTOJUDGE_INFRA.md](../autojudge/docs/AUTOJUDGE_INFRA.md)

**Image management (`image_sync.py`):**
- Registry image sync at startup: `sync_registry_images_from_settings`, `assert_required_images_present`
- Image pull policy enforcement: `_ensure_image_available`, `_pull_image`, `_local_image_exists`
- Canonical image ref derivation per language ID

**Queue operations (`queue_ops.py`):**
- Low-level Valkey queue primitives: `dequeue_job_id`, `remove_from_inflight`, `get_job_kind`, `publish_verdict`

**Submission job (`submission_job.py`):**
- Full submission judgment pipeline: `process_submission_job`
- Test case loading (`_load_test_cases`) and repeated execution (`_run_repeated_test_case`)
- `_load_test_cases` normalizes content to Unix line endings (LF only) at read time, covering files already on disk with CRLF endings from Windows-originated uploads

**Arena submission job (`arena_submission_job.py`):**
- Arena-specific adapter for `JobKind.ARENA_SUBMISSION`
- Uses the same compile/run/container pipeline as contest submissions
- Loads test case identity and order from PostgreSQL, then reads input and
  expected-output bytes from `<testcase-root>/arena/<problem_id>/`
- Stores only the first non-AC case result
- Writes final verdicts directly to Arena tables and publishes a best-effort
  `ArenaVerdictEvent` on `arena:results`
- Normalizes test case files to Unix line endings (LF only) at read time,
  covering files written with CRLF endings

**Profiling job (`profiling_job.py`):**
- Auto-Limit profiling pipeline: `process_profiling_job`
- Hard limit derivation and profiled limit persistence

**Runtime helpers (`runtime_utils.py`):**
- Shared runtime predicates reused by multiple job pipelines
- `is_recoverable_isolate_runtime_error` for transient isolate/cgroup retry decisions; it also
  matches the "suspicious sandbox signal kill" errors raised by the runner

**Compilation (`compiler.py`):**
- `compile_submission`: runs the language compile command inside a short-lived Docker container
- Produces a binary artifact returned as raw bytes

**Execution pipeline (`runner.py`):**
- `run_test_case`: injects artifact + input, runs `isolate`, parses meta, returns `RunResult`
- `RunResult` carries `exit_signal` (isolate `exitsig`) so signal deaths are persisted per test case
  and surfaced in the UI as e.g. "SIGSEGV — segmentation fault" instead of a bare RE
- Suspicious signal kills (status `SG`, near-zero wall time, sub-MB memory, no stdout, no stderr)
  are treated as judge-environment failures, not contestant crashes: the runner raises a recoverable
  `IsolateError`, the job pipeline recycles the container and retries the test case once, and a second
  occurrence marks the judgment FAILED instead of assigning the contestant a false RE verdict

**Isolate sandbox (`sandbox.py`):**
- Isolate box lifecycle: `_sync_isolate_init`, `_sync_isolate_cleanup`, `_sync_reset_run_artifacts`, `_sync_run_isolate`
- Meta parsing: `_parse_isolate_meta`, `_resolve_peak_pids`, `_read_isolate_cgroup_peak_pids`
- Judge images are standardized on Debian-family or other mainstream glibc-based bases to keep runtime
  loader paths predictable inside the isolate sandbox; each `:run` image receives the `isolate` binary
  via `COPY --from` of the shared `noca/isolate-base` build-time artifact rather than recompiling it
- The shared isolate binary is built from upstream `ioi/isolate` v2.7. Run images install both
  `libcap2` and `libseccomp2` so the copied binary can start, while `isolate-base` installs
  `libcap-dev` and `libseccomp-dev` only for compilation.
- Isolate's inner seccomp filtering applies to contestant processes and is independent of
  `NOCA_JUDGE_DOCKER_APPARMOR_PROFILE`, which controls the outer Docker AppArmor profile.

**Container I/O (`container_io.py`):**
- Docker tar stream helpers: `_put_bytes`, `_get_file_bytes_safe`, `_get_file_text_safe`, `_get_file_size_safe`

**Container pool (`pool.py`, `container_pool.py`):**
- `ContainerPool` (`container_pool.py`): Docker container lifecycle, fixed-size pool with byte-range allocation, port conflict detection
- `PoolManager` (`pool.py`): higher-level acquire/release interface used by job processors

**Database access (`db/` package):**
- `db/__init__.py`: re-exports all public symbols; callers use `from autojudge.db import DatabaseAccess`
- `db/engine.py`: `create_worker_engine`, `open_db` context manager
- `db/access.py`: `DatabaseAccess` assembled from mixins below
- `db/_base.py`: shared engine holder and audit helpers
- `db/_languages.py`: language registry queries
- `db/_submission.py`: submission dequeue and recovery
- `db/_arena_submission.py`: Arena submission dequeue, recovery, judgment transitions, and first-solve stats
- `db/_problem.py`: problem limits and test case map
- `db/_judgment.py`: judgment state transitions and balloon creation
- `db/_profiling.py`: profiling run lifecycle
- `db/_results.py`: test result and profiling case result persistence

**Reaper logic (`reaper.py`):**
- Stale in-flight job detection and atomic recovery, including current timestamp
  and inflight-membership revalidation across worker replicas
- Zombie container cleanup

**Shared types (`types.py`):**
- Dataclasses shared across judge modules: `CompileResult`, `RunResult`, `IsolateMeta`, `IsolateError`, `ProblemLimits`, `SubmissionSource`, `RepetitionCaseResult`
- DB-layer types: `QueuedSubmission`, `QueuedProfilingRun`, `RecoverableSubmissionJob`, `RecoverableProfilingJob`, `ProfilingObservedLimits`

**Verdict aggregation (`verdict.py`):**
- Output comparison and verdict priority ordering (CE → RE → TLE → MLE → OLE → WA → PE → AC)

**Supporting modules:**
- `config.py`: Judge-specific configuration (Pydantic settings)
- `healthcheck.py`: Container healthcheck entrypoint for heartbeat freshness
- `languages.py`: Language registry integration

The worker architecture is fixed-width concurrency: N async worker loops + 1 reaper loop + 1 reconciler loop, all managed by `asyncio.gather()`.

The judge queue carries five first-class job kinds in the same Valkey hash namespace:

- `submission` jobs for normal/rejudge submission judgments
- `arena_submission` jobs for Arena submission judgments
- `profiling` jobs for Auto-Limit reference implementations
- `solution_test` jobs for non-scoring judge/admin solution tests
- `custom_validator_validation` jobs for staged interactive validator candidates

Profiling jobs are consumed from a dedicated priority queue before normal contest submissions. The worker persists profiling history in PostgreSQL and only applies computed `ProblemLanguageLimit` rows when the reference implementation returns `AC` for every test case.

Per-language profiling behavior is stored in the database-backed language registry. Each language carries a default profiling repetition count plus a minimum profiled PID floor so Auto-Limit runs can use different timing and process-safety defaults for native binaries versus interpreter or managed runtimes. When Auto-Limit persists a `ProblemLanguageLimit`, it also stores the repetition count used for that profiling run so later language-default changes do not alter existing judging semantics. If a problem/language pair has no explicit row, judging falls back to the problem-level resource limits with exactly 1 repetition.

Running-contest problem limit edits are tracked as persisted limit-change batches. Each batch stores the languages whose effective limits changed plus the captured set of currently active submissions affected at save time: `AC`, `RE`, `TLE`, `MLE`, `OLE`, and `PE` only when the contest treats `PE` as accepted. `WA`, `CE`, and non-accepted `PE` are excluded.

When `NOCA_JUDGE_IMAGE_REGISTRY` is configured, the worker treats the database-backed language registry
as runtime state for Docker image refs instead of immutable seed data. Startup derives canonical
compile/run refs from each active `language.id`, optionally pulls those images through the Docker
daemon according to `NOCA_JUDGE_IMAGE_PULL_POLICY`, persists the effective refs back into PostgreSQL,
and then runs the usual local-only image presence check. This lets container-only deployments keep
using stable language IDs while changing the actual image registry/tag scheme across releases.
`NOCA_JUDGE_IMAGE_NAMING=path` supports nested repos such as `ghcr.io/org/repo/judge-python3`,
while `NOCA_JUDGE_IMAGE_NAMING=flat` supports flattened repos such as
`docker.io/org/repo-judge-python3`.

### `shared/`

The shared module contains the pieces all runtime modules must agree on:

**Schema and data structures:**
- `db_schema/`: Centralized SQLAlchemy Core table definitions
- `queue_schema.py`: Pydantic models for judge, profiling, Arena submission,
  Arena AI review, and verdict-event queue payloads
- `enumerations.py`: RoleEnum, Verdict, JudgmentStatus, ContestStatus, TaskType, Environment

**Services:**
- `services/valkey_service/`: shared Valkey package for runtime lifecycle, queue operations, and queue metrics
- `services/lock_service.py`: ephemeral Valkey TTL locks for clarifications, staff tasks, and submission reviews
- `services/scoreboard_cache.py`: Scoreboard caching with TTL management

**Language support:**
- `language_registry.py`: Language configuration and compilation/execution commands
- `language_configs.py`: Language runtime configuration models
- `language_stubs.py`: Template source used for language-specific starter code

**Utilities:**
- `app_logging.py`: Structured logging configuration
- `timezone.py`, `timing.py`: Time-related utilities
- Wordlists for content filtering/security

Shared runtime services used by both `web` and `arena` live under `shared/services/`.
This includes email delivery and validation, safe outbound network helpers, IP
geolocation, image processing, token revocation, Valkey runtime helpers, locks,
and scoreboard cache support. `web/services/` keeps compatibility re-export shims
for the migrated services while new code imports from `shared.services.*` directly.

### `arena/`

The arena module is a second FastAPI server (default port 8001) that owns the public-facing Arena platform:

**Core application (`arena/*.py`)**:
- `main.py`: FastAPI application factory with lifespan management and service initialization
- `config.py`: Pydantic `BaseSettings` (same `NOCA_` prefix as
  `web/config.py`); omits contest-admin and judge-queue settings; Arena uses
  `NOCA_ARENA_APP_NAME` (default `"noca-arena"`) as its JWT issuer
- `database.py`: SQLAlchemy async engine and session factory; `ArenaBase` shares `shared_metadata` so Alembic manages all tables in one migration history
- `healthcheck.py`: container healthcheck entrypoint that probes the local
  `/health` endpoint over loopback on `NOCA_ARENA_PORT`

**Models (`arena/models/`)**:
- `arena_users.py`: `ArenaUser`, `ArenaBackup2FA`, `ArenaLoginHistory` ORM models with password hashing, photo helpers, age calculation, and TOTP support
- `_otp_secret` is stored via `EncryptedString` (a `TypeDecorator` in `shared/db_schema/custom_types.py`); the `SecretsManager` instance must be registered with `init_encrypted_string()` at startup before any DB I/O on that column

**Secrets management**:
- `SecretsConfig.from_environment()` (from `dclobato/secrets-manager`) reads `ENCRYPTION_KEYS__<version>`, `ENCRYPTION_SALT__<version>` (base64), and `ACTIVE_ENCRYPTION_VERSION` from the environment — these are not `NOCA_`-prefixed
- `SecretsManager` supports multiple key versions so OTP secrets can be re-encrypted to a new key without downtime
- `init_encrypted_string(manager)` registers the manager globally in `shared/db_schema/custom_types`; because `web` and `arena` run in separate OS processes they each have an independent module namespace

**Identity domain**:
- Arena users (`ArenaUser`) are entirely separate from contest users (`User`) and uber-admins (`UberAdmin`)
- Arena JWT tokens use `NOCA_ARENA_APP_NAME` as their issuer. It must differ
  from the web module's `NOCA_WEB_APP_NAME` so tokens aren't accepted across
  modules
- Arena signup and login apply LGPD age gates through `shared/age_check.py`: users under 13 are blocked, users from 13 to 17 require parent/legal guardian consent, and legacy users missing date of birth must regularise it before a session token is issued

### `rating/`

The rating module is a standalone single-replica worker that owns the Arena
rating recomputation cycles. It exists because these loops previously ran inside
the Arena FastAPI lifespan, so every Arena replica behind a load balancer ran a
duplicate set of cycles writing the same `arena_*_rating*` tables.

- `config.py`: Pydantic `BaseSettings` (`NOCA_` prefix, shared `.env`) — only the
  DB, Valkey, and rating-cadence subset of Arena settings
- `database.py`: async SQLAlchemy engine + session factory (Core queries against
  the shared schema; no ORM base)
- `loops.py`: `run_problem_rating_loop`, `run_user_rating_loop`,
  `run_affiliation_rating_loop` — the sequential `problems → users → affiliations`
  chain, importing the pure rate functions from `shared/services/arena_rating.py`
- `worker.py`: `main` / `run_rating_worker` — boots the engine + `ValkeyRuntime`,
  installs SIGTERM/SIGINT handlers, and runs the three chained rating loops,
  independent problem-stat and user-stat loops, the badge-assignment loop, the
  heartbeat-file loop, and the worker-presence loop together before graceful
  shutdown (console script `noca-rating`)
- `healthcheck.py`: container healthcheck entrypoint for heartbeat freshness
  (`NOCA_RATING_HEARTBEAT_*`); the worker has no HTTP surface, so the Compose
  probe reads the container-local heartbeat file rather than Valkey presence,
  whose key is namespaced by a worker ID the probe process cannot reconstruct

The worker publishes scheduler metadata to Valkey: the next scheduled cycle
timestamp at `arena:rating:next_update` (ISO8601, absent while a cycle is
running, TTL `RATING_INTERVAL + 600`), the formatted active interval at
`arena:rating:interval_text`, and the affiliation decay factor at
`arena:rating:affiliation_factor`. Each Arena instance polls those keys into app
state (`_next_rating_update_poller` in `arena/main.py`) so the synchronous footer
global and `/help/rating` page stay consistent across replicas without shared
process memory. Arena does not validate or format `RATING_INTERVAL` locally.

**Deployment constraint:** run exactly one `noca-rating` replica. Running more
than one reintroduces the duplicate-cycle problem this module was created to
solve. The loops have no inter-process lock; single-replica deployment is the
contract.

### `aiassistant/`

The aiassistant module is a standalone async worker that owns Arena AI code
review execution. It keeps external AI provider calls outside the Arena HTTP
process and uses only shared infrastructure boundaries.

- `config.py`: Pydantic `BaseSettings` (`NOCA_` prefix, shared `.env`) for the
  DB, Valkey, OpenAI, crypto dotenv, queue polling, reaper, and batch-pricing
  settings
- `database.py`: async SQLAlchemy engine factory for Core queries against the
  shared schema
- `worker.py`: `main` / `run_ai_worker` entrypoint for `noca-aiassistant`; runs
  the dequeue loop, stale-job reaper, batch flusher, OpenAI batch poller,
  reconciler, heartbeat-file loop, and worker-presence loop together, plus the
  optional signed command loop
- `healthcheck.py`: container healthcheck entrypoint for heartbeat freshness
  (`NOCA_AI_HEARTBEAT_*`), on the same rationale as the rating worker's
- `reconciler.py`: database-driven safety net that re-enqueues AI review jobs
  lost between the request route's PostgreSQL commit and its Valkey enqueue
- `reviewer.py`: online OpenAI Responses API path used when the Arena user has
  a personal `ai_api_key`
- `batch_reviewer.py`, `batch_flusher.py`, and `batch_poller.py`: platform-key
  OpenAI Batch API submission, staged-job accumulation, polling, terminal-state
  handling, result storage, and uploaded OpenAI file cleanup
- `db/queries.py` and `db/batch_queries.py`: SQLAlchemy Core access to
  `arena_submissions`, `arena_submission_ai_reviews`, `arena_users`, and
  `arena_ai_batch_jobs` without importing Arena ORM code
- `reaper.py`: stale `ai:queue:inflight` recovery using
  `ai:queue:inflight:times`

The worker decrypts user-owned API keys through the shared `EncryptedString`
type, so it loads `NOCA_CRYPTO_ENV_FILE` and registers its own
`SecretsManager` during startup. User-key jobs use the online Responses API and
store results immediately. Platform-key jobs use `NOCA_AI_OPENAI_API_KEY`, create
a durable staged `arena_ai_batch_jobs` row without calling OpenAI, and remove
the queue item from inflight. The batch flusher periodically collects all staged
rows into one multi-item OpenAI batch, and the batch poller stores results after
OpenAI reaches a terminal state.

### `mailer/`

The mailer module is a standalone async worker that owns outbound email
delivery. It runs the dequeue loop over `mail:queue:pending`, the stale-inflight
reaper, the heartbeat and worker-presence loops, and the pause/resume command
loop when `NOCA_WORKER_COMMAND_SECRET` is set.

- `worker.py`: `main` / `run_mailer_worker` entrypoint for `noca-mailer`;
  `process_job` delivers one rendered `MailJob` through the shared
  `EmailProvider` on a worker thread, drops a job past its TTL unsent, completes
  a delivered one, and leaves a provider-refused one inflight for the reaper
- `reaper.py`: requeues stale inflight jobs with an incremented `requeue_count`
  (TTL re-applied) and drops them past `NOCA_MAILER_MAX_REQUEUE_COUNT`
- `config.py`: the shared `NOCA_EMAIL_*` / `NOCA_SMTP_*` provider settings plus
  the `NOCA_MAILER_*` worker knobs; `NOCA_MAILER_MAX_PER_MINUTE` is the
  deployment-wide sending pace
- `healthcheck.py` / `database.py`: the container probe and the tiny pool used
  only for pause-state reconciliation

Single replica by design: the pace is per process.

### `healthmonitor/`

The healthmonitor module is a standalone FastAPI server (default port 8002) with a
Valkey connection only — no database, no JWT, no session handling. Its dashboard
and refresh fragment are public. Structure:

- `main.py`: FastAPI app, lifespan (Valkey runtime, templates, background
  loops), static mounts, `noca-healthmonitor` entrypoint
- `routes/dashboard.py`, `routes/health.py`: the public uptime dashboard, its
  HTMX refresh fragment, the ECharts uptime JSON endpoint, and the runtime
  health endpoint
- `services/service_registry.py`: the ordered list of monitored services
- `services/presence_probe.py`: live up/down reads from the shared
  worker-presence keys (Valkey outage reports every service as unknown)
- `services/uptime_stats.py`: per-slot `up`/`total` counters under
  `noca:healthmon:stats:{service}:{slot_epoch}` (12-hour UTC slots, 60-slot
  window, retention TTL plus explicit reaping)
- `services/loops.py`: the prober and reaper background loops

The `web`, `arena`, and `animator` HTTP servers publish worker presence from
their lifespans (`WorkerClass.WEB` / `WorkerClass.ARENA` /
`WorkerClass.ANIMATOR`) so the monitor can probe them the same way it probes the
workers; those classes are presence-only and are excluded from the Arena admin
dashboard and pause machinery.

### `animator/`

The animator module is a standalone FastAPI presentation server (default port 8003) with
an async PostgreSQL connection (SQLAlchemy Core over the shared schema) and a
Valkey connection. It defines no ORM mappings and never imports `web`.
Structure:

- `main.py`: FastAPI app, lifespan (database pool, Valkey runtime, event stream,
  worker-presence heartbeat, templates, static mounts), `noca-animator`
  entrypoint
- `routes/`: the public scoreboard shell and `/meta` + `/snapshot` feeds, the SSE
  event stream, the reveal projector and its public state feed, team photo and
  audio media, the operator control page, and the authenticated control API
- `services/`: the contest feed projection (delegating scoring to the shared
  `compute_icpc`), the Valkey-backed event stream, the reveal engine, and the
  fenced reveal session store

Deployment: `containers/animator/Dockerfile` builds the `noca/animator` image
from the animator workspace slice. Its entrypoint waits for PostgreSQL and
Valkey, then blocks on `scripts/wait_for_migrations.py` — the animator is a pure
schema consumer, never a steward. Caddy proxies it on host port 83; SSE passes
through unbuffered because Caddy ignores `flush_interval` for
`text/event-stream` responses.

### `landingpage/`

The landing page is a standalone Caddy runtime (default internal port 8080).
It has no Python package, application framework, database connection, Valkey
connection, or background loop. Its runtime consists of:

- `landingpage/Caddyfile`: read-only routing, security headers, template
  rendering, static file serving, and the dependency-free `/health` response
- `landingpage/index.html`: the environment overview, whose four public
  application URLs and footer release tag come from Caddy's environment template
  function
- `landingpage/static/`: the page's own stylesheet, scripts, site icon, and
  authored SVG icon set, all served same-origin
- `containers/landingpage/entrypoint.sh`: fail-fast validation for the required
  absolute HTTP(S) URLs and the release tag

The page reaches nothing at runtime. Its `Content-Security-Policy` starts from
`default-src 'none'` and allows only same-origin styles, scripts, fonts, and
images, with `connect-src 'none'`. It therefore cannot show live service status
by construction; the Health Monitor link serves that need instead.

Deployment: `containers/landingpage/Dockerfile` builds the `noca/landingpage`
image from the official Caddy image, plus a build-only stage that supplies the
shared NOCA webfonts (Public Sans, Inter, IBM Plex Mono) and the Contest and
Arena illustrations owned by `web/` and `arena/`. The resulting image carries no
Python or Node.js runtime. The process runs as the unprivileged `caddy` user and
doesn't wait for any other module. The sample Compose stack publishes it on host
port 84.

## 2. Communication model between `web` and `autojudge`

### PostgreSQL

PostgreSQL is the canonical store for:

- contests
- users and uber admins
- problems, test cases, and language limits
- submissions
- submission judgments
- per-test-case results
- confirmations, overrides, and audit history
- clarifications and other contest state

The worker does not receive full source code or problem metadata from Valkey. Instead, it dequeues a lightweight `judgment_id` and loads the authoritative payload from PostgreSQL.

### Valkey

Valkey is used as the lightweight coordination layer for judgment execution and scoreboard caching:

**Judge queue and event keys:**

- `judge:queue:priority`
- `judge:queue:profiling`
- `judge:queue:pending`
- `judge:queue:inflight`
- `judge:queue:inflight:times`
- `judge:job:<job_id>`
- `judge:results`: contest verdict events
- `judge:submissions`: new contest-submission nudges
- `arena:results`: Arena final-verdict nudges

**Arena AI review queue keys:**

- `ai:queue:pending`
- `ai:queue:inflight`
- `ai:queue:inflight:times`
- `ai:job:<submission_id>`

**Scoreboard cache keys** (managed by `shared/services/scoreboard_cache.py`):

| Key | TTL | Purpose |
|-----|-----|---------|
| `scoreboard:<contest_id>:full` | 5 s | Admin/judge live view during a running contest (includes results hidden from teams) |
| `scoreboard:<contest_id>:public` | 180 s | Team/staff/public live view while the contest is not yet frozen |
| `scoreboard:<contest_id>:frozen` | None (until release or metadata change) | Public snapshot locked at freeze time; regular verdict invalidation does not touch this key |
| `scoreboard:<contest_id>:final` | None (permanent) | Released final scoreboard after an admin calls `POST /c/{slug}/admin/release-scoreboard`. Written once with no TTL; reveals all frozen results. |

The `:frozen` key is written the first time a public viewer requests a frozen scoreboard and remains intentionally static until the scoreboard is released or the freeze boundary changes. The `:final` key is written by `ScoreboardService.get_or_compute_final()` and is pre-warmed before `Contest.release_scoreboard_after_end` is committed to PostgreSQL. Once set, the scoreboard route serves it to all roles without computing a fresh snapshot.

Typical web contest flow:

1. Web creates `Submission` and `SubmissionJudgment` rows in PostgreSQL.
2. Web pushes the `judgment_id` into the priority or pending Valkey list and stores job metadata at `judge:job:<judgment_id>` (`judgment_id`, `contest_id`, `is_rejudge`, `requeue_count`, optional `submission_id`).
3. Autojudge moves the job to inflight, processes it, and writes authoritative results to PostgreSQL.
4. Autojudge publishes a `VerdictEvent` on `judge:results`.

Typical Arena flow:

1. Arena creates `ArenaSubmission` and `ArenaSubmissionJudgment` rows and enqueues `JobKind.ARENA_SUBMISSION` on `judge:queue:pending`.
2. Autojudge loads Arena source, limits, and test case metadata from PostgreSQL,
   reads test case bytes from the shared Arena test case directory, and uses the
   same language containers as web submissions.
3. Autojudge writes the final Arena verdict directly to `arena_submission_judgments` and records only the first non-AC row in `arena_submission_test_results`.
4. Autojudge publishes a best-effort final `ArenaVerdictEvent` on
   `arena:results`. Owner-scoped SSE treats the event as a freshness nudge and
   fetches the authoritative `status.json`; fallback polling bounds staleness.
   Arena jobs do not invalidate a scoreboard cache.

Typical Arena AI review flow:

1. Arena enqueues an `ArenaAIReviewJob` by storing metadata at
   `ai:job:<submission_id>` and pushing the submission id to
   `ai:queue:pending`.
2. The aiassistant worker moves the submission id to `ai:queue:inflight`,
   records a dispatch timestamp in `ai:queue:inflight:times`, and loads the
   source, problem statement, optional problem image, user locale, and API-key
   state from PostgreSQL.
   The `ai:job:<submission_id>` hash remains available while the job can be
   recovered or requeued.
3. If the user has a personal OpenAI key, the worker calls the Responses API and
   writes `arena_submission_ai_reviews` immediately.
4. If the user has no personal key and `NOCA_AI_OPENAI_API_KEY` is configured,
   the worker writes a durable staged `arena_ai_batch_jobs` row without calling
   OpenAI. The batch flusher later collects all staged rows into one multi-item
   OpenAI batch, and the batch poller stores each review after terminal
   completion.
5. Completed and failed AI reviews create durable Arena notifications through
   `shared.services.arena_notification_service`.
6. After terminal handling or durable batch staging, the worker atomically
   removes the job from the pending and inflight queues, removes its dispatch
   timestamp, and deletes `ai:job:<submission_id>`.

Per-contest queue metrics are derived directly from Valkey (`judge:queue:*` + `judge:job:*`) without PostgreSQL lookups. During rollout, legacy hashes missing `contest_id` are treated as `unknown_contest` in metrics tooling.

The web runtime has a `ValkeyRuntime` abstraction that maintains the connection, performs health checks, buffers write commands in memory during temporary Valkey outages, and exposes contest-scoped queue metrics reads (`get_contest_queue_metrics`) from Valkey-only queue artifacts.

### Shared filesystem

The two modules also communicate through shared filesystem directories configured by environment.
The web process and the autojudge worker each point their own variable at the same shared paths:

- `NOCA_WEB_PROBLEM_STATEMENT_DIR` (web)
- `NOCA_PROBLEM_TESTCASE_DIR` — shared root for web, arena, and autojudge; web problems under `<root>/contest/`, arena problems under `<root>/arena/`

Current usage:

- `web` writes and serves problem statements and test case files
- `autojudge` reads problem test case files during execution

The filesystem contract matters because the database stores the metadata and ordinals, while the actual test case bytes live on disk using stable ordinal-based filenames.

## 3. Judging architecture

The judging data model deliberately separates immutable submissions from judgment attempts:

- `submissions`: what the team actually sent
- `submission_judgments`: one or more judgment attempts for that submission

That split supports:

- rejudging without mutating the original submission record
- separate machine and human verdict flows
- auditability

A judgment progresses through these states:

- `QUEUED`
- `DISPATCHED`
- `JUDGING`
- `DONE`
- `FAILED`
- `SUPERSEDED`

Verdict handling is also intentionally split:

- `autojudge_verdict`: what the machine concluded
- `final_verdict`: the effective visible verdict after contest policy is applied

If `contest.autojudge_only` is true, the worker can finalize immediately. Otherwise, the machine verdict remains recorded but the final verdict is resolved in the web layer from human confirmations or a chief-judge override.

Current confirmation flow for non-`autojudge_only` contests:

- `GET /c/{slug}/submissions/{submission_id}/review` renders the unified review page with source, compile log, per-test results, confirmation panel, override UI, and judging history
- `POST /c/{slug}/submissions/{submission_id}/acquire-review` lets a judge claim the active `DONE` judgment for review through a Valkey TTL lock
- `POST /c/{slug}/submissions/{submission_id}/confirm` accepts a judge's confirmation while that judge holds the review lock when Valkey is available; if Valkey is down, the UI switches to degraded mode and the DB remains the source of truth for final verdict rules
- `POST /c/{slug}/submissions/{submission_id}/release-review` releases the review lock, with admin/chief-judge force-release support when Valkey is available
- the `web.models.submission` hooks derive `final_verdict` from stored human confirmations
- a chief-judge confirmation finalizes immediately
- otherwise the current rule is: finalization occurs once two non-chief confirmations match the autojudge verdict
- rejudging supersedes the active judgment and creates a fresh `QUEUED` judgment attempt
- when confirmation or override finalizes a verdict, the web layer publishes a verdict event and invalidates the scoreboard cache

## 4. Web messaging and UI flow

The web module is primarily server-rendered and uses redirects plus flash messages for user feedback.

Messages from routes and services are delivered to the UI via `flash()`, using the FastAPI-compatible Flask-style pattern documented in [FASTAPI_FLASH.md](FASTAPI_FLASH.md):

- `web/main.py` calls `setup_flash(templates)`
- routes receive `FlashDep`
- routes call `flash(message, category)`
- templates render `get_flashed_messages(with_categories=True)`

This is the main message-passing mechanism from request handlers to rendered pages after POST/redirect/GET flows.

The web layer also uses server-sent events for live submission-list refreshes:

- `GET /c/{slug}/runs/events` subscribes to Valkey verdict events through `ValkeyRuntime`
- finalized verdicts trigger lightweight SSE refresh notifications for the contest
- this complements the flash-based request/response UX rather than replacing it

## 5. Identity, contest scoping, and RBAC

Authorization is deliberately strict and contest-aware.

There are two identity domains:

- `UberAdmin`: global system-level actor
- `User`: contest-scoped actor tied to exactly one contest

Contest users carry both:

- a role audience in the JWT (`ADMIN`, `JUDGE`, `STAFF`, `TEAM`, `USER`)
- the `contest_id` in token extra data

Dependencies in `web/dependencies.py` and `web/services/actor_service.py` enforce that:

- the token is valid
- the actor exists
- contest-scoped users can only access the contest identified in the token
- route handlers can further restrict allowed roles

Important RBAC characteristics:

- users are contest-scoped, not globally shared identities
- route-level checks often use `ensure_allowed_role(...)`
- some actions are even more rigid than simple role checks

Examples:

- only `TEAM` users can submit runs
- judges and admins may acquire and answer clarifications
- the chief judge or an admin may override a verdict
- only the contest owner or an uberadmin may assign the chief judge
- non-uberadmin access to user assets is limited to the same contest

The full permission model — who may answer, confirm, override, and handle tasks, plus the
uberadmin attribution boundary — lives in one place:
[web/docs/ROUTES.md § Permission Model](../web/docs/ROUTES.md#permission-model). Do not
restate role rules here; link to it.
- language availability for submissions is contest-scoped via the `contest_languages` junction table; `get_contest_languages(session, contest)` is the authoritative query for any contest-scoped language list

This combination of contest scoping plus role checks is a central part of the app architecture, not just a UI concern.

## 6. Background processing and reapers

### Reaper architecture

The web module runs multiple async background reapers (long-lived coroutines) alongside the main FastAPI server. Each reaper runs in a separate `asyncio.Task` and manages a specific background processing concern:

- **Clarification reaper (`clarification_reaper.py`):** Auto-answers leftover open clarifications once a contest is past; active clarification coordination uses Valkey TTL locks
- **Task reaper (`task_reaper.py`):** Auto-concludes leftover tasks once a contest is past; active staff coordination uses Valkey TTL locks
- **Security events reaper (`shared.services.security_events_reaper`):** Deletes
  expired Web security events according to the configured retention period; the
  Web process scopes this pass to events whose module is `web`

**Reaper lifecycle management:**
- Created during FastAPI lifespan startup in `main.py` with `asyncio.Event` stop signals
- Run as `app.state.*_reaper_task` for graceful shutdown coordination
- Service layer exceptions are caught and logged; reapers continue running
- Stop events triggered during FastAPI shutdown to allow clean termination

### Task queue system

The task queue (`TaskType` enum) provides in-contest services:

- **BALLOON**: Triggered when a team solves a problem correctly; staff prepare and deliver physical balloons
- **FIRST_BALLOON**: Replaces `BALLOON` for the earliest accepted solve on a problem; rendered with a golden glow in task, run, and scoreboard solved-cell views
- **PRINT**: Teams request printouts of code or notes; staff acquire and print to designated printers
- **SOS**: Teams request immediate assistance (computer failure, medical emergency, etc.)

**Task workflow:**
1. Created automatically after accepted/finalized solves (BALLOON/FIRST_BALLOON) or by team request (PRINT/SOS)
2. Staff users acquire Valkey TTL locks for tasks; timeout is contest-configured via `tasks_timeout_minutes` (`0` means lock until contest end)
3. Staff mark tasks as finished or release them; PostgreSQL keeps the authoritative finished state and finisher identity
4. When Valkey is unavailable, the UI warns that locks are unavailable and staff actions continue in degraded mode

**Task concurrency control:**
- Concurrency control via Valkey TTL locks plus service-side business checks
- Lock expiration enforced by Valkey TTL rather than PostgreSQL fields or DB cleanup
- `source_hash` prevents duplicate PRINT tasks from same code

## 7. Security and middleware

### Authentication and session management

**JWT-based authentication:**
- Contest-scoped JWT tokens with role claims
- Access token stored in the HTTP-only `noca_access_token` cookie
- Active web sessions use sliding expiration. A middleware validates the cookie once
  per request, caches the result on `request.state`, and rotates the JWT when the
  remaining lifetime reaches half of `NOCA_JWT_EXPIRE_SECONDS`
- Login-issued tokens carry an original `session_started_at` claim so the web layer
  can enforce the optional absolute cap configured by
  `NOCA_JWT_REFRESH_MAX_SESSION_SECONDS`
- Logout deletes the cookie and best-effort revokes the JWT ID in Valkey for the
  token's remaining lifetime. Revocation checks fail open during a Valkey
  outage, so the token can remain usable until it expires in that degraded mode
- UberAdmin tokens contain global system access claims

**Session middleware:**
- `starlette.middleware.sessions.SessionMiddleware` with signed cookies
- Used for cookie-backed session features such as flash messages; authentication itself is based on the JWT cookie above
- `web.middleware.auth_token_refresh.AuthTokenRefreshMiddleware` handles sliding
  auth-cookie refresh and absolute session expiry without duplicating JWT
  validation across dependencies

### Role-based access control (RBAC)

**Role hierarchy (most to least privileged):**
1. `UBERADMIN`: Global system access, all contests, user management
2. `ADMIN`: Contest management, all submissions, user management within contest
3. `JUDGE`: View all submissions (anonymized), judge, answer clarifications
4. `STAFF`: View results, handle tasks (BALLOON/PRINT/SOS), no submission access
5. `TEAM`: Submit solutions, view own submissions, request clarifications
6. `USER`: View contests and scoreboards, no submission capability

**Authorization enforcement:**
- Route-level: `ensure_allowed_role()` dependency checks
- Service-level: Role checks in business logic
- Data-level: Contest scoping in all queries (`contest_id` filters)
- Action-level: Granular permission checks (for example, a contest administrator
  or the contest chief judge can override a verdict; UberAdmin cannot because
  the audit row references a contest user). The same pair carries a second
  granular permission: a contest administrator or the contest chief judge may
  publish a clarification announcement at any point in the contest lifecycle,
  while an ordinary judge may only do so while the contest is running — so
  setup and post-contest notices are possible without widening who may answer
  clarifications

**Contest scoping:**
- All users (except UBERADMIN) are tied to exactly one `contest_id`
- JWT token contains `contest_id` in extra data
- Every database query includes contest scoping
- Cross-contest data access is impossible by design

### Arena default-deny access control

The arena module is locked down by default: a single global FastAPI dependency,
`arena.dependencies.access_control.enforce_arena_authentication` (registered via
`dependencies=[...]` on the `FastAPI` app in `arena/main.py`), requires a valid
logged-in session for **every** route except a small public allowlist:

- exact paths `/`, `/dashboard`, `/problems` (the problem list only — the
  `/problems/{number}` detail and its sub-resources stay protected), `/health`,
  and the root favicon assets
- prefixes `/legal`, `/help`, and `/auth` (the whole `/auth/*` namespace, since
  login, signup, activation, 2FA, password reset, and parental-consent flows all
  run while logged out)

The gate is intentionally cheap: it reads only `request.state.validated_token`
and `token_cap_exceeded` (populated by `ArenaAuthMiddleware` after JWT
validation — no database I/O) and raises `HTTPException(401)` for any non-public
path without a valid session. The Arena exception handler
(`arena/error_handlers.py`) converts that 401 into a 303 login redirect for
browsers, an `HX-Redirect` for HTMX requests, or a plain 401 for API clients.
Static files are Starlette `Mount`s, not `APIRoute`s, so the gate never runs for
`/static/*`. Per-route `get_current_arena_user` / `require_arena_*` dependencies
still run on top of the gate and enforce full database-backed identity gating and
role checks. New routes are protected automatically unless their path is added to
the allowlist.

## 8. Important design consequences

The current architecture gives the project a few strong properties:

- clear isolation between user-facing web logic and untrusted-code execution
- one DB schema definition shared by all runtime modules
- lightweight queue protocol with durable state in PostgreSQL
- auditable judgment lifecycle and manual override path
- contest-local identity boundaries with explicit RBAC
- server-rendered workflows with consistent flash-based feedback

It also implies some operational constraints:

- PostgreSQL schema changes must preserve both web ORM behavior and worker Core queries
- filesystem layout and DB ordinals must stay in sync for test cases
- Valkey is coordination infrastructure, but not the source of truth for judgment data
- web-only ORM hooks must not be assumed by the autojudge side

### Performance and scalability

- **Valkey usage**: Lightweight queue protocol minimizes Redis memory usage; PostgreSQL is source of truth
- **Fixed-width concurrency**: Judge workers have predictable resource consumption (N slots × container overhead)
- **Scoreboard caching**: Separate full/public caches, a frozen public snapshot, and a permanent final cache reduce repeated recomputation
- **Async throughout**: Non-blocking I/O for database, Valkey, filesystem, and Docker API calls
- **Cookie-backed web state**: Auth and flash/session state live in cookies, which keeps the web tier easy to scale horizontally

### Operational considerations

**PostgreSQL schema changes:**
- Must preserve both web ORM behavior and worker Core queries
- Alembic migrations must be tested against all runtime modules
- Table locks during migration will pause both web and judge operations

**Filesystem contract:**
- Test case layout and DB ordinals must stay in sync
- Problem statement and test case directories must be writable by web and readable by judge
- Backup strategy must include both database and filesystem

**Valkey as coordination layer:**
- Queue data is ephemeral; persistent state lives in PostgreSQL
- Valkey outages delay judging for new submissions and other queued work, but
  don't affect existing PostgreSQL data
- `ValkeyRuntime` buffers writes during outages for best-effort continuity

**Worker deployment:**
- Judge workers can be scaled independently of web processes
- Worker ID configuration enables multiple workers on same host
- Heartbeat files monitor worker health for process managers
- Lazy container pool warming shifts the first-submission cost to the first job
  for each language while still validating required images at startup

### Security properties

**Isolation guarantees:**
- Judge workers run untrusted code in Docker containers with `isolate` as the authoritative inner judge
- No network access inside containers (by default)
- Resource limits enforced in layers: Docker as an outer safety brake, `isolate` as the inner authoritative source for time, memory, process/thread, and stdout-file growth
- Web and judge processes run as different system users in production

**Auditing:**
- Submission immutability: original submission record never changes
- Judgment audit trail: every judgment attempt creates a record
- Manual overrides create audit entries with actor attribution
- All contest state changes (freeze, end, release) are logged

**Data integrity:**
- Final verdict derived deterministically from confirmations and overrides
- Scoreboard cache invalidation on verdict finalization, with the public frozen snapshot intentionally exempt during the freeze window
- Acquisition checks and reapers prevent lost updates and stale ownership for concurrent task/review workflows

## 9. Summary

### Custom-validator runtime

Custom-validator candidates are durable staged revisions. Web and Arena commit
the candidate first and enqueue a `custom_validator_validation` job afterward.
Startup and periodic reconciliation reconstruct missing queue hashes for every
still-`PENDING` token; stale jobs are harmless because database updates include
the candidate token.

At submission dispatch, Autojudge loads the active `VALID` revision and compiles
it in a disposable compile container before compiling contestant source. Each
interactive attempt then consumes two fresh network-disabled run containers,
one for the contestant and one for the trusted validator, without reserving a
second worker-concurrency slot. The bridge pumps both stdout-to-stdin directions,
propagates EOF, records bounded diagnostics, and destroys both containers.

Only a validator signal, startup/communication failure, or emergency watchdog
expiration is retryable. After two such attempts, an Arena validator becomes
`RUNTIME_FAILED`: the problem is disabled, its queued judgments are failed and
removed from Valkey, and its owner receives one idempotent notification. Clean
exit codes never trigger containment.

In short, NOCA is a nine-process contest platform:

- `web` manages contest and business workflows (default port 8000)
- `autojudge` manages sandboxed compilation and execution
- `arena` manages the public Arena participant platform (default port 8001)
- `rating` manages the single-replica Arena rating recomputation cycles
- `aiassistant` manages Arena AI code review execution and OpenAI batch polling
- `mailer` manages outbound email delivery from the Valkey mail queue
- `healthmonitor` manages the public availability dashboards (default port 8002)
- `animator` manages the public live scoreboard and reveal presentation (default port 8003)
- `landingpage` serves the environment entry point and public module links
  (default internal port 8080)
- `shared` defines the common contract between them

The runtime architecture is built around a strong separation of concerns, a shared
PostgreSQL schema contract, lightweight Valkey queueing, shared problem/testcase
storage, server-rendered FastAPI pages with flash-based feedback, role-based
access control with strict contest scoping, and multiple background reapers for
asynchronous task processing.
