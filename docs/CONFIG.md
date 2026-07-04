# NOCA Environment Configuration Reference

All configuration is supplied through environment variables.
Copy `.env.example` to `.env` and adjust values before starting the stack.
`.env` is gitignored and must never be committed.

Every variable is prefixed to make its scope explicit:

| Prefix | Scope |
|--------|-------|
| `NOCA_` | Common to every module (database, Valkey, environment, log level, crypto) **and** the settings shared by the web and arena modules (email, JWT, images, password policy) |
| `NOCA_WEB_` | Web application only (`web/config.py`) |
| `NOCA_ARENA_` | Arena application only (`arena/config.py`) |
| `NOCA_JUDGE_` | Autojudge worker only (`autojudge/config.py`) |
| `NOCA_AI_` | AI assistant worker only (`aiassistant/config.py`) |
| `NOCA_RATING_` | Rating worker only (`rating/config.py`) |

The sections below are grouped the same way: **Common**, **Shared between Web and
Arena**, then one section per module.

---

## Common — all modules

These variables are read by every runtime module (`web`, `arena`, `autojudge`,
`aiassistant`, `rating`).

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
| `NOCA_STARTUP_TIMEOUT_SECONDS` | `60` | Maximum seconds each module waits for PostgreSQL and Valkey to become reachable at startup before aborting. Set to `0` to skip the wait and fail immediately. Applied to **web**, **arena**, **autojudge**, and **aiassistant**; the **rating** module only waits for PostgreSQL (0–300 s). |
| `NOCA_WORKER_COMMAND_SECRET` | *(empty)* | Shared `HMAC-SHA256` secret for the authenticated worker pause/resume protocol. Read by **arena** (signs/publishes), **autojudge**, and **aiassistant** (verify/apply). When empty the feature is disabled: the Arena dashboard hides pause/resume buttons, direct POSTs are rejected (`rejected_disabled`), and worker command loops do not start. Keep it secret; theft allows pausing queue consumers. |

### Health rate limiting

The Web and Arena `/health` endpoints are public so local containers and load
balancers can probe them. These settings bound public probe traffic before the
endpoint checks PostgreSQL and Valkey. Trusted CIDRs bypass the limit for local
health checks. The limiter keys requests by the ASGI client IP after Uvicorn's
trusted proxy processing, so configure `NOCA_FORWARDED_ALLOW_IPS` when a
reverse proxy must pass through the original client IP.

| Variable | Default | Description |
|----------|---------|-------------|
| `NOCA_HEALTH_RATE_LIMIT_ENABLED` | `true` | Enable public `/health` endpoint rate limiting in Web and Arena. |
| `NOCA_HEALTH_RATE_LIMIT_WINDOW_SECONDS` | `60` | Fixed-window length in seconds for `/health` rate limiting. |
| `NOCA_HEALTH_RATE_LIMIT_MAX_REQUESTS` | `30` | Maximum public `/health` requests per client IP in each window. |
| `NOCA_HEALTH_RATE_LIMIT_TRUSTED_CIDRS` | `127.0.0.0/8,::1/128` | Comma-separated CIDRs that bypass `/health` rate limiting. Keep local probe networks here. |

### Security headers and auth throttling

Web and Arena share browser security headers and Valkey-backed authentication
throttling. Auth throttling keys attempts by module, action, ASGI client IP,
and a hashed normalized account identifier. It does not trust raw
`X-Forwarded-For`; configure `NOCA_FORWARDED_ALLOW_IPS` so Uvicorn sets the
ASGI client correctly behind a trusted reverse proxy.

| Variable | Default | Description |
|----------|---------|-------------|
| `NOCA_SECURITY_HEADERS_ENABLED` | `true` | Enable security headers on Web and Arena responses. |
| `NOCA_CSP_REPORT_ONLY` | `true` | Send `Content-Security-Policy-Report-Only` instead of enforcing CSP. Set to `false` only after validating current assets. |
| `NOCA_AUTH_RATE_LIMIT_ENABLED` | `true` | Enable auth throttling on Web login and Arena login, 2FA, password reset, and signup. |
| `NOCA_AUTH_RATE_LIMIT_WINDOW_SECONDS` | `900` | Failure-count window in seconds. |
| `NOCA_AUTH_RATE_LIMIT_IP_MAX_FAILURES` | `20` | Maximum failures per ASGI client IP in the window. |
| `NOCA_AUTH_RATE_LIMIT_ACCOUNT_MAX_FAILURES` | `5` | Maximum failures per hashed account identifier in the window. |
| `NOCA_AUTH_RATE_LIMIT_LOCKOUT_SECONDS` | `900` | Lockout duration in seconds. Lockout responses include `Retry-After`. |
| `NOCA_SECURITY_EVENTS_RETENTION_DAYS` | `180` | Shared retention policy: days to retain `security_events` rows before the retention reaper deletes them. Read by both Web and Arena. `0` disables cleanup. |
| `NOCA_WEB_SECURITY_EVENTS_REAPER_INTERVAL_SECONDS` | `86400` | Polling interval for the Web security-events retention reaper (1 hour to 7 days). Web reaps `module=web` rows. |
| `NOCA_ARENA_SECURITY_EVENTS_REAPER_INTERVAL_SECONDS` | `86400` | Polling interval for the Arena security-events retention reaper (1 hour to 7 days). Arena reaps `module in (arena, aiassistant)` rows. |

### Environment and logging

| Variable | Default | Description |
|----------|---------|-------------|
| `NOCA_ENVIRONMENT` | `development` | Runtime environment. Set to `production` in production deployments. Affects debug logging, error detail exposure, and other safety defaults. |
| `NOCA_LOG_LEVEL` | *(unset)* | Logging level honored by every module (`web`, `arena`, `autojudge`, `rating`, `aiassistant`): `DEBUG`, `INFO`, `WARNING`, `ERROR`, or `CRITICAL`. When unset, the level falls back to `DEBUG` in development and `INFO` in production. SQLAlchemy statement echo is enabled only when the effective level is `DEBUG`. |

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

---

## Shared between Web and Arena

These variables are read by both the web and arena modules.

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
| `NOCA_FORWARDED_ALLOW_IPS` | `127.0.0.1,::1` | Comma-separated trusted reverse proxy IPs/CIDRs used to accept `X-Forwarded-*` headers in Uvicorn/FastAPI. Example: `127.0.0.1,10.0.0.0/8`. Use `*` only in trusted private networks where clients cannot reach the app directly (e.g. a proxy on the same Docker network while the app publishes no ports). Loopback-only default silently drops forwarded headers from a containerized proxy — see the warning above. |

### JWT

| Variable | Default | Description |
|----------|---------|-------------|
| `NOCA_JWT_SECRET_KEY` | *(required)* | Secret key used to sign JWT tokens. Use a long, random string (e.g. `import os; os.urandom(32).hex()`). |
| `NOCA_JWT_ALGORITHM` | `HS256` | JWT signing algorithm |
| `NOCA_JWT_EXPIRE_SECONDS` | `3600` | Per-token JWT lifetime in seconds. Active authenticated web sessions rotate the cookie automatically when the remaining lifetime reaches half of this value. Arena also uses this as the LOGIN token lifetime; remembered Arena sessions rotate those 1-hour tokens at half-life while preserving a separate 30-day absolute cap. |
| `NOCA_JWT_REFRESH_MAX_SESSION_SECONDS` | `0` | Optional absolute cap for sliding web sessions in seconds. Set to `0` to disable the cap and keep active users signed in indefinitely while they remain active. |

Sliding-session notes:

- The web layer refreshes `noca_access_token` only for requests that
  successfully resolve a real authenticated actor.
- Refresh happens at half-life. With
  `NOCA_JWT_EXPIRE_SECONDS=3600`, the cookie rotates when a valid token has
  1800 seconds or less remaining.
- `NOCA_JWT_REFRESH_MAX_SESSION_SECONDS` applies to the whole login session, not
  to one token instance. When the cap is exceeded, the user must log in again.
- The Arena layer refreshes `arena_access_token` only for remembered sessions
  after `get_current_arena_user()` resolves a real authenticated user.
- Arena remembered sessions keep a 30-day persistent cookie, rotate 1-hour
  LOGIN JWTs at half-life, and stop rotating after 30 days from the original
  login timestamp.

### Email Service

| Variable | Default | Description |
|----------|---------|-------------|
| `NOCA_SEND_EMAIL` | `false` | Enables real email sending across the platform. When `false`, the app uses the mock provider and logs emails only. |
| `NOCA_EMAIL_PROVIDER` | `mock` | Email backend provider. Supported values: `mock`, `smtp`. |
| `NOCA_EMAIL_SENDER` | `no-reply@noca.local` | Default sender email used by `EmailService`. |
| `NOCA_EMAIL_SENDER_NAME` | *(empty)* | Default sender display name. Falls back to the module app name when empty. |
| `NOCA_SMTP_SERVER` | *(empty)* | SMTP server hostname. Required when `NOCA_SEND_EMAIL=true` and `NOCA_EMAIL_PROVIDER=smtp`. |
| `NOCA_SMTP_PORT` | `587` | SMTP server port (1-65535). |
| `NOCA_SMTP_USE_TLS` | `true` | Enables STARTTLS for SMTP connections. |
| `NOCA_SMTP_USERNAME` | *(empty)* | SMTP username. Required when SMTP sending is enabled. |
| `NOCA_SMTP_PASSWORD` | *(empty)* | SMTP password. Required when SMTP sending is enabled. |
| `NOCA_EMAIL_MBOX_LOG_DIR` | *(empty)* | Absolute directory for an append-only mbox audit log of every successfully delivered email (real SMTP sends only; mock sends are skipped). When empty the feature is disabled. Files rotate on fixed 15-day calendar windows named `YYYY-MM-01-to-YYYY-MM-15.mbox` and `YYYY-MM-16-to-YYYY-MM-<end>.mbox` (UTC). Writes are serialized with an mbox lock; the directory is created `0700` and each file `0600` because messages may contain secrets (OTPs, reset tokens). |

### Geolocation key

| Variable | Default | Description |
|----------|---------|-------------|
| `NOCA_GEOLOCATION_API_KEY` | *(empty)* | API key for the geolocation service (ipgeolocation.io). Required only if geolocation features are enabled. |

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
| `NOCA_IMAGE_MAX_FILE_SIZE` | `5242880` | Maximum allowed upload size in bytes (default 5 MiB) |
| `NOCA_IMAGE_MAX_WIDTH` | `2048` | Maximum allowed upload width in pixels (up to 4096) |
| `NOCA_IMAGE_MAX_HEIGHT` | `2048` | Maximum allowed upload height in pixels (up to 4096) |
| `NOCA_IMAGE_FONT_DIR` | *(empty)* | Optional directory containing fonts used by generated placeholder images |
| `NOCA_IMAGE_RESPONSE_CACHE_MAX_AGE` | `3600` | `Cache-Control: max-age` value for image responses in seconds |

---

## Web module

| Variable | Default | Description |
|----------|---------|-------------|
| `NOCA_WEB_APP_NAME` | `noca` | Web application name used in titles and generated content |
| `NOCA_WEB_URL_BASE` | *(empty)* | Public base URL used to build absolute links in credential emails and downloadable reports (e.g. `https://contest.example.com` or `http://192.168.1.10:8000`). Must include scheme and host; trailing slash is stripped. When not set, links are derived from the incoming HTTP request — this may produce incorrect URLs behind a reverse proxy that does not forward `X-Forwarded-*` headers. |

### Problem storage

| Variable | Default | Description |
|----------|---------|-------------|
| `NOCA_WEB_PROBLEM_STATEMENT_DIR` | *(required)* | Directory where problem statement PDFs are stored. Must be readable and writable by the web process. |
| `NOCA_PROBLEM_TESTCASE_DIR` | *(required)* | Root directory shared by Web, Arena, and Autojudge for problem test case files. Domains are namespaced into subdirectories: Web problems under `<root>/contest/<problem_id>/NNN.in\|out`, Arena problems under `<root>/arena/<problem_id>/NNN.in\|out`. The subdirectories are created on demand by the writers. Must be readable and writable by the web and arena processes, and readable by the autojudge worker. Replaces the former `NOCA_WEB_PROBLEM_TESTCASE_DIR` / `NOCA_JUDGE_PROBLEM_TESTCASE_DIR` pair (a one-time manual relocation of existing Web files into `<root>/contest/` is required at rollout). |

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
| `NOCA_WEB_SUBMISSION_RATE_LIMIT_WINDOW_SECONDS` | `60` | Rolling window length in seconds for per-team submission rate limiting. Only TEAM users are subject to this limit; staff and admin roles cannot reach the submission route. |
| `NOCA_WEB_SUBMISSION_RATE_LIMIT_MAX_SUBMISSIONS` | `3` | Maximum number of submissions a team may send within the rate-limit window. |

### UberAdmin Bootstrap

The web container's entrypoint runs `scripts/web/create_uberadmin.py` only when `NOCA_WEB_UBERADMIN_USERNAME` is set. The script is idempotent — if the username already exists it exits cleanly.

| Variable | Default | Description |
|----------|---------|-------------|
| `NOCA_WEB_UBERADMIN_USERNAME` | *(empty)* | Username for the bootstrap UberAdmin account |
| `NOCA_WEB_UBERADMIN_FULLNAME` | *(empty)* | Full name for the bootstrap UberAdmin account |
| `NOCA_WEB_UBERADMIN_EMAIL` | *(empty)* | Email address for the bootstrap UberAdmin account |
| `NOCA_WEB_UBERADMIN_PASSWORD` | *(empty)* | Password for the bootstrap UberAdmin account |

---

## Arena module

| Variable | Default | Description |
|----------|---------|-------------|
| `NOCA_ARENA_APP_NAME` | `noca-arena` | Arena application name used in titles, generated content, and JWT issuer claims. |
| `NOCA_ARENA_URL_BASE` | *(empty)* | Public base URL used to build absolute links in Arena emails (e.g. `https://arena.example.com`). Must include scheme and host; trailing slash is stripped. When not set, links are derived from the incoming HTTP request — this may produce incorrect URLs behind a reverse proxy that does not forward `X-Forwarded-*` headers. |
| `NOCA_ARENA_PASSWORD_MAX_AGE` | `0` | Maximum password age in days before a warning flash is shown at Arena login. `0` disables the check. Does not block login or enforce a password change. |

### Submission rate limiting

| Variable | Default | Description |
|----------|---------|-------------|
| `NOCA_ARENA_RATE_LIMIT_WINDOW_MINUTES` | `5` | Rolling window length in minutes for per-user submission rate limiting. Admins and judges are exempt. |
| `NOCA_ARENA_RATE_LIMIT_MAX_SUBMISSIONS` | `10` | Maximum number of submissions a regular Arena user may submit within the rate-limit window. |

### Live feed

| Variable | Default | Description |
|----------|---------|-------------|
| `NOCA_ARENA_LIVE_FEED_LIMIT` | `20` | Maximum number of finalized submissions returned by `/live/feed.json` and shown on the public Arena live feed page (1–100). |

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

## Autojudge worker

The autojudge worker also reads the common infrastructure variables above (database, Valkey, environment, log level).

### Problem storage

| Variable | Default | Description |
|----------|---------|-------------|
| `NOCA_PROBLEM_TESTCASE_DIR` | *(required)* | Root directory where problem test case files are read by the worker. The same shared volume the web and arena processes write through `NOCA_PROBLEM_TESTCASE_DIR`. The Web job reads `<root>/contest/<problem_id>`; the Arena job reads `<root>/arena/<problem_id>`. |

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
| `NOCA_JUDGE_ISOLATE_WALL_TIME_MULTIPLIER` | `3` | Multiplier applied to each problem's CPU time limit to compute the authoritative inner isolate `--wall-time` budget (1.0–10.0). |
| `NOCA_JUDGE_OUTER_TIMEOUT_MULTIPLIER` | `2` | Multiplier applied to the computed inner isolate wall-time budget to derive the outer `asyncio.wait_for()` safety timeout (1.0–10.0). |
| `NOCA_JUDGE_COMPILE_TIMEOUT_S` | `180` | Global ceiling for the compile phase in seconds (minimum 5 s). Per-language values configured in the database take precedence when set. |
| `NOCA_JUDGE_OUTPUT_LIMIT_BYTES` | `67108864` (64 MiB) | Global hard ceiling for stdout handling per test case. The effective NOCA output limit is `min(problem_or_language_output_limit, NOCA_JUDGE_OUTPUT_LIMIT_BYTES)` when a problem-level limit exists, otherwise this global value alone applies (minimum 1024). |
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
| `NOCA_JUDGE_LOCK_TTL_SECONDS` | `660` | TTL in seconds for the per-judgment Redis idempotency lock (`judge:lock:<id>`). Must be strictly greater than `NOCA_JUDGE_REAPER_STALE_THRESHOLD_MINUTES x 60` so that a slow-but-alive worker keeps its lock past the reaper's requeue window. Default of 660 s = 600 s stale threshold + 60 s margin. |

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

---

## Docker Compose Only

The following variables are used by `docker-compose.yml` for container runtime wiring and
host volume path expansion. They are not read by the application config classes in
`web/config.py`, `autojudge/config.py`, or `rating/config.py`.

| Variable | Default | Description |
|----------|---------|-------------|
| `NOCA_DATA_ROOT` | `.docker` | Host path prepended to all persistent data volume mounts (problem statements, test cases, PostgreSQL and Valkey data). |
| `PUID` | `1000` | UID that the `webapp`, `arena`, `autojudge`, `rating`, and `aiassistant` processes run as inside their containers. Use this to match ownership of bind-mounted host directories. |
| `PGID` | `100` | Primary GID that the `webapp`, `arena`, `autojudge`, `rating`, and `aiassistant` processes run as inside their containers. Use this to match ownership of bind-mounted host directories. |

### Language Seed

The web and arena container entrypoints run `scripts/bootstrap_languages.py` on startup only when explicitly enabled. The script is idempotent — it inserts missing languages and skips ones that already exist.

| Variable | Default | Description |
|----------|---------|-------------|
| `NOCA_SEED_LANGUAGES` | `false` | Set to `true` to seed the built-in language definitions into the database on startup |

---

## Build-time Variables

These variables are consumed by `containers/build.sh` during image construction. They are not read at runtime by the application.

| Variable | Default | Description |
|----------|---------|-------------|
| `JUDGE_ISOLATE_TAG` | `v2.6` | Git tag of [ioi/isolate](https://github.com/ioi/isolate/releases) to compile into `noca/isolate-base`. The binary is then copied into every `judge-<language>:run` image from that single base. Passed as a Docker build argument by `build.sh`; override to pin a different release. Isolate 2.6 requires `libseccomp-dev` at build time and `libseccomp2` in each run image. |
| `NOCA_IMAGE_PREFIX` | `noca` | Registry prefix applied to every image tag produced by the build script (e.g. `ghcr.io/myorg/noca`). Overridden by `--repo` on the command line. |
| `NOCA_IMAGE_NAMING` | `path` | Build-script image naming mode. `path` builds refs like `ghcr.io/myorg/noca/webapp`; `flat` builds refs like `docker.io/myuser/noca-webapp`. Overridden by `--naming` on the command line. |
