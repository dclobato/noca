# Environment file layers

[docs/CONFIG.md](CONFIG.md) documents what every variable *means*. This document
describes how those variables are **packaged** for a deployment.

## Why there is more than one file

NOCA has 322 configuration variables. They used to live in a single 946-line
`.env.full`, which every service loaded in its entirety. That had two costs:

- **Nobody could tell which service needed what.** A variable's section heading
  was the only hint, and headings drift. `NOCA_COOKIE_SECURE` sat under *Landing
  page* while being read by Web and Arena.
- **Every container received every secret.** The autojudge held the OpenAI key,
  the health monitor held the database password and the JWT signing key, and the
  mailer held the bootstrap UberAdmin password -- none of which those processes
  read.

The variables are now split into **layers**. Each layer is one coherent group of
settings with one set of readers; each service composes the layers it actually
reads. Nothing is duplicated: a shared value has exactly one home, so its copies
cannot drift apart.

`env_layers.toml` is the machine-readable source of truth for the composition,
and `tests/test_env_layers.py` enforces it (see [Invariants](#invariants)).

## Setting up a deployment

Copy the templates a deployment needs and fill them in. Every `.env.<layer>.full`
is a template with defaults and inline documentation; keep the `.full` originals
in the repository and edit your copies.

```sh
for layer in common database http webarena email storage workers aireview \
             web arena autojudge rating aiassistant mailer healthmonitor animator; do
  cp ".env.$layer.full" ".env.$layer"
done
cp .env.compose.full .env      # Compose interpolation + entrypoint knobs
```

Then point each service's `env_file:` at your copies. `docker-compose.yml.sample`
ships wired to the `.full` templates so a fresh clone starts without renaming
anything; a real deployment should drop the `.full` suffix and keep the filled-in
files out of version control.

A **Web-only** or **Arena-only** install copies just that service's stack. The
layer boundaries are what make that possible: a Web-only site never has to invent
a value for `NOCA_AI_OPENAI_API_KEY` or `NOCA_ARENA_GOOGLE_OAUTH_CLIENT_SECRET`.

## The layers

| Layer | Holds | Read by |
|---|---|---|
| `common` | Environment, log level, Valkey connection, startup wait | every module |
| `database` | PostgreSQL connection | every module except the health monitor |
| `http` | Proxy trust, security headers, `/health` limit, status-page link | web, arena, animator, healthmonitor |
| `webarena` | Cookies, JWT, auth throttling, password policy, images, API keys | web, arena |
| `email` | Sender identity, queue TTL, per-actor budget | web, arena, mailer |
| `storage` | Shared problem test-case directory | web, arena, autojudge |
| `workers` | Worker pause/resume HMAC secret | arena, autojudge, aiassistant, mailer |
| `aireview` | The two settings Arena and the AI worker must agree on | arena, aiassistant |
| `web`, `arena`, `autojudge`, `rating`, `aiassistant`, `mailer`, `healthmonitor`, `animator`, `landingpage` | that module's own settings | that module |
| `postgres` | the bundled database server, in `POSTGRES_*` names | the bundled `postgres` service |

Three further templates are **not** layers and never belong in an `env_file:`:

| Template | Read by |
|---|---|
| `.env.compose.full` | Compose interpolation (`NOCA_DATA_ROOT`, `DOCKER_GID`) and container entrypoints |
| `.env.build.full` | `containers/build.sh`, at build time only |
| `.env.devtools.full` | `scripts/generate_backlog_index.py` and the Playwright checks |

`NOCA_DATA_ROOT` in particular must live in the project-root `.env`: Compose reads
that file to interpolate `${...}` in the compose file itself, which is a different
mechanism from `env_file:` and cannot be satisfied by a layer. The sample stack
sets it to `.` because `scripts/backup_noca.sh` archives `problem_statements`,
`problem_testcases`, and `email_log` directly below the project directory.

## The stacks

| Service | Layers, broad to narrow |
|---|---|
| web | common, database, http, webarena, email, storage, web |
| arena | common, database, http, webarena, email, storage, aireview, workers, arena |
| autojudge | common, database, storage, workers, autojudge |
| rating | common, database, rating |
| aiassistant | common, database, aireview, workers, aiassistant |
| mailer | common, database, email, workers, mailer |
| healthmonitor | common, http, healthmonitor |
| animator | common, database, http, animator |
| landingpage | landingpage |
| postgres (bundled server) | postgres |

## Precedence

Compose applies the sources in this order, each winning over the ones before it:

1. `env_file:` entries, **in the order listed** -- a later file overrides an
   earlier one.
2. The service's `environment:` block, which beats every `env_file`.
3. Anything already in the container image's environment is overridden by both.

Stacks are therefore written broad-to-narrow, and `environment:` in the sample
stack is reserved for values that are true *because the process runs in this
container*: the bind address (`0.0.0.0`, because Caddy reaches the service over
the compose network), the service names `postgres` and `valkey`, the in-container
mount paths, and the pinned ports. Those must override the templates, which hold
host-oriented defaults.

Every module's settings class is configured with `extra="ignore"`, so a variable a
service does not read is harmless. That is a safety net, not a licence: the point
of the split is that secrets do not travel to processes with no use for them.

## Interpolation is a separate mechanism

`env_file:` and `${...}` look alike and are not. **`${...}` in the compose file is
resolved by Compose itself, from the shell environment and the project-root `.env`
-- never from an `env_file:` entry.** An `env_file` is handed to the container and
read by nothing else.

```yaml
env_file: [.env.web.full]              # NOCA_WEB_PORT=8000 lives here
environment:
  NOCA_WEB_PORT: ${NOCA_WEB_PORT:-8000}   # does NOT read that file
```

That line resolves to the literal `8000` whatever `.env.web.full` says -- and
because `environment:` outranks every `env_file`, it then *overrides* the layer's
real value on the way into the container. The stack starts, nothing warns, and the
proxy talks to a port the service is not listening on.

So the split imposes a rule the single-file layout never needed, because there
`.env` was both the interpolation source and the env_file:

- a variable an **application** reads belongs in a layer, and must never appear
  inside `${...}` in the compose file;
- a variable **Compose itself** needs -- `NOCA_DATA_ROOT` for a volume path,
  `DOCKER_GID` for `group_add`, `PUID`/`PGID`, published `ports:`, healthcheck
  URLs, Caddy's own upstream ports -- belongs in the project-root `.env`, which is
  what `.env.compose.full` is a template for.

`tests/test_env_layers.py::test_no_compose_interpolation_reads_an_env_file_layer`
enforces the first half.

Two consequences are visible in the sample stack. The four HTTP ports are
**pinned literals** in `environment:`, in `ports:`, in the healthchecks, and in
Caddy's environment, held equal to the settings defaults by
`tests/test_deployment_ports.py`; every service in the stack is private behind
Caddy, so its port is a property of the stack, and an operator who wants a
different internal port edits the compose file rather than a template. And the
bundled `postgres` service has a template of its own, `.env.postgres.full`, in the
postgres image's `POSTGRES_*` names, instead of deriving them from `NOCA_DB_*`
through interpolation that cannot see `.env.database.full`.

## Invariants

`tests/test_env_layers.py` fails the build when any of these breaks:

- every variable of a module's `Settings` class is defined by exactly one layer in
  that module's stack, so a new setting cannot be documented without also being
  deployable;
- no variable is defined by two templates;
- a per-module template holds only that module's own variables;
- `docker-compose.yml.sample` lists exactly the manifest's stack, in order;
- every variable in every template appears in `docs/CONFIG.md`.

## Adding a variable

1. Add the field to the module's `config.py`.
2. Document it in `docs/CONFIG.md`.
3. Add it to the right template: the module's own `.env.<module>.full` if one
   module reads it; the layer matching its set of readers if several do. If no
   layer matches that set, add a layer -- to `env_layers.toml`, to the stacks that
   need it, and to `docker-compose.yml.sample` -- rather than duplicating the
   variable into two files.
4. Run `uv run pytest tests/test_env_layers.py`.
