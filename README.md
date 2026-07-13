# NOCA - Next Online Contest Administrator

NOCA is an ecosystem for competitive programming. It combines two independent
products: **Contest**, for organizing and running ICPC-style competitions, and
**Arena**, a free-to-use training environment. Both products use the same
AutoJudge infrastructure for compiling submissions and running them against
problem test cases.

Contest and Arena can run together or as separate deployments. Arena also has
optional Rating and AI Assistant workers for ratings, statistics, badges, and
submission feedback.

# Project structure

NOCA is a `uv` workspace. Each runtime module is an independent package, while
`shared` contains the contracts and services that the modules use in common.

```text
noca/
|-- web/                    # Contest FastAPI application
|   |-- routes/             # HTTP endpoints
|   |-- services/           # Contest business logic
|   |-- models/             # Contest ORM models
|   |-- template/           # Jinja templates
|   `-- static/             # Contest CSS, JavaScript, and images
|-- arena/                  # Arena FastAPI application
|   |-- routes/             # HTTP endpoints
|   |-- services/           # Arena business logic
|   |-- models/             # Arena ORM models
|   |-- template/           # Jinja templates
|   `-- static/             # Arena CSS, JavaScript, and images
|-- autojudge/              # Compilation and execution worker
|-- rating/                 # Arena rating, statistics, and badge worker
|-- aiassistant/            # Arena AI review worker
|-- shared/                 # Shared schemas, services, assets, and contracts
|   |-- db_schema/          # SQLAlchemy Core table definitions
|   |-- services/           # Cross-module services
|   |-- static/             # Shared browser assets
|   `-- template/           # Shared Jinja templates
|-- containers/             # Application and judge image definitions
|-- migrations/             # Alembic database migrations
|-- scripts/                # Bootstrap, maintenance, and diagnostic tools
|-- tests/                  # Test suite for all workspace packages
|-- docs/                   # System-wide architecture and operations docs
|-- web/docs/               # Contest route and service references
|-- arena/docs/             # Arena route and service references
|-- docker-compose.yml.sample
`-- pyproject.toml          # Workspace and development tool configuration
```

## Modules

The runtime is composed of two user-facing applications, three workers, and one
shared library. Arrows in the following figure show logical dependencies and
workflows, not direct imports between runtime applications.

```text

 ┌───────────────────────────────────────────────────────┐
 │      Shared (shared ws) contracts and services        │
 └────┬────────┬───────┬────────────────┬───────────────┬┘
 ┌────▼─────┐  │  ┌────▼─────┐     ┌────▼─────┐         │
 │ Contest  │  │  │  Arena   │     │  Rating  │         │
 └────┬─────┘  │  └────┬───▲─┘     └────┬─────┘         │
      │        │       │   └────────────┘               │
      │        │   ratings, statistics and badges  ┌────▼─────┐
      │        │       │                           │    AI    │
      │        │       ├── optional review jobs ───► Assistant│
      │        │       │                           └────┬─────┘
      │        │       │                                │
      │        │       │       Responses & Batch APIs   │
      │        │       │                          ┌─────▼────┐
      │        │       │ submission jobs          │  OpenAI  │
      │        │       │                          │   API    │
      │        │ ┌─────▼─────┐                    └──────────┘
      │        └─► AutoJudge │
      │          └─────▲─────┘
      └────────────────┘
   submission and profiling jobs
```

The workspace packages and their entry points are:

| Workspace | Package | Entrypoint | Responsibility |
| --- | --- | --- | --- |
| `web/` | `noca-web` | `uv run noca-web` | Contest application |
| `arena/` | `noca-arena` | `uv run noca-arena` | Arena application |
| `autojudge/` | `noca-autojudge` | `uv run noca-autojudge` | Shared judge worker |
| `rating/` | `noca-rating` | `uv run noca-rating` | Arena rating worker |
| `aiassistant/` | `noca-aiassistant` | `uv run noca-aiassistant` | Arena AI review worker |
| `shared/` | `noca-shared` | Library only | Shared contracts and services |

## Module relationships

Runtime modules don't import code from one another. They coordinate through
shared infrastructure and import only the `shared` package for common schemas,
enumerations, queue payloads, and services.

- **PostgreSQL is authoritative.** It stores identities, contests, problems,
  submissions, immutable judgment attempts, results, ratings, AI reviews,
  notifications, worker pause state, and other durable application data.
- **Valkey provides cache and synchronization.** It carries judge and AI work
  queues, idempotency locks, short-lived coordination state, cache entries,
  worker presence, and signed worker-control notifications. Durable state is
  reconciled from PostgreSQL when queue delivery is interrupted.
- **The shared filesystem stores problem data.** Contest statements and test
  cases are stored in configured directories. Test cases use separate
  `contest/` and `arena/` namespaces and are shared with AutoJudge.
- **Docker provides the execution boundary.** AutoJudge manages language
  containers through the Docker daemon. Contest and Arena never execute
  submitted code in their application processes.
- **OpenAI is optional.** Only AI Assistant calls the OpenAI API, and Arena
  remains usable when the worker or API is unavailable.

This separation lets you deploy and scale the user-facing applications and
workers independently while keeping durable transitions auditable.

## Contest

Contest is the `web` workspace. It organizes and runs programming competitions,
including ICPC-style events, with contest-scoped users, roles, problems,
submissions, clarifications, operational tasks, and scoreboards.

Its main features include:

- Support for every active language in the shared NOCA language registry. The
  built-in registry currently defines 18 languages.
- Multiple concurrent contests with isolated users, problems, schedules,
  rules, and scoreboards.
- Contest-scoped role-based access control for administrators, judges, staff,
  teams, and read-only users.
- Automatic judging or a human-confirmation workflow, with rejudging and chief
  judge overrides.
- Live, frozen, and final scoreboards, plus contest reports and analytics.
- Multi-site organization for teams, staff, and event logistics.
- CSV and JSON user import and export.
- Import and export of NOCA-compatible problem packages.
- Compatibility exports for SBC BOCA Animeitor and Reveleitor workflows.
- PDF and Markdown problem statements with math and Mermaid support.
- An online test case editor and ZIP-based test case management.
- Auto-Limit profiling of reference solutions to calculate per-language time,
  memory, process, and output limits.
- Per-contest language selection and problem limits.
- Clarification, balloon, print, and SOS task workflows.
- Submission and verdict audit trails without executing untrusted code in the
  web process.

See the [architecture overview](docs/ARCHITECTURE.md),
[Contest routes](web/docs/ROUTES.md), and
[Contest services](web/docs/SERVICES.md) for implementation details.

## Arena

Arena is the `arena` workspace. It is a free-to-use training environment where
users can browse problems, submit solutions, track progress, and participate in
teacher-managed classes. Arena has its own identity and authorization domain,
separate from Contest accounts.

Its main features include:

- Free self-service registration, email confirmation, password recovery, and
  optional two-factor authentication.
- Regular user and teacher roles. Teachers can create classes and assign
  scheduled problem sets.
- Optional class self-registration and teacher-reviewed registration requests.
- AutoJudge-only submissions using every active language in the shared NOCA
  language registry.
- Public problem browsing, samples, statistics, and rating history.
- Interactive problems with custom validators. Arena marks these problems in
  the list and detail pages, validates uploaded validator source before
  publishing, and blocks submissions while a configured validator is not usable.
- User profiles with solved and attempted problems, submission statistics,
  rating history, affiliation, and optional public visibility.
- Leaderboards and live submission activity.
- Gamification through Capybara badges, because everybody loves capybaras.
- An LGPD/GDPR age gate that rejects registrations under age 13 and requires
  parental or legal-guardian consent for users aged 13 through 17.
- Notifications for judging, rating, and optional AI review events.
- Administrative tools for users, affiliations, categories, problems, test
  cases, and worker status.

See the [Arena overview](arena/docs/ARENA.md),
[Arena routes](arena/docs/ROUTES.md), and
[Arena services](arena/docs/SERVICES.md) for implementation details. See
[custom interactive validators](docs/CUSTOM_VALIDATOR.md) for validator
authoring, packaging, judging, and diagnostics.

### Rating

Rating is an optional, single-replica Arena worker. It periodically derives
competitive and analytical data from authoritative Arena submission records.

The worker:

- Computes problem difficulty from accepted submissions.
- Computes user ratings and rating-history snapshots.
- Aggregates institution and affiliation ratings.
- Precomputes problem and user statistics.
- Assigns Capybara badges through incremental passes and periodic full
  reconciliation.
- Publishes scheduler metadata so Arena can display consistent update timing.

Rating isn't required to submit or judge Arena solutions, but rating,
statistics, and badge data won't update while it is stopped.

### AI Assistant

AI Assistant is an optional Arena worker that provides feedback on a user's
submission. A review request combines the problem statement, submitted source
code, and a system prompt that asks the model to guide the user without writing
the solution for them.

AI Assistant supports two API-key paths:

- **Bring your own key (BYOK):** Arena encrypts the user's OpenAI API key at
  rest. The worker processes that user's request through the OpenAI Responses
  API and stores the review immediately.
- **Platform key:** When `NOCA_AI_OPENAI_API_KEY` is configured, users without
  a personal key can use platform-funded reviews. These requests use the OpenAI
  Batch API and complete asynchronously.

The worker tracks credits, token usage, estimated cost, durable batch jobs,
notifications, stale-job recovery, and queue reconciliation. Prompt-injection
patterns in submission content are rejected before an OpenAI request is made.

See [AI Assistant](docs/AIASSISTANT.md) and the
[AI review flow](docs/AIREVIEW_FLOW.md) for the full lifecycle.

## AutoJudge

AutoJudge is the `autojudge` workspace and is shared by Contest and Arena. Each
submission is stored in PostgreSQL and queued through Valkey. One worker slot
claims the job, compiles the source when required, runs it against the problem's
test cases, and persists the judgment and test results.

Its main capabilities include:

- **Language registry:** AutoJudge uses the database-backed registry shared by
  Contest and Arena. NOCA currently ships definitions for 18 languages, and
  deployments can activate the required subset.
- **Container-based isolation:** Each submission runs in isolated Docker
  containers. `isolate` is the authoritative inner sandbox for time, memory,
  PID, and output limits, while Docker is the outer safety boundary. Languages
  can define custom compilation and execution images and commands.
- **Verdict aggregation:** The final priority is CE -> RE -> TLE -> MLE -> OLE
  -> WA -> PE -> AC, with per-test results stored alongside the final verdict.
- **Immutable submissions, mutable judgments:** Source submissions aren't
  changed. Separate judgment-attempt rows support rejudging without modifying
  the original submission.
- **Separate machine and human verdict flows:** Contest can require a judge to
  confirm a machine verdict. Arena uses machine verdicts directly.
- **Container pool:** Pre-warmed run containers per language reduce execution
  latency.
- **Auto-Limit profiling:** Dedicated priority jobs execute reference solutions
  repeatedly and persist measured per-language resource limits.
- **Stale in-flight recovery:** Startup and periodic reconciliation requeue
  non-terminal database jobs missing from Valkey, including jobs lost between a
  producer's commit and follow-up enqueue.
- **Zombie container cleanup and worker heartbeat:** Background reapers clean
  up stuck containers and publish worker liveness.
- **Fixed-width concurrency:** Each worker process runs a configured number of
  independent consumer slots.

See the [AutoJudge infrastructure reference](autojudge/docs/AUTOJUDGE_INFRA.md)
and [submission data flow](docs/DATA_FLOW_FROM_SUBMISSION_TO_VERDICT.md) for
implementation details.

### Prometheus observability

AutoJudge can expose Prometheus metrics for job processing, verdicts,
compilation, execution resources, queue depth, container-pool saturation,
recovery activity, and worker health.

Set `NOCA_JUDGE_METRICS_ENABLED=true` and configure
`NOCA_JUDGE_METRICS_PORT` for the exposition server. A minimal Prometheus scrape
job is:

```yaml
scrape_configs:
  - job_name: autojudge
    static_configs:
      - targets: ["autojudge:9101"]
```

See the `NOCA_JUDGE_METRICS_*` entries in the
[configuration reference](docs/CONFIG.md) for the complete settings.

## Communication model

A submission follows the same durable queue pattern in Contest and Arena. The
consumer-specific persistence step differs because Contest can include human
review, while Arena uses the machine judgment directly.

```text
User                 App                           PG                  VK                  AJ
  (User)          (Contest/Arena)                (PostgreSQL)          (Valkey)          (AutoJudge)
    |                    |                              |                   |                   |
    | Submit source code |                              |                   |                   |
    |------------------->|                              |                   |                   |
    |                    | Store submission and queued judgment             |                   |
    |                    |----------------------------->|                   |                   |
    |                    | Enqueue job identifier       |                   |                   |
    |                    |------------------------------------------------->|                   |
    |                    |                              |                   | Claim one job     |
    |                    |                              |                   |<------------------|
    |                    | Load source, language, limits, and test cases    |                   |
    |                    |                              |<--------------------------------------|
    |                    |                              |                   |                   |---+ Compile and
    |                    |                              |                   |                   |   | run in Docker 
    |                    |                              |                   |                   |<--+ and isolate
    |                    | Store test results and final machine verdict     |                   |
    |                    |                              |<--------------------------------------|
    |                    |                              |                   |                   |
    |                    |                              | Remove in-flight state & publish updates
    |                    |                              |                   |<------------------|
    |                    | Read current judgment state  |                   |                   |
    |                    |----------------------------->|                   |                   |
    | Display result     |                              |                   |                   |
    |< - - - - - - - - - |                              |                   |                   |
    |                    |                              |                   |                   |
```

PostgreSQL remains the recovery source when the queue and database temporarily
disagree. AutoJudge's reconciler finds durable, non-terminal jobs missing from
Valkey and enqueues them again. Idempotency locks and judgment state transitions
prevent duplicate consumers from applying the same attempt concurrently.

# Running

You can run NOCA as a complete ecosystem or deploy only the product and workers
you need. PostgreSQL and Valkey are common dependencies for normal deployments;
AutoJudge additionally needs access to Docker and the configured test case
storage.

## Configuration

NOCA reads configuration from environment variables, normally supplied through
a `.env` file. Variables use prefixes that identify their owners:

| Prefix | Scope |
| --- | --- |
| `NOCA_` | Shared database, Valkey, security, email, and runtime settings |
| `NOCA_WEB_` | Contest application |
| `NOCA_ARENA_` | Arena application |
| `NOCA_JUDGE_` | AutoJudge worker |
| `NOCA_RATING_` | Rating worker |
| `NOCA_AI_` | AI Assistant worker |

The [configuration reference](docs/CONFIG.md) lists every supported option,
its default, validation rules, ownership, and operational notes. Production
deployments must use secure secrets, secure cookies behind HTTPS, persistent
storage, and a network-disabled judge execution environment.

Default values are a safe start.

## Quickstart

Install all workspace packages and fetch the shared browser assets before
starting a development environment:

```bash
uv sync --all-packages
uv run python scripts/fetch_assets.py
uv run alembic upgrade head
uv run python scripts/bootstrap_languages.py
```

Start each selected runtime in a separate terminal. Common combinations are:

- Contest: `noca-web` + `noca-autojudge`.
- Arena core: `noca-arena` + `noca-autojudge`.
- Arena with ratings: `noca-arena` + `noca-autojudge` + `noca-rating`.
- Arena with AI feedback: `noca-arena` + `noca-autojudge` +
  `noca-aiassistant`.
- Full ecosystem: all five runtime modules.

For example, start the complete ecosystem with:

```bash
uv run noca-web
uv run noca-arena
uv run noca-autojudge
uv run noca-rating
uv run noca-aiassistant
```

Run **only one** Rating replica. Contest doesn't depend on Arena, Rating, or AI
Assistant. Arena doesn't depend on Contest, and its Rating and AI Assistant
workers are optional.

See [Bootstrap and deployment](docs/BOOTSTRAP.md) for database setup, secrets,
language images, and production preparation.

## Docker

The repository includes [a Docker Compose sample](docker-compose.yml.sample)
with Caddy, Contest, Arena, AutoJudge, Rating, AI Assistant, PostgreSQL, and
Valkey services. Use it as a deployment template and remove application or
worker services that you don't need.

The sample mounts persistent PostgreSQL and Valkey volumes, problem statements,
shared test case storage, the crypto environment file, and the Docker socket
required by AutoJudge. Review every environment value and volume path before
using it in production, especially:

- Database, JWT, worker-command, and encryption secrets.
- `NOCA_COOKIE_SECURE` and reverse-proxy trust settings.
- Test case and statement storage paths.
- Docker socket access and the judge container network mode.
- OpenAI credentials and AI credit policy.
- The single-replica requirement for Rating.

Build and start the selected Compose services with:

```bash
docker compose -f docker-compose.yml.sample up --build
```

# Development

NOCA requires Python 3.14 and uses `uv` for workspace and dependency management.
PostgreSQL, Valkey, and Docker must be available for integration paths that use
them. During local application development, run Contest, Arena, AutoJudge,
Rating, and AI Assistant directly instead of placing them in containers.

The repository's implementation conventions are documented in
[AGENTS.md](AGENTS.md), and the detailed architecture is in
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Code quality

Run formatting, linting, type checking, and the relevant tests before opening a
change:

```bash
uv run ruff format .
uv run ruff check --fix .
uv run mypy web shared autojudge arena rating
uv run pytest
```

The full test suite takes more than five minutes. During development, run the
focused module or test file first, then run the full suite before release.
Template changes also require `djlint`:

```bash
uv run djlint web/template --reformat
uv run djlint arena/template --reformat
```

# Credits and acknowledgments

NOCA builds on open-source projects and the work of the competitive programming
community.

- [FastAPI](https://fastapi.tiangolo.com/) by Sebastian Ramirez provides the
  asynchronous web framework.
- [HTMX](https://htmx.org/) provides focused browser interactions without a
  client-side application framework.
- SQLAlchemy, Pydantic, Uvicorn, Starlette, PostgreSQL, Valkey, Docker, and
  `isolate` provide core runtime infrastructure.
- [Country Flags](https://github.com/hampusborgos/country-flags) by Hampus
  Borgos provides the ISO 3166-1 flag assets used in Arena.
- [BOCA](https://www.github.com/cassiopc/boca) Online Contest Administrator and Brazil's competitive programming
  community inspired NOCA's Contest workflows.

See [CREDITS](CREDITS) for the maintained attribution list.

# Legal

NOCA is distributed under the terms in [LICENSE](LICENSE), without warranty.
Deployment operators are responsible for their own privacy notices, terms of
service, data-retention policies, OpenAI usage, and compliance obligations.

Arena's user-facing legal documents live in [docs/legal](docs/legal/). Review
and adapt them for the organization and jurisdiction operating the deployment.
