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

The web module is a server-rendered FastAPI application (~13k LOC) with these main layers:

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
- **Chief judge workflow**: Special role for verdict overrides and final decisions

### `autojudge/` (~23 Python files)

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
- The three job kinds (submission, profiling, Arena submission) share one
  parameterized rebuild loop driven by a per-kind spec

**Valkey decoding (`valkey_decode.py`):**
- `decode_valkey_scalar`, `hash_requeue_count` — normalize raw `bytes` queue values

**Heartbeat (`heartbeat.py`):**
- Heartbeat file management for health monitoring
- `touch_heartbeat_file`, `remove_heartbeat_file`, `heartbeat_loop`
- Compatibility export of `worker_id`, which is centrally implemented in `worker_identity.py`

**Worker identity (`worker_identity.py`):**
- Shared worker identity generation used by the main worker loop and Docker container labels
- `worker_id`

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
- Loads Arena test cases from database rows and stores only the first non-AC case result
- Writes final verdicts directly to Arena tables; no pub/sub or SSE event is emitted
- Test case content is normalized to Unix line endings (LF only) when fetched from the database, covering rows stored with CRLF endings before the normalization contract was enforced

**Profiling job (`profiling_job.py`):**
- Auto-Limit profiling pipeline: `process_profiling_job`
- Hard limit derivation and profiled limit persistence

**Runtime helpers (`runtime_utils.py`):**
- Shared runtime predicates reused by multiple job pipelines
- `is_recoverable_isolate_runtime_error` for transient isolate/cgroup retry decisions

**Compilation (`compiler.py`):**
- `compile_submission`: runs the language compile command inside a short-lived Docker container
- Produces a binary artifact returned as raw bytes

**Execution pipeline (`runner.py`):**
- `run_test_case`: injects artifact + input, runs `isolate`, parses meta, returns `RunResult`

**Isolate sandbox (`sandbox.py`):**
- Isolate box lifecycle: `_sync_isolate_init`, `_sync_isolate_cleanup`, `_sync_reset_run_artifacts`, `_sync_run_isolate`
- Meta parsing: `_parse_isolate_meta`, `_resolve_peak_pids`, `_read_isolate_cgroup_peak_pids`
- Judge images are standardized on Debian-family or other mainstream glibc-based bases to keep runtime
  loader paths predictable inside the isolate sandbox; each `:run` image receives the `isolate` binary
  via `COPY --from` of the shared `noca/isolate-base` build-time artifact rather than recompiling it
- The shared isolate binary is built from upstream `ioi/isolate` v2.6. Run images install both
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
- Stale in-flight job detection and recovery
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

The judge queue carries three first-class job kinds in the same Valkey hash namespace:

- `submission` jobs for normal/rejudge submission judgments
- `arena_submission` jobs for Arena submission judgments
- `profiling` jobs for Auto-Limit reference implementations

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

The shared module (~30k LOC including wordlists) contains the pieces all runtime modules must agree on:

**Schema and data structures:**
- `db_schema.py`: Centralized SQLAlchemy Core table definitions
- `queue_schema.py`: Pydantic models for judge, profiling, Arena submission,
  Arena AI review, and verdict-event queue payloads
- `enumerations.py`: RoleEnum, Verdict, JudgmentStatus, ContestStatus, TaskType, Environment

**Services:**
- `services/valkey_service/`: shared Valkey package for runtime lifecycle, queue operations, and queue metrics
- `services/lock_service.py`: ephemeral Valkey TTL locks for clarifications, staff tasks, and submission reviews
- `services/scoreboard_cache.py`: Scoreboard caching with TTL management

**Language support:**
- `language_registry.py`: Language configuration and compilation/execution commands
- `languages.py`: Language runtime definitions

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

The arena module is a second FastAPI server (~port 8001) that owns the public-facing Arena platform:

**Core application (`arena/*.py`)**:
- `main.py`: FastAPI application factory with lifespan management and service initialization
- `config.py`: Pydantic `BaseSettings` (same `NOCA_` prefix as `web/config.py`); omits contest-admin and judge-queue settings; Arena JWT issuer is fixed to `"noca-arena"` so JWT issuer claims differ from the web module even when shared app-name settings are present
- `database.py`: SQLAlchemy async engine and session factory; `ArenaBase` shares `shared_metadata` so Alembic manages all tables in one migration history

**Models (`arena/models/`)**:
- `arena_users.py`: `ArenaUser`, `ArenaBackup2FA`, `ArenaLoginHistory` ORM models with password hashing, photo helpers, age calculation, and TOTP support
- `_otp_secret` is stored via `EncryptedString` (a `TypeDecorator` in `shared/db_schema/custom_types.py`); the `SecretsManager` instance must be registered with `init_encrypted_string()` at startup before any DB I/O on that column

**Secrets management**:
- `SecretsConfig.from_environment()` (from `dclobato/secrets-manager`) reads `ENCRYPTION_KEYS__<version>`, `ENCRYPTION_SALT__<version>` (base64), and `ACTIVE_ENCRYPTION_VERSION` from the environment — these are not `NOCA_`-prefixed
- `SecretsManager` supports multiple key versions so OTP secrets can be re-encrypted to a new key without downtime
- `init_encrypted_string(manager)` registers the manager globally in `shared/db_schema/custom_types`; because `web` and `arena` run in separate OS processes they each have an independent module namespace

**Identity domain**:
- Arena users (`ArenaUser`) are entirely separate from contest users (`User`) and uber-admins (`UberAdmin`)
- Arena JWT tokens use fixed issuer `"noca-arena"`, preventing cross-module token acceptance with the web module's `"noca"` issuer
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
  installs SIGTERM/SIGINT handlers, `asyncio.gather`s the three loops, and shuts
  down gracefully (console script `noca-rating`)

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
  the dequeue loop, the stale-job reaper, the OpenAI batch poller, and the
  reconciler together
- `reconciler.py`: database-driven safety net that re-enqueues AI review jobs
  lost between the request route's PostgreSQL commit and its Valkey enqueue
- `reviewer.py`: online OpenAI Responses API path used when the Arena user has
  a personal `ai_api_key`
- `batch_reviewer.py` and `batch_poller.py`: platform-key OpenAI Batch API
  submission, polling, terminal-state handling, result storage, and uploaded
  OpenAI file cleanup
- `db/queries.py` and `db/batch_queries.py`: SQLAlchemy Core access to
  `arena_submissions`, `arena_submission_ai_reviews`, `arena_users`, and
  `arena_ai_batch_jobs` without importing Arena ORM code
- `reaper.py`: stale `ai:queue:inflight` recovery using
  `ai:queue:inflight:times`

The worker decrypts user-owned API keys through the shared `EncryptedString`
type, so it loads `NOCA_CRYPTO_ENV_FILE` and registers its own
`SecretsManager` during startup. User-key jobs use the online Responses API and
store results immediately. Platform-key jobs use `NOCA_AI_OPENAI_API_KEY`, create
a durable `arena_ai_batch_jobs` row, remove the queue item from inflight, and
let the batch poller store results after OpenAI reaches a terminal state.

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

**Judgment queue keys:**

- `judge:queue:priority`
- `judge:queue:pending`
- `judge:queue:inflight`
- `judge:queue:inflight:times`
- `judge:job:<judgment_id>`
- `judge:results`

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
2. Autojudge loads Arena source, limits, and test cases from PostgreSQL, using the same language containers as web submissions.
3. Autojudge writes the final Arena verdict directly to `arena_submission_judgments` and records only the first non-AC row in `arena_submission_test_results`.
4. Arena users poll for result state; no `VerdictEvent`, SSE, or scoreboard cache invalidation is emitted for Arena jobs.

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
4. If the user has no personal key and `NOCA_AI_OPENAI_API_KEY` is configured, the
   worker submits a single-item OpenAI Batch API job, writes
   `arena_ai_batch_jobs`, and the batch poller stores the review after terminal
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
- only judges can acquire and answer clarifications
- only the contest chief judge may override a verdict
- only the contest owner or an uberadmin may assign the chief judge
- non-uberadmin access to user assets is limited to the same contest
- language availability for submissions is contest-scoped via the `contest_languages` junction table; `get_contest_languages(session, contest)` is the authoritative query for any contest-scoped language list

This combination of contest scoping plus role checks is a central part of the app architecture, not just a UI concern.

## 6. Background processing and reapers

### Reaper architecture

The web module runs multiple async background reapers (long-lived coroutines) alongside the main FastAPI server. Each reaper runs in a separate `asyncio.Task` and manages a specific background processing concern:

- **Clarification reaper (`clarification_reaper.py`):** Auto-answers leftover open clarifications once a contest is past; active clarification coordination uses Valkey TTL locks
- **Task reaper (`task_reaper.py`):** Auto-concludes leftover tasks once a contest is past; active staff coordination uses Valkey TTL locks

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
- Logout still deletes the cookie but does not invalidate an already-issued JWT
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
- Action-level: Granular permission checks (e.g., only chief judge can override)

**Contest scoping:**
- All users (except UBERADMIN) are tied to exactly one `contest_id`
- JWT token contains `contest_id` in extra data
- Every database query includes contest scoping
- Cross-contest data access is impossible by design

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

In short, NOCA is a five-process contest platform:

- `web` manages contest and business workflows (port 8000)
- `autojudge` manages sandboxed compilation and execution
- `arena` manages the public Arena participant platform (port 8001)
- `rating` manages the single-replica Arena rating recomputation cycles
- `aiassistant` manages Arena AI code review execution and OpenAI batch polling
- `shared` defines the common contract between them

The runtime architecture is built around a strong separation of concerns, a shared
PostgreSQL schema contract, lightweight Valkey queueing, shared problem/testcase
storage, server-rendered FastAPI pages with flash-based feedback, role-based
access control with strict contest scoping, and multiple background reapers for
asynchronous task processing.
