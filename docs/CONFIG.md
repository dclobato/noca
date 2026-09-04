# NOCA Environment Configuration Reference

All configuration is supplied through environment variables.

This document is the **reference**: what each variable means, its range, and its
default. How those variables are **packaged** for a deployment is a separate
question, answered by [ENV_LAYERS.md](ENV_LAYERS.md): they are split into layer
templates (`.env.common.full`, `.env.web.full`, ...) that each service composes
through `env_file:`, so no process receives credentials it does not read. Copy the
templates a deployment needs, fill them in, and keep the copies out of version
control -- `.env` is gitignored and must never be committed.

Every variable is prefixed to make its scope explicit:

| Prefix | Scope |
|--------|-------|
| `NOCA_` | Common to every module (database, Valkey, environment, log level, crypto) **and** the settings shared by the web and arena modules (email, JWT, images, password policy) |
| `NOCA_WEB_` | Web application only (`web/config.py`) |
| `NOCA_ARENA_` | Arena application only (`arena/config.py`) |
| `NOCA_JUDGE_` | Autojudge worker only (`autojudge/config.py`) |
| `NOCA_AI_` | AI assistant worker only (`aiassistant/config.py`) |
| `NOCA_RATING_` | Rating worker only (`rating/config.py`) |
| `NOCA_MAILER_` | Mailer worker only (`mailer/config.py`) |
| `NOCA_HEALTHMON_` | Health monitor only (`healthmonitor/config.py`) |
| `NOCA_ANIMATOR_` | Animator presentation runtime only (`animator/config.py`) |
| `NOCA_LANDINGPAGE_` | Standalone landing page only (`landingpage/`) |

The sections below are grouped the same way: **Common**, **Shared between Web and
Arena**, then one section per module. The environment templates follow the same
grouping, one file per layer; `env_layers.toml` maps each layer to the services
that load it.

---

## Common — all modules

These variables are read by every runtime module (`web`, `arena`, `autojudge`,
`aiassistant`, `rating`, `mailer`, `animator`).

### Database (PostgreSQL)

> **Docker Compose note:** `NOCA_DB_SERVER` must be reachable from inside the container — do not use `localhost` unless the service runs in the same container namespace. Use the Compose service name (e.g. `postgres`) instead.

| Variable | Default | Description |
|----------|---------|-------------|
| `NOCA_DB_USER` | *(required)* | PostgreSQL username |
| `NOCA_DB_PASSWORD` | *(required)* | PostgreSQL password |
| `NOCA_DB_SERVER` | *(required)* | Hostname or IP of the PostgreSQL server (for example, `127.0.0.1` or `postgres`). **On Windows use `127.0.0.1` instead of `localhost`** — asyncpg tries IPv6 first when given a hostname, causing a ~20 s delay before falling back to IPv4. |
| `NOCA_DB_PORT` | `5432` | PostgreSQL port (1–65535). |
| `NOCA_DB_NAME` | *(required)* | Name of the PostgreSQL database |

### Valkey / Redis

> **Docker Compose note:** `NOCA_VALKEY_SERVER` must be reachable from inside the container — do not use `localhost` unless the service runs in the same container namespace. Use the Compose service name (e.g. `valkey`) instead.

| Variable | Default | Description |
|----------|---------|-------------|
| `NOCA_VALKEY_SERVER` | `127.0.0.1` | Hostname of the Valkey server |
| `NOCA_VALKEY_PORT` | `6379` | Valkey port (1–65535) |
| `NOCA_VALKEY_DB` | `0` | Valkey logical database index (0 or greater) |
| `NOCA_VALKEY_USER` | *(empty)* | Valkey username for ACL authentication. Leave empty if auth is not configured. |
| `NOCA_VALKEY_PASSWORD` | *(empty)* | Valkey password. Leave empty if auth is not configured. |
| `NOCA_VALKEY_HEALTHCHECK_INTERVAL_SECONDS` | `5` | How often the app pings Valkey and attempts reconnection while running (1–300 s) |
| `NOCA_STARTUP_TIMEOUT_SECONDS` | `60` | Maximum seconds each module waits for PostgreSQL and Valkey to become reachable at startup before aborting. Set to `0` to skip the wait and fail immediately. Applied to **web**, **arena**, **autojudge**, **aiassistant**, **mailer**, and **animator**; **web** and **arena** also wait this long for a live `noca-mailer` worker (they queue every email for it and cannot send on their own); the **rating** module only waits for PostgreSQL, and **healthmonitor** only waits for Valkey (0–300 s). |
| `NOCA_WORKER_COMMAND_SECRET` | *(empty)* | Shared `HMAC-SHA256` secret for the authenticated worker pause/resume protocol. Read by **arena** (signs/publishes), **autojudge**, **aiassistant**, and **mailer** (verify/apply). When empty the feature is disabled: the Arena dashboard hides pause/resume buttons, direct POSTs are rejected (`rejected_disabled`), and worker command loops do not start. Keep it secret; theft allows pausing queue consumers. |

#### Server memory bounds

The variables above configure how the modules *reach* Valkey. How the Valkey
server itself is bounded is a deployment concern with no `NOCA_` variable: it
configures the server, not its clients, so it belongs in the Compose file (or
your `valkey.conf`) rather than in `.env`.

`docker-compose.yml.sample` runs the server as:

```
valkey-server --loglevel verbose --maxmemory 768mb --maxmemory-policy noeviction
```

with a `mem_limit: 1g` container ceiling. Both halves matter:

- **`--maxmemory` must be set explicitly.** Valkey defaults to `maxmemory 0`
  (unlimited) and knows nothing about the cgroup limit it runs under. Without
  it, memory pressure ends in a kernel OOM kill of the container rather than in
  any policy the server applies.
- **`--maxmemory-policy noeviction` is deliberate.** NOCA's Valkey is a
  **coordination store, not a blob cache**: queue entries, `lock_service` locks,
  worker presence, scoreboard cache, and animator reveal session state. An LRU
  policy would evict any of them silently, with no error anywhere — and the
  reveal ceremony state has no backstop outside Valkey (PostgreSQL holds
  nothing), so losing it forces an operator to restart a live ceremony. Refusing
  writes with a loud OOM error is the correct trade for this keyspace.
- **Keep `--maxmemory` comfortably below `mem_limit`** — roughly 75%. Allocator
  fragmentation, client output buffers, and the RDB fork's copy-on-write all
  count against the container limit but *not* against `maxmemory`. Setting the
  two equal reintroduces the OOM kill the setting exists to prevent.

That coordination-store rule is also a design constraint: **do not put bulk
payloads in Valkey** (rendered documents, statement assets, test data,
submission source). Those belong in PostgreSQL or on the shared filesystem.

Sizing: measure rather than guess, with `valkey-cli INFO memory` (`used_memory`,
`used_memory_rss`, `mem_fragmentation_ratio`) during a contest. The RDB file size
is a floor, not an estimate — compact on-disk encodings and the absence of
per-key in-memory overhead typically make resident memory 1.5–3x the dump. For
reference, a reveal ceremony's persisted state is 25–70 KiB for a realistic
freeze and reaches 1 MiB only near 10 000 post-freeze submissions in one scope,
so ceremonies are not a sizing concern.

Persistence is left at the Valkey defaults, and the sample mounts `valkey-data`,
so the built-in RDB save points apply and a restart recovers a **stale**
snapshot. For coordination state that is usually harmless (the reconcilers
correct the queues), but note that a partially rewound reveal ceremony can be
more confusing than an absent one; recover it with `start-reveal` and
`restart=true` rather than trusting a restored snapshot.

### Health rate limiting

The Web, Arena, and Animator `/health` endpoints are public so local containers
and load balancers can probe them. These settings bound public probe traffic
before the endpoint checks PostgreSQL and Valkey. Trusted CIDRs bypass the limit for local
health checks. The limiter keys requests by the ASGI client IP after Uvicorn's
trusted proxy processing, so configure `NOCA_FORWARDED_ALLOW_IPS` when a
reverse proxy must pass through the original client IP.

The limiter itself is the shared per-IP primitive in
`shared/services/request_rate_limit.py` (see `docs/SHARED_SERVICES.md`). These
four variables govern only the `health` bucket; every route bucket that adopts
the primitive later defines its own module-prefixed
`NOCA_<MODULE>_<BUCKET>_RATE_LIMIT_MAX_REQUESTS` / `_WINDOW_SECONDS` pair.

| Variable | Default | Description |
|----------|---------|-------------|
| `NOCA_HEALTH_RATE_LIMIT_ENABLED` | `true` | Enable public `/health` endpoint rate limiting in Web, Arena, Animator, and Health Monitor. |
| `NOCA_HEALTH_RATE_LIMIT_WINDOW_SECONDS` | `60` | Fixed-window length in seconds for `/health` rate limiting. |
| `NOCA_HEALTH_RATE_LIMIT_MAX_REQUESTS` | `30` | Maximum public `/health` requests per client IP in each window. |
| `NOCA_HEALTH_RATE_LIMIT_TRUSTED_CIDRS` | `127.0.0.0/8,::1/128` | Comma-separated CIDRs that bypass `/health` rate limiting. Keep local probe networks here. |

### Trusted CIDRs — how every `*_TRUSTED_CIDRS` variable behaves

Each rate-limit bucket family has its own trusted list, and the rules below
apply to all of them: `NOCA_HEALTH_RATE_LIMIT_TRUSTED_CIDRS`,
`NOCA_HEALTHMON_RATE_LIMIT_TRUSTED_CIDRS`, `NOCA_ANIMATOR_PUBLIC_RATE_LIMIT_TRUSTED_CIDRS`,
`NOCA_ANIMATOR_SSE_TRUSTED_CIDRS`, `NOCA_WEB_PUBLIC_RATE_LIMIT_TRUSTED_CIDRS`,
`NOCA_WEB_SSE_TRUSTED_CIDRS`, and `NOCA_ARENA_SSE_TRUSTED_CIDRS`. There is no
global list: a client is exempt from a bucket only if its address is in *that*
bucket's variable.

- **The value replaces the default; it does not append to it.** Setting a
  variable to `200.241.240.1/32` alone drops the loopback entries, and local
  `/health` probes and same-host tooling become rate-limited. To add one address
  and keep the default, write the whole list:

  ```
  NOCA_HEALTH_RATE_LIMIT_TRUSTED_CIDRS=127.0.0.0/8,::1/128,200.241.240.1/32
  ```

- **Format.** Comma-separated IPv4 or IPv6 networks; a bare address is accepted
  and treated as a single-host network (`/32` or `/128`), but write the prefix
  so the intent is explicit. Each value is validated at startup, and an invalid
  entry or an empty list refuses to start the module.

- **What address is matched.** The limiters compare the trusted list against
  `request.client.host` — the ASGI client address after Uvicorn's trusted-proxy
  processing — and never read `X-Forwarded-For` themselves. Behind Caddy (or any
  reverse proxy) this is the original client only when the proxy's address is in
  `NOCA_FORWARDED_ALLOW_IPS` (see *Reverse proxy*). If it is not, every request
  arrives as the proxy's own address: a trusted entry for the real client never
  matches, and all clients share one bucket. So the list names the *client*
  range as the proxy reports it, not the proxy's address.

- **Buckets with no bypass.** `arena:signup` and `arena:signup-requests`
  deliberately have no trusted list
  (it fronts paid reputation lookups and outbound email), and `arena:ai-review`
  is keyed per user id rather than per IP, so a CIDR cannot exempt it. The
  animator's `NOCA_ANIMATOR_MAX_SSE_CLIENTS` process ceiling is a capacity limit
  and ignores trusted lists as well.

- **Scope narrowly.** A trusted entry is an unlimited pass for that address on
  every route in the bucket. Exempt only the buckets a client actually needs — a
  venue projector network belongs in the animator lists, a status scraper in
  the health-monitor list — rather than adding it to all seven.

### Security headers and auth throttling

Web and Arena share browser security headers and Valkey-backed authentication
throttling. Auth throttling keys attempts by module, action, ASGI client IP,
and a hashed normalized account identifier. It does not trust raw
`X-Forwarded-For`; configure `NOCA_FORWARDED_ALLOW_IPS` so Uvicorn sets the
ASGI client correctly behind a trusted reverse proxy.

| Variable | Default | Description |
|----------|---------|-------------|
| `NOCA_SECURITY_HEADERS_ENABLED` | `true` | Enable security headers on Web, Arena, animator, and health monitor responses. Deliberately unprefixed so one setting governs every HTTP module at once. |
| `NOCA_CSP_REPORT_ONLY` | `true` | Send `Content-Security-Policy-Report-Only` instead of enforcing CSP. Applies to all four HTTP modules. Set to `false` only after validating current assets. |
| `NOCA_AUTH_RATE_LIMIT_ENABLED` | `true` | Enable auth throttling on Web login and password reconfirmation (profile password, contest start/end-now, contest remove/export) and Arena login, 2FA, password reset, signup, the 2FA setup confirmation, the password re-verification behind change-password and 2FA-disable, and the IP quotas on the email-resend and pending-session (date of birth, terms acceptance) actions. Also the master switch for the Arena per-IP signup window (`NOCA_ARENA_SIGNUP_RATE_LIMIT_*`). |
| `NOCA_AUTH_RATE_LIMIT_WINDOW_SECONDS` | `900` | Failure-count window in seconds. |
| `NOCA_AUTH_RATE_LIMIT_IP_MAX_FAILURES` | `20` | Maximum failures per ASGI client IP in the window. At the Arena login 2FA step this ceiling is additionally gated on `NOCA_AUTH_RATE_LIMIT_2FA_IP_DISTINCT_ACCOUNTS`. |
| `NOCA_AUTH_RATE_LIMIT_ACCOUNT_MAX_FAILURES` | `5` | Maximum failures per hashed account identifier in the window. |
| `NOCA_AUTH_RATE_LIMIT_LOCKOUT_SECONDS` | `900` | Lockout duration in seconds. Lockout responses include `Retry-After`. |
| `NOCA_AUTH_RATE_LIMIT_2FA_IP_DISTINCT_ACCOUNTS` | `3` | Distinct accounts an address must have failed against, *on top of* `NOCA_AUTH_RATE_LIMIT_IP_MAX_FAILURES`, before the Arena login 2FA step locks that address. Both ceilings must be exceeded. Set to `1` to restore the plain per-IP count. |
| `NOCA_SECURITY_EVENTS_RETENTION_DAYS` | `180` | Shared retention policy: days to retain `security_events` rows before the retention reaper deletes them. Read by both Web and Arena. `0` disables cleanup. |
| `NOCA_WEB_SECURITY_EVENTS_REAPER_INTERVAL_SECONDS` | `86400` | Polling interval for the Web security-events retention reaper (1 hour to 7 days). Web reaps `module=web` rows. |
| `NOCA_ARENA_SECURITY_EVENTS_REAPER_INTERVAL_SECONDS` | `86400` | Polling interval for the Arena security-events retention reaper (1 hour to 7 days). Arena reaps `module in (arena, aiassistant)` rows. |

#### Why the 2FA step counts distinct accounts

The per-IP failure bucket exists to stop one host spraying credentials across
many accounts. At `POST /auth/login` that is the primary defence: an attempt
needs no credential, so the address is the only thing to count. At the login
**2FA** step it is not. Reaching that step requires a valid `pending_2fa_token`,
which means the caller has already passed the password check and holds working
credentials for the account they are attacking -- and that account is bounded at
`NOCA_AUTH_RATE_LIMIT_ACCOUNT_MAX_FAILURES` regardless.

What the raw IP count catches there instead is honest failure. TOTP fails far
more often than a password does (clock drift, a code that rolled over
mid-typing, a mistyped digit), Arena is a classroom product, and a school lab is
one NAT address, so twenty unremarkable fumbles across a room cost the whole
room the 2FA step. Worse, a student can spend the address's whole budget on
their **own** valid account on purpose, with traffic indistinguishable from
struggling with a phone.

So the IP bucket at this step locks only once its failures have also spanned
`NOCA_AUTH_RATE_LIMIT_2FA_IP_DISTINCT_ACCOUNTS` distinct account identifiers --
the signature that actually separates spraying from fumbling. The identifiers
are the same non-reversible HMAC hashes the account bucket keys on, kept in a
bounded per-IP set that expires with the failure window. `action="login"` is
deliberately unchanged. A successful 2FA verification clears the account
counter but preserves the IP counter and distinct-account set. This prevents a
controlled account from erasing failures sprayed across other accounts.

### Environment and logging

| Variable | Default | Description |
|----------|---------|-------------|
| `NOCA_ENVIRONMENT` | `development` | Runtime environment. Set to `production` in production deployments. Affects debug logging, error detail exposure, and other safety defaults. |
| `NOCA_LOG_LEVEL` | *(unset)* | Logging level honored by every module (`web`, `arena`, `autojudge`, `rating`, `aiassistant`, `mailer`, `animator`): `DEBUG`, `INFO`, `WARNING`, `ERROR`, or `CRITICAL`. When unset, the level falls back to `DEBUG` in development and `INFO` in production. SQLAlchemy statement echo is enabled only when the effective level is `DEBUG`. |

### Cookies and secrets

| Variable | Default | Description |
|----------|---------|-------------|
| `NOCA_COOKIE_SECURE` | `false` | Set the `Secure` attribute on session cookies. Web and Arena refuse to start in production unless this is `true`. Use HTTPS directly or a trusted TLS-terminating reverse proxy. |
| `NOCA_CRYPTO_ENV_FILE` | `.env.crypto` | Dotenv file loaded by the Arena app and the AI assistant worker before initializing `SecretsManager` (encrypted OTP and user-owned OpenAI API key fields). |

Encrypted-at-rest fields use `SecretsManager` key versions stored in the crypto
dotenv file. That file is not part of the normal settings namespace and must remain
out of git. Use the standalone maintenance script to create and inspect it:

```bash
uv run python scripts/secrets_config.py generate
uv run python scripts/secrets_config.py list
uv run python scripts/secrets_config.py rotate
uv run python scripts/secrets_config.py set-active -v v1
uv run python scripts/secrets_config.py set-active --latest
uv run python scripts/secrets_config.py analyze-column --table arena_users --column _otp_secret
```

The generated file is written with `600` permissions and contains
`ACTIVE_ENCRYPTION_VERSION`, `ENCRYPTION_KEYS__<version>`,
`ENCRYPTION_SALT__<version>`, and `ENCRYPTION_SALT_HASH__<version>` entries.

#### In-container bootstrap and rotation

`scripts/secrets_config.py` ships inside both the `arena` and `aiassistant`
images, so the crypto key can be bootstrapped or rotated without a separate
tooling image. Run it against a running service container — its `secrets_manager`
dependency comes in through `noca-shared`, and `.env.crypto` is the file mounted
into the project directory (path from `NOCA_CRYPTO_ENV_FILE`):

```bash
# Bootstrap on first deploy (writes .env.crypto with 600 perms)
docker compose exec arena python scripts/secrets_config.py generate

# Inspect / rotate during normal operation
docker compose exec arena python scripts/secrets_config.py list
docker compose exec arena python scripts/secrets_config.py rotate
docker compose exec arena python scripts/secrets_config.py set-active --latest
```

Because `.env.crypto` is read at startup, restart the Arena app and the AI
assistant worker after a `rotate` / `set-active` so both pick up the new active
version. Either image can run these commands (`docker compose exec aiassistant …`
works the same way); the file they all read is the single shared `.env.crypto`.

> **Caveat:** the `analyze-column` subcommand falls back to `web`'s database
> settings when `--database-url` is omitted, and the `web` package is not present
> in the `arena`/`aiassistant` images. Inside these containers, always pass
> `--database-url` explicitly for `analyze-column` (the key-management commands
> above need no database).

---

## Shared application settings

These variables are shared by the application modules identified below.

### Health monitor link

| Variable | Default | Description |
|----------|---------|-------------|
| `NOCA_HEALTHMON_URL` | *(empty)* | Public URL of the health monitor uptime dashboard (for example, `https://status.example.com` or `http://192.168.1.10:8002`). Rendered as the "Status" link in the Web, Arena, and Animator footers; the link is hidden when empty. |

### Reverse proxy

> **Set this when a reverse proxy (Caddy, nginx, Traefik, …) terminates TLS in
> front of Web/Arena.** Uvicorn only honors `X-Forwarded-Proto` / `X-Forwarded-For`
> from a peer whose IP is in `NOCA_FORWARDED_ALLOW_IPS`. The default
> `127.0.0.1,::1` only trusts loopback, so **a proxy that connects over a Docker
> network (any non-loopback IP) is not trusted** and its forwarded headers are
> discarded. When that happens:
>
> - `request.url_for()` builds `http://…` instead of `https://…`. On an HTTPS
>   page, htmx 2.x rejects the mismatched-scheme request client-side
>   (`htmx:invalidPath`), so htmx buttons and auto-refreshing partials silently
>   stop working — while `<script>`/`<link>` subresources still load because HSTS
>   transparently upgrades them.
> - `request.client.host` stays the proxy's IP for **every** request, so auth
>   rate limiting buckets all users together (one lockout affects everyone) and
>   the `security_events` audit log records the proxy IP instead of the real
>   client.
> - `request.client.port` is usually the proxy-to-app connection port, not the
>   user's original source port (behind Uvicorn's proxy-headers middleware it is
>   rewritten to `0`, which is rejected and stored as NULL). If you need
>   source-port retention behind a proxy, configure the proxy to set the trusted
>   header named by `NOCA_SOURCE_PORT_HEADER` — the sample Caddyfile sends
>   `X-Source-Port {remote_port}`, so pair it with
>   `NOCA_SOURCE_PORT_HEADER=X-Source-Port`. Note that the port recorded is the
>   proxy's peer port: behind a CDN (e.g. Cloudflare) that is the CDN→proxy
>   connection port, which correlates with proxy access logs but is not the
>   browser's original source port.
> - The sample Caddyfile overwrites `X-Request-ID` with Caddy's
>   `{http.request.uuid}`, sends it to Web/Arena, returns it in responses, and
>   appends it to Caddy access logs as `request_id`. Web/Arena persist that
>   value in `security_events.request_id` for correlation.
> - The sample Caddyfile refuses every method outside `GET`, `HEAD`, and `POST`
>   at the edge with a `405` carrying `Allow: GET, HEAD, POST`, so `OPTIONS`,
>   `TRACE`, `PUT`, `PATCH`, and `DELETE` are never proxied and no upstream
>   framework default can answer them. Every NOCA route is a `GET` or a `POST`
>   (`HEAD` is served implicitly by each `GET`), so this removes response surface
>   without removing functionality. If a future route needs another verb, widen
>   the `@method_not_allowed` matcher and the `Allow` header together.
> - The sample Caddyfile strips `Server` and `Via` from every response. These
>   must be **deferred** deletes (`header { -Server \n -Via \n defer }`): both are
>   written by the server/proxy layer after the `header` directive would normally
>   run, so a non-deferred delete leaves them on the wire. Behind Cloudflare this
>   matters most for `Via` — Cloudflare replaces `Server` with its own value but
>   forwards `Via: 1.1 Caddy` verbatim, naming the origin proxy to every client.
> - The sample Caddyfile also sets a defense-in-depth security-header baseline
>   with the `?` (set-only-if-absent) operator, covering upstreams that do not run
>   `shared.services.security_headers` themselves (animator and healthmonitor;
>   web and arena do). CSP and `Cross-Origin-Resource-Policy` are deliberately
>   excluded — both need per-application tuning and a blanket edge value would
>   break pages rather than harden them.
> - **Caddy pitfall:** a `?` default must be deferred *and* live in its own
>   `header` block. Caddy applies a block's defaults under a single "none of these
>   fields are present" condition, so grouping several `?` ops means one header
>   already set by the application silently suppresses **all** the other defaults
>   in that block — the headers just never appear, with no error.
> - **Caddy pitfall:** header ops apply as add → set → delete (not in written
>   order), so `header_up -X-Request-ID` alongside `header_up X-Request-ID …`
>   deletes the value *after* setting it and the app receives nothing. A bare
>   `header_up <field> <value>` is a SET that already replaces client-supplied
>   values — never add a `-` strip line for a header you also set.
>
> **Fix:** set `NOCA_FORWARDED_ALLOW_IPS` to the proxy's source network, then
> restart Web/Arena. For a proxy on the same Docker network with no published app
> ports, `*` is safe because Uvicorn is unreachable except through the proxy;
> otherwise pin the subnet (e.g. from
> `docker network inspect <network> -f '{{range .IPAM.Config}}{{.Subnet}}{{end}}'`).
> Note `NOCA_WEB_URL_BASE` / `NOCA_ARENA_URL_BASE` do **not** help here — they
> only affect absolute links in emails and reports, not in-page `request.url_for()`.

| Variable | Default | Description |
|----------|---------|-------------|
| `NOCA_FORWARDED_ALLOW_IPS` | `127.0.0.1,::1` | Comma-separated trusted reverse proxy IPs/CIDRs used to accept `X-Forwarded-*` headers in Uvicorn/FastAPI. Honored by Web, Arena, **Animator** (whose reveal-control audit log records the client IP), **and the health monitor**. Example: `127.0.0.1,10.0.0.0/8`. Use `*` only in trusted private networks where clients cannot reach the app directly (e.g. a proxy on the same Docker network while the app publishes no ports). Loopback-only default silently drops forwarded headers from a containerized proxy — see the warning above. |
| `NOCA_SOURCE_PORT_HEADER` | *(empty)* | Optional trusted reverse-proxy header carrying the original client source port for login history and `security_events`. Leave empty for direct ASGI `request.client.port`. When set, the reverse proxy must remove any inbound client-supplied value and set its own sanitized integer port value. |

### JWT

| Variable | Default | Description |
|----------|---------|-------------|
| `NOCA_JWT_SECRET_KEY` | *(required)* | Secret key used to sign JWT tokens. Use a long, random string (e.g. `import os; os.urandom(32).hex()`). |
| `NOCA_JWT_ALGORITHM` | `HS256` | JWT signing algorithm |
| `NOCA_JWT_EXPIRE_SECONDS` | `3600` | Per-token JWT lifetime in seconds. Active authenticated web and Arena sessions rotate the cookie automatically when the remaining lifetime reaches half of this value. It also derives the client keepalive cadence: `/4` in web, and in Arena when presence is disabled (see below). |
| `NOCA_JWT_REFRESH_MAX_SESSION_SECONDS` | `0` | Optional absolute cap for sliding web and Arena sessions in seconds. Set to `0` to disable the cap and keep active users signed in indefinitely while they remain active. |

Sliding-session notes:

- The web layer refreshes `noca_access_token` only for requests that
  successfully resolve a real authenticated actor.
- Refresh happens at half-life. With
  `NOCA_JWT_EXPIRE_SECONDS=3600`, the cookie rotates when a valid token has
  1800 seconds or less remaining.
- `NOCA_JWT_REFRESH_MAX_SESSION_SECONDS` applies to the whole login session, not
  to one token instance. When the cap is exceeded, the user must log in again.
- The Arena layer refreshes `arena_access_token` for **every** session — not
  only remembered ones — after `get_current_arena_user()` resolves a real
  authenticated user, and honours the same
  `NOCA_JWT_REFRESH_MAX_SESSION_SECONDS` cap. Rotation used to be limited to
  remembered sessions, which meant a plain login died a fixed hour after it
  started no matter how active the user was: a long edit ended at the login page
  with the form contents discarded.
- Arena "remember me" governs cookie **persistence** only. It sets a 30-day
  `max_age` so the session survives a browser restart, where a plain login uses
  a browser-session cookie that ends with the browser. It no longer affects
  whether or for how long a session slides.
- Because rotation happens on a request inside the refresh window, a page that
  sits open without navigating keeps its session alive through the client-side
  heartbeat in `shared/static/js/noca-presence.js`, which **both** modules load
  from `_base.html`.
- In Arena that heartbeat runs for every logged-in user regardless of
  `NOCA_ARENA_PRESENCE_ENABLED`; the flag governs only the green-dot refresh.
  Its cadence is `NOCA_ARENA_PRESENCE_HEARTBEAT_SECONDS` when presence is
  enabled, and `NOCA_JWT_EXPIRE_SECONDS / 4` (minimum 60 s) otherwise.
- In web it pings `POST /session/heartbeat` every
  `NOCA_JWT_EXPIRE_SECONDS / 4` (minimum 60 s), for any request carrying a live
  session. Web has no presence feature, so the keepalive is all it does. The
  cadence is derived rather than configurable on purpose: a value set past the
  half-life window would rotate nothing and would silently restore the expiry it
  exists to prevent.
- A web session therefore ends on: an explicit logout, a browser restart, a
  password change, the configured absolute cap, or roughly 30–60 minutes with no
  web page open and pinging — the same range, for the same reason, as Arena's
  below.
- An Arena session therefore ends on: an explicit logout, a browser restart
  without "remember me", a password change or session-version bump (which
  invalidates every outstanding token), an access-gate change (deactivated,
  email unconfirmed, forced password change), the configured absolute cap, or
  roughly 30–60 minutes with no Arena page open and sending heartbeats. The
  window is a range because rotation only fires inside the refresh window: at
  best a full token lifetime remains, at worst half of one.
- The Arena footer's "Session expires in" indicator reports the absolute-cap
  deadline, and is hidden when no cap is configured, since a sliding session
  with no cap has no expiry to announce.
- **Both modules refuse to start** when the effective heartbeat cadence is not
  strictly inside the refresh window, because such a configuration pings without
  ever rotating anything and silently restores the mid-edit expiry the heartbeat
  exists to prevent. The error names both settings, the window, and the remedy.
  Two combinations trip it, and each setting is individually within its own
  documented range:
  - Arena with presence enabled, where the cadence is
    `NOCA_ARENA_PRESENCE_HEARTBEAT_SECONDS` (5-300 s) and is otherwise validated
    only against `NOCA_ARENA_PRESENCE_TTL_SECONDS`: a 120 s cadence against
    `NOCA_JWT_EXPIRE_SECONDS=200` pings every 120 s into a 100 s window.
  - Either module on the derived cadence, whose 60 s floor bounds request volume
    and therefore stops tracking the lifetime below about 120 s. Here the only
    remedy is raising `NOCA_JWT_EXPIRE_SECONDS`; there is no cadence setting to
    lower.

### Email Service

Web and Arena never talk to a mail provider. Every email is rendered in the
process that decided to send it, charged to the acting actor's budget, and
handed as a fully formed job to the **`noca-mailer` worker** over Valkey; the
worker alone sends it (or, with sending disabled, logs it). A Web/Arena
process therefore needs only the sender identity, the job TTL and the budget
below. `NOCA_SEND_EMAIL`, `NOCA_EMAIL_PROVIDER`, `NOCA_SMTP_*` and
`NOCA_EMAIL_MBOX_LOG_DIR` are the mailer's settings — see [Mailer worker](#mailer-worker).

| Variable | Default | Description |
|----------|---------|-------------|
| `NOCA_EMAIL_SENDER` | `no-reply@noca.local` | Default sender email every queued message carries. |
| `NOCA_EMAIL_SENDER_NAME` | *(empty)* | Default sender display name. Falls back to the module brand name (`NOCA_WEB_BRAND_NAME` / `NOCA_ARENA_BRAND_NAME`) when empty. |
| `NOCA_EMAIL_QUEUE_JOB_TTL_SECONDS` | `3600` | Seconds a queued email may wait for the mailer. The job hash expires on its own, and the worker drops anything older than this rather than deliver a stale credential. Minimum 60. Set the same value on the mailer. |
| `NOCA_EMAIL_BUDGET_ENABLED` | `true` | Enforce the per-actor outbound-email budget (`shared/services/email_budget.py`), counted when a message is handed to the service so a hot session cannot flood the queue; the mailer's own pace (`NOCA_MAILER_MAX_PER_MINUTE`) bounds the provider. Fails open when Valkey is unavailable. |
| `NOCA_EMAIL_BUDGET_WINDOW_SECONDS` | `600` | Fixed-window length for the budget. |
| `NOCA_EMAIL_BUDGET_USER_MAX` | `20` | Emails one ordinary user (or, before login, one client IP; for password resets, one recipient address) may trigger per window. `0` disables the tier. |
| `NOCA_EMAIL_BUDGET_ADMIN_MAX` | `200` | Emails one admin actor (contest admin, uberadmin, Arena admin or teacher) may trigger per window -- sized for a batch credentials send. `0` disables the tier. |

A queue that cannot take a message (Valkey unreachable) is reported to the
caller as a delivery failure -- nothing is buffered in the process, so the UI
never claims a message is queued when it is not. **Without a running mailer,
queued mail simply waits and is dropped at the TTL.**

### Geolocation key

| Variable | Default | Description |
|----------|---------|-------------|
| `NOCA_GEOLOCATION_API_KEY` | *(empty)* | API key for the geolocation service (ipgeolocation.io). Required only if geolocation features are enabled. |

### IPQualityScore key

| Variable | Default | Description |
|----------|---------|-------------|
| `NOCA_IPQUALITYSCORE_APIKEY` | *(empty)* | API key for IPQualityScore services (ipqualityscore.com), including email validation and IP reputation lookups. When empty, IPQualityScore-backed services are disabled and lookups return `None`. |

### Password Policy

| Variable | Default | Description |
|----------|---------|-------------|
| `NOCA_WORDLIST_FILENAME` | `wordlist-pt.txt` | Filename of the wordlist used for diceware password generation by web and arena (resolved relative to `shared/`). The file must exist inside the `shared/` directory, which is baked into the container image. |
| `NOCA_PASSWORD_WORD_COUNT` | `4` | Number of words in generated diceware passwords |
| `NOCA_MIN_PASSWORD_LENGTH` | `12` | Minimum character length required for any password (minimum 8) |
| `NOCA_PASSWORD_UPPERCASE_REQUIRED` | `true` | Require at least one uppercase letter in generated passwords |
| `NOCA_PASSWORD_LOWERCASE_REQUIRED` | `true` | Require at least one lowercase letter in generated passwords |
| `NOCA_PASSWORD_NUMBER_REQUIRED` | `true` | Require at least one digit in generated passwords |
| `NOCA_PASSWORD_SYMBOL_REQUIRED` | `true` | Require at least one symbol in generated passwords |

### Images

| Variable | Default | Description |
|----------|---------|-------------|
| `NOCA_IMAGE_AVATAR_SIZE` | `64` | Maximum size in pixels for generated avatars from uploaded images (up to 256) |
| `NOCA_IMAGE_MAX_FILE_SIZE` | `2097152` | Maximum allowed image upload size in bytes. The value must not exceed the shared 5 MiB hard limit (`5242880`). Web and Arena photo streams stop as soon as selected image bytes exceed this value. API clients receive HTTP 413; browser forms return to the same page with a warning. Arena logos and problem images use fixed 2 MiB limits. |
| `NOCA_IMAGE_MAX_WIDTH` | `2048` | Maximum allowed upload width in pixels (up to 4096). Problem images instead use a fixed 2048-pixel limit so packages remain portable between deployments. |
| `NOCA_IMAGE_MAX_HEIGHT` | `2048` | Maximum allowed upload height in pixels (up to 4096). Problem images instead use a fixed 2048-pixel limit so packages remain portable between deployments. |
| `NOCA_IMAGE_FONT_DIR` | *(empty)* | Optional directory containing fonts used by generated placeholder images |
| `NOCA_IMAGE_RESPONSE_CACHE_MAX_AGE` | `3600` | `Cache-Control: max-age` value for image responses in seconds |
| `NOCA_AUDIO_MAX_FILE_SIZE` | `2097152` | Maximum allowed Web audio upload size in bytes. The value must not exceed the 5 MiB hard limit (`5242880`). The Web audio stream stops as soon as uploaded file bytes exceed this value. API clients receive HTTP 413; browser forms return to the same page with a warning. |

---

## Web module

| Variable | Default | Description |
|----------|---------|-------------|
| `NOCA_WEB_HOST` | `0.0.0.0` | Bind address for the web HTTP server. Container deployments must leave this at `0.0.0.0`: Caddy reaches the service over the container network and the container healthcheck probes loopback. A narrower bind is only for direct `uv run noca-web` execution on a host. |
| `NOCA_WEB_PORT` | `8000` | TCP port for the web HTTP server (1–65535). In the compose stack this same variable drives the Caddy upstream (`containers/Caddyfile`) and the container healthcheck, so overriding it stays consistent end to end. The `EXPOSE` line in `containers/webapp/Dockerfile` is documentary and does not follow it. |
| `NOCA_WEB_APP_NAME` | `noca` | Web application name used as the JWT issuer claim. Must differ from `NOCA_ARENA_APP_NAME` so tokens issued by each server are not mutually valid. UI naming uses `NOCA_WEB_BRAND_NAME` instead. |
| `NOCA_WEB_BRAND_NAME` | `NOCA Contest` | Public brand name shown in the UI (page titles, footer, nav), and in credential email subjects/bodies. Injected into templates as the `brand_name` global. |
| `NOCA_WEB_URL_BASE` | *(empty)* | Public base URL used to build absolute links in credential emails and downloadable reports (e.g. `https://contest.example.com` or `http://192.168.1.10:8000`). Must include scheme and host; trailing slash is stripped. When not set, links are derived from the incoming HTTP request — this may produce incorrect URLs behind a reverse proxy that does not forward `X-Forwarded-*` headers. |

### Problem storage

| Variable | Default | Description |
|----------|---------|-------------|
| `NOCA_WEB_PROBLEM_STATEMENT_DIR` | *(required)* | Directory where problem statement PDFs are stored. Must be readable and writable by the web process. |
| `NOCA_PROBLEM_TESTCASE_DIR` | *(required)* | Root directory shared by Web, Arena, and Autojudge for problem test case files. Domains are namespaced into subdirectories: Web problems under `<root>/contest/<problem_id>/NNN.in\|out`, Arena problems under `<root>/arena/<problem_id>/NNN.in\|out`. The subdirectories are created on demand by the writers. Must be readable and writable by the web and arena processes, and readable by the autojudge worker. Replaces the former `NOCA_WEB_PROBLEM_TESTCASE_DIR` / `NOCA_JUDGE_PROBLEM_TESTCASE_DIR` pair (a one-time manual relocation of existing Web files into `<root>/contest/` is required at rollout). The directory must support **atomic same-directory renames** (how an editor save promotes its staged files and restores the originals on failure). Hardlinks are used to seed staging when the filesystem supports them; where it does not, the code copies instead and logs one WARNING naming the directory -- slower on large problems, identical otherwise. See [BOOTSTRAP.md](BOOTSTRAP.md). |
| `NOCA_WEB_PUBLIC_PROBLEM_PACK_PATH` | *(required in production)* | Cache directory backing **two** problem-package caches: the post-contest problem-set archives served by the anonymous `GET /problem-set/{slug}.zip`, and the per-problem contestant packages served by `GET /c/{slug}/problems/{label}/export` (in a `problem-export/` subdirectory). When set, it must be an absolute path; both directories are created at startup if missing, and an existing one must be readable and writable by the web process. Each archive is built once into a temporary sibling file, published atomically by rename, and reused while its `.sha256` sidecar matches the file on disk; concurrent first-hit requests are serialized by a per-key lock, so one problem or contest costs at most one build at a time per process. **Web refuses to start in production when this is unset**, because the per-problem export is contestant-facing during a live contest and would otherwise answer `503` mid-contest; outside production, unset means every download rebuilds from scratch. The two caches invalidate differently: the problem-set archive has no automatic invalidation (its release gate is re-checked per request, and revoking a release discards the file), while the per-problem export is keyed on `problems.public_export_generation` and rebuilds itself whenever a problem changes. |

### Clarification Reaper

| Variable | Default | Description |
|----------|---------|-------------|
| `NOCA_WEB_ENABLE_CLARIFICATION_REAPER` | `false` | Enable the in-process clarification reaper. Active clarification expiration is handled by Valkey TTL locks; the reaper auto-answers leftover open clarifications after the contest ends. |
| `NOCA_WEB_CLARIFICATION_REAPER_INTERVAL_SECONDS` | `1800` | How often the clarification reaper runs (180–1800 s) |

### Task Reaper

| Variable | Default | Description |
|----------|---------|-------------|
| `NOCA_WEB_ENABLE_TASK_REAPER` | `false` | Enable the in-process task reaper. Active task expiration is handled by Valkey TTL locks; the reaper still auto-concludes leftover tasks after the contest ends. |
| `NOCA_WEB_TASK_REAPER_INTERVAL_SECONDS` | `1800` | How often the task reaper runs (180–1800 s) |

### Submission Form

| Variable | Default | Description |
|----------|---------|-------------|
| `NOCA_WEB_SHOW_COMPILE_RUN_CMDS` | `false` | When `true`, the submission form shows the compile and run commands for the selected language (loaded via HTMX on language selection). Useful for contestants who want visibility into how their code will be compiled and executed. |

### Submission rate limiting

| Variable | Default | Description |
|----------|---------|-------------|
| `NOCA_WEB_SUBMISSION_RATE_LIMIT_WINDOW_SECONDS` | `60` | Rolling window length in seconds. It bounds two independent budgets: per-team contest submissions, and per-actor non-scoring solution-test runs (JUDGE/ADMIN/UBERADMIN, `/c/{slug}/solution-tests/submit`). |
| `NOCA_WEB_SUBMISSION_RATE_LIMIT_MAX_SUBMISSIONS` | `3` | Maximum runs allowed within the window, applied separately to each budget. Solution-test runs are counted independently per actor, so a judge's tests never consume a team's submission allowance and vice versa. |

### Team write rate limiting

Caps on the three team-facing creation routes -- `POST /c/{slug}/tasks/sos`,
`POST /c/{slug}/tasks/print`, and `POST /c/{slug}/clarifications/new` -- so one team
cannot drown the staff and judge queues during a live contest. Like the submission
limiter, these count rows in PostgreSQL under a per-team advisory lock, so they are exact
across replicas and need no Valkey.

Each family has two rules. The **window** rules are ordinary rate limits and a refusal
names the time the team may try again. The **open-count** rules (`MAX_OPEN_SOS`,
`MAX_UNANSWERED`) are released by staff action rather than by time: a team with three
unanswered SOS calls gains nothing from a fourth, and its refusal says so instead of naming
a clock time. Hidden clarifications do not count toward `MAX_UNANSWERED`, since a hidden
question is never answered and would otherwise block the team permanently.

Setting any maximum to `0` disables that one rule (the window lengths must stay positive).
Judges, staff, and admins are unaffected: they cannot use these routes at all, and
`POST /c/{slug}/clarifications/announcement` is deliberately not throttled.

| Variable | Default | Description |
|----------|---------|-------------|
| `NOCA_WEB_TEAM_TASK_RATE_LIMIT_WINDOW_SECONDS` | `600` | Rolling window for both team task budgets below. |
| `NOCA_WEB_TEAM_TASK_RATE_LIMIT_MAX_SOS_REQUESTS` | `5` | SOS tasks a team may create within the window; `0` disables this limit. |
| `NOCA_WEB_TEAM_TASK_RATE_LIMIT_MAX_OPEN_SOS` | `3` | Unfinished SOS tasks a team may hold at once; released when staff finishes one. `0` disables this limit. |
| `NOCA_WEB_TEAM_TASK_RATE_LIMIT_MAX_PRINT_REQUESTS` | `10` | Print tasks a team may create within the window; `0` disables this limit. Applied after the upload validations and the duplicate check, so those keep their own messages. |
| `NOCA_WEB_CLARIFICATION_RATE_LIMIT_WINDOW_SECONDS` | `600` | Rolling window for team clarification requests. |
| `NOCA_WEB_CLARIFICATION_RATE_LIMIT_MAX_REQUESTS` | `5` | Clarifications a team may ask within the window; `0` disables this limit. |
| `NOCA_WEB_CLARIFICATION_RATE_LIMIT_MAX_UNANSWERED` | `3` | Unanswered clarifications a team may hold at once; released when a judge answers one. `0` disables this limit. |

### Mass rejudge cooldown

`POST /c/{slug}/admin/problems/{id}/limit-change-batches/{batch}/rejudge-all` queues one
priority autojudge job per pending submission of the batch. The batch rows are consumed
once, so a repeat is already harmless to the queue; the cooldown refuses it before the
batch is even read, with a flash naming the wait. It is keyed per problem, not per admin,
and held in Valkey with a process-local fallback. The per-language rejudge is not subject
to it. Queuing nothing (every row already handled) releases the window.

| Variable | Default | Description |
|----------|---------|-------------|
| `NOCA_WEB_REJUDGE_COOLDOWN_SECONDS` | `300` | Seconds after a batch-wide rejudge during which another batch-wide rejudge of the same problem is refused; `0` disables. |

### SSE connection limits

Both Web event streams -- `GET /c/{slug}/live/events` and
`GET /c/{slug}/runs/events` -- share one bucket (`web:sse`) in the shared
`sse_connection_limit` lease: a client IP may hold at most
`NOCA_WEB_SSE_MAX_PER_IP` of them open at once, and an authenticated actor (a
contest `User` or an `UberAdmin`, keyed on its id) at most
`NOCA_WEB_SSE_MAX_PER_USER` across addresses. The next connection is refused
with `429` and `Retry-After: 5`; slots are released when the client
disconnects. The lease is Valkey-backed and **fails open** -- a Valkey outage
never refuses a stream -- and each held slot is renewed while the connection is
open, so the TTL only bounds a leak after a crashed process.

| Variable | Default | Description |
|----------|---------|-------------|
| `NOCA_WEB_SSE_LIMIT_ENABLED` | `true` | Cap concurrent SSE streams per IP and per actor. |
| `NOCA_WEB_SSE_MAX_PER_IP` | `200` | Open streams allowed per client IP across both routes. Behind a shared NAT this is the venue's budget -- every contestant page holds two streams, so the default covers roughly a hundred machines -- and a refused stream fails *silently* (the browser just keeps retrying), so size it for the largest venue or trust the venue's range rather than disabling the cap. |
| `NOCA_WEB_SSE_MAX_PER_USER` | `10` | Open streams allowed per authenticated actor, across IPs and both routes -- two per open tab, so this is a handful of tabs, not one. This is the cap that bounds a single abusive actor; the per-IP one only bounds many accounts behind one address. |
| `NOCA_WEB_SSE_CONNECTION_TTL_SECONDS` | `600` | Lease lifetime of one held slot in Valkey, renewed every third of it while the stream is open. Only a process that dies mid-stream leaks a slot, for at most this long. |
| `NOCA_WEB_SSE_TRUSTED_CIDRS` | `127.0.0.0/8,::1/128` | Comma-separated CIDRs exempt from the caps (loopback, or a trusted venue network). |

### Public read rate limiting

Web's two anonymous read routes each count against their own per-IP fixed
window of the shared `request_rate_limit` primitive, checked **before** the
contest gate query so a `429` never confirms a slug: `GET /problem-set/{slug}.zip`
(bucket `web:problem-set`) and `GET /c/{slug}/live/feed.json` (bucket
`web:live-feed`). The live feed has no timer poll -- the page refetches the
snapshot on every SSE `refresh` ping with a 250 ms debounce -- so its budget is
sized for a few spectator tabs behind one address during a verdict burst; the
SSE connection cap above already bounds how many tabs can trigger refetches.

In **production** the download additionally answers `503` whenever
`NOCA_WEB_PUBLIC_PROBLEM_PACK_PATH` is unset, before any query: rebuilding the
whole problem set per anonymous request is never acceptable there. Development
keeps the per-request rebuild. In production that state is no longer reachable by
configuration: Web **refuses to start** without the variable, since the same
setting also backs the contestant-facing per-problem export, and discovering a
`503` when a team clicks Download mid-contest is worse than failing the deploy
that caused it. The route's `503` remains as the per-request guard.

| Variable | Default | Description |
|----------|---------|-------------|
| `NOCA_WEB_PUBLIC_RATE_LIMIT_ENABLED` | `true` | Enable both public read limits. |
| `NOCA_WEB_PUBLIC_RATE_LIMIT_TRUSTED_CIDRS` | `127.0.0.0/8,::1/128` | Comma-separated CIDRs exempt from both limits. |
| `NOCA_WEB_PUBLIC_RATE_LIMIT_PROBLEM_SET_MAX_REQUESTS` | `10` | Problem-set downloads accepted per client IP in each window; the next one gets `429` with `Retry-After`. A human downloads once. |
| `NOCA_WEB_PUBLIC_RATE_LIMIT_PROBLEM_SET_WINDOW_SECONDS` | `600` | Fixed-window length in seconds for problem-set downloads. |
| `NOCA_WEB_PUBLIC_RATE_LIMIT_LIVE_FEED_MAX_REQUESTS` | `120` | Live-feed snapshot requests accepted per client IP in each window. |
| `NOCA_WEB_PUBLIC_RATE_LIMIT_LIVE_FEED_WINDOW_SECONDS` | `60` | Fixed-window length in seconds for live-feed snapshot requests. |

### UberAdmin Bootstrap

The web container's entrypoint runs `scripts/web/create_uberadmin.py` only when `NOCA_WEB_UBERADMIN_USERNAME` is set. The script is idempotent — if the username already exists it exits cleanly.

| Variable | Default | Description |
|----------|---------|-------------|
| `NOCA_WEB_UBERADMIN_USERNAME` | *(empty)* | Username for the bootstrap UberAdmin account |
| `NOCA_WEB_UBERADMIN_FULLNAME` | *(empty)* | Full name for the bootstrap UberAdmin account |
| `NOCA_WEB_UBERADMIN_EMAIL` | *(empty)* | Email address for the bootstrap UberAdmin account |
| `NOCA_WEB_UBERADMIN_PASSWORD` | *(empty)* | Password for the bootstrap UberAdmin account |

### Worker presence

The web server publishes a worker-presence heartbeat to Valkey so the health
monitor can probe it like the background workers. Web never appears in the
Arena admin dashboard or pause UI.

| Variable | Default | Description |
|----------|---------|-------------|
| `NOCA_WEB_WORKER_ID` | *(empty)* | Stable identity for the presence keys. Defaults to `<fqdn>:<pid>` when empty. |
| `NOCA_WEB_WORKER_PRESENCE_INTERVAL_SECONDS` | `30` | Seconds between worker-presence updates in Valkey (1–300 s). |
| `NOCA_WEB_WORKER_PRESENCE_TTL_SECONDS` | `60` | TTL for the live worker marker (2–3600 s). Must exceed `NOCA_WEB_WORKER_PRESENCE_INTERVAL_SECONDS`. |

---

## Arena module

| Variable | Default | Description |
|----------|---------|-------------|
| `NOCA_ARENA_HOST` | `0.0.0.0` | Bind address for the arena HTTP server. Container deployments must leave this at `0.0.0.0`: Caddy reaches the service over the container network. A narrower bind is only for direct `uv run noca-arena` execution on a host. |
| `NOCA_ARENA_PORT` | `8001` | TCP port for the arena HTTP server (1–65535). In the compose stack this same variable drives the Caddy upstream (`containers/Caddyfile`), so overriding it stays consistent end to end. The `EXPOSE` line in `containers/arena/Dockerfile` is documentary and does not follow it. |
| `NOCA_ARENA_APP_NAME` | `noca-arena` | Arena application name used as the JWT issuer claim and to derive the reverse-geocoder User-Agent. Must differ from `NOCA_WEB_APP_NAME` so tokens issued by each server are not mutually valid. UI naming uses `NOCA_ARENA_BRAND_NAME` instead. |
| `NOCA_ARENA_BRAND_NAME` | `NOCA Arena` | Public brand name shown in the UI (page titles, footer, nav), the 2FA/TOTP issuer, and email subjects/bodies. Injected into templates as the `brand_name` global and into Arena emails by `arena/services/email_rendering.py`. |
| `NOCA_ARENA_URL_BASE` | *(empty)* | Public base URL used to build absolute links in Arena emails (e.g. `https://arena.example.com`). Must include scheme and host; trailing slash is stripped. When not set, links are derived from the incoming HTTP request — this may produce incorrect URLs behind a reverse proxy that does not forward `X-Forwarded-*` headers. |
| `NOCA_ARENA_PASSWORD_MAX_AGE` | `0` | Maximum password age in days before a warning flash is shown at Arena login. `0` disables the check. Does not block login or enforce a password change. |

### Google sign-in

Google is an *alternative* Arena login door, off by default. While
`NOCA_ARENA_GOOGLE_OAUTH_ENABLED` is false, every `/auth/google` route answers `404`
-- except `GET /auth/google/blocked`, deliberately left reachable so a visitor
already refused there for being under 13 can still see why (see
`arena/docs/ROUTES.md`) -- and the login, signup, and profile pages render no
Google affordance, so a deployment without an OAuth client is otherwise
indistinguishable from one built before the feature existed.

The redirect URI is **derived, never configured**: it is `NOCA_ARENA_URL_BASE` (or the
incoming request's base URL when that is empty) plus `/auth/google/callback`. Register
exactly that value as an Authorized redirect URI in the Google Cloud Console. Deriving
it is deliberate — a separately configured URI can drift from the deployment's own base
URL, and behind a TLS-terminating proxy a request-derived URI would yield `http://`,
which is why setting `NOCA_ARENA_URL_BASE` matters in production.

Google sign-in does not weaken any existing gate: an account with 2FA enabled still
completes TOTP, the Terms of Service gate still applies, and a Google-first signup is
routed through the LGPD age gate before the account becomes usable.

The existing `profile` scope also supplies an optional picture URL. Linked users can choose
Arena or Google as their avatar source on the profile page. Arena validates, downloads, and
resizes that picture under `NOCA_IMAGE_MAX_FILE_SIZE`, stores it locally, and refreshes it on
later Google logins while selected; browsers never load the Google URL directly. No extra
Google API, refresh token, environment variable, or OAuth scope is required.

Obtaining the client ID and secret, configuring the consent screen, and registering
the redirect URI in the Google Cloud console are walked through step by step in
[ARENA_GOOGLE_OAUTH.md](ARENA_GOOGLE_OAUTH.md).

| Variable | Default | Description |
|----------|---------|-------------|
| `NOCA_ARENA_GOOGLE_OAUTH_ENABLED` | `false` | Offer Google as an alternative Arena login door. Arena refuses to start when this is true and either credential below is empty, so a misconfiguration fails at startup rather than on the first click. |
| `NOCA_ARENA_GOOGLE_OAUTH_CLIENT_ID` | *(empty)* | OAuth 2.0 client ID from the Google Cloud Console. Required when the feature is enabled. |
| `NOCA_ARENA_GOOGLE_OAUTH_CLIENT_SECRET` | *(empty)* | OAuth 2.0 client secret from the Google Cloud Console. Required when the feature is enabled. |

### Submission rate limiting

| Variable | Default | Description |
|----------|---------|-------------|
| `NOCA_ARENA_RATE_LIMIT_WINDOW_MINUTES` | `5` | Rolling window length in minutes for per-user submission rate limiting. Admins and judges are exempt. |
| `NOCA_ARENA_RATE_LIMIT_MAX_SUBMISSIONS` | `10` | Maximum number of submissions a regular Arena user may submit within the rate-limit window. |

### Per-actor read ceilings

Web and Arena each put a **loose** ceiling on their polled partials and
authenticated read routes -- runs, tasks, clarifications, admin counters, job
status, problem pages and the scoreboard on Web; notifications, presence, the
live feed, submission status, problem, profile and ranking pages on Arena.

It is a ceiling, not an emergency brake. Every route it covers costs a bounded
amount per call and honest clients poll them every 5-60 seconds, so the default
sits roughly an order of magnitude above the fastest legitimate poller: it stops
one actor multiplying that cost during a live contest, and must never refuse a
partial to somebody reading normally. The counter is keyed on the account id
(from the session cookie the auth layer has already validated, so the check does
no I/O of its own), falling back to the client IP when there is no session --
so it follows an account across addresses rather than charging everyone behind a
shared one, and there is no trusted-CIDR bypass, which could only ever lift the
ceiling for anonymous callers.

Over budget the answer is `429` with `Retry-After`.
`shared/static/js/htmx-poll-backoff.js`, loaded by both `_base.html` files,
parks the page's *timer-driven* htmx partials until it passes; a click or a form
submit is never cancelled, because silently dropping something a person asked
for looks like a broken page while a paused background refresh costs one stale
minute.

The two limits are independent, so a deployment can tune or disable either
module's on its own. Which routers carry it is listed in `web/docs/ROUTES.md`
and `arena/docs/ROUTES.md` under *Per-actor/Per-user read ceiling*; the SSE
streams are outside it and are bounded by connection leases instead.

| Variable | Default | Description |
|----------|---------|-------------|
| `NOCA_WEB_USER_READ_RATE_LIMIT_ENABLED` | `true` | Enable the Web per-actor read ceiling (bucket `web:user-read`). |
| `NOCA_WEB_USER_READ_RATE_LIMIT_MAX_REQUESTS` | `300` | Requests one Web actor may make to the guarded routers in each window. |
| `NOCA_WEB_USER_READ_RATE_LIMIT_WINDOW_SECONDS` | `60` | Fixed-window length in seconds for the Web ceiling. |
| `NOCA_ARENA_USER_READ_RATE_LIMIT_ENABLED` | `true` | Enable the Arena per-user read ceiling (bucket `arena:user-read`). |
| `NOCA_ARENA_USER_READ_RATE_LIMIT_MAX_REQUESTS` | `300` | Requests one Arena user may make to the guarded routers in each window. |
| `NOCA_ARENA_USER_READ_RATE_LIMIT_WINDOW_SECONDS` | `60` | Fixed-window length in seconds for the Arena ceiling. |

### Arena per-problem download limiting and cache

`GET /problems/{n}/export` and `GET /problems/{n}/sample-testcases.zip` build an artifact
per request and are reachable by every logged-in user -- the Arena twin of the Web export
that #152 cached. Both carry a tight per-user budget (bucket `arena:problem-export`) stacked
under the loose ceiling above, charged whether or not the artifact is served from cache, and
both are served from an on-disk cache keyed on `arena_problems.public_export_generation`
when `NOCA_ARENA_PUBLIC_PROBLEM_PACK_PATH` is set. **Arena refuses to start in production
without it**, as Web does for its own cache path; the routes keep a `503` as the
per-request guard. It is deliberately Arena's own variable rather than a reuse of
`NOCA_WEB_PUBLIC_PROBLEM_PACK_PATH`: nothing needs to agree on one location, and a
`NOCA_WEB_`-prefixed name read by Arena would mislead forever.

| Variable | Default | Description |
|----------|---------|-------------|
| `NOCA_ARENA_PUBLIC_PROBLEM_PACK_PATH` | *(required in production)* | Absolute cache directory for the per-problem public export and sample-case ZIPs (`problem-export/` subdirectory). Created at startup; an existing path must be a writable directory. Each artifact is built once and rebuilt only when the problem changes. |
| `NOCA_ARENA_PROBLEM_EXPORT_RATE_LIMIT_ENABLED` | `true` | Enable the per-user budget on both download routes. |
| `NOCA_ARENA_PROBLEM_EXPORT_RATE_LIMIT_MAX_REQUESTS` | `10` | Downloads accepted per user in each window; the next gets `429` with `Retry-After`. |
| `NOCA_ARENA_PROBLEM_EXPORT_RATE_LIMIT_WINDOW_SECONDS` | `600` | Fixed-window length in seconds. |

### Per-problem export limiting

`GET /c/{slug}/problems/{label}/export` builds a contestant-facing problem
package, which is far more expensive than the polled partials the ceiling above
is sized for -- 300 package builds a minute per team is not a bound on anything.
So the route carries a second, much tighter per-actor budget of its own (bucket
`web:problem-export`), stacked *under* that ceiling, and refuses with `429` plus
`Retry-After` when it is spent.

It is keyed per actor rather than per client IP because the route is
authenticated and a whole venue legitimately shares one address; counting by
address would refuse a room full of contestants for one team's behaviour. It
applies whether or not the export is served from cache, so the budget cannot be
widened by editing a problem.

| Variable | Default | Description |
|----------|---------|-------------|
| `NOCA_WEB_PROBLEM_EXPORT_RATE_LIMIT_ENABLED` | `true` | Enable the per-actor budget on per-problem package downloads. |
| `NOCA_WEB_PROBLEM_EXPORT_RATE_LIMIT_MAX_REQUESTS` | `10` | Package downloads accepted per actor in each window; the next gets `429` with `Retry-After`. A contestant downloads a given problem once. |
| `NOCA_WEB_PROBLEM_EXPORT_RATE_LIMIT_WINDOW_SECONDS` | `600` | Fixed-window length in seconds for per-actor package downloads. |

### Signup rate limiting

`POST /auth/signup` carries **two** per-client-IP fixed windows, because two
different things are worth bounding and one budget cannot do both. Both use the
shared `request_rate_limit` primitive keyed by the proxy-corrected ASGI client
IP, share `NOCA_ARENA_SIGNUP_RATE_LIMIT_WINDOW_SECONDS`, have no
trusted-network bypass, and are switched off together with
`NOCA_AUTH_RATE_LIMIT_ENABLED`.

The **attempt budget** (bucket `arena:signup`, 5/hour) pays for the expensive
half of a signup: the account-existence lookup, the paid IPQualityScore
lookups, the user insert and the activation email. It is counted once a
submission has passed form validation and is about to reach them, whatever the
outcome from there on, and a successful signup does not reset it -- which is
what keeps a fresh-email loop from firing paid lookups unthrottled. A
submission rejected for a mismatched password confirmation, a blank name, an
unchecked terms box or a malformed date of birth reaches none of that work and
therefore spends none of this budget: Arena is a classroom product, a school lab
is one NAT address, and five fumbled forms must not cost a whole room an hour of
registration.

The **flood guard** (bucket `arena:signup-requests`, 60/hour) runs in a custom
`APIRoute` wrapper before FastAPI parses form fields or multipart files.
It bounds raw requests because parsing a multipart submission with a photo is
not free. It sits far above the attempt budget on purpose: an honest shared
address never approaches it.

Both refusals answer `429` with `Retry-After`, re-render the form, and are
audited as `auth_throttle_lockout` with `reason` `ip_window` (attempt budget) or
`ip_request_window` (flood guard). The hashed-email lockout for
already-registered addresses stays on the `NOCA_AUTH_RATE_LIMIT_*` settings.

| Variable | Default | Description |
|----------|---------|-------------|
| `NOCA_ARENA_SIGNUP_RATE_LIMIT_MAX_REQUESTS` | `5` | Signup attempts accepted per client IP in each window, counted only once a submission passes form validation. |
| `NOCA_ARENA_SIGNUP_REQUEST_RATE_LIMIT_MAX_REQUESTS` | `60` | Total `POST /auth/signup` requests accepted per client IP in each window, counted before validation. |
| `NOCA_ARENA_SIGNUP_RATE_LIMIT_WINDOW_SECONDS` | `3600` | Fixed-window length in seconds, shared by both signup windows. |

### Class registration retry

`POST /classes/{id}/request-registration` emails the class teacher, and a
student whose request was denied could re-request -- and re-email -- without
limit (a second *pending* request was already refused). After a denial the
same student must now wait before requesting the same class again.

| Variable | Default | Description |
|----------|---------|-------------|
| `NOCA_ARENA_CLASS_REGISTRATION_RETRY_SECONDS` | `86400` | Seconds a student must wait after a denied request before requesting the same class again; `0` disables the wait. |

### AI review request rate limiting

Every authenticated `POST /submissions/{id}/request-ai-review` is counted
against a per-**user** fixed window (bucket `arena:ai-review`, keyed on the
user id rather than the client IP) before the submission is even looked up,
whatever the outcome. Over budget the route flashes an error and redirects
back to the submission. A request on an already-pending submission never
re-enqueues the job — the aiassistant reconciler is the only recovery path —
so this cap bounds the remaining per-request cost (a row lock and a query).

| Variable | Default | Description |
|----------|---------|-------------|
| `NOCA_ARENA_AI_REVIEW_RATE_LIMIT_ENABLED` | `true` | Enable the per-user cap. |
| `NOCA_ARENA_AI_REVIEW_RATE_LIMIT_MAX_REQUESTS` | `30` | Requests accepted per user in each window. |
| `NOCA_ARENA_AI_REVIEW_RATE_LIMIT_WINDOW_SECONDS` | `600` | Fixed-window length in seconds. |

### Mass rejudge cooldown

`POST /admin/problems/{id}/rejudge-all` supersedes every settled submission's judgment
and enqueues one job each. Submissions already being judged are skipped, so a repeat
queues nothing new; the cooldown refuses the repeat before the selection even runs. Keyed
per problem, held in Valkey with a process-local fallback; queuing nothing releases it.

| Variable | Default | Description |
|----------|---------|-------------|
| `NOCA_ARENA_REJUDGE_COOLDOWN_SECONDS` | `300` | Seconds after a rejudge-all during which another rejudge-all of the same problem is refused; `0` disables. |
| `NOCA_ARENA_USERNAME_CHANGE_COOLDOWN_DAYS` | `30` | Days a user must wait between username changes; `0` disables. The username is the pseudonym an age-shielded user is published under, so unlimited churn would let an observer correlate old and new handles across the public ranking. |

### Reverse-geocoder proxy limits

`POST /user/profile/location/detect` relays browser coordinates to a
Nominatim-compatible provider whose usage policy is stated **per application**
(an absolute maximum of one request per second), not per user, so a per-user cap
alone cannot keep a deployment inside it. Three things apply, in order: a
per-user fixed window (bucket `arena:geocode:user`, counted on every valid
request including one that will hit the cache); a Valkey cache of each
0.001-degree cell (about 100 m), which is what makes a whole lecture hall cost
one provider call; and a deployment-wide gate that decides per-second pacing and
the windowed budget in one atomic step against *Valkey server time*, consumed
only on a cache miss and before the call, so a failing provider still spends its
slot.

Unlike every other limiter in NOCA, that gate **fails closed**: any Valkey
failure -- an outage, a script error, a read-only replica -- returns `503` and
never calls the provider. The shared limiter's fallback is process-local, so a
Valkey outage across N Arena replicas would otherwise multiply the
deployment-wide budget by N, exactly the upstream ban these limits exist to
prevent. A refusal is `429` with `Retry-After`.

There is deliberately **no switch that turns the gate off**.
`NOCA_ARENA_REVERSE_GEOCODER_ENABLED` already disables the proxy itself, and a
deployment needing more headroom -- one running its own provider rather than the
public instance -- raises the ceilings below, which keeps the structure intact.
Only pacing may be set to `0`, and the windowed budget still applies underneath.

| Variable | Default | Description |
|----------|---------|-------------|
| `NOCA_ARENA_GEOCODE_RATE_LIMIT_USER_MAX_REQUESTS` | `5` | Detections accepted per user in each window, cache hits included. |
| `NOCA_ARENA_GEOCODE_RATE_LIMIT_USER_WINDOW_SECONDS` | `3600` | Fixed-window length in seconds for the per-user cap. |
| `NOCA_ARENA_GEOCODE_RATE_LIMIT_GLOBAL_MAX_REQUESTS` | `30` | Upstream calls the whole deployment may make in each global window. |
| `NOCA_ARENA_GEOCODE_RATE_LIMIT_GLOBAL_WINDOW_SECONDS` | `60` | Fixed-window length in seconds for the deployment-wide budget. |
| `NOCA_ARENA_GEOCODE_RATE_LIMIT_GLOBAL_MIN_INTERVAL_SECONDS` | `1` | Minimum seconds between two upstream calls across the deployment. `0` disables pacing and suits only a self-hosted provider; the windowed budget still applies. |
| `NOCA_ARENA_GEOCODE_CACHE_TTL_SECONDS` | `86400` | How long a reverse-geocoded cell stays cached in Valkey. |

### SSE connection limits

Both Arena event streams -- `GET /live/events` and
`GET /user/submissions/status/events` -- share one bucket (`arena:sse`) in the shared
`sse_connection_limit` lease: a client IP may hold at most
`NOCA_ARENA_SSE_MAX_PER_IP` of them open at once, and the logged-in Arena user (keyed on
`ArenaUser.id`) at most
`NOCA_ARENA_SSE_MAX_PER_USER` across addresses. The next connection is refused
with `429` and `Retry-After: 5`; slots are released when the client
disconnects. The lease is Valkey-backed and **fails open** -- a Valkey outage
never refuses a stream -- and each held slot is renewed while the connection is
open, so the TTL only bounds a leak after a crashed process.

| Variable | Default | Description |
|----------|---------|-------------|
| `NOCA_ARENA_SSE_LIMIT_ENABLED` | `true` | Cap concurrent SSE streams per IP and per user. |
| `NOCA_ARENA_SSE_MAX_PER_IP` | `200` | Open streams allowed per client IP across both routes. Behind a shared NAT this is the venue's budget -- every contestant page holds two streams, so the default covers roughly a hundred machines -- and a refused stream fails *silently* (the browser just keeps retrying), so size it for the largest venue or trust the venue's range rather than disabling the cap. |
| `NOCA_ARENA_SSE_MAX_PER_USER` | `10` | Open streams allowed per logged-in user, across IPs and both routes -- two per open tab. This is the cap that bounds a single abusive actor; the per-IP one only bounds many accounts behind one address. |
| `NOCA_ARENA_SSE_CONNECTION_TTL_SECONDS` | `600` | Lease lifetime of one held slot in Valkey, renewed every third of it while the stream is open. Only a process that dies mid-stream leaks a slot, for at most this long. |
| `NOCA_ARENA_SSE_TRUSTED_CIDRS` | `127.0.0.0/8,::1/128` | Comma-separated CIDRs exempt from the caps (loopback, or a trusted venue network). |

### Live feed

| Variable | Default | Description |
|----------|---------|-------------|
| `NOCA_ARENA_LIVE_FEED_LIMIT` | `20` | Maximum number of finalized submissions returned by `/live/feed.json` and shown on the public Arena live feed page (1–100). |

### Ranking medals

Each cutoff is the last ranking position awarded that medal, applied to the dashboard
leaderboard card, `/ranking/users` and `/ranking/affiliations` (the affiliation-scoped
user list shows plain global ranks). Bands are tried gold → silver → bronze, and `0`
disables a band: `0/2/3` gives no gold and silver to positions 1–2. Ignoring disabled
bands, the values must not decrease, otherwise Arena refuses to start.

| Variable | Default | Description |
|----------|---------|-------------|
| `NOCA_ARENA_RANKING_MEDAL_GOLD_CUTOFF` | `1` | Last ranking position awarded a gold medal (0–1000; `0` disables gold). |
| `NOCA_ARENA_RANKING_MEDAL_SILVER_CUTOFF` | `2` | Last ranking position awarded a silver medal (0–1000; `0` disables silver). |
| `NOCA_ARENA_RANKING_MEDAL_BRONZE_CUTOFF` | `3` | Last ranking position awarded a bronze medal (0–1000; `0` disables bronze). |

### Online presence

| Variable | Default | Description |
|----------|---------|-------------|
| `NOCA_ARENA_PRESENCE_ENABLED` | `true` | Enable the online-presence green dot on Arena user avatars. When disabled, the client script and endpoints become inert. |
| `NOCA_ARENA_PRESENCE_TTL_SECONDS` | `60` | Seconds a user stays "online" after their last heartbeat or page view (10–600). Must be greater than the heartbeat interval. |
| `NOCA_ARENA_PRESENCE_HEARTBEAT_SECONDS` | `30` | Client heartbeat / dot-refresh interval in seconds (5–300). Must be smaller than the TTL. |

### Reverse geocoder

| Variable | Default | Description |
|----------|---------|-------------|
| `NOCA_ARENA_REVERSE_GEOCODER_ENABLED` | `true` | Enables Arena profile browser-coordinate reverse geocoding. When disabled, `/user/profile/location/detect` returns 503. |
| `NOCA_ARENA_REVERSE_GEOCODER_URL` | `https://nominatim.openstreetmap.org/reverse` | Nominatim-compatible reverse-geocoder endpoint used for optional Arena profile location detection. |
| `NOCA_ARENA_REVERSE_GEOCODER_USER_AGENT` | *(derived from app name and version)* | Optional User-Agent sent to the reverse geocoder. If unset, Arena derives one from `NOCA_ARENA_APP_NAME` and the package version. |

### Arena Admin Bootstrap

The arena container's entrypoint runs `scripts/arena/create_arena_admin.py` only when `NOCA_ARENA_ADMIN_EMAIL` is set. The script is idempotent — if a user with the given email already exists it exits cleanly. The created account has the `ARENA_ADMIN` role, is active, and has email confirmed.

| Variable | Default | Description |
|----------|---------|-------------|
| `NOCA_ARENA_ADMIN_FULLNAME` | *(empty)* | Full name for the bootstrap Arena admin account. |
| `NOCA_ARENA_ADMIN_EMAIL` | *(empty)* | Email address for the bootstrap Arena admin account. Setting this variable triggers the bootstrap on container startup. |
| `NOCA_ARENA_ADMIN_PASSWORD` | *(empty)* | Password for the bootstrap Arena admin account. |

### Worker presence

The arena server publishes a worker-presence heartbeat to Valkey so the health
monitor can probe it like the background workers. This is process presence,
distinct from the user online-presence settings above. Arena never appears in
its own admin dashboard worker cards or pause UI.

| Variable | Default | Description |
|----------|---------|-------------|
| `NOCA_ARENA_WORKER_ID` | *(empty)* | Stable identity for the presence keys. Defaults to `<fqdn>:<pid>` when empty. |
| `NOCA_ARENA_WORKER_PRESENCE_INTERVAL_SECONDS` | `30` | Seconds between worker-presence updates in Valkey (1–300 s). |
| `NOCA_ARENA_WORKER_PRESENCE_TTL_SECONDS` | `60` | TTL for the live worker marker (2–3600 s). Must exceed `NOCA_ARENA_WORKER_PRESENCE_INTERVAL_SECONDS`. |

---

## Rating worker

These variables are consumed by the standalone **`noca-rating`** worker, which owns
the rating recomputation loops (run exactly one replica). The worker publishes the
formatted active interval to Valkey key `arena:rating:interval_text`, the
affiliation factor to `arena:rating:affiliation_factor`, and the live "next rating
update" countdown to `arena:rating:next_update`; every Arena instance polls those
keys for display.

| Variable | Default | Description |
|----------|---------|-------------|
| `NOCA_RATING_INTERVAL` | `86400` | Seconds between Arena rating recomputation cycles. Valid range: 900 (15 min) – 604800 (1 week). Problem difficulty, user scores, and affiliation ratings are all recomputed each cycle. |
| `NOCA_RATING_COMPUTE_ON_STARTUP` | `false` | When `true`, the problem, user, and affiliation rating cycles, the problem-statistics cycle, and the badge-assignment cycle run immediately at startup instead of waiting for their first interval to elapse. |
| `NOCA_RATING_BADGE_INTERVAL` | `900` | Seconds between Arena gamification badge-assignment cycles. Valid range: 900 (15 min) – 604800 (1 week). Runs on its own timer in the rating worker, independent of `NOCA_RATING_INTERVAL`. Each cycle awards badges from newly Accepted submissions. |
| `NOCA_RATING_BADGE_LOOKBACK_SECONDS` | `600` | Overlap subtracted from the badge incremental watermark so judgments committed slightly late or out of order are re-seen and deduplicated by idempotency. Valid range: 0 – 86400. |
| `NOCA_RATING_BADGE_RECONCILE_INTERVAL` | `86400` | Minimum seconds between full badge reconciliation passes that ignore the watermark and re-evaluate all Accepted history (keeps CLEAN_CODE dynamic and repairs missed late data). Valid range: 900 – 604800. A full reconcile also runs on the first cycle after startup. |
| `NOCA_RATING_STATS_INTERVAL` | `86400` | Seconds between Arena per-problem statistics recomputation cycles. Valid range: 900 (15 min) – 604800 (1 week). Runs on its own timer in the rating worker, independent of `NOCA_RATING_INTERVAL`. Produces the snapshots read by the problem statistics page. |
| `NOCA_RATING_AFFILIATION_FACTOR` | `5.0` | Geometric decay factor `f` used in the affiliation rating formula `S = (1/f) × Σ (1−1/f)^i × s_i`. Larger `f` = slower weight decay = more members contribute meaningfully to the score. Valid range: 2–50. |
| `NOCA_RATING_WORKER_ID` | *(empty)* | Stable identity shown on the Arena admin dashboard. Defaults to `<fqdn>:<pid>` when empty. |
| `NOCA_RATING_PRESENCE_INTERVAL_SECONDS` | `30` | Seconds between worker-presence updates in Valkey (1–300 s). |
| `NOCA_RATING_PRESENCE_TTL_SECONDS` | `60` | TTL for the live worker marker (2–3600 s). Must exceed `NOCA_RATING_PRESENCE_INTERVAL_SECONDS`. |
| `NOCA_RATING_HEARTBEAT_FILE` | `/tmp/rating-heartbeat` | Absolute path to the heartbeat file refreshed by the worker process. The Compose healthcheck reads this same path. |
| `NOCA_RATING_HEARTBEAT_INTERVAL_SECONDS` | `10` | How often the worker refreshes the heartbeat file in seconds (1–300 s). |
| `NOCA_RATING_HEARTBEAT_STALE_SECONDS` | `30` | Maximum allowed age of the heartbeat file before the container is considered unhealthy. Must be greater than `NOCA_RATING_HEARTBEAT_INTERVAL_SECONDS` (2–3600 s). |

---

## AI Assistant worker

These variables are consumed by the standalone **`noca-aiassistant`** worker,
which dequeues Arena AI review jobs from Valkey, calls OpenAI, and stores the AI
feedback in `arena_submission_ai_reviews`. The worker reads the same common
infrastructure variables (database, Valkey, environment, log level, crypto).

When a user has their own API key stored in `ArenaUser.ai_api_key`, the worker
uses the online OpenAI Responses API path and stores the review immediately.
When the platform key (`NOCA_AI_OPENAI_API_KEY`) is used as fallback, the worker
submits the review through the OpenAI Batch API, records an `arena_ai_batch_jobs`
row, and stores the review after the batch poller receives the completed output.
For both paths, token cost is computed when OpenAI returns usage data and stored
in `_ai_review_cost` as integer microdollars.

### OpenAI Integration

| Variable | Default | Description |
|----------|---------|-------------|
| `NOCA_AI_OPENAI_API_KEY` | *(empty)* | Platform fallback OpenAI API key. Used when the Arena user has no personal key. Cost is recorded against the submission only when this key is used. Leave empty to disable AI review for users without a personal key. |
| `NOCA_AI_OPENAI_MODEL` | `gpt-5.4-mini` | OpenAI model identifier passed to the Responses API. Change to use a different model (e.g. `gpt-4o-mini`). |
| `NOCA_AI_OPENAI_MAX_OUTPUT_TOKENS` | `500` | Maximum number of output tokens the AI may generate per review. Controls response length and limits cost. |
| `NOCA_AI_OPENAI_REASONING_EFFORT` | `medium` | Reasoning effort passed to the OpenAI Responses API (both the online and batch review paths). Lower effort favors speed and lower token usage; higher effort yields more complete reasoning and higher-quality reviews. Models reason adaptively, using fewer tokens for simpler tasks. One of: `none`, `low`, `medium`, `high`, `xhigh`. |
| `NOCA_AI_OPENAI_INPUT_TOKEN_PRICE` | `0.75` | Price per 1 million input tokens in USD. Used to compute cost when the platform key is active. Update when the model's pricing changes. |
| `NOCA_AI_OPENAI_OUTPUT_TOKEN_PRICE` | `4.50` | Price per 1 million output tokens in USD. Used to compute cost when the platform key is active. Update when the model's pricing changes. |
| `NOCA_AI_OPENAI_BATCH_INPUT_TOKEN_PRICE` | *(half of `NOCA_AI_OPENAI_INPUT_TOKEN_PRICE`)* | Batch input token price in USD per 1 million tokens. Leave empty to use the default 50% batch discount calculation. |
| `NOCA_AI_OPENAI_BATCH_OUTPUT_TOKEN_PRICE` | *(half of `NOCA_AI_OPENAI_OUTPUT_TOKEN_PRICE`)* | Batch output token price in USD per 1 million tokens. Leave empty to use the default 50% batch discount calculation. |

### Queue Polling

| Variable | Default | Description |
|----------|---------|-------------|
| `NOCA_AI_POLL_INTERVAL_SECONDS` | `5.0` | Seconds to sleep between poll attempts when the AI review queue is empty (0.5–60 s). Lower values reduce review latency at the cost of more Valkey traffic. |
| `NOCA_AI_BATCH_POLL_INTERVAL_SECONDS` | `300.0` | Seconds between scans for pending OpenAI batch jobs (60–3600 s). Batch reviews can take up to 24 h, so the default checks every 5 min. Arena also reads this value to compute the displayed batch-window size in the AI review confirmation modal. |
| `NOCA_AI_BATCH_STALE_HOURS` | `24` | Hours after submission before a non-terminal OpenAI batch job is considered stale (1–168 h). At the top of each batch poll cycle, stale jobs are expired locally: the consumed platform credit is refunded, `submit_to_ai` is cleared so the review can be requested again, the user is notified, and the OpenAI batch is cancelled with its files deleted. |
| `NOCA_AI_WORKER_ID` | *(empty)* | Stable identity shown on the Arena admin dashboard. Defaults to `<fqdn>:<pid>` when empty. |
| `NOCA_AI_PRESENCE_INTERVAL_SECONDS` | `30` | Seconds between worker-presence updates in Valkey (1–300 s). |
| `NOCA_AI_PRESENCE_TTL_SECONDS` | `60` | TTL for the live worker marker (2–3600 s). Must exceed `NOCA_AI_PRESENCE_INTERVAL_SECONDS`. |
| `NOCA_AI_HEARTBEAT_FILE` | `/tmp/aiassistant-heartbeat` | Absolute path to the heartbeat file refreshed by the worker process. The Compose healthcheck reads this same path. |
| `NOCA_AI_HEARTBEAT_INTERVAL_SECONDS` | `10` | How often the worker refreshes the heartbeat file in seconds (1–300 s). |
| `NOCA_AI_HEARTBEAT_STALE_SECONDS` | `30` | Maximum allowed age of the heartbeat file before the container is considered unhealthy. Must be greater than `NOCA_AI_HEARTBEAT_INTERVAL_SECONDS` (2–3600 s). |
| `NOCA_AI_WORKER_COMMAND_POLL_SECONDS` | `3.0` | Seconds between Valkey command-key polls (0.5–60 s). PostgreSQL pause state is also reconciled at startup, for each verified command, and every 60 seconds as a fallback. |
| `NOCA_AI_WORKER_COMMAND_FRESHNESS_SECONDS` | `30.0` | Symmetric freshness window for accepting a signed command (1–300 s). |
| `NOCA_AI_WORKER_COMMAND_NONCE_TTL_SECONDS` | `60` | TTL for the single-use command nonce (2–3600 s). Must exceed `NOCA_AI_WORKER_COMMAND_FRESHNESS_SECONDS`. |

### Reaper and reconciler

The AI review reaper is a background coroutine that detects AI review jobs that were
dispatched but never completed (e.g. due to a transient OpenAI error or worker crash)
and requeues them up to a configurable limit.

| Variable | Default | Description |
|----------|---------|-------------|
| `NOCA_AI_STALE_THRESHOLD_SECONDS` | `300.0` | Age in seconds after which an in-flight AI review job is considered stale and eligible for requeue (minimum 30 s). Should be set above the expected maximum OpenAI API call duration including upload time. |
| `NOCA_AI_REAPER_INTERVAL_SECONDS` | `60.0` | How often the reaper scans the inflight sorted set for stale jobs in seconds (minimum 5 s). |
| `NOCA_AI_MAX_REQUEUE_COUNT` | `3` | Maximum number of times a stale AI review job is requeued before being discarded. Prevents poison-pill jobs from cycling indefinitely (1–20). |
| `NOCA_AI_RECONCILER_INTERVAL_SECONDS` | `120.0` | How often the reconciler sweeps PostgreSQL for AI review jobs lost after commit (jobs flagged `submit_to_ai` with no Valkey queue presence), in seconds (minimum 10 s). |
| `NOCA_AI_RECONCILER_GRACE_SECONDS` | `120.0` | Minimum age in seconds since a submission was flagged before the reconciler will re-enqueue it, so it does not race a fresh request whose Valkey enqueue is still in flight (minimum 10 s). |
| `NOCA_AI_RECONCILER_BATCH_SIZE` | `100` | Maximum number of lost AI review jobs re-enqueued per reconciler sweep (1–1000). |

---

## Mailer worker

The `noca-mailer` worker is the one process in NOCA that talks to a mail
provider: it drains the Valkey mail queue the Web and Arena processes fill
with fully rendered messages. Whether mail is really sent, and through what,
is decided **here and only here** -- the producers carry none of these
settings. Run **one replica**: the pace is per process. With
`NOCA_SEND_EMAIL=false` or the `mock` provider the worker still drains the
queue, into its log, which is what a development install wants.

| Variable | Default | Description |
|----------|---------|-------------|
| `NOCA_SEND_EMAIL` | `false` | Deliver for real. When `false` (or the provider is `mock`) each dequeued message goes to the worker's log instead of a mail server. |
| `NOCA_EMAIL_PROVIDER` | `mock` | Backend the worker delivers through: `mock` or `smtp`. |
| `NOCA_SMTP_SERVER` | *(empty)* | SMTP relay hostname. Required when `NOCA_SEND_EMAIL=true` and the provider is `smtp`. |
| `NOCA_SMTP_PORT` | `587` | SMTP port (1-65535). |
| `NOCA_SMTP_USE_TLS` | `true` | STARTTLS before authentication. |
| `NOCA_SMTP_USERNAME` | *(empty)* | SMTP username. Required when SMTP sending is enabled. |
| `NOCA_SMTP_PASSWORD` | *(empty)* | SMTP password. Required when SMTP sending is enabled. |
| `NOCA_EMAIL_SENDER` / `NOCA_EMAIL_SENDER_NAME` | *(shared)* | Read here only as the fallback From identity for a job that carries none; the producers stamp their own. |
| `NOCA_EMAIL_QUEUE_JOB_TTL_SECONDS` | `3600` | A job older than this when dequeued is dropped unsent. Keep it equal to the Web/Arena value. |
| `NOCA_EMAIL_MBOX_LOG_DIR` | *(empty)* | Absolute directory for an append-only mbox audit log of every delivered email (real SMTP sends only). Files rotate on fixed 15-day windows; the directory is created `0700` and each file `0600` because messages carry secrets. Each copy carries `X-NOCA-SMTP-Relay`, `X-NOCA-Delivery-Date`, `X-NOCA-Recipients`, `X-NOCA-Queued-At`, `X-NOCA-Queue-Seconds` and `X-NOCA-Delivery-Attempt`. |
| `NOCA_MAILER_MAX_PER_MINUTE` | `60` | Deployment-wide delivery pace: the worker rests `60 / this` seconds after every delivery attempt, so no burst of requests can exceed it at the provider. Size it to the provider plan (1–6000). |
| `NOCA_MAILER_POLL_INTERVAL_SECONDS` | `2` | Seconds between queue polls while the pending queue is empty (0.5–60). |
| `NOCA_MAILER_STALE_THRESHOLD_SECONDS` | `300` | Seconds after which an inflight job is considered stale by the reaper. A job the provider refused is left inflight on purpose, so this is also the retry delay. Minimum 30. |
| `NOCA_MAILER_REAPER_INTERVAL_SECONDS` | `60` | Seconds between reaper scans for stale inflight jobs (minimum 5). |
| `NOCA_MAILER_MAX_REQUEUE_COUNT` | `3` | Times a stale job is re-enqueued before being dropped with a warning (1–20). |
| `NOCA_MAILER_BRAND_NAME` | `NOCA` | Fallback From display name when `NOCA_EMAIL_SENDER_NAME` is empty (the queued job already carries the sending module's name; this only covers a job without one). |
| `NOCA_MAILER_WORKER_ID` | *(empty)* | Stable worker identifier; defaults to `<fqdn>:<pid>`. |
| `NOCA_MAILER_PRESENCE_INTERVAL_SECONDS` | `30` | Seconds between Valkey worker-presence heartbeats (1–300). |
| `NOCA_MAILER_PRESENCE_TTL_SECONDS` | `60` | TTL of the presence live marker; must exceed the interval (2–3600). |
| `NOCA_MAILER_HEARTBEAT_FILE` | `/tmp/mailer-heartbeat` | Absolute path the worker touches while healthy; the container healthcheck (`python -m mailer.healthcheck`) reads it. |
| `NOCA_MAILER_HEARTBEAT_INTERVAL_SECONDS` | `10` | How often the heartbeat file is refreshed (1–300). |
| `NOCA_MAILER_HEARTBEAT_STALE_SECONDS` | `30` | Heartbeat age after which the container is unhealthy; must exceed the interval (2–3600). |
| `NOCA_MAILER_WORKER_COMMAND_POLL_SECONDS` | `3` | Seconds between pause/resume command-key polls (0.5–60). |
| `NOCA_MAILER_WORKER_COMMAND_FRESHNESS_SECONDS` | `30` | Freshness window for accepting a signed command (1–300). |
| `NOCA_MAILER_WORKER_COMMAND_NONCE_TTL_SECONDS` | `60` | TTL of the single-use command nonce; must exceed the freshness window (2–3600). |

---

## Landing page

These variables configure the standalone `noca/landingpage` Caddy container.
The module doesn't read the common database, Valkey, authentication, or logging
settings. Its entrypoint requires all four URLs and rejects values that aren't
absolute HTTP(S) URLs or that contain whitespace.

| Variable | Default | Description |
| --- | --- | --- |
| `NOCA_LANDINGPAGE_PORT` | `8080` | Internal Caddy listener port. The sample Compose stack publishes this listener on host port `84`. |
| `NOCA_LANDINGPAGE_WEB_URL` | *(required)* | Public URL for the Contest Web deployment. |
| `NOCA_LANDINGPAGE_ARENA_URL` | *(required)* | Public URL for the Arena deployment. |
| `NOCA_LANDINGPAGE_ANIMATOR_URL` | *(required)* | Public URL for the Animator deployment. |
| `NOCA_LANDINGPAGE_HEALTHMON_URL` | *(required)* | Public URL for the Health Monitor deployment. |
| `NOCA_LANDINGPAGE_VERSION` | *(required)* | Release tag rendered in the landing page footer; set it to the tag you deployed. The page has no application behind it to ask, so the value is configuration: the entrypoint rejects an empty value, whitespace, or more than 32 characters. |

The URL values are browser destinations, not container-network upstreams. Use
the externally reachable HTTPS addresses in production.

---

## Health monitor

These variables are consumed by the standalone **`noca-healthmonitor`** server
(default port 8002), which renders the public uptime dashboard. The module
reads only Valkey (common `NOCA_VALKEY_*` variables plus
`NOCA_ENVIRONMENT`, `NOCA_LOG_LEVEL`, `NOCA_STARTUP_TIMEOUT_SECONDS`, and
`NOCA_FORWARDED_ALLOW_IPS` — same contract as Web, Arena, and Animator); it
has no database or JWT configuration. Its `/health` route reads the shared
`NOCA_HEALTH_RATE_LIMIT_*` settings exactly like the other three HTTP modules
(see "Health rate limiting"). The related `NOCA_HEALTHMON_URL` variable
is consumed by Web, Arena, and Animator (footer "Status" link), not by this
module — see "Shared application settings".

| Variable | Default | Description |
|----------|---------|-------------|
| `NOCA_HEALTHMON_HOST` | `0.0.0.0` | Bind address for the health monitor HTTP server. Container deployments must leave this at `0.0.0.0`: Caddy reaches the service over the container network and the container healthcheck probes loopback. A narrower bind is only for direct `uv run noca-healthmonitor` execution on a host. |
| `NOCA_HEALTHMON_PORT` | `8002` | TCP port for the health monitor HTTP server (1–65535). In the compose stack this same variable drives the Caddy upstream (`containers/Caddyfile`) and the container healthcheck, so overriding it stays consistent end to end. The `EXPOSE` line in `containers/healthmonitor/Dockerfile` is documentary and does not follow it. |
| `NOCA_HEALTHMON_PROBE_INTERVAL` | `300` | Seconds between up/down probes of the monitored services (30 s – 1 h). Each probe increments the current 12-hour heatmap slot's `up`/`total` counters in Valkey. |
| `NOCA_HEALTHMON_REAPER_INTERVAL` | `43200` | Seconds between cleanup passes that delete uptime slots older than the retention window (1 h – 1 week). Must be greater than or equal to `NOCA_HEALTHMON_PROBE_INTERVAL`. |
| `NOCA_HEALTHMON_RETENTION_DAYS` | `30` | Days of per-slot uptime history kept for the heatmap (7–90). Slot keys also carry a TTL one day longer than this window as a safety net. |
| `NOCA_HEALTHMON_BRAND_NAME` | `NOCA` | Exact public brand name shown in the dashboard navigation and page title. |

### Public route rate limiting

Every health monitor route is anonymous, so the three dashboard routes (`/`,
`/refresh`, `/uptime.json`) share one per-IP fixed window under the
`healthmon:public` bucket. One idle dashboard tab issues four requests per
minute (the 30-second HTMX `/refresh` poll and the `/uptime.json` fetch that
follows each swap), so the default budget absorbs roughly thirty tabs behind a
single NAT plus manual refreshes. `/uptime.json` is additionally served from a
per-process cache for one `NOCA_HEALTHMON_PROBE_INTERVAL`, so a flood that stays
under the limit still costs Valkey at most one pipelined read per interval.

| Variable | Default | Description |
|----------|---------|-------------|
| `NOCA_HEALTHMON_RATE_LIMIT_ENABLED` | `true` | Enable per-IP rate limiting of `/`, `/refresh`, and `/uptime.json`. |
| `NOCA_HEALTHMON_RATE_LIMIT_MAX_REQUESTS` | `120` | Maximum dashboard requests per client IP in each fixed window, shared by the three routes. |
| `NOCA_HEALTHMON_RATE_LIMIT_WINDOW_SECONDS` | `60` | Fixed-window length in seconds for dashboard rate limiting. |
| `NOCA_HEALTHMON_RATE_LIMIT_TRUSTED_CIDRS` | `127.0.0.0/8,::1/128` | Comma-separated CIDRs that bypass dashboard rate limiting (for example an internal status scraper). Independent of `NOCA_HEALTH_RATE_LIMIT_TRUSTED_CIDRS`. |

---

## Animator

These variables are consumed by the standalone **`noca-animator`** presentation
server (default port 8003), the public live scoreboard runtime. It serves the
scoreboard page (`/c/{slug}/scoreboard`), its `/meta` and scoped
`/snapshot` feeds, and a live Server-Sent Events stream (`/events`); the page
animates rank/cell changes and falls back to polling when the stream is
unavailable. It also serves the post-freeze reveal ceremony and its
authenticated operator control API. The
module reads PostgreSQL (common `NOCA_DB_*` variables) and Valkey (common
`NOCA_VALKEY_*` variables) directly, plus `NOCA_ENVIRONMENT`, `NOCA_LOG_LEVEL`,
`NOCA_VALKEY_HEALTHCHECK_INTERVAL_SECONDS`, `NOCA_STARTUP_TIMEOUT_SECONDS`,
`NOCA_FORWARDED_ALLOW_IPS` (same contract as Web and Arena — Uvicorn runs with
`proxy_headers=True`, and without it the reveal-control audit log records the
reverse proxy instead of the operator), and the shared
`NOCA_HEALTH_RATE_LIMIT_*` health limiter settings (see "Health rate limiting").
The three anonymous feeds have their own limiter and caches, described under
"Public feed caching and rate limiting" below. It has no JWT configuration.

| Variable | Default | Description |
|----------|---------|-------------|
| `NOCA_ANIMATOR_HOST` | `0.0.0.0` | Bind address for the animator HTTP server. Container deployments must leave this at `0.0.0.0`: Caddy reaches the service over the container network and the container healthcheck probes loopback. A narrower bind is only for direct `uv run noca-animator` execution on a host. |
| `NOCA_ANIMATOR_PORT` | `8003` | TCP port for the animator HTTP server (1–65535). Chosen to not collide with Web (8000), Arena (8001), or the health monitor (8002). In the compose stack this same variable drives the Caddy upstream (`containers/Caddyfile`) and the container healthcheck, so overriding it stays consistent end to end. The `EXPOSE` line in `containers/animator/Dockerfile` is documentary and does not follow it. |
| `NOCA_ANIMATOR_POLL_FALLBACK_SECONDS` | `15` | Fallback interval in seconds for the scoreboard page to re-poll `/snapshot` when the live SSE stream is unavailable — after two consecutive stream errors, a terminally closed source, or when the browser has no `EventSource` (1 s – 1 h). Each interval also replaces a terminally closed source so SSE can recover after an Animator restart. Embedded in the page as `data-poll-fallback` and cancelled once the stream reopens. |
| `NOCA_ANIMATOR_CONTROL_LOCKOUT_ENABLED` | `true` | Lock a client IP out of the operator-token gate (`/c/{slug}/control/*` and the controller-lease routes) after repeated credential failures. IP-only, so it needs no secret; the counters live in Valkey with an in-memory fallback. |
| `NOCA_ANIMATOR_CONTROL_LOCKOUT_FAILURES` | `10` | Credential failures from one address, inside one window, that trigger the lockout. A valid token resets the address's counter; scope and ownership refusals are not counted. |
| `NOCA_ANIMATOR_CONTROL_LOCKOUT_SECONDS` | `300` | Lockout duration in seconds, also the window the failures are counted in. A locked address gets the same generic `403` a bad token gets — no `Retry-After` — so the refusal shapes stay indistinguishable; the attempt is audited as `outcome=throttled`. |
| `NOCA_ANIMATOR_ENABLE_CONTROL` | `false` | Process-wide kill-switch for the authenticated reveal control endpoints (`/c/{slug}/control/*`). While `false` — the default — every control route answers the same bare `404` an unknown slug does, regardless of operator secrets, so a deployment that has not enabled control does not advertise that the routes exist. Set it to `true` only on the instance an operator drives the ceremony from. |
| `NOCA_ANIMATOR_REVEAL_TTL_MARGIN_SECONDS` | `3600` | Seconds added to the contest-end instant when setting a reveal session's Valkey state-key TTL, so a ceremony run after the contest ends keeps its persisted state alive. The TTL is refreshed on every successful reveal mutation — but **not** by a replayed command, which performs no write — and floored at this margin once the contest has ended (60 s – 24 h). It also bounds retry protection: the ceremony's `Idempotency-Key` receipts live inside that state, so they expire with it. Set it comfortably longer than a ceremony plus any pause you would tolerate mid-reveal; when the key expires, an in-flight ceremony is gone and must be restarted. The single-writer lock lease (30 s) is an internal round-trip bound and is deliberately **not** configurable. |
| `NOCA_ANIMATOR_CONTROLLER_LEASE_TTL_SECONDS` | `45` | Lifetime of one controller's ownership lease over a reveal ceremony scope (3 s – 1 h). Exactly one panel — browser or Android remote — may mutate each scope; the second controller receives `409` and read-only mode until it explicitly takes over or the lease expires. A crashed controller's commands stop after at most this TTL, so the value trades crash-detection latency against how long an operator may sit on a throttled background tab (timers clamped to roughly 1/min) before it must re-claim on returning to the foreground. Must be at least 3× `NOCA_ANIMATOR_CONTROLLER_HEARTBEAT_SECONDS`. |
| `NOCA_ANIMATOR_CONTROLLER_HEARTBEAT_SECONDS` | `10` | Recommended seconds between controller-lease heartbeats (1–1200 s). Returned by every lease operation so clients follow the server's cadence; the server itself never depends on the heartbeat arriving — TTL expiry remains authoritative. |
| `NOCA_ANIMATOR_PROJECTOR_PRESENCE_TTL_SECONDS` | `30` | Lifetime of one projector's presence entry per ceremony scope (5 s – 10 min). Every open `/reveal/events` stream registers itself in a per-scope Valkey sorted set, renews at a third of this value, and leaves on disconnect; the controller-lease responses report the unexpired count as `projector_count`, so the operator's panel and Android remote can show how many projectors their commands reach. It is a gauge, never a gate: an unreachable Valkey makes the count `null` ("unknown") and never refuses or ends a stream. A projector whose process died stops counting after at most this TTL, so the value trades accuracy after a crash against renewal traffic. |
| `NOCA_ANIMATOR_BRAND_NAME` | `NOCA Animator` | Brand name shown on the animator pages. Injected into templates as the `brand_name` global and rendered as the page title and scoreboard heading. |
| `NOCA_ANIMATOR_WORKER_ID` | *(empty)* | Stable identity for the presence keys. Defaults to `<fqdn>:<pid>` when empty. |
| `NOCA_ANIMATOR_WORKER_PRESENCE_INTERVAL_SECONDS` | `30` | Seconds between worker-presence updates in Valkey (1–300 s). |
| `NOCA_ANIMATOR_WORKER_PRESENCE_TTL_SECONDS` | `60` | TTL for the live worker marker (2–3600 s). Must exceed `NOCA_ANIMATOR_WORKER_PRESENCE_INTERVAL_SECONDS`. |

### Public feed caching and rate limiting

`GET /c/{slug}/meta`, `GET /c/{slug}/snapshot` and `GET /c/{slug}/reveal/state`
are anonymous, and each one used to reload every team, problem, submission and
judgment of the contest from PostgreSQL and re-score it on every request. They
are now served from one **per-process** cache (`animator/services/feed_cache.py`):
a snapshot is built at most once per TTL per `(contest, scope, phase)` and
dropped early whenever a verdict or submission event for that contest reaches the
animator's event stream; the ceremony dataset behind `/reveal/state` and every
control command is loaded once per ceremony *generation* (a fresh `start-reveal`
or an explicit `restart`) and reused by every later `step`, `back`, state read
and spectator refetch. Multi-replica deployments build once per replica, which
stays bounded and needs no shared state. `/meta` and `/snapshot` answer with a
matching `Cache-Control: public, max-age=<seconds left>`.

| Variable | Default | Description |
|----------|---------|-------------|
| `NOCA_ANIMATOR_SNAPSHOT_CACHE_SECONDS` | `5` | Seconds a built `/snapshot` response is served from the cache while the contest runs (1 s – 1 h). Verdict and submission events invalidate it earlier, so this is the staleness bound only when an event is missed. |
| `NOCA_ANIMATOR_SNAPSHOT_CACHE_ENDED_SECONDS` | `60` | Seconds a `/snapshot` response is cached once the contest has ended (1 s – 24 h). There is deliberately no permanent "final" entry: Web's contest, team and problem edit paths cannot invalidate animator entries, so this TTL is the ceiling. |
| `NOCA_ANIMATOR_META_CACHE_SECONDS` | `30` | Seconds a built `/meta` response is served from the cache (1 s – 1 h). Site and problem edits publish no event, so this TTL is the only bound; the pre-start → started and running → frozen transitions are part of the cache key and are visible on the next request regardless. |
| `NOCA_ANIMATOR_REVEAL_DATASET_CACHE_SECONDS` | `300` | Seconds a ceremony's frozen dataset stays cached per process between reveal commands and spectator state reads (1 s – 24 h). A new `start-reveal` or `restart=true` mints a new generation and always reloads; a verdict or submission event for the contest drops it too. |

The three feeds and the two team-media routes (`/teams/{team_id}/photo` and
`/audio`) share one per-IP fixed window (bucket `animator:public`)
through the shared limiter in `shared/services/request_rate_limit.py`. The
limiter runs **before** the contest gate on purpose: its answer depends on the
client IP alone, never on the slug or scope, so a `429` cannot be used to probe
the non-enumerating `404`, and a flood has to be stopped ahead of the gate's own
query or the limiter would protect nothing. The default budget covers a
scoreboard tab in poll fallback (four `/snapshot` per minute), a projector
refetching `/reveal/state` once per reveal step, the pre-start `/meta`
re-polls, and the one photo (plus optional clip) the modal fetches per team
click, with headroom for a venue NAT sharing one address across a few screens.
`/health` keeps the shared `NOCA_HEALTH_RATE_LIMIT_*` limiter; the authenticated
control and controller-lease routes are not rate-limited.

| Variable | Default | Description |
|----------|---------|-------------|
| `NOCA_ANIMATOR_PUBLIC_RATE_LIMIT_ENABLED` | `true` | Enable per-IP rate limiting of `/meta`, `/snapshot`, `/reveal/state`, and the team photo/audio routes. |
| `NOCA_ANIMATOR_PUBLIC_RATE_LIMIT_MAX_REQUESTS` | `300` | Requests accepted per client IP in each fixed window, shared by the five public routes. |
| `NOCA_ANIMATOR_PUBLIC_RATE_LIMIT_WINDOW_SECONDS` | `60` | Fixed-window length in seconds. `Retry-After` on a `429` reports the seconds left in the current window. |
| `NOCA_ANIMATOR_PUBLIC_RATE_LIMIT_TRUSTED_CIDRS` | `127.0.0.0/8,::1/128` | Comma-separated CIDRs that bypass the public-route limiter (loopback, or the venue's projector network). Independent of `NOCA_HEALTH_RATE_LIMIT_TRUSTED_CIDRS`. |

### SSE connection limits

The two animator streams -- `GET /c/{slug}/events` and
`GET /c/{slug}/reveal/events` -- are bounded twice, in this order, both checked
**before** the contest gate so a refusal cannot probe the non-enumerating
`404`:

1. a **process-wide ceiling**, `NOCA_ANIMATOR_MAX_SSE_CLIENTS`, an in-memory
   gauge shared by both streams that needs no Valkey and answers `503` with
   `Retry-After: 5` when this replica is full;
2. the shared Valkey-backed `sse_connection_limit` lease (bucket
   `animator:sse`), capping open streams per client IP at
   `NOCA_ANIMATOR_SSE_MAX_PER_IP` with `429`. Both streams are anonymous, so there
   is no per-user slot. The lease fails open on a Valkey outage -- which is why
   the ceiling above exists -- and renews held slots while connected.

Slots are released when the client disconnects. A venue where many projectors
share one egress address should raise the per-IP cap or trust that range; the
Android remote opens exactly one `/reveal/events` stream and reconnects with
backoff on either refusal.

| Variable | Default | Description |
|----------|---------|-------------|
| `NOCA_ANIMATOR_SSE_LIMIT_ENABLED` | `true` | Cap concurrent SSE streams per client IP. |
| `NOCA_ANIMATOR_SSE_MAX_PER_IP` | `100` | Open streams allowed per client IP across both routes. Both streams are anonymous, so a venue watching the scoreboard from many machines behind one NAT spends this budget; `NOCA_ANIMATOR_MAX_SSE_CLIENTS` remains the process capacity bound. |
| `NOCA_ANIMATOR_SSE_CONNECTION_TTL_SECONDS` | `600` | Lease lifetime of one held slot in Valkey, renewed every third of it while the stream is open. |
| `NOCA_ANIMATOR_SSE_TRUSTED_CIDRS` | `127.0.0.0/8,::1/128` | Comma-separated CIDRs exempt from the per-IP cap (loopback, the venue's projector network). |
| `NOCA_ANIMATOR_MAX_SSE_CLIENTS` | `2000` | Hard ceiling on open SSE clients in one animator process, across both streams; `503` when reached. Independent of Valkey. |

---

## Autojudge worker

The autojudge worker also reads the common infrastructure variables above (database, Valkey, environment, log level).

### Problem storage

| Variable | Default | Description |
|----------|---------|-------------|
| `NOCA_PROBLEM_TESTCASE_DIR` | *(required)* | Root directory where problem test case files are read by the worker. The same shared volume the web and arena processes write through `NOCA_PROBLEM_TESTCASE_DIR`. The Web job reads `<root>/contest/<problem_id>`; the Arena job reads `<root>/arena/<problem_id>`. The worker only reads, so the rename and hardlink requirements the writers have (see the web/arena table) do not apply to it. |

### Worker

| Variable | Default | Description |
|----------|---------|-------------|
| `NOCA_JUDGE_WORKER_CONCURRENCY` | `4` | Maximum number of simultaneous container executions on this host (1–32). Set to the number of available CPU cores minus one. |
| `NOCA_JUDGE_WORKER_ID` | *(empty)* | Stable identity string for this worker process. Defaults to `<fqdn>:<pid>` at runtime when left empty. Useful when running multiple replicas for log correlation. |
| `NOCA_JUDGE_PRESENCE_INTERVAL_SECONDS` | `30` | Seconds between worker-presence updates in Valkey (1–300 s). |
| `NOCA_JUDGE_PRESENCE_TTL_SECONDS` | `60` | TTL for the live worker marker (2–3600 s). Must exceed `NOCA_JUDGE_PRESENCE_INTERVAL_SECONDS`. |
| `NOCA_JUDGE_WORKER_COMMAND_POLL_SECONDS` | `3.0` | Seconds between Valkey command-key polls (0.5–60 s). PostgreSQL pause state is also reconciled at startup, for each verified command, and every 60 seconds as a fallback. |
| `NOCA_JUDGE_WORKER_COMMAND_FRESHNESS_SECONDS` | `30.0` | Symmetric freshness window for accepting a signed command (1–300 s). |
| `NOCA_JUDGE_WORKER_COMMAND_NONCE_TTL_SECONDS` | `60` | TTL for the single-use command nonce (2–3600 s). Must exceed `NOCA_JUDGE_WORKER_COMMAND_FRESHNESS_SECONDS`. |
| `NOCA_JUDGE_PRE_WARM_CONTAINERS` | `true` | When `true`, each language's container pool is filled on the first submission for that language (lazy warming) and replenished after each acquire. Image presence is still validated eagerly at startup. When `false`, run containers are created only when an acquire actually needs one. |

### Container Pool

| Variable | Default | Description |
|----------|---------|-------------|
| `NOCA_JUDGE_POOL_SIZE_PER_LANGUAGE` | `2` | Number of warm idle containers maintained per language after that language's pool is lazily initialized (1–10). Increase on hosts with many concurrent submissions. |
| `NOCA_JUDGE_CONTAINER_MEM_LIMIT_MB` | `512` | Upper-bound memory limit applied to pool containers (64–8192 MB). This is the Docker outer safety cap; it must be set above any problem-level memory limit that should be enforced authoritatively by isolate. |
| `NOCA_JUDGE_CONTAINER_PID_LIMIT` | `256` | Upper-bound PID limit applied to pool containers (32–1024). This is the Docker outer safety cap; isolate remains the authoritative inner process/thread limiter. |
| `NOCA_JUDGE_POOL_ACQUIRE_TIMEOUT_S` | `30` | Maximum seconds to wait for a warm container from the pool before raising a `PoolExhaustedError` and marking the judgment FAILED (minimum 1 s). |

For the full startup behavior matrix covering image sync, pull policy, and lazy pool warming, see
[CONTAINER_STARTUP_OPTIONS.md](CONTAINER_STARTUP_OPTIONS.md).

### Execution Limits

| Variable | Default | Description |
|----------|---------|-------------|
| `NOCA_JUDGE_ISOLATE_BINARY_PATH` | `/usr/local/bin/isolate` | Absolute path to the isolate binary inside run containers. Pool creation fails fast if this binary or its cgroup support is unavailable. |
| `NOCA_JUDGE_ISOLATE_MAX_BOXES` | `1000` | Number of distinct isolate box-ids the worker allocates from (`0 .. value-1`). Each live run container holds one unique box-id for its lifetime so concurrent containers never collide on the shared host cgroup `box-N` (the cause of intermittent `Cannot remove control group /sys/fs/cgroup/box-0` init/cleanup failures). Must not exceed isolate's configured `num_boxes` (default 1000); assumes a single autojudge worker process per host. |
| `NOCA_JUDGE_ISOLATE_WALL_TIME_MULTIPLIER` | `3` | Multiplier applied to each problem's CPU time limit to compute the authoritative inner isolate `--wall-time` budget (1.0–10.0). |
| `NOCA_JUDGE_OUTER_TIMEOUT_MULTIPLIER` | `2` | Multiplier applied to the computed inner isolate wall-time budget to derive the outer `asyncio.wait_for()` safety timeout (1.0–10.0). |
| `NOCA_JUDGE_COMPILE_TIMEOUT_S` | `180` | Global ceiling for the compile phase in seconds (minimum 5 s). Per-language values configured in the database take precedence when set. |
| `NOCA_JUDGE_OUTPUT_LIMIT_BYTES` | `67108864` (64 MiB) | Global hard ceiling for stdout handling per test case. This is a **hard ceiling**, never a fallback: every problem states an output limit of its own (the column is NOT NULL, defaulting to 65536), so the effective limit is always `min(problem_or_language_output_limit, NOCA_JUDGE_OUTPUT_LIMIT_BYTES)` (minimum 1024). |
| `NOCA_JUDGE_STDOUT_EXCERPT_BYTES` | `8192` | How many bytes of contestant stdout to persist in `submission_test_results.stdout_excerpt` for display in the UI (minimum 256). |
| `NOCA_JUDGE_STDERR_EXCERPT_BYTES` | `4096` | Same as `NOCA_JUDGE_STDOUT_EXCERPT_BYTES` but for stderr (minimum 256). |
| `NOCA_JUDGE_PROFILING_MAX_CPU_TIME_SEC` | `30` | Hard CPU-time ceiling applied to each profiling repetition run before Auto-Limit metrics are collected. |
| `NOCA_JUDGE_PROFILING_MAX_WALL_TIME_SEC` | `90` | Hard wall-time ceiling reserved for profiling runs. Intended as an infrastructure safety cap above any problem-level budget. |
| `NOCA_JUDGE_PROFILING_MAX_MEMORY_MB` | `2048` | Hard memory ceiling used while profiling reference implementations. |
| `NOCA_JUDGE_PROFILING_MAX_PIDS` | `256` | Hard process/thread ceiling used while profiling reference implementations. |
| `NOCA_JUDGE_PROFILING_MAX_OUTPUT_BYTES` | `67108864` (64 MiB) | Hard stdout ceiling used while profiling reference implementations. |

Timeout formulas:

- `cpu_limit_s = time_limit_ms / 1000.0`
- `inner_wall_limit_s = cpu_limit_s * NOCA_JUDGE_ISOLATE_WALL_TIME_MULTIPLIER`
- `outer_timeout_s = inner_wall_limit_s * NOCA_JUDGE_OUTER_TIMEOUT_MULTIPLIER`

### Idempotency Lock

| Variable | Default | Description |
|----------|---------|-------------|
| `NOCA_JUDGE_LOCK_TTL_SECONDS` | `660` | TTL in seconds for the per-judgment Redis idempotency lock (`judge:lock:<id>`). Must be strictly greater than `NOCA_JUDGE_REAPER_STALE_THRESHOLD_MINUTES x 60` so that the lock cannot expire naturally before the reaper makes its atomic stale decision. Default of 660 s = 600 s stale threshold + 60 s margin. |

### Reaper

The reaper is a background coroutine that detects in-flight jobs that were never completed (e.g. due to a worker crash) and requeues them.

| Variable | Default | Description |
|----------|---------|-------------|
| `NOCA_JUDGE_REAPER_INTERVAL_S` | `30` | How often the reaper scans the in-flight sorted set for stale jobs (minimum 5 s). |
| `NOCA_JUDGE_REAPER_STALE_THRESHOLD_MINUTES` | `5` | A dispatched job older than this many minutes is considered stale and requeued. Must be longer than the longest legitimate judge run — `compile_timeout + n_test_cases x time_limit` (minimum 1 min). |
| `NOCA_JUDGE_REAPER_MAX_REQUEUE_COUNT` | `3` | Maximum number of times the reaper will requeue a stale job before giving up and dropping it entirely. Prevents poison-pill jobs from cycling indefinitely (1–20). |
| `NOCA_JUDGE_RECONCILER_INTERVAL_S` | `120` | How often the reconciler re-scans the database for non-terminal jobs (QUEUED/DISPATCHED/JUDGING) missing from the Valkey queue and re-enqueues them. Recovers jobs lost between a web/arena DB commit and the follow-up Valkey enqueue without waiting for a worker restart (minimum 10 s). |

### Heartbeat

These settings control the autojudge worker heartbeat file used by Docker Compose to determine whether the worker is still alive.

| Variable | Default | Description |
|----------|---------|-------------|
| `NOCA_JUDGE_HEARTBEAT_FILE` | `/tmp/autojudge-heartbeat` | Absolute path to the heartbeat file refreshed by the worker process. The Compose healthcheck reads this same path. |
| `NOCA_JUDGE_HEARTBEAT_INTERVAL_S` | `10` | How often the worker refreshes the heartbeat file in seconds (1–300 s). |
| `NOCA_JUDGE_HEARTBEAT_STALE_THRESHOLD_S` | `30` | Maximum allowed age of the heartbeat file before the container is considered unhealthy. Must be greater than `NOCA_JUDGE_HEARTBEAT_INTERVAL_S` (2–3600 s). |

### Metrics

The worker exposes a Prometheus scrape endpoint at `GET http://<host>:<NOCA_JUDGE_METRICS_PORT>/metrics`.
All counters, histograms, and gauges are prefixed with `autojudge_` and cover job processing,
compile/run phases, container pool, queue depths, reaper activity, and worker process state.

| Variable | Default | Description |
|----------|---------|-------------|
| `NOCA_JUDGE_METRICS_ENABLED` | `true` | Expose a Prometheus `/metrics` HTTP endpoint. Set to `false` to disable the exposition server and the background gauge-update loop entirely. |
| `NOCA_JUDGE_METRICS_PORT` | `9101` | TCP port the worker binds for Prometheus scraping (1024–65535). Configure your Prometheus scrape job to target `http://<worker-host>:9101/metrics`. |

### Docker

| Variable | Default | Description |
|----------|---------|-------------|
| `NOCA_JUDGE_DOCKER_BASE_URL` | `unix:///var/run/docker.sock` | Docker daemon socket or TCP address used by the worker to manage compile and run containers. Use `tcp://host:2376` for a remote daemon with TLS. |
| `NOCA_JUDGE_DOCKER_NETWORK` | `none` | Network mode for judge containers. **Must be `none` in production** to prevent contestant code from accessing the network. Can be set to `bridge` in local development for debugging only. |
| `NOCA_JUDGE_DOCKER_APPARMOR_PROFILE` | *(empty)* | Optional AppArmor profile for run containers. Set to `unconfined` on Ubuntu hosts where AppArmor blocks `isolate --run` with errors such as `Cannot privatize mounts: Permission denied`. Empty leaves Docker's default AppArmor handling unchanged. |
| `NOCA_JUDGE_CUSTOM_VALIDATOR_WATCHDOG_SECONDS` | `300` | Emergency wall-clock watchdog for one complete interactive custom-validator attempt. This protects the worker from a stalled protocol; it is not the contestant time limit. |

### Canonical Judge Image Sync

When `NOCA_JUDGE_IMAGE_REGISTRY` is set, worker startup treats that registry prefix as the canonical source of truth for judge language images. For each active language ID, it derives:

- path naming: compile image `<registry>/judge-<language_id>:compile[-<tag>]`, run image `<registry>/judge-<language_id>:run[-<tag>]`
- flat naming: compile image `<registry>-judge-<language_id>:compile[-<tag>]`, run image `<registry>-judge-<language_id>:run[-<tag>]`

The worker then applies `NOCA_JUDGE_IMAGE_PULL_POLICY`, updates the `languages.compile_image` and `languages.run_image` columns in PostgreSQL when needed, and only then runs the normal local image preflight.

| Variable | Default | Description |
|----------|---------|-------------|
| `NOCA_JUDGE_IMAGE_REGISTRY` | *(empty)* | Canonical registry/repository prefix for judge images, for example `ghcr.io/dclobato/noca` or `docker.io/dclobato/noca`. Empty disables startup image sync and preserves the image refs already stored in the database. |
| `NOCA_JUDGE_IMAGE_NAMING` | `path` | Image naming mode for canonical judge refs. `path` yields refs like `ghcr.io/org/repo/judge-python3:run`; `flat` yields refs like `docker.io/org/repo-judge-python3:run`. |
| `NOCA_JUDGE_IMAGE_TAG` | *(empty)* | Optional tag suffix appended after the slot name. Empty yields tags like `:compile` and `:run`; `v5.0.0` yields `:compile-v5.0.0` and `:run-v5.0.0`. |
| `NOCA_JUDGE_IMAGE_PULL_POLICY` | `missing` | Startup behavior when canonical image sync is enabled: `never` rewrites the DB only, `missing` pulls only absent images, and `always` always pulls before continuing. |

Operational notes:

- This feature is intended for container-only deployments where the worker image must discover or pull the matching language images on startup.
- Startup sync uses the same Docker daemon configured by `NOCA_JUDGE_DOCKER_BASE_URL`, so the worker container must still be able to reach the daemon and, when pulling is enabled, that daemon must have registry access.
- If `NOCA_JUDGE_IMAGE_PULL_POLICY=never` and the canonical images are not already present locally, the subsequent preflight still fails fast with the missing image list.

## Browser UI checks (development only)

Credentials and targets for the Playwright checks in `tests/browser/`. These are
read by the tests, not by any application config class, so they never affect a
deployed instance.

The checks exist because a whole class of defect is invisible to the rest of the
suite: a button that renders but has no listener, an editor that paints blank
because it was built inside a hidden tab pane. Those only fail in a browser. The
checks therefore drive a **running development server** and log in as a real
user.

| Variable | Default | Description |
| --- | --- | --- |
| `NOCA_UI_CHECK_USERNAME` | *(unset)* | Login identifier the checks authenticate with. **Every browser check is skipped unless this and the password are set**, so a normal `uv run pytest` is unaffected. |
| `NOCA_UI_CHECK_PASSWORD` | *(unset)* | Password for that account. |
| `NOCA_UI_CHECK_ARENA_URL` | `http://127.0.0.1:8001` | Base URL of the running Arena instance. |
| `NOCA_UI_CHECK_WEB_URL` | `http://127.0.0.1:8000` | Base URL of the running Web instance. |
| `NOCA_UI_CHECK_WEB_SLUG` | *(unset)* | Contest slug for the Web checks, whose editor lives under `/c/{slug}/`. The Web checks are skipped without it. |
| `NOCA_UI_CHECK_WEB_USERNAME` | *(falls back to `NOCA_UI_CHECK_USERNAME`)* | UberAdmin login for the Contest checks. |
| `NOCA_UI_CHECK_WEB_PASSWORD` | *(falls back to `NOCA_UI_CHECK_PASSWORD`)* | Password for that UberAdmin. |

Web and Arena are **separate identity domains**, so one pair rarely covers both:
the Contest checks sign in at `/login`, which is the **UberAdmin** login, while
the Arena checks sign in at `/auth/login`. Set `NOCA_UI_CHECK_WEB_USERNAME` and
`NOCA_UI_CHECK_WEB_PASSWORD` when the two accounts differ; leave them blank when
the same login genuinely exists in both.

An UberAdmin administers any contest, so the Contest checks need no per-contest
membership -- only a contest to open. **They are skipped until
`NOCA_UI_CHECK_WEB_SLUG` names an existing contest**, which on a fresh database
means creating one first.

A rejected login fails with an explicit message rather than a selector timeout
thirty seconds later.

Point these at development only. The account is used interactively, and the
checks navigate the real admin UI.

## Docker Compose Only

The following variables are used by `docker-compose.yml` for container runtime wiring and
host volume path expansion. They are not read by the application config classes in
`web/config.py`, `autojudge/config.py`, or `rating/config.py`.

These belong in the **project-root `.env`**, not in a layer template. Compose resolves
`${...}` from the shell and the project-root `.env` and never from an `env_file:` entry,
so a template is the one place these cannot be read from. See
[ENV_LAYERS.md](ENV_LAYERS.md).

| Variable | Default | Description |
|----------|---------|-------------|
| `NOCA_DATA_ROOT` | `.` | Host path prepended to the bind-mounted data directories: `problem_statements`, `problem_testcases`, `email_log`, and `caddy_logs`. PostgreSQL and Valkey use named volumes and are unaffected. The first three directory names are the `scripts/backup_noca.sh` contract. The script archives them relative to the project directory and does not read this variable, so a non-`.` value backs up empty directories without reporting the mismatch. |
| `DOCKER_GID` | `989` | GID of the host's `docker` group, added to the autojudge container through `group_add` so it can reach the mounted Docker socket. Find it with `getent group docker \| cut -d: -f3` on the judge host. |
| `PUID` | `1000` | UID that the `webapp`, `arena`, `autojudge`, `rating`, `aiassistant`, `mailer`, `healthmonitor`, and `animator` processes run as inside their containers. Use this to match ownership of bind-mounted host directories. |
| `PGID` | `100` | Primary GID that the `webapp`, `arena`, `autojudge`, `rating`, `aiassistant`, `healthmonitor`, and `animator` processes run as inside their containers. Use this to match ownership of bind-mounted host directories. |

### Language Seed

The web and arena container entrypoints run `scripts/bootstrap_languages.py` on startup only when explicitly enabled. The script is idempotent — it inserts missing languages and skips ones that already exist.

| Variable | Default | Description |
|----------|---------|-------------|
| `NOCA_SEED_LANGUAGES` | `false` | Set to `true` to seed the built-in language definitions into the database on startup |
| `NOCA_WAIT_FOR_MIGRATIONS_TIMEOUT` | `300` | Max seconds a schema-consumer container (autojudge/rating/aiassistant/mailer/animator) waits for `web`/`arena` to migrate the schema to head before its entrypoint fails. Web and arena still run migrations directly. |

### Bundled PostgreSQL server

The sample stack's `postgres` service is configured in the postgres image's own variable
names, from `.env.postgres.full`. It is a separate file from `.env.database.full` rather
than derived from it because deriving one from the other requires `${...}` interpolation,
which cannot read an `env_file` -- the derived value would silently be the interpolation
default while the layer's real value went unread. The two files describe the same database
from two ends and must agree. A deployment pointing NOCA at an external PostgreSQL drops
both this file and the bundled service.

| Variable | Default | Description |
|----------|---------|-------------|
| `POSTGRES_USER` | *(required)* | Role the bundled server creates. Must equal `NOCA_DB_USER`. |
| `POSTGRES_PASSWORD` | *(required)* | Password for that role. Must equal `NOCA_DB_PASSWORD`. |
| `POSTGRES_DB` | *(required)* | Database the bundled server creates. Must equal `NOCA_DB_NAME`. |

---

## Build-time Variables

These variables are consumed by `containers/build.sh` during image construction. They are not read at runtime by the application.

| Variable | Default | Description |
|----------|---------|-------------|
| `JUDGE_ISOLATE_TAG` | `v2.7` | Git tag of [ioi/isolate](https://github.com/ioi/isolate/tags) to compile into `noca/isolate-base`. The binary is then copied into every `judge-<language>:run` image from that single base. Passed as a Docker build argument by `build.sh`; override to pin a different release. Isolate 2.7 requires `libseccomp-dev` at build time and `libseccomp2` in each run image. |
| `NOCA_IMAGE_PREFIX` | `noca` | Registry prefix applied to every image tag produced by the build script (e.g. `ghcr.io/myorg/noca`). Overridden by `--repo` on the command line. |
| `NOCA_IMAGE_NAMING` | `path` | Build-script image naming mode. `path` builds refs like `ghcr.io/myorg/noca/webapp`; `flat` builds refs like `docker.io/myuser/noca-webapp`. Overridden by `--naming` on the command line. |
