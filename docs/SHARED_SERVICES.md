# NOCA Shared Service Reference

This document lists service modules under `shared/services/` that are used by more than one runtime
module (`web`, `arena`, `autojudge`, `rating`, or `aiassistant`).

For web-specific services see [web/docs/SERVICES.md](../web/docs/SERVICES.md).
For arena-specific services see [arena/docs/SERVICES.md](../arena/docs/SERVICES.md).

---

## `timing.py`

Purpose:
- provide compact duration formatting and contest-relative timestamp utilities
  shared across application modules

Canonical location:
- `shared/timing.py`

Main entrypoints:
- `format_compact_duration(total_seconds) -> str` formats seconds as `10s`,
  minutes and seconds as `4m08s`, or hours and minutes as `1h03m`
- contest timestamp conversion helpers normalize elapsed times for display and
  ICPC scoring

---

## `age_check.py`

Purpose:
- centralise Arena age-gate policy for LGPD child/adolescent handling
- classify dates of birth as blocked, requiring parental consent, or allowed

Canonical location:
- `shared/age_check.py`

Main types:
- `AgeStatus`

Main entrypoints:
- `calculate_age_years(birth_date, reference_date=None) -> int`
- `check_age(birth_date, reference_date=None) -> AgeStatus`

Notes:
- `<13` returns `BLOCKED`
- `13..17` returns `NEEDS_PARENTAL_CONSENT`
- `18+` returns `ALLOWED`
- `reference_date` is available for deterministic tests.

---

## `app_logging.py`

Purpose:
- centralise console logging setup (`configure_logging`) reused by every runtime module
- provide a startup configuration dump (`log_settings`) so each module logs the exact settings
  values it resolved, distinguishing env/`.env` overrides from declared defaults

Canonical location:
- `shared/app_logging.py`

Main entrypoints:
- `configure_logging(logging_level=logging.DEBUG) -> None`
- `log_settings(logger, settings, *, level=logging.DEBUG) -> None`

Notes:
- `log_settings` accepts any `pydantic.BaseModel` settings instance; all five module
  `Settings` classes qualify. It is called once, immediately after each module's
  `| Initializing services |` startup marker.
- Each field is logged on its own line as `name = value [default|override]`, where the tag
  comes from `model_fields_set`.
- Secret-bearing string fields (name token in `PASSWORD/PASSWD/PWD/SECRET/KEY/TOKEN` with a
  non-empty `str` value) render as `********`. The `str` guard keeps numeric/bool policy
  fields visible (e.g. `PASSWORD_WORD_COUNT`), and token matching keeps `VALKEY_*` visible.
- Computed `@property` values (e.g. `db_url`, `valkey_url`) are excluded because they embed
  secrets; only declared model fields are dumped.
- Emitted at DEBUG, so the dump is silent when a module runs at INFO (the production default).

---

## `error_handlers.py`

Purpose:
- centralize HTML/JSON content negotiation for backend failures in the Web and
  Arena HTTP applications
- provide configured handlers for database unavailability (`503`) and unexpected
  application failures (`500`)
- register `SQLAlchemyError`, connection, timeout, and fallback exception handlers
  consistently

Canonical location:
- `shared/error_handlers.py`

Main entrypoints:
- `BackendErrorConfig` defines each application's template state attribute,
  template name, unavailable heading, logger, and optional presentation context
- `create_backend_error_handlers(config) -> BackendErrorHandlers`
- `register_backend_error_handlers(app, handlers) -> None`
- `render_error_response(...) -> Response`
- `request_accepts_html(request) -> bool`

Notes:
- Web and Arena own their templates and presentation context. Web remains
  text-only, while Arena adds its backend illustration URL through its context
  builder.
- HTTP-specific behavior remains local. Web owns its branded `404` response, and
  Arena owns its illustrated `404`, authentication redirects, permission
  redirects, and forced logout handling.

---

## `arena_rating.py`

Purpose:
- pure, loop-free Arena rating logic (problem difficulty internal 1–100, displayed
  as 0.1–10.0; user score 0–∞; affiliation rating 0–∞) shared by the Arena server
  and the standalone rating worker
- the periodic background loops that drive these functions live in the `rating/`
  worker module (`rating.loops`), not here

Canonical location:
- `shared/services/arena_rating.py`

Main entrypoints:
- `rate_problem(*, session, problem_id)`, `rate_all_problems(session)`
- `rate_user(*, session, user_id)`, `rate_all_users(session)`
- `rate_affiliation(*, session, affiliation_id, f)`, `rate_all_affiliations(session, f)`
- `format_next_rating_update(next_update) -> str | None` (Arena footer countdown)
- `format_rating_interval(seconds) -> str | None` (Arena help-page cadence text)
- algorithm constants (`ALPHA`, `BETA`, `W_SOLVE_RATE`, `W_TRIES`, `BASE_POINTS`,
  `GROWTH`, …) and `_points_for_difficulty` (consumed by Arena's `/help/rating`)

Notes:
- `NEXT_RATING_UPDATE_KEY = "arena:rating:next_update"` — Valkey key the rating worker
  writes the next scheduled cycle timestamp to (ISO8601); absent while a cycle runs.
  Every Arena instance polls it for a consistent footer.
- `RATING_INTERVAL_TEXT_KEY = "arena:rating:interval_text"` — Valkey key the rating
  worker writes the formatted active cycle interval to. Arena polls it for
  `/help/rating`; Arena does not validate or format `NOCA_RATING_INTERVAL` locally.
- `RATING_AFFILIATION_FACTOR_KEY = "arena:rating:affiliation_factor"` — Valkey key
  the rating worker writes the active affiliation decay factor to for `/help/rating`.
- rate functions do not commit; the caller owns the transaction.

---

## `arena_stats.py`

Purpose:
- compute precomputed per-problem statistics for the Arena statistics page, so no
  heavy aggregate query runs during an HTTP request
- the periodic loop that drives this lives in the `rating/` worker module
  (`rating.loops.run_problem_stats_loop`), on its own `STATS_INTERVAL` timer

Canonical location:
- `shared/services/arena_stats.py`

Main entrypoint:
- `compute_all_problem_statistics(session) -> int` — rebuilds every row in
  `arena_problem_statistics` (one JSON snapshot per problem with at least one judged
  submission) and returns the number of problems written

Aggregation rules:
- only judged submissions that count toward a problem are aggregated: `ARENA_ADMIN`
  submissions and the problem author's own submissions are excluded (`ARENA_JUDGE` and
  regular users count), matching the rating and public solver-count rules
  (`shared/services/arena_query_helpers.counts_toward_problem_rating`)
- verdict and language distributions cover those judged submissions (active = most
  recent non-`SUPERSEDED` judgment per submission)
- per-language wall-time / peak-memory tables and the wall-time histogram
  (`HISTOGRAM_BINS = 20` bins over `[0, time_limit_ms]`) cover **AC submissions only**

Notes:
- does not commit; the caller owns the transaction
- the Arena read side is `arena/services/problem_stats_service.py`, which only reads
  the latest snapshot

---

## `arena_heatmap.py`

Purpose:
- compute precomputed per-user submission heatmaps for the Arena user profile page, so
  no aggregate query runs during an HTTP request
- called by the rating worker (`rating.loops.run_user_rating_loop`) after each successful
  user rating cycle

Canonical location:
- `shared/services/arena_heatmap.py`

Main entrypoint:
- `compute_all_user_heatmaps(session) -> int` — deletes all existing rows in
  `arena_user_submission_heatmap`, then rebuilds one row per user who has at least one
  submission in the last 364 days; returns the number of heatmaps written

Algorithm:
- computes a 364-day UTC window (today − 363 days … today, inclusive)
- fetches `(user_id, created_at)` for all in-window submissions in one query
- aggregates by `(user_id, UTC date)` in Python (portable across SQLite and PostgreSQL)
- bulk-inserts one row per user with `data` (non-zero days only), `range_start`, and
  `range_end` so the client can size the calendar without its own date arithmetic

Notes:
- does not commit; the caller owns the transaction
- the full rebuild (delete → insert) ensures stale rows for inactive users never survive
  a cycle
- the Arena read side is `arena/routes/user_profile_api.py`
  (`arena_user_profile_submission_heatmap`)

---

## `arena_query_helpers.py`

Purpose:
- centralize reusable SQLAlchemy Core query fragments over Arena submission judgments
  so the "active judgment" definition lives in one place and callers cannot drift

Canonical location:
- `shared/services/arena_query_helpers.py`

Main entrypoint:
- `active_arena_judgment_subquery() -> Subquery` — most-recent non-`SUPERSEDED`
  judgment timestamp per submission (`submission_id`, `max_created_at`); join it back
  against `arena_submission_judgments` on `(submission_id, created_at)` to pick the
  active row

Reused by:
- `arena/services/live_feed_service.py`, `arena/services/submission_list_service.py`,
  and `shared/services/arena_stats.py`

---

## `sse_refresh.py`

Purpose:
- own the Server-Sent Events refresh loop shared by the web contest live feed and the
  Arena live feed, so the subtle async lifecycle (heartbeat, reconnect, task
  cancellation, generator cleanup) cannot drift between the two routes

Canonical location:
- `shared/services/sse_refresh.py`

Main entrypoint:
- `iter_refresh_events(*, open_event_stream, is_disconnected, should_emit=…,
  emit_initial_ping=False, heartbeat_seconds=15, reconnect_seconds=1) -> AsyncIterator[str]`
  — yields only generic `data: ping` / `data: refresh` frames. The event payload is
  consumed solely by the `should_emit` predicate (a bool); **no event data is ever
  serialized to the client**, so verdicts/identities never leak through the channel

Reused by:
- `web/routes/contest_live_feed.py` (filters by `contest_id` via `should_emit`)
- `arena/routes/live.py` (`emit_initial_ping=True`, emits for every event)

Frontend counterpart:
- `shared/static/js/live-feed-core.js` is the shared browser engine for both feeds
  (fetch/SSE/status/debounce/known-row highlight, overflow summary line, trailing-row
  fade); each app mounts it at `/static/shared-js` and supplies only a `renderRow`
  callback via `NocaLiveFeed.init(...)`

---

## Shared static JS (`shared/static/js/`)

Browser scripts that were byte-for-byte (or logic-) identical between the web and
arena modules now live once in `shared/static/js/` and are served by both apps
through the `static_shared_js` mount (`/static/shared-js`). Templates reference
them via `request.url_for('static_shared_js', path='<file>.js')`.

- `live-feed-core.js`: shared live-feed engine (see above).
- `flatpickr-init.js`: initializes date and datetime inputs marked with
  `data-fp-date` or `data-fp-datetime`; supports range-end, min-date, and modal
  options through `data-fp-*` attributes. Used by web and arena templates that
  include Flatpickr vendor assets.
- `highlight-row.js`: highlights a list row/item matching the URL hash fragment
  after a CRUD redirect. Used by web and arena admin list pages.
- `render-tc-explanation.js`: renders sample test-case explanations as Markdown
  + KaTeX on problem-detail pages.
- `problem-edit-unsaved-guard.js`: warns before leaving the problem create/edit
  form with unsaved changes.
- `tc-reorder-sortable.js`: shared drag-to-reorder for the admin test-case list
  (and the Web problem list), driven by `data-reorder-*` attributes and
  `.noca-drag-handle` / `.noca-sortable-*`; posts the move and swaps the refreshed
  list partial named by `data-reorder-target`.
- `tc-pending-remove.js`: shared pending-removal + undo for test cases; marks rows
  client-side and submits the ids in the hidden `tc_remove_ids` input on save.
- `tc-add-row.js`: shared inline "Add test case" rows appended to `#tc-add-rows`
  and submitted with the problem form (`tc_in_N` / `tc_out_N` / `tc_explanation_N`
  / `tc_is_sample_N`).
- `tc-replace-row.js`: per-row offline ZIP replace trigger (opens the hidden file
  input and submits its form).
- `problem-statement-editor-core.js`: shared core for the statement editor on the
  problem create/edit forms. Exposes `window.NocaStatementEditor.create()`, which
  builds the EasyMDE editor on `#stmt-md-editor` (restricted toolbar, KaTeX preview
  rendering, `noca:problem-statement-changed` dispatch) and returns a
  `StatementEditor` with `value`/`setValue`/`setEnabled`/`syncToTextarea`/
  `notifyChanged` helpers. Each module loads this first, then its own thin script:
  `arena/static/js/problem-statement-editor.js` only syncs on submit, while
  `web/static/js/problem-statement-editor.js` adds the web-only PDF/MD source
  switching (file input, "Replace with empty Markdown" button, `statement_source`).

---

## Shared static CSS (`shared/static/css/`)

Stylesheet rules that were byte-for-byte identical between the web and arena
modules live once in `shared/static/css/common.css`, served by both apps through
the `static_shared_css` mount (`/static/shared-css`). Each module's stylesheet
pulls it in with `@import url('/static/shared-css/common.css')` at the top
(consistent with the absolute `/static/...` paths already used in `url(...)`
references), so no per-template `<link>` is required.

`common.css` currently holds the `.noca-icon-btn-group` segmented-button rules and
the shared `live-feed-*` rules / `live-feed-row-flash` keyframes (paired with
`live-feed-core.js`). Module-specific design tokens stay in the per-module
stylesheets: `web/static/css/contest.css` and `arena/static/css/arena.css` keep
their own `:root` variables, `.material-symbols-outlined`, and `.live-feed-summary`
(which references a module-specific border token). The `arena-`-prefixed
look-alikes (e.g. `.arena-icon-btn`) are intentionally left in `arena.css` as part
of the Arena design-system namespace.

---

## `network_utils/`

Purpose:
- validate and sanitize outbound HTTP requests
- provide SSRF protection against localhost and private-network targets
- perform bounded JSON fetches for web and arena integrations

Canonical location:
- `shared/services/network_utils/`
- `web/services/network_utils/` is a compatibility re-export shim

Internal structure:
- `errors.py` — network and validation exception types
- `validation.py` — URL, header, param, IP, and SSRF-protection helpers
- `service.py` — `NetworkService` request execution and compatibility static methods

Main types:
- `NetworkService`
- `NetworkServiceError`
- `RequestValidationError`
- `URLValidationError`
- `SSRFProtectionError`
- `ParamsValidationError`
- `HeadersValidationError`

Main entrypoints on `NetworkService`:
- `make_json_request(url, params=None, header=None) -> dict[str, Any]`
- `validate_and_parse_url(url, *, block_private_networks=False, allowed_schemes=None) -> ParseResult`
- `sanitize_params(params, *, max_params=100, max_depth=5) -> dict[str, Any] | None`
- `sanitize_headers(headers) -> dict[str, str] | None`
- `build_safe_request_kwargs(...) -> tuple[str, dict[str, Any], int]`
- `get_ip_from_request(request) -> str | None`
- `is_private_network(hostname) -> bool`

Reuse this module when:
- calling third-party HTTP JSON endpoints from the web or arena layers
- validating URLs or proxy-forwarded client IPs
- adding SSRF-safe outbound request behavior

Do not reimplement:
- private-network blocking and DNS resolution checks
- request param and header sanitization
- bounded response-size handling for JSON fetches

Notes:
- `make_json_request` streams responses and enforces the `MAX_RESPONSE_SIZE` cap before parsing JSON
- SSRF protection checks both literal IPs and all resolved A/AAAA records for hostnames
- the package preserves the previous `web.services.network_utils` import surface via `NetworkService`

---

## `geolocation.py`

Purpose:
- resolve a client IP address to a human-readable location string for login history
- disabled gracefully when no API key is configured

Canonical location:
- `shared/services/geolocation.py`
- `web/services/geolocation.py` is a compatibility re-export shim

Main types:
- `GeolocationIP`

Constructor:
- `GeolocationIP(api_key: str | None, network_service: NetworkService, logger=None)`
  - `api_key`: ipgeolocation.io API key; when `None` the service is disabled and `get_location_by_ip` always returns `None`
  - `network_service`: a `NetworkService` instance for the outbound HTTP call

Main entrypoints:
- `get_location_by_ip(ip_address: str) -> str | None`
  - Returns a comma-joined string of `country_name, state_prov, district, city` or `None` on any failure

Notes:
- Private and loopback IPs (RFC 1918, RFC 4193, etc.) always return `None` without making an HTTP request
- API errors and network failures are logged and return `None` — the caller never raises
- Configured via `NOCA_GEOLOCATION_API_KEY` in both the `web` and `arena` modules
- In `arena/main.py` the instance is exposed on `app.state.geo_service`
- In `web/main.py` the instance is injected into `AuthenticationService`

---

## `email_validation.py`

Purpose:
- centralize email address validation, normalization, masking, and RFC 5322 formatting
- single source of truth for how NOCA validates and partially hides email addresses

Canonical location:
- `shared/services/email_validation.py`

Main types:
- `EmailValidationService`

Main entrypoints:
- `EmailValidationService.is_valid(email) -> bool`
- `EmailValidationService.normalize(email) -> str` — raises `ValueError` on invalid input
- `EmailValidationService.mask(email) -> str` — partial display (e.g. `use***@ex****.com`)
- `EmailValidationService.montar_destinatario(nome, email) -> str` — RFC 5322 display-name + address string

Notes:
- backed by the `email-validator` package with `check_deliverability=False`
- used by `arena/services/user_registration_service.py` and `web` registration flows

---

## `startup_wait.py`

Purpose:
- block module startup until PostgreSQL and Valkey are reachable, producing clean retry
  log lines instead of raw exception tracebacks during transient unavailability

Canonical location:
- `shared/services/startup_wait.py`

Main entrypoints:
- `wait_for_db(db_url, *, timeout_s, logger) -> None` — retries every 5 s; raises `RuntimeError` on timeout
- `wait_for_valkey(valkey_url, *, timeout_s, logger) -> None` — retries every 5 s; raises `RuntimeError` on timeout

Notes:
- passing `timeout_s=0` skips the wait and raises immediately on first failure (useful in tests)
- `wait_for_db` runs early in all five module startup sequences
- `wait_for_valkey` runs in web, Arena, autojudge, and AI assistant startup;
  the rating worker does not use Valkey

---

## `worker_pause_state.py`

Purpose:
- data-access layer for the authoritative Arena worker pause state stored in PostgreSQL
- used by `autojudge` and `aiassistant` workers to read whether they should be paused

Canonical location:
- `shared/services/worker_pause_state.py`

Main types:
- `PauseStateRow` — frozen dataclass with `worker_class`, `worker_id`, `paused`, `paused_by`, `generation`

Main entrypoints:
- `read_worker_pause_state(executor, worker_class, worker_id) -> PauseStateRow | None`
- `bump_worker_pause_state(executor, *, worker_class, worker_id, paused, paused_by) -> int`

Notes:
- accepts either `AsyncSession` (arena) or `AsyncConnection` (workers), typed loosely via duck typing
- the monotonic `generation` column makes replayed Valkey commands a no-op once the worker has adopted
  that generation; see [ARCHITECTURE.md](ARCHITECTURE.md) for the full pause/resume trust model
- the Arena route writes these rows; workers only read them via this service

---

## `email_service.py`

Purpose:
- central shared email service for the web and arena layers
- provider selection and validated config loading
- email address validation and normalization utilities

Canonical location:
- `shared/services/email_service.py`, `shared/services/email_validation.py`,
  `shared/services/email_models.py`, and `shared/services/email_providers.py`
- `web/services/email_*.py` modules are compatibility re-export shims

Main types:
- `EmailService`
- `EmailConfig`
- `EmailValidationService`

Main entrypoints:
- `EmailConfig.from_settings(settings) -> EmailConfig`
- `EmailService.send_email(...) -> EmailResult`
- `EmailService.get_provider_info() -> dict[str, str | None]`
- `EmailValidationService.is_valid(email) -> bool`
- `EmailValidationService.normalize(email) -> str`

Notes:
- provider selection is environment-driven (`NOCA_SEND_EMAIL`, `NOCA_EMAIL_PROVIDER`, `NOCA_SMTP_*`)
- `EmailValidationService` is the single public utility for email normalization in both the web and arena modules
- arena user models and services import email validation from `shared.services.email_validation`

---

## `email_models.py`

Purpose:
- dataclasses used by email providers and service APIs

Main types:
- `EmailMessage`
- `EmailResult`

Notes:
- `EmailMessage` requires `to_email` and at least one body (`text_body` or `html_body`)

---

## `email_providers.py`

Purpose:
- provider contracts and concrete providers for email delivery

Main types:
- `EmailProvider`
- `SMTPProvider`
- `MockProvider`
- `EmailProviderError`

Main entrypoints:
- `EmailProvider.send(message) -> EmailResult`
- `EmailProvider.get_provider_name() -> str`

Notes:
- `MockProvider` stores in-memory sent messages for tests
- `SMTPProvider` uses STARTTLS when configured
- `SMTPProvider` sets a real `Message-ID` (via `email.utils.make_msgid`) before
  sending and, when `mbox_log_dir` is configured, writes each accepted delivery
  to the mbox audit log (see `email_mbox_log.py`)

---

## `email_mbox_log.py`

Purpose:
- append-only, date-rotated mbox audit log of successfully delivered emails

Main entrypoints:
- `current_window_filename(when) -> str`
- `append_message(mbox_dir, mime_message, *, sender, relay, recipients, when) -> None`

Notes:
- only real SMTP deliveries are logged (configured via `NOCA_EMAIL_MBOX_LOG_DIR`);
  empty/unset disables the feature
- files rotate on fixed 15-day calendar windows (days 1-15 and 16-end of month),
  computed in UTC, named e.g. `2026-06-01-to-2026-06-15.mbox`
- writes are serialized with an in-process `threading.Lock` plus `mailbox.mbox`
  inter-process locking; the inter-process lock is non-blocking, so it is
  retried with bounded backoff to avoid dropping records under cross-process
  contention
- a directory the service creates is set `0700` and each file `0600` (messages
  may contain OTPs, reset tokens, activation links); a pre-existing directory is
  left untouched (so a misconfigured path like `/var/log` is never chmod-ed);
  added headers: `X-NOCA-SMTP-Relay`, `X-NOCA-Delivery-Date`, `X-NOCA-Recipients`
- logging failures are swallowed (warning logged) so audit logging never turns a
  successful delivery into a failure

---

## `password_service.py`

Purpose:
- generate diceware passwords
- validate password policy using runtime settings supplied by web or arena

Canonical location:
- `shared/services/password_service.py`
- `web/services/password_service.py` is a compatibility wrapper using `web.config.settings`

Main types:
- `PasswordSettings`
- `PasswordPolicyError`
- `PasswordPolicy`

Main entrypoints:
- `generate_diceware_password(settings, *, wordlist_path=None, size=None) -> str`
- `PasswordPolicy(settings).validate_new_password(password) -> None`
- `PasswordPolicy(settings).policy_hint -> str` — returns the current policy description for UI display

Notes:
- policy is controlled by `NOCA_PASSWORD_*`, `NOCA_MIN_PASSWORD_LENGTH`, and
  `NOCA_WORDLIST_FILENAME`
- generated passwords are aligned with the configured minimum length and enabled character-class
  requirements

Reuse this module when:
- generating initial credentials from web or arena
- validating new passwords in any runtime module

Do not reimplement:
- password complexity checks
- diceware generation

---

## `imageprocessing_service/`

Purpose:
- process uploaded/base64 images
- generate avatars
- crop to aspect ratio
- validate images
- convert image formats
- generate placeholder images
- build image responses with cache headers

Canonical location:
- `shared/services/imageprocessing_service/`
- `web/services/imageprocessing_service/` is a compatibility re-export shim

Internal structure:
- `models.py` — image result/error dataclasses
- `helpers.py` — crop, avatar, font, and placeholder helpers
- `validation.py` — raw image validation and format conversion helpers
- `service.py` — `ImageProcessingService` orchestration and FastAPI response helpers

Main types:
- `ImageProcessingError`
- `ImageProcessingConfig`
- `ImageBasicMetadata`
- `ImageProcessingResult`
- `ImageProcessingService`

Main entrypoints on `ImageProcessingService`:
- `process_upload_image(...) -> ImageProcessingResult`
- `process_base64(...) -> ImageProcessingResult`
- `crop_to_aspect_ratio(image, aspect_width=2, aspect_height=3) -> Image.Image`
- `generate_placeholder(...) -> bytes`
- `build_image_response(image_data, mime_type="image/png", *, cache_directive="public") -> Response`
- `image_validation(...) -> ImageBasicMetadata`
- `convert_to(content, output_format="PNG") -> bytes`

Reuse this module when:
- handling web or arena user photos/avatars
- validating raw image uploads
- serving image bytes with consistent cache headers

Do not reimplement:
- avatar resizing
- Pillow validation and decompression-bomb handling
- placeholder generation

---

## `token_revocation.py`

Purpose:
- provide a Valkey-backed JWT revocation store compatible with the `RevocationStore` protocol from `jwtservice`
- allow `JWTService.validate()` to automatically reject tokens that were revoked at logout
- store revoked JTIs with a TTL equal to the token's remaining lifetime so entries expire automatically

Canonical location:
- `shared/services/token_revocation.py`
- `web/services/token_revocation.py` is a compatibility re-export shim

Main types:
- `ValkeyRevocationStore`

Main entrypoints on `ValkeyRevocationStore`:
- `is_revoked(jti) -> bool` — returns `True` when the JTI is present in Valkey; fails open (returns `False`) on connection errors
- `revoke(jti, ttl_seconds, metadata=None) -> bool` — writes `auth:revoked:{jti}` with NX+EX; returns `True` if newly written, `False` if already present or on error
- `close() -> None` — closes the underlying sync Valkey client

Notes:
- uses a **synchronous** `valkey.Valkey` client (separate from the async `ValkeyRuntime`) to match the sync `RevocationStore` protocol
- key format: `auth:revoked:{jti}` (never conflicts with queue or lock keys)
- `is_revoked` failure mode is fail-open: Valkey outages do not block authenticated requests
- `revoke` failure mode is best-effort: if Valkey is unavailable, the token cookie is still deleted but the JTI may remain replayable until natural expiry
- wired into `JWTService` at startup (`main.py`) so revocation checking is automatic in `validate()`
- `app.state.revocation_store` holds the instance; `logout()` route calls `auth_service.logout(token)` to trigger revocation

Reuse this module when:
- any web or arena flow needs to eagerly invalidate a JWT (e.g. forced logout, password change)

Do not reimplement:
- the `auth:revoked:` key prefix outside this service
- manual JTI extraction from raw tokens — pass the raw token to `JWTService.revoke()` instead

---

## `lock_service.py`

Purpose:
- ephemeral Valkey TTL locks for clarification answering, staff task handling, and submission review coordination
- bulk lock reads for merged UI rendering and degraded-mode detection

Lock timeout semantics:
- callers pass `ttl_seconds` based on contest settings
- `ttl_seconds > 0`: lock expires after that many seconds
- `ttl_seconds = 0` in contest metadata (`clarifications_timeout_minutes`, `tasks_timeout_minutes`, `review_timeout_minutes`) means lock should last until contest end; service callers convert it to `contest.remaining_time_seconds` before calling `acquire_lock`
- `acquire_lock` still clamps Valkey TTL to at least 1 second as a safety guard

Main types:
- `LockClient` — `valkey.asyncio.Valkey | ValkeyRuntime`
- `LockState` — one active lock payload (`kind`, `contest_id`, `resource_id`, `holder_id`, `holder_role`, `acquired_at`, `expires_at`)
- `LockBatchResult` — bulk lookup result with `service_available` plus `locks_by_resource_id`

Main entrypoints:
- `lock_key(kind, contest_id, resource_id) -> str`
- `acquire_lock(client_or_runtime, *, kind, contest_id, resource_id, holder_id, holder_role, ttl_seconds, now=None) -> bool | None`
- `get_lock(client_or_runtime, *, kind, contest_id, resource_id) -> LockState | None`
- `get_locks(client_or_runtime, *, kind, contest_id, resource_ids) -> LockBatchResult`
- `release_lock(client_or_runtime, *, kind, contest_id, resource_id, holder_id) -> bool | None`
- `force_release_lock(client_or_runtime, *, kind, contest_id, resource_id) -> bool | None`

Reuse this module when:
- adding a new Valkey-backed lockable resource
- merging lock state into server-rendered list DTOs
- implementing force-release behavior for admins

Do not reimplement:
- lock key naming
- lock payload serialization/parsing
- compare-and-delete release semantics

---

## `valkey_service/`

Purpose:
- shared Valkey connection pool, queue operations, and metrics
- consumed via thin module-local shims in `web/services/valkey_service.py` and
  `arena/services/valkey_service.py`

Canonical location:
- `shared/services/valkey_service/`

Main entrypoints:
- `ValkeyRuntime` — owns pool/client lifecycle, periodic ping health checks, reconnect attempts, and local buffering of write commands while Valkey is unavailable
- `create_valkey_pool() -> ConnectionPool`
- `enqueue_job(client_or_runtime, job, *, priority) -> None`
- `enqueue_arena_submission_job(client_or_runtime, job) -> None`
- `enqueue_profiling_job(client_or_runtime, job) -> None`
- `dequeue_job_id(client_or_runtime) -> str | None`
- `get_contest_queue_metrics(client_or_runtime, contest_id) -> ContestQueueMetrics | None`
- `remove_from_inflight(client_or_runtime, judgment_id) -> None`
- `publish_verdict(client_or_runtime, event) -> None`
- `worker_presence_loop(...) -> None` — immediately publishes a worker and
  refreshes its live marker until shutdown
- `list_all_workers(client_or_runtime) -> dict[WorkerClass, list[WorkerPresence]]`
- `remove_worker(...) -> None` — removes a worker until its next heartbeat
- `build_command(...)`, `publish_command(...)`, `verify_command(...)`, `claim_nonce(...)`, `worker_command_loop(...)`, `WorkerCommandType`, `LivePauseFlag` — signed worker pause/resume command transport (see "Worker pause/resume commands")
- `ValkeyRuntime.set_reporting(key, value, *, ex) -> bool` — atomic `SET … EX` reporting delivery success for auditing
- `ValkeyRuntime.get_and_delete(key) -> str | None` — atomically consumes a
  string key with `GETDEL`
- Queue key constants: `QUEUE_PENDING_KEY`, `QUEUE_PRIORITY_KEY`, `QUEUE_INFLIGHT_KEY`, `QUEUE_INFLIGHT_TIMES_KEY`, `QUEUE_JOB_HASH_PREFIX`, `QUEUE_RESULTS_CHANNEL`

Worker presence:
- `WorkerClass` defines `autojudge`, `rating`, and `aiassistant`.
- `noca:worker-presence:<class>:seen` is a durable hash from worker ID to JSON
  containing the latest process start and heartbeat timestamps. Readers also
  accept the earlier start-timestamp-only value during upgrades.
- `noca:worker-presence:<class>:live:<worker_id>` is a JSON live marker with a
  configurable TTL. The heartbeat updates both keys atomically.
- `noca:worker-presence:<class>:last-jobs` is a **durable hash with no TTL**
  from worker ID to a UTC ISO 8601 timestamp recording when that worker last
  dequeued a job. Written by `publish_worker_last_job(client, *, worker_class,
  worker_id)` every time a worker picks up work. Populated by autojudge on each
  `dequeue_job_id` hit, by aiassistant on each `dequeue_arena_ai_review_job_id`
  hit, and by the rating worker at the start of each problem-rating cycle via an
  `on_cycle_start` callback. Exposed as `WorkerPresence.last_job_at` (None when
  no entry exists) and surfaced in the dashboard "Last job" column.
- Graceful shutdown deletes only the live marker. A crash becomes offline when
  the live marker expires, while the durable hash keeps the worker visible.
- Dashboard removal runs a 3-key Lua script that deletes the registry entry,
  the live marker, and the last-job hash field atomically. A running worker
  reappears when it publishes its next heartbeat.

Worker pause/resume commands (`worker_commands.py` + `worker_pause_state.py`):
- **PostgreSQL is the authoritative, monotonic source of truth.**
  `arena_worker_pause_state` holds `paused`, `paused_by`, and a per-worker
  monotonic `generation`. The signed Valkey command is only a low-latency,
  authenticated *nudge*: a worker derives its paused/running state solely from
  committed PG rows and treats a verified command as a trigger to reconcile now.
- **Commit-before-publish ordering.** The Arena route bumps the pause state and
  writes an `arena_worker_command_audit` row in one transaction and commits
  *before* publishing the nudge, so a rolled-back/raced command can never point
  at state that does not exist. A failed publish is recorded as
  `transport_status=transport_failed` while the operation still succeeds — the
  worker applies it on its next PG reconcile.
- **Trust model.** Commands are signed `HMAC-SHA256` over every field; an empty
  secret disables the feature (nothing is published or accepted). `verify_command`
  binds each command to its target `worker_class`/`worker_id` (`wrong_target`),
  enforces symmetric freshness (`abs(now-issued_at) <= freshness`, rejecting both
  stale and future-dated), validates every signed field, and rejects unexpected
  fields. `claim_nonce` fails closed: it accepts only an exact `True` from the
  set-if-absent, so replays, prior claims, truthy status strings, and Valkey
  errors all reject. A command-triggered reconcile applies state only when the
  committed PG generation exactly matches the signed generation. The regular
  poll still independently adopts any newer authoritative PG generation.
- **Native atomic transport only.** Delivery is a single per-worker
  `noca:worker-command:<class>:<worker_id>` key written with `SET … EX` via
  `ValkeyRuntime.set_reporting` (returns delivery success for auditing). Workers
  consume the key once with atomic `GETDEL`, so a handled payload is not logged
  on every poll and consumption cannot delete a newer command. Nonces use
  `noca:worker-command:nonce:<nonce>` via `set_if_absent`. No list ops.
- `WorkerCommandType` now includes `FLUSH_NOW` and `POLL_NOW` in addition to
  `PAUSE` and `RESUME`. `FLUSH_NOW`/`POLL_NOW` are *trigger* commands: they
  carry no authoritative PG state and must not bump `arena_worker_pause_state`.
  They are published with `generation=0` (valid per the `generation >= 0` check),
  which the worker discards after dispatching the callback. Only the aiassistant
  worker exposes trigger handling; autojudge silently claims the nonce and no-ops.
- `worker_command_loop(client, session_factory, *, worker_class, worker_id,
  secret, poll_seconds, freshness_seconds, nonce_ttl_seconds, flag, stop_event,
  logger, reconcile_seconds=60, on_trigger=None)` polls Valkey at `poll_seconds`,
  applies verified PAUSE/RESUME nudges immediately, and uses a slower PostgreSQL
  reconciliation as fallback for a lost nudge. When a verified FLUSH_NOW or
  POLL_NOW command arrives, `on_trigger(cmd)` is called (if set) instead of
  reconciling pause state. The autojudge and aiassistant workers check
  `flag.paused` before dequeuing and reconcile their pause state from PG at
  startup, so a restarted worker returns to its committed paused state. The loop
  starts only when `NOCA_WORKER_COMMAND_SECRET` is set.
- `read_worker_pause_state(executor, worker_class, worker_id) -> PauseStateRow | None`
  and `bump_worker_pause_state(executor, *, worker_class, worker_id, paused,
  paused_by) -> int`
  (atomic dialect-aware upsert returning the new monotonic generation) live in
  `shared/services/worker_pause_state.py` and accept an AsyncSession or
  AsyncConnection.

Verdict pub/sub channels (live feeds):
- `QUEUE_RESULTS_CHANNEL = "judge:results"` — contest (web) verdicts. Produced by autojudge/web; consumed by `ValkeyRuntime.iter_verdict_events()` (web runs SSE and the public contest live feed).
- `ARENA_RESULTS_CHANNEL = "arena:results"` — Arena verdicts. Produced **only** by the autojudge worker via `publish_arena_verdict_with_client` (exported as `_publish_arena_verdict_with_client`); consumed by `ValkeyRuntime.iter_arena_verdict_events()` (Arena public live feed). The channel name has a single source of truth in this constant so the autojudge producer and Arena subscriber cannot drift.
- `ArenaVerdictEvent` (`shared/queue_schema.py`) is the minimal `{submission_id, judgment_id, verdict}` payload on `arena:results`. It is a "changed" signal only: the Arena live feed refetches a server-side snapshot rather than rendering event fields. There is deliberately no runtime publish path / `PendingCommand` operation for it, since nothing publishes Arena verdicts through `ValkeyRuntime`.

Arena AI review queue helpers (used by `aiassistant/`):
- `enqueue_arena_ai_review_job(client_or_runtime, job) -> None`
- `dequeue_arena_ai_review_job_id(client_or_runtime) -> str | None` — atomically moves item from `ai:queue:pending` to `ai:queue:inflight` and records dispatch timestamp in `ai:queue:inflight:times`
- `remove_from_ai_review_inflight(client_or_runtime, submission_id) -> None`
- `complete_arena_ai_review_job(client_or_runtime, submission_id) -> None` —
  atomically removes matching pending and inflight entries, the dispatch
  timestamp, and `ai:job:<submission_id>` after terminal handling
- `get_stale_ai_review_job_ids(client_or_runtime, stale_threshold_s) -> list[str]` — ZRANGEBYSCORE on `ai:queue:inflight:times` for jobs older than `stale_threshold_s` seconds
- `get_ai_review_job_hash(client_or_runtime, submission_id) -> dict[str, str] | None` — retrieves job metadata hash at `ai:job:<submission_id>`
- `get_ai_review_queued_ids(client_or_runtime) -> set[str]` — union of `ai:queue:pending` and `ai:queue:inflight`; used by the reconciler to detect submissions flagged `submit_to_ai` whose queue job was lost after commit
- AI review queue constants: `QUEUE_AI_REVIEW_PENDING_KEY`, `QUEUE_AI_REVIEW_INFLIGHT_KEY`, `QUEUE_AI_REVIEW_INFLIGHT_TIMES_KEY`, `QUEUE_AI_REVIEW_JOB_HASH_PREFIX`
- `AI_BATCH_TURNAROUND_STATS_KEY = "ai:batch:turnaround:stats"` — persistent
  JSON statistics for the 100 most recent successful platform-key reviews.
  `AIBatchTurnaroundStats` defines the versioned payload with average, median,
  population standard deviation, sample count, and UTC update timestamp. The
  AI assistant poller replaces the complete value with one atomic `SET`; Arena
  validates and displays it on the AI credits dashboard and platform-credit
  review confirmation modal.

Arena AI batch review state:
- `shared/db_schema/arena/arena_ai_batch_jobs.py` defines the durable batch job
  table used by the `aiassistant` platform-key path.
- `aiassistant/db/batch_queries.py` inserts submitted batch jobs, finds active
  jobs for idempotency, lists pending rows for the poller, updates OpenAI poll
  metadata, and finalizes terminal states.
- `ArenaAIBatchJobStatus` in `shared/enumerations.py` defines the local state
  machine values: `preparing`, `submitted`, `polling`, `completed`, `failed`,
  `expired`, and `cancelled`.

Notes:
- `dequeue_job_id` atomically moves the first ready job from profiling, priority, or pending into
  inflight with Lua, and returns `None` immediately when all queues are empty.
- `dequeue_arena_ai_review_job_id` uses a separate Lua script for the `ai:queue:*` namespace
  and adds a ZADD timestamp after the atomic list transition to enable reaper staleness detection.
- The `aiassistant` worker uses user-owned API keys for online Responses API
  reviews. When it uses `NOCA_AI_OPENAI_API_KEY`, it submits a single-item OpenAI
  batch job and the batch poller stores the review after completion.
- `aiassistant/db/queries.py` reads `arena_users.prefered_language` so online
  and batch review tasks can request output in the user's locale.

---

## `scoreboard_cache.py`

Purpose:
- scoreboard cache invalidation helpers shared between web and future arena ranking cache needs

Canonical location:
- `shared/services/scoreboard_cache.py`

Notes:
- arena computes rankings on demand without a scoreboard cache; this module is currently web-only but lives in shared for future use

---

## `testcase_files.py`

Purpose:
- generic, model-free filesystem helpers for problem test cases, shared by Web, Arena, and the autojudge worker

Canonical location:
- `shared/services/testcase_files.py`

Key functions / constants:
- `CONTEST_TC_SUBDIR = "contest"`, `ARENA_TC_SUBDIR = "arena"` — domain subdirectories under the shared `NOCA_PROBLEM_TESTCASE_DIR` root
- `get_testcase_path(problem_id, ordinal, ext, testcase_dir)` — resolve `<testcase_dir>/<problem_id>/NNN.in|out`
- `save_testcase_files(...) -> (in_size, out_size)` — normalize to LF and write a pair, returning on-disk byte sizes
- `read_testcase_preview`, `read_testcase_full`, `read_testcase_sizes`
- `delete_testcase_files`, `delete_all_testcase_files`, `renumber_testcase_files`, `reorder_testcase_files`

Notes:
- callers pass the domain-specific root (`settings.PROBLEM_TESTCASE_DIR`, already resolved to `<root>/contest` for Web and `<root>/arena` for Arena); the helper is domain-agnostic
- the single-source-of-truth inline-edit threshold `MAX_INLINE_TESTCASE_BYTES` (10 KB) and the single-case ZIP helpers (`parse_single_testcase_zip`, `build_single_testcase_zip`, `SingleTestCase`) live in `shared/tc_zip.py`

---

## `testcase_view.py`

Purpose:
- cross-module presentation model that lets the Web and Arena admin pages share one
  test-case list partial (`shared/template/_partials/testcase_list_table.html`)

Canonical location:
- `shared/services/testcase_view.py`

Key types:
- `TestCaseRowView` — frozen dataclass holding per-row display fields (`ordinal`,
  `is_sample`, `has_explanation`, previews, sizes, `is_large`) plus pre-built per-row
  URLs (`edit_url`, `download_url`, `replace_url`, `move_url`, `toggle_sample_url`)

Notes:
- each module builds the URLs in its own route layer (Web adapter in
  `web/routes/contest_admin_problem_helpers.py::build_testcase_row_views`, Arena adapter in
  `arena/routes/admin_problem_form_views.py::build_testcase_row_views`) so the shared
  partial never resolves module-specific `url_for` names
- `is_large` (case exceeds `MAX_INLINE_TESTCASE_BYTES`) is baked in so the template needs
  no Jinja global
- both modules add `shared/template` to their Jinja `ChoiceLoader` search path; the shared
  edit-form body (`shared/template/_partials/testcase_edit_form.html`) and the shared TC
  scripts (`tc-reorder-sortable.js`, `tc-pending-remove.js`, `tc-add-row.js`,
  `tc-replace-row.js`) complete the unified test-case editing UI

---

## `arena_notification_service.py`

Purpose:
- durable Arena user notifications shared by producers and the Arena UI
- cross-module notification insertion without importing Arena route or ORM code

Canonical location:
- `shared/services/arena_notification_service.py`

Main entrypoints:
- `create_arena_notification(executor, *, user_id, notification_kind, title, message, target_url=None, source_ref=None, context=None) -> str`
- `count_unread_arena_notifications(executor, *, user_id) -> int`
- `list_latest_arena_notifications(executor, *, user_id, limit=20) -> list[RowMapping]`
- `mark_arena_notification_read(executor, *, notification_id, user_id) -> bool`

Notes:
- helpers accept an async SQLAlchemy session or connection
- helpers do not commit or roll back; callers own the transaction boundary
- `source_ref` is the producer-owned idempotency key
- `target_url` is nullable until a concrete destination page exists
- the v1 list is capped at 20 rows for the topbar dropdown
- `autojudge` emits `SUBMISSION_JUDGED` notifications when Arena judging
  finishes
- `aiassistant` emits `AI_REVIEW_COMPLETED` notifications when an AI review is
  stored and `AI_REVIEW_FAILED` notifications when an online or batch review
  cannot be completed

### `ARENA_NOTIFICATION_ICONS`

A module-level dict in `shared/enumerations.py` that maps each `ArenaNotificationKind` value to
a [Material Symbols](https://fonts.google.com/icons) icon name:

| Kind | Icon |
|---|---|
| `SUBMISSION_JUDGED` | `bug_report` |
| `AI_REVIEW_COMPLETED` | `psychology_alt` |
| `AI_REVIEW_FAILED` | `cognition` |
| `CLASS_REGISTRATION_REQUEST` | `how_to_reg` |
| `CLASS_REGISTRATION_APPROVED` | `check_circle` |
| `CLASS_REGISTRATION_DENIED` | `cancel` |
| `CLASS_MEMBERSHIP_ADDED` | `person_add` |
| `CLASS_MEMBERSHIP_REMOVED` | `person_remove` |
| `PROBLEM_REMOVAL_REQUEST` | `delete_forever` |
| `TEACHER_FEEDBACK_POSTED` | *(fallback `notifications`)* |
| `OTHER` | `stacked_email` |

The Arena notification serialiser (`arena/routes/notifications.py`) uses this dict to add an
`"icon"` field to each JSON notification. The fallback when a kind is absent from the dict is
`"notifications"`. Icon resolution is a view concern — it is never stored in the database.

---

## `user_presence.py`

Purpose:
- track which end users are currently online, backed by Valkey, to drive the
  green online-dot overlaid on user avatars
- identity-domain aware (`arena` now, `contest` later) so the Web module can
  reuse it without key collisions

Canonical location:
- `shared/services/user_presence.py`

Key functions / constants:
- `PRESENCE_PREFIX = "noca:user-presence"`, `MAX_PRESENCE_BATCH = 500`
- `user_live_key(domain, user_id)` — `noca:user-presence:{domain}:live:{user_id}`; its existence means "online"
- `online_set_key(domain)` — `noca:user-presence:{domain}:online`; sorted set (member = user id, score = last-seen epoch) used for counting
- `mark_user_online(client, *, domain, user_id, ttl_seconds)` — atomic Lua `eval`: `SET ... EX` the live key + `ZADD` the online set (best-effort)
- `mark_user_offline(client, *, domain, user_id)` — atomic `DEL` live key + `ZREM` from the online set
- `get_users_online_map(client, *, domain, user_ids) -> dict[str, bool]` — one batch `mget`; dedupes/caps ids
- `count_online_users(client, *, domain, ttl_seconds) -> int | None` — `ZREMRANGEBYSCORE` to purge stale members, then `ZCARD`; returns `None` when unavailable (kept distinct from a real `0`)

Notes:
- accepts a `ValkeyRuntime` or a raw `valkey.asyncio.Valkey` client and never raises:
  writes drop silently on outage, reads degrade to "everyone offline", and the count returns `None`
- model mirrors `valkey_service/worker_presence.py` (live key + TTL + batch read); the online
  sorted set adds global counting without enumerating keys (no `SCAN`/set ops needed)
- the Arena footer counter reads a cached value refreshed by the `_online_users_count_poller`
  background task (`arena/main.py`), so `count_online_users` is not called per request
- Arena wiring: `arena/routes/presence.py` (heartbeat + status endpoints),
  `arena/dependencies/auth.py` (best-effort mark-online per page view),
  `shared/static/js/noca-presence.js` + `shared/static/css/presence.css` (client)

---

## Arena module — shared service instances

The arena module initializes its own instances of the shared services listed below. All
instances live on `app.state` and are created in `arena/main.py`'s lifespan in the order shown.

### `SecretsManager` (startup)

Purpose:
- transparent Fernet encryption/decryption for `EncryptedString` columns (OTP secrets)

Canonical location:
- `secrets_manager` PyPI package (`dclobato/secrets-manager`)
- registered globally via `shared.db_schema.custom_types.init_encrypted_string(manager)`

Configuration:
- `SecretsConfig.from_environment()` reads three env-var groups (no `NOCA_` prefix):
  - `ENCRYPTION_KEYS__<version>` — plaintext password for each key version
  - `ENCRYPTION_SALT__<version>` — base64-encoded salt for each key version
  - `ACTIVE_ENCRYPTION_VERSION` — which version to use for new writes

Main entrypoints:
- `SecretsManager.encrypt(plaintext: bytes) -> tuple[str, bytes]`
- `SecretsManager.decrypt(ciphertext: bytes, version_hint: str | None) -> tuple[str, bytes]`
- `SecretsManager.get_active_version() -> str`

Notes:
- `init_encrypted_string()` must be called before any ORM read or write on `_otp_secret`
- multiple key versions allow rolling re-encryption without downtime
- `web` and `arena` run as separate OS processes; the global state in `custom_types` is not shared

### `JWTService` (arena instance)

Purpose:
- issue and validate Arena JWT tokens with `issuer = settings.APP_NAME` (`"noca-arena"`)
- automatic revocation-store check on `validate()`

Canonical location: `jwtservice` PyPI package; `app.state.jwt_service`

Notes:
- the issuer `"noca-arena"` differs from the web module's `"noca"`, preventing cross-server token acceptance
- uses the same `NOCA_JWT_SECRET_KEY`, `NOCA_JWT_ALGORITHM`, and `NOCA_JWT_EXPIRE_SECONDS` env vars as the web module by default; configure separate values for stronger isolation

### `EmailService` (arena instance)

Purpose:
- transactional email delivery for arena user flows (account activation, password reset, 2FA)

Canonical location: `shared/services/email_service.py`; `app.state.email_service`

Notes:
- configured via the same `NOCA_SEND_EMAIL`, `NOCA_EMAIL_PROVIDER`, and `NOCA_SMTP_*` env vars as the web module
- see the `email_service.py` section above for full API reference
