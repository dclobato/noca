# NOCA - Next Online Contest Administrator

NOCA is a modern, Docker-based platform for running ICPC-like programming contests. Built with Python and FastAPI, it provides a modular infrastructure for managing competitive programming competitions with support for multiple concurrent contests, automated judging, and comprehensive contest administration.

## Overview

NOCA reimagines the core principles of BOCA (the backbone of Brazilian programming marathons) for the modern era. It is a modular application designed specifically for competitive programming contests, offering flexible contest management, secure code execution, public Arena accounts, and real-time scoring.

### Key Features

- **Multiple Concurrent Contests**: Run multiple contests simultaneously, each with isolated problems, users, and configurations
- **Per-Contest Scope**: Problems and users are scoped to individual contests, ensuring clean separation
- **Multi-Site Contest Management**: Organize each contest into physical sites, assign teams and staff to sites, auto-create missing sites during imports, and manage users with site-aware grouping
- **Flexible Judging Modes**: 
  - Autojudge-only contests for fully automated verdicts
  - Human-reviewed contests requiring judge confirmation for final verdicts
  - Submissions can be re-judged if required
- **Customizable Contest Rules**: Each contest can define its own:
  - Duration and timing
  - Penalty systems
  - Reaper cycles for background tasks
  - Scoreboard freeze/thaw mechanics
- **Import/Export**: Bulk import and export problems and users via CSV/JSON
- **Site Logistics Reporting**: Generate a markdown users-per-site report with role grouping, chief judge annotation, login URL, contest rules summary, and team location data for printing or PDF conversion
- **Submission Management**: Teams can download their submissions after contest ends
- **Contest Analytics Reports**: Admins and judges can access a reports page with problem summaries, verdict/language cross-tables, team-vs-problem breakdowns, and time-based charts
- **Animeitor / Reveleitor Compatibility Service**: Admins and UberAdmins can download an Animeitor-compatible webcast ZIP from `Administration > Export/Import` for use with [maratona-animeitor](https://github.com/wuerges/maratona-animeitor), including live animated scoreboards and post-freeze revelation ceremonies
- **Flexible Problem Statements**: Support for both PDF and Markdown formats with LaTeX math equations and Mermaid diagram rendering
- **Online Test Case Editor**: Web-based interface for managing problem test cases
- **Operational Task Workflow**: Accepted runs can auto-create balloon tasks, and task dashboards include queue time, service time, and finished-task detail views
- **Container-Based Isolation**: 
  - Each submission runs in isolated Docker containers
  - `isolate` is the authoritative inner sandbox for time, memory, PID, and output limits
  - Docker remains the outer safety boundary around judge execution
  - Custom Dockerfile support for compilation and execution
- **Per-Contest Language Selection**: At contest creation, the uberadmin selects which active languages are available to teams. The allowed set is immutable after creation and enforced across submission, problem limits, and language-display pages
- **Language Support**: Script for seeding common languages (C, C++, Java, Python, etc.) with optional icon metadata for richer UI presentation
- **Auto-Limit Profiling**: Reference solutions can be profiled per language to populate problem limits, with per-language profiling defaults for repetitions and PID floors
- **Modern Web UI**: Plain HTML + CSS + JavaScript + Bootstrap with Server-Sent Events
- **Safer Team Workflow**: Submission forms can show a confirmation modal before upload, while team profile identity, site, and location fields remain administrator-managed during contests
- **Background Services**: Task queue for balloons, printing, and SOS requests
- **Comprehensive RBAC**: Role-based access control with contest-scoped identities

## Architecture

NOCA follows a strict multi-module architecture with clear separation between contest administration, public Arena identity, shared contracts, and untrusted code execution.

### Module Structure

```
├── web/              # Contest administration and contest-facing FastAPI app
├── arena/            # Public Arena FastAPI app with its own identity domain
├── autojudge/        # Asynchronous judge worker (compilation + sandboxed execution)
├── rating/           # Single-replica Arena rating recomputation worker
├── aiassistant/      # Arena AI code review worker (OpenAI Responses / Batch API)
└── shared/           # Common schema, enums, queue payloads, and services
```

The repository root is a non-package uv workspace. Each runtime module has its
own workspace package and console-script entrypoint:

| Package | Entrypoint | Role |
|---------|-----------|------|
| `noca-web` | `uv run noca-web` | Contest administration HTTP server (port 8000) |
| `noca-arena` | `uv run noca-arena` | Public Arena HTTP server (port 8001) |
| `noca-autojudge` | `uv run noca-autojudge` | Asynchronous judge worker |
| `noca-rating` | `uv run noca-rating` | Arena rating recomputation worker (single replica) |
| `noca-aiassistant` | `uv run noca-aiassistant` | Arena AI review worker |
| `noca-shared` | *(library only)* | Cross-module schema, enums, and services |

### Module Relationships

No module imports Python code from another runtime module. All coordination happens
through **PostgreSQL**, **Valkey**, and the **shared filesystem**.

```
                         ┌─────────────────────────────────────────────┐
                         │              noca-shared                     │
                         │  (schema · enums · services · queue payloads)│
                         └──────────────────┬──────────────────────────┘
                                            │  imported by all runtime modules
          ┌─────────────────────────────────┼──────────────────────────────────┐
          │                                 │                                  │
          ▼                                 ▼                                  ▼
  ┌───────────────┐                ┌────────────────┐               ┌──────────────────┐
  │   noca-web    │                │   noca-arena   │               │  noca-autojudge  │
  │  (port 8000)  │                │  (port 8001)   │               │  (judge worker)  │
  │               │                │                │               │                  │
  │ Contest admin │                │ Public Arena   │               │ Compile + run    │
  │ Auth / RBAC   │                │ Signup / Login │               │ submissions in   │
  │ Problems      │                │ OTP accounts   │               │ Docker+isolate   │
  │ Submissions   │                │ Submissions    │               │ containers       │
  │ Scoreboard    │                │ Notifications  │               │                  │
  └───────┬───────┘                └───────┬────────┘               └────────┬─────────┘
          │                                │                                  │
          │    ┌───────────────────────────┼──────────────────────────────────┤
          │    │                           │                                  │
          ▼    ▼                           ▼                                  ▼
  ┌──────────────────────────────────────────────────────────────────────────────────┐
  │                          Infrastructure Boundaries                               │
  │                                                                                  │
  │   PostgreSQL (system of record)  ·  Valkey (queues & cache)  ·  Filesystem      │
  └──────────────────────────────────────────────────────────────────────────────────┘
          ▲                                ▲
          │                                │
  ┌───────┴───────┐                ┌───────┴────────────┐
  │  noca-rating  │                │  noca-aiassistant  │
  │               │                │                    │
  │ Arena problem │                │ AI code review     │
  │ user &        │                │ OpenAI Responses   │
  │ affiliation   │                │ API (user key)     │
  │ rating cycles │                │ OpenAI Batch API   │
  │ (single       │                │ (platform key,     │
  │  replica)     │                │  ~50% cost saving) │
  └───────────────┘                └──────────┬─────────┘
                                              │
                                       OpenAI API
```

#### Key data flows

| Flow | Producers | Channel | Consumers |
|------|-----------|---------|-----------|
| Contest submission judging | `noca-web` | Valkey `judge:queue:pending` | `noca-autojudge` |
| Arena AI review (online) | `noca-arena` | Valkey `ai:queue:pending` | `noca-aiassistant` |
| Arena AI review (batch) | `noca-aiassistant` | OpenAI Batch API + `arena_ai_batch_jobs` PostgreSQL table | `noca-aiassistant` batch poller |
| Arena rating recomputation | `noca-rating` | PostgreSQL + Valkey (next-cycle timestamp) | `noca-arena` (footer display) |
| Arena notifications | `noca-aiassistant`, `noca-autojudge` | PostgreSQL `arena_notifications` | `noca-arena` |

### Communication Model

The modules communicate through three infrastructure boundaries:

1. **PostgreSQL**: System of record for all persistent data
2. **Valkey (Redis)**: Lightweight coordination layer for judging queues, AI review queues, and rating cache
3. **Shared Filesystem**: Problem statements and test case storage

This design enables:
- Independent scaling of each module
- Different security hardening per module
- Clear failure boundaries
- Auditability of all operations

### Judging Architecture

```
Team submits code → Web validates & stores → Valkey queue → Autojudge processes
   ↓                                                                              ↓
SubmissionJudgment ← PostgreSQL ← Container execution ← Verdict calculation
```

The system separates immutable submissions from judgment attempts, supporting:
- Rejudging without mutating original submissions
- Separate machine and human verdict flows
- Complete audit trails

### AI Review Architecture

Arena participants can request AI-powered code review on their submissions:

- **User-funded (fast path)**: if the user has configured a personal OpenAI API key,
  the aiassistant worker calls the Responses API synchronously and stores the result immediately.
- **Platform-funded (batch path)**: if no user key is present, the worker submits to
  the OpenAI Batch API (up to 24 h, ~50% cost reduction) and polls for completion via
  the `arena_ai_batch_jobs` state-machine table.

### Rating Architecture

The `rating` worker runs as a **single replica** and periodically recomputes Arena
problem difficulty, user scores, and affiliation ratings. It publishes the next scheduled
cycle timestamp to Valkey so all Arena replicas can display a consistent rating-update
countdown in the footer without querying the database.

### Core Components

- **Web Module**: Handles HTTP requests, authentication, contest management, and business logic
- **Autojudge Worker**: Processes submission queues, runs code in containers, produces verdicts
- **Rating Worker**: Owns periodic Arena rating recomputation cycles (single-replica)
- **AI Assistant Worker**: Dequeues Arena AI review jobs; routes to online or batch OpenAI path
- **Container Pool**: Pre-warmed Docker containers for low-latency execution
- **Scoreboard Cache**: Aggressive caching with TTL management for live contests
- **Background Reapers**: Async tasks for clarification, task, and stale-job recovery

## Technology Stack

- **Backend**: Python 3.14+, FastAPI, SQLAlchemy (async), Pydantic
- **Database**: PostgreSQL with asyncpg
- **Queue**: Valkey (Redis) with BLMOVE-based protocol
- **Containerization**: Docker with `isolate`-backed execution sandboxing
- **Frontend**: Server-rendered HTML, Bootstrap 5, Vanilla JS, HTMX, LaTeX (KaTeX), Mermaid diagrams
- **Build**: uv workspace packages for module dependency management, Ruff for formatting/linting
- **Type Checking**: MyPy with strict mode

## Installation

### Prerequisites

- Python 3.14 or higher
- PostgreSQL 13+
- Valkey (Redis) 6+
- Docker with buildx
- UV package manager

### Quick Start

See [BOOTSTRAP.md](docs/BOOTSTRAP.md) for detailed instructions on running in development or production mode.

### Upgrade Notes for 5.0.0

This release changes the judging stack in a breaking way:

- apply the latest database migrations before starting the web app or worker
- rebuild the judge images so the run containers include `isolate`
- re-bootstrap languages so the DB-backed language registry picks up the new per-language profiling defaults

Typical upgrade commands:

```bash
uv run alembic upgrade head
uv run python scripts/bootstrap_languages.py
```

### Running the Application

**Development mode:**

```bash
# Terminal 1: Web server (contest administration, port 8000)
uv run noca-web

# Terminal 2: Arena server (public platform, port 8001)
uv run noca-arena

# Terminal 3: Autojudge worker
uv run noca-autojudge

# Terminal 4: Arena rating worker (single replica)
uv run noca-rating

# Terminal 5: AI review worker
uv run noca-aiassistant
```

**Production deployment:**

See `docker-compose.yml.sample` for production Docker Compose configuration.

## Configuration

Configuration is entirely environment-based. See [CONFIG.md](docs/CONFIG.md) for complete reference of all environment variables.

Key configuration areas:
- Database and Valkey connection settings
- JWT secret and cookie security
- Judge worker concurrency and container limits
- Password policies and geolocation
- Problem statement and test case directories
- OpenAI API key, model, token limits, and per-token pricing (for AI reviews)
- Batch API poll interval and optional batch price overrides
- Arena rating recomputation interval and algorithm weights

## Usage

### Creating Your First Contest

1. **Access the system**: Log in as UberAdmin (create account via environment variables)
2. **Create a contest**: Navigate to contest management and create a new contest
3. **Login into the contest**: Using the contest owner account, log into the contest
3. **Add problems**: Upload problem statements and configure test cases
4. **Import users**: Bulk import teams via CSV or create individually
5. **Start contest**: Transition contest from DRAFT to ACTIVE state
6. **Monitor**: Use admin dashboard to track submissions and scoreboard

### Contest Lifecycle

Contests progress through these states:
- **DRAFT**: Configuration phase, not visible to teams
- **ACTIVE**: Running contest, accepting submissions
- **FROZEN**: Scoreboard frozen for teams (judges see live data)
- **ENDED**: Contest finished, no new submissions
- **PAST**: Finalized, scoreboard released

### Judging Workflow

1. Team submits solution through web interface
2. Web validates and queues submission in Valkey
3. Autojudge dequeues and processes in container:
   - Compilation in isolated container
   - Execution through `isolate` with per-test resource limits
   - Verdict aggregation (CE → RE → TLE → MLE → OLE → WA → PE → AC)
4. Results stored in PostgreSQL
5. For human-reviewed contests: judges confirm verdicts
6. Scoreboard cache invalidated, live updates via SSE

Problems can also be profiled with a reference implementation to derive per-language limits before teams submit solutions.

### Observability

The autojudge worker exposes a Prometheus metrics endpoint at `http://<worker-host>:9101/metrics`
(port configurable via `NOCA_JUDGE_METRICS_PORT`; disable with `NOCA_JUDGE_METRICS_ENABLED=false`).

Metrics cover:
- **Job processing**: dispatch/completion counters, submission and profiling duration histograms, verdict totals by language
- **Compile phase**: outcome counters and duration histograms per language
- **Run phase**: wall time, CPU time, and memory histograms; timeout counters per timeout kind
- **Container pool**: available container gauges, acquire duration histograms and outcome counters
- **Queue depths**: Valkey queue lengths polled every 15 seconds (`pending`, `priority`, `profiling`, `inflight`)
- **Reaper**: cycle, requeue, drop, and error counters
- **Worker process**: concurrency slot gauge and start timestamp

See `NOCA_JUDGE_METRICS_*` variables in [CONFIG.md](docs/CONFIG.md) for full configuration details.

#### Connecting Prometheus

Add a scrape job to your `prometheus.yml`:

```yaml
scrape_configs:
  - job_name: autojudge
    static_configs:
      - targets: ["<worker-host>:9101"]
```

If you are using Docker Compose, add a Prometheus service and mount the config:

```yaml
services:
  prometheus:
    image: prom/prometheus:latest
    ports:
      - "9090:9090"
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml:ro
```

#### Useful starter queries

Once Prometheus is scraping, open the expression browser at `http://<prometheus>:9090` or connect Grafana:

```promql
# Verdict rate by language over the last 5 minutes
rate(autojudge_verdicts_total[5m])

# p95 submission duration per language over the last 10 minutes
histogram_quantile(0.95, rate(autojudge_submission_duration_seconds_bucket[10m]))

# Available warm containers per language (pool saturation)
autojudge_pool_available_containers

# Current queue backlog per queue
autojudge_queue_depth
```

### Animeitor / Reveleitor Compatibility Service

NOCA includes an admin-only compatibility export for the legacy BOCA webcast
format consumed by `maratona-animeitor`.

- **Where to access it**: `Administration > Export/Import`
- **Route**: `GET /c/{slug}/admin/export-animeitor`
- **Who can use it**: Contest Admins and UberAdmins
- **Export format**: ZIP archive containing `contest`, `runs`, `time`, `version`, and `icpc`
- **Protocol details**: Uses the ASCII file separator (`0x1C`), ICPC-rounded run times, and legacy status mapping (`Y` / `N` / `X` / `?`)
- **Compatibility behavior**: The export contains real verdicts and relies on the consumer to reapply scoreboard freeze locally from the `contest` metadata
- **Preconditions**: The contest must have at least one team and one problem

This export can be generated during the contest for live scoreboard playback or
after the contest for the reveleitor ceremony. The compatibility layer keeps a
hardcoded penalty of `20` to match the behavior expected by the downstream
consumer.

## Documentation

- **[AIREVIEW_FLOW.md](docs/AIREVIEW_FLOW.md)**: End-to-end AI review flow (online fast-track and batch paths)
- **[ARCHITECTURE.md](docs/ARCHITECTURE.md)**: Concise system architecture overview
- **[ARCHITECTURE_RUNTIME.md](docs/ARCHITECTURE_RUNTIME.md)**: Detailed runtime architecture reference
- **[ANIMEITOR-REVELEITOR.md](docs/ANIMEITOR-REVELEITOR.md)**: How to use the compatibility export with `animeitor` and `reveleitor`
- **[DATA_FLOW_FROM_SUBMISSION_TO_VERDICT.md](docs/DATA_FLOW_FROM_SUBMISSION_TO_VERDICT.md)**: Submission lifecycle details
- **[CONFIG.md](docs/CONFIG.md)**: Environment configuration reference
- **[DEVELOPMENT.md](docs/DEVELOPMENT.md)**: Development setup and workflows
- **[PADROES_UI.md](docs/PADROES_UI.md)**: UI patterns and conventions
- **[AUTOJUDGE_INFRA.md](autojudge/docs/AUTOJUDGE_INFRA.md)**: Worker isolation and container execution

## Development

### Code Quality

```bash
# Format code
uv run ruff format .

# Lint code
uv run ruff check --fix .

# Type checking
uv run mypy web shared autojudge arena rating aiassistant

# Run tests
uv run pytest
```

### Project Structure

```
noca/
├── web/                      # FastAPI web application (contest administration)
│   ├── routes/              # HTTP route handlers
│   ├── services/            # Business logic
│   ├── models/              # ORM models
│   ├── template/            # Jinja2 templates
│   └── static/              # CSS, JS, images
├── arena/                    # Public Arena FastAPI application
│   ├── routes/              # HTTP route handlers
│   ├── services/            # Arena business logic
│   ├── models/              # Arena ORM models
│   ├── template/            # Jinja2 templates
│   └── static/              # CSS, JS, images
├── autojudge/               # Judge worker
│   ├── worker.py            # Queue consumer
│   ├── runner.py            # Container execution
│   └── pool.py              # Container pool management
├── rating/                  # Arena rating recomputation worker (single replica)
│   ├── worker.py            # Main entry point and loop orchestration
│   └── loops.py             # Rating cycle loops (problems, users, affiliations)
├── aiassistant/             # Arena AI review worker
│   ├── worker.py            # Queue consumer + loop orchestration
│   ├── reviewer.py          # Online (synchronous) Responses API reviewer
│   ├── batch_reviewer.py    # Batch API submission (platform key path)
│   ├── batch_poller.py      # Polls OpenAI batch jobs, orchestrates results
│   ├── batch_results.py     # Stores batch review results, notifs, file cleanup
│   ├── batch_status.py      # OpenAI status mapping + response parsing helpers
│   ├── prompts.py           # Shared AI system prompt
│   ├── config.py            # AI assistant settings
│   └── db/                  # SQLAlchemy Core query helpers
├── shared/                  # Common code
│   ├── db_schema/           # Database schema definitions (per-domain sub-packages)
│   ├── enumerations.py      # Cross-module enums (verdicts, roles, statuses…)
│   ├── queue_schema.py      # Queue payload models
│   ├── language_registry.py # Language configurations
│   └── services/            # Shared service modules (email, Valkey, rating…)
├── docs/                    # Architecture and design docs
├── containers/              # Docker image definitions
├── migrations/              # Alembic database migrations
├── scripts/                 # Shared utility scripts
└── tests/                   # Test suite
```

### Adding New Features

1. Follow existing code patterns and conventions
2. Add type hints for all functions and methods
3. Write tests for new functionality
4. Update documentation as needed
5. Ensure all checks pass: format, lint, typecheck, tests

## Security

- **Container Isolation**: Untrusted code runs in Docker containers with `network=none` and `isolate` as the authoritative inner sandbox
- **Contest Scoping**: All users (except UberAdmin) are isolated to single contests
- **RBAC**: Strict role-based access control with JWT tokens
- **No Code Execution in Web Layer**: Judge worker handles all compilation and execution

## License

See [LICENSE](LICENSE) file for details.

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes with tests
4. Ensure all quality checks pass
5. Submit a pull request

## Support

For issues and questions:
- GitHub Issues: [Report bugs and request features](https://github.com/dclobato/noca/issues)
- Documentation: Check the [docs/](docs/) directory
- Architecture: Review [ARCHITECTURE.md](docs/ARCHITECTURE.md) for system understanding
