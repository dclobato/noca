# NOCA Shared Service Reference

This document lists service modules under `shared/services/` that are used by more than one runtime
module (`web`, `arena`, `autojudge`, `rating`, `aiassistant`, `healthmonitor`, or
`animator`).

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

## `signal_names.py`

Purpose:
- translate fatal POSIX signal numbers (isolate `exitsig`) into human-readable
  descriptions so Runtime Error verdicts explain *how* the process died
  (e.g. "SIGSEGV — segmentation fault (invalid memory access)")

Canonical location:
- `shared/signal_names.py`

Main entrypoints:
- `describe_signal(signum) -> str` returns `"<NAME> — <explanation>"` for known
  fatal signals, the bare signal name for other valid signals, and
  `"signal <n>"` for unknown numbers
- `signal_name(signum) -> str` returns the short name (`"SIGSEGV"`), or
  `"SIG<n>"` for unknown numbers

Consumers:
- Web submission review page (registered as the `describe_signal` Jinja global)
- Arena submission detail page and problem-set batch feedback service

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
- `sqlalchemy_echo_enabled(logging_level) -> bool` — used by the database engine
  setup of web, arena, autojudge, rating, aiassistant, and animator

Notes:
- `log_settings` accepts any `pydantic.BaseModel` settings instance; all seven runtime
  module `Settings` classes qualify. It is called once, immediately after the
  `| Initializing services |` startup marker, in web, arena, autojudge, rating, and
  aiassistant; healthmonitor logs the marker but does not call `log_settings`, and
  animator does neither.
- Each field is logged on its own line as `name = value [default|override]`, where the tag
  comes from `model_fields_set`.
- Secret-bearing string fields (name token in `PASSWORD/PASSWD/PWD/SECRET/KEY/TOKEN` with a
  non-empty `str` value) render as `********`. The `str` guard keeps numeric/bool policy
  fields visible (e.g. `PASSWORD_WORD_COUNT`), and token matching keeps `VALKEY_*` visible.
- Computed `@property` values (e.g. `db_url`, `valkey_url`) are excluded because they embed
  secrets; only declared model fields are dumped.
- Emitted at DEBUG, so the dump is silent when a module runs at INFO (the production default).
- `configure_logging` also installs the `log_redaction.py` filter (see below) on the console
  handler and on the HTTP-client loggers. Installation is idempotent, so repeated calls (tests,
  hot reload) never stack filters on the same logger.

---

## `log_redaction.py`

Purpose:
- mask credentials that travel inside URLs before they reach any log output

Canonical location:
- `shared/log_redaction.py`

Main entrypoints:
- `redact_secrets(text) -> str`
- `SecretRedactingFilter` (a `logging.Filter` that rewrites rather than suppresses)
- `MASK` — the shared `********` placeholder, also used by `app_logging.log_settings`

Notes:
- IPQualityScore takes its API key as a **URL path segment** (`/api/json/ip/<key>/<ip>` and
  `/api/json/email/<key>/<email>`), so the key travels in every request URL. It reaches logs by
  two independent routes: the HTTP client logs the request line at DEBUG, and
  `NetworkServiceError` messages embed the failed URL, which the reputation services log at
  ERROR (on in production).
- Both routes are closed. `configure_logging` attaches `SecretRedactingFilter` to the console
  handler and to the `urllib3`, `requests`, `httpx`, and `httpcore` loggers — filtering at the
  *emitting* logger also covers handlers we do not own, such as pytest's log capture. In
  addition, `ip_reputation.py` and `email_reputation.py` pass exception text through
  `redact_secrets` at the call site, so redaction does not depend on logging configuration or
  on which logger was injected.
- Limitation: a filter cannot reach text a handler renders later from `exc_info`, so exception
  objects carrying a secret must be redacted at the call site (as those two services do) rather
  than logged with `logger.exception`.

---

## `error_handlers.py`

Purpose:
- centralize HTML/JSON content negotiation for backend failures in all four HTTP
  applications (Web, Arena, animator, health monitor)
- provide configured handlers for database unavailability (`503`) and unexpected
  application failures (`500`)
- register `SQLAlchemyError`, connection, timeout, and fallback exception handlers
  consistently
- answer generic router and validation failures with a neutral body that does not
  name the application stack

Canonical location:
- `shared/error_handlers.py`

Main entrypoints:
- `BackendErrorConfig` defines each application's template state attribute,
  template name, unavailable heading, logger, and optional presentation context
- `create_backend_error_handlers(config) -> BackendErrorHandlers`
- `register_backend_error_handlers(app, handlers) -> None`
- `render_error_response(...) -> Response`
- `request_accepts_html(request) -> bool`
- `is_generic_http_exception(exc) -> bool`
- `generic_error_response(request, config, *, status_code) -> Response`
- `create_validation_exception_handler(config) -> ExceptionHandler`
- `create_generic_http_exception_handler(config) -> ExceptionHandler`
- `register_generic_error_handlers(app, config) -> None` — the registration path
  consumed by animator and healthmonitor; web and arena hand-roll their generic
  handling from `is_generic_http_exception` + `generic_error_response`

Notes:
- Each application owns its templates and presentation context. Web remains
  text-only, Arena adds its backend illustration URL through its context builder,
  and animator and health monitor use a minimal standalone `errors/backend.html`.
- HTTP-specific behavior remains local. Web owns its branded `404` response, and
  Arena owns its illustrated `404`, authentication redirects, permission
  redirects, and forced logout handling.
- **Only generic failures are rewritten.** A router-generated `404`/`405` carries
  no application message (Starlette fills `detail` with the status phrase), so it
  answers `{"error": "not_found"}` / `{"error": "method_not_allowed"}` instead of
  the framework's `{"detail": ...}` shape. A `RequestValidationError` always
  answers `{"error": "invalid_request"}` and the exception is never inspected,
  because Pydantic's default body names the validation library, discloses internal
  parameter names, and echoes the caller's input back.
- Anything an application authored keeps its `detail` verbatim, and statuses
  outside `{404, 405, 422}` are untouched. This is what lets the animator control
  panel keep reading refusal messages out of `payload.detail`.
- The animator relies on this: its unknown-slug, disabled-contest, and
  control-kill-switch refusals must stay indistinguishable, so every handler
  derives its response from the status code alone and never from the cause.

---

## `http_params.py`

Purpose:
- bound integer request parameters so a value larger than the PostgreSQL column
  it is compared against is refused at the HTTP boundary instead of failing
  inside a query

Canonical location:
- `shared/http_params.py`

Main entrypoints:
- `PG_INT32_MAX` — PostgreSQL `integer` is signed 32-bit, and the shared schema
  uses it for every route-addressable natural-number column
- `MAX_PAGE` — page-number cap, chosen so the computed SQL `OFFSET` stays orders
  of magnitude inside `int32`
- `DbId` / `DbIdQuery` — a path or query parameter naming a row by integer id
- `PageNumber` — a one-based page number from the query string
- `BoundedIntConvertor`, registered at import time as the `dbid` path convertor —
  use `{name:dbid}` in a route path, never the built-in `{name:int}`

Notes:
- Python integers are unbounded, so a bare `int` route parameter accepts values
  no column can hold. asyncpg then raises `DataError: value out of int32 range`
  at query time, which reaches the caller as a **503** and writes a full SQL
  statement to the log — a trivially triggerable error path, and a misleading one
  since nothing is actually unavailable.
- Row primary keys are `String(36)` UUIDs, so `str`-typed path parameters cannot
  overflow. These aliases are for the natural-number columns: `arena_number`,
  ordinals, page numbers, and the problem limit fields.
- **The path convertor matters as much as the annotation.** Starlette's built-in
  `int` convertor matches `[0-9]+` and then calls `int(value)`, which CPython
  refuses above `sys.get_int_max_str_digits()` (4300). The resulting `ValueError`
  is raised inside `Route.matches()` — during routing, before any dependency or
  exception handler — so an oversized numeric path became an **unauthenticated
  500 with a traceback**, bypassing the neutral bodies entirely. `dbid` bounds the
  digit count in the regex, so an oversized path simply fails to match and is an
  ordinary 404. A value that parses but exceeds the column is still refused by the
  `le` bound. Negative and non-numeric segments remain 404s, as before.
- The handful of `BigInteger` columns (login/rating history, worker control) are
  not addressed by route parameters. A route that ever does address one needs its
  own wider bound rather than `DbId`.

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
- `rate_problem(*, session, problem_id, pivot: float | None = None)` — `pivot` is an
  optional population-contrast pivot — and `rate_all_problems(session)`
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
- `rate_all_problems()` ends each cycle by calling
  `arena_difficulty_histogram.persist_difficulty_histogram()` to snapshot the
  catalogue-wide difficulty distribution; see below.

---

## `arena_difficulty_histogram.py`

Purpose:
- bucket the internal difficulties (`[1, 100]`) computed by one
  `arena_rating.rate_all_problems()` cycle into a 20-bin histogram over the
  `[0, 10]` display scale and persist the snapshot, so the Arena `/help/rating`
  page can show a current catalogue-wide distribution chart without an
  aggregate query at request time

Canonical location:
- `shared/services/arena_difficulty_histogram.py`

Main entrypoints:
- `build_difficulty_histogram(difficulties: list[int]) -> dict` — pure bucketing,
  20 bins of width 0.5 over the display scale
- `persist_difficulty_histogram(session, difficulties, computed_at)` — builds the
  payload and upserts it into the singleton `arena_rating_cycle_state` row
  (`id = "singleton"`); does not commit, called once per cycle from
  `rate_all_problems()`

Notes:
- the Arena read side is `arena/routes/help.py`
  (`arena_help_difficulty_distribution`, `GET /help/rating/difficulty-distribution`)

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
- only judged submissions that count toward a problem are aggregated: the problem
  owner's own submissions are excluded, and user roles do not affect the rule,
  matching the rating and public solver-count rules
  (`shared/services/arena_query_helpers.counts_toward_problem_rating`)
- verdict and language distributions cover those judged submissions (active = most
  recent non-`SUPERSEDED` judgment per submission)
- per-language wall-time / peak-memory tables and the wall-time histogram
  (`HISTOGRAM_BINS = 20` bins over `[0, time_limit_ms]`) cover **AC submissions only**

Notes:
- does not commit; the caller owns the transaction
- the Arena read side is `arena/services/problem_stats_service.py`, which only reads
  the latest snapshot

Secondary entrypoint (per-user statistics):
- `compute_all_user_statistics(session) -> int` — rebuilds every row in
  `arena_user_statistics` (one JSON snapshot per user with at least one judged
  submission) and returns the number of users written. Powers the verdict and
  language doughnut charts on the Arena public profile page. No rating
  exclusion applies: the user's own submissions all count toward their own
  statistics. Driven by `rating.loops.run_user_stats_loop` on the same
  `STATS_INTERVAL` timer.
- the Arena read side is `arena/services/user_stats_service.py`, which only
  reads the latest snapshot

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

Main entrypoints:
- `active_arena_judgment_subquery() -> Subquery` — most-recent non-`SUPERSEDED`
  judgment timestamp per submission (`submission_id`, `max_created_at`); join it back
  against `arena_submission_judgments` on `(submission_id, created_at)` to pick the
  active row
- `counts_toward_problem_rating(user_id_col, owner_id) -> ColumnElement[bool]` —
  whether a submission counts toward problem rating; used by
  `shared/services/arena_rating.py`, `shared/services/arena_stats.py`, and
  `arena/services/problem_browse_service.py`
- `is_excluded_from_problem_rating(user_id, owner_id) -> bool` — Python-side
  counterpart used by `arena/services/submission_service.py` and
  `autojudge/db/_arena_submission.py`

Reused by:
- `arena/services/live_feed_service.py`, `arena/services/submission_list_service.py`,
  `arena/services/arena_problem_set_service.py`,
  `arena/services/arena_batch_feedback_service.py`,
  `arena/services/arena_problem_set_report_service.py`,
  `shared/services/arena_stats.py`, and the badge siblings
  `shared/services/arena_badge_data.py`, `shared/services/arena_badge_rules.py`,
  `shared/services/arena_badge_rules_catalogue.py`,
  `shared/services/arena_badge_rules_sets.py`, and
  `shared/services/arena_badge_rules_sequences.py`

---

## `arena_badges.py`

Purpose:
- award Arena gamification badges (`ArenaBadge`) into the append-only `arena_user_badges`
  ledger from Accepted submissions
- the periodic loop that drives this lives in the `rating/` worker module
  (`rating.loops.run_badge_assignment_loop`), on its own `BADGE_INTERVAL` timer

Canonical location:
- `shared/services/arena_badges.py` — public API and the per-submission evaluator
- `shared/services/arena_badge_data.py` — sibling: state/cursor access, the Accepted and
  non-AC batch queries, per-(user, problem) history, and the badge-insert helper
- `shared/services/arena_badge_rules.py` — sibling: aggregate/dynamic rules (streaks,
  CLEAN_CODE, FULL_CLEAR, distinct-problem-count tiers)
- `shared/services/arena_badge_rules_catalogue.py` — sibling: catalogue aggregate rules
  (distinct-language tiers, FIRST_SOLVER, ROCK_CRACKER)
- `shared/services/arena_badge_rules_sets.py` — sibling: problem-set scoped rules
  (FIRST_TO_HAND_IN, ALMOST_LATE)
- `shared/services/arena_badge_rules_sequences.py` — sibling: ordered sequence and burst
  rules (THIS_IS_THE_WAY, LOCO_CODER)

Main entrypoints:
- `compute_badge_awards(session, *, full_reconcile=None, reconcile_interval_seconds=86400,
  lookback_seconds=600, now=None) -> int` — evaluates the relevant Accepted submissions and
  returns the number of badge rows newly inserted. When `full_reconcile` is `None` the mode is
  derived from the persisted `arena_badge_cycle_state.last_reconciled_at` so a process restart
  does not force a reconciliation. Does not commit; the caller owns the transaction.
- `award_badge(session, user_id, badge) -> bool` — inserts one badge with
  `ON CONFLICT (user_id, badge) DO NOTHING`; returns whether a new row was written.

Model:
- two passes share one implementation: an **incremental** pass each cycle processes active AC
  judgments and active non-AC DONE judgments with `finished_at >= watermark − lookback`
  (best-effort, bounded by the singleton `arena_badge_cycle_state` watermark), and a periodic
  **full reconciliation** pass (`full_reconcile=True`) re-evaluates all relevant history.
  Correctness rests on the reconcile pass; every operation is idempotent (unique
  `(user_id, badge)`, advance-only/award-only logic, order-independent streak recompute), so
  reprocessing an event is harmless. The watermark advances from the maximum `finished_at` seen
  in either the AC or non-AC batch.
- badge eligibility uses **only** the active-judgment selection
  (`active_arena_judgment_subquery`); it does **not** apply
  `counts_toward_problem_rating` by default. Rule-specific filters still apply,
  such as `FIRST_SOLVER` excluding the problem owner.
- event ordering is canonical `(submission.created_at, submission.id)`; per-submission badges use
  the AC's `created_at` in the submitter's timezone (via `user_timezone.py`), while the watermark
  cursor is the judgment `finished_at`.
- CLEAN_CODE is award-only and recomputed per problem: a user qualifies whose best AC sits in the
  top 5% by wall time **or** by memory. STRIKE badges use the user's **historical maximum**
  consecutive solve-day run (recomputed into `arena_users.current_streak` / `longest_streak` /
  `last_ac_date`). The distinct-problem-count tiers (PROBLEMS_10 / PROBLEMS_25 / PROBLEMS_100 /
  PROBLEMS_500) are award-only: each user in the batch is awarded every threshold their distinct
  solved-problem count (from `arena_problem_solvers`) has crossed.
- FIRST_SOLVER joins `arena_problem_solvers` to `arena_problems` to find each affected problem's
  earliest solver who is not the problem owner. Eligibility is gated by ownership, not role: the
  owner is excluded and any other user is eligible regardless of role.
  FIRST_TO_HAND_IN and ALMOST_LATE consider only AC submissions explicitly tied to a problem set
  through `arena_submissions.problem_set_id`, with ALMOST_LATE requiring a non-null elapsed
  deadline. ROCK_CRACKER reads solve rates from `arena_problem_ratings`, THIS_IS_THE_WAY scans a
  user's ordered DONE verdict history for a 15-problem distinct AC run, and LOCO_CODER scans
  non-AC DONE verdict bursts independently of the AC batch.

---

## `problem_judgeability.py`

Purpose: one decision for "can this problem be judged in its current state",
applied at every execution gate.

Incomplete drafts are explicitly allowed. A problem may be saved with no test
cases and no validator source, which is what makes "choose a strategy, create,
then upload the validator" possible; nothing about judgeability is enforced at
save time. The gates -- submission creation (both domains), Web solution tests,
Arena enablement, AutoJudge dispatch, and full export -- apply this rule
instead.

| Symbol | Behavior |
| --- | --- |
| `ProblemJudgeabilityFacts` | Frozen bundle of domain-neutral facts: the stored strategy, total and secret case counts, how many cases are missing an expected-output *file*, and whether an active `VALID` validator exists. |
| `judgeability_error(facts)` | The operator-facing reason the problem cannot be judged, or `None`. |

The rules:

- **Standard** -- at least one case, and every case has a present
  expected-output file. A present-but-empty file is valid output.
- **Interactive** -- at least one secret input-only case and an active `VALID`
  validator revision. A problem that lost its validator source stays interactive
  and is refused here, rather than being token-compared against cases that carry
  no expected output.
- **Anything else** (currently the reserved output checker) -- never judgeable,
  rejected explicitly rather than by falling through, so a future strategy cannot
  inherit the standard rules by accident.

The module runs no queries and imports neither `web` nor `arena`: each caller
gathers the facts with whatever query shape suits it, and the decision stays in
one place. See [ARCHITECTURE.md](ARCHITECTURE.md) for why the strategy is stored
rather than inferred.

---

## `problem_definition_view.py`

Purpose: the presentation contract for the problem *definition* editor -- what the
problem is -- so the Web and Arena editors render the same panes without either
module's route names leaking into shared markup.

`ProblemDefinitionView` is a frozen dataclass holding pre-resolved URLs and
strategy flags, in the style `shared.services.testcase_view.TestCaseRowView`
already established for the per-row test-case table. Each module builds one in its
own presentation helper (`web/routes/contest_admin_problem_view.py`,
`arena/routes/admin_problem_form_views.py`), and the shared panes read only that.

One member exists because a URL is not enough: `validator_status_template`. The
compile-status badge is itself shared
(`_partials/validator_status_badge.html`), but each module wraps it at its own
path (`admin/problems/_validator_status.html` for Web, `admin/_validator_status.html`
for Arena) to bind its own HTMX polling route -- the wrapper is also what those poll
endpoints re-render. The definition editor shows the badge read-only; the judgment
editor's validator page owns the actions.

The module owns the canonical pane vocabulary -- `metadata`, `statement`, `limits`
-- so a redirect written in one module cannot name a pane the other never renders.
Which panes a given editor *accepts* stays per-module: Arena keeps its resource
limits inside Metadata and renders no Limits pane.

`resolve_tab(raw, *, allowed)` falls back to Metadata rather than failing. A pane
is a view preference, never an authorization or correctness decision, so an
unknown value or the retired `content` alias resolves to Metadata -- the
alternative is a `404` on a page the author is entitled to see.

`MOVED_TO_JUDGMENT` is deliberately *not* an alias table: `test-cases` and
`sample-interactions` name pages that live in the judgment editor now, so the editor
redirects those requests there instead of quietly resolving them to a pane an
author did not ask for.

---

## `problem_editor_header.py`

Purpose: the chrome *both* problem-editor doors render -- the title row and the
sticky action bar -- so that moving between the definition editor and the
judgment-data editor does not move the title, resize the buttons or shift the
page margins. The two doors used to draw their own headers, and an author
switching between them saw a different page shape each time.

`ProblemEditorHeaderView` is a frozen dataclass holding the title, subtitle, the
`EditorAction` submitters, a Back link, trailing `EditorLink`s (the cross-link to
the other door, a module extra such as Contest's "Test a solution", the
problem-package download) and the read-only strategy badge. Both
`ProblemDefinitionView` and `JudgmentShellView` carry one, and
`_partials/problem_editor_header.html` is the only template that renders it.

An action is a **submitter**, not a link, because both doors post. The definition
editor submits the detached `#edit-form` its panes attach to; the judgment editor
has no such form, so the partial renders its own from `state_form_url` and the
buttons attach to that. `form_id` names whichever applies, so the partial does not
branch on which door it is drawing.

`publish_state_actions()` words the enable/disable pair once, because Arena offers
that same choice from both doors and two templates would drift. Contest passes no
actions from its judgment editor at all: it has no publication state to set there,
and the bar simply holds the Back link and the trailing group.

`EditorNotice` feeds one notice slot, rendered by the definition shell between the
action bar and the pane strip -- the same place the judgment shell puts its
read-only reason. A notice rendered *above* the title would push one door's chrome
below the other's, which is the drift this module exists to remove.

---

## `judgment_page_view.py`

Purpose: the presentation contract for the *judgment-data* editor -- what judging
runs against. Test cases, the custom validator and sample interactions are pages
rather than panes, because a problem can carry many large cases and every action
on existing data posts immediately.

- `JudgmentShellView` — the chrome each page renders inside: the shared
  `ProblemEditorHeaderView`, the page navigation, the consolidated
  `JudgmentReadinessView`, and the links back to the definition editor and the
  problem list. The shell also renders the active page's body itself, inside the
  same bordered pane the definition editor's tab strip sits on, so the two doors
  cannot end up with differently shaped content areas. Modules can attach a permission-checked rejudge URL and safe
  local return path to the readiness model; a module without that operation
  renders status and guidance without an action.
- `build_judgment_readiness(...)` — derives `ready`, `pending`, or `incomplete`
  from canonical `ProblemJudgeabilityFacts`, the validator lifecycle, and
  submission presence. It therefore cannot disagree with submission,
  enablement, solution-test, or worker gates about missing expected output,
  secret interactive cases, or active-validator validity. A valid active
  validator keeps an interactive problem ready while a replacement candidate
  compiles.
- `build_judgment_nav(validator_type, urls, badges)` — which pages a strategy
  offers, in display order. A standard problem is offered Test cases alone; an
  interactive problem is offered all three; the reserved `checker` strategy is
  refused before any page is built.
- `TestCasesPageView`, `ValidatorPageView`, `InteractionsPageView` — one small
  per-page model carrying that page's own action URLs, so the shared page
  partials resolve no module route names, exactly as
  `problem_definition_view.py` and `testcase_view.py` do.

---

## `judgment_case_action.py`

Purpose: the one place a judgment action that touches **files** is staged, so the
ordering is written once instead of once per endpoint.

`stage_case_action(...)` takes the current cases and a single-op
`PendingTestCaseOps`, plans the desired directory, opens the edit-aware swap and
returns it with the materialized cases; the caller applies the rows and finishes
with `commit_with_edit_swap`, abandoning the swap on failure. Staging is seeded by
hardlink (see `testcase_files.py`), except for a replace-all, which claims nothing
from the seed and passes `seed=False`.

If staging itself fails it removes its own staging paths before re-raising: the
caller never receives the swap, so it has nothing to call `abandon_swap` on, and
staging is where a problem's entire test data is written. Both this cleanup and
`abandon_swap` are cancellation-shielded; an already-cancelled request cannot
interrupt rollback and leave a large staging tree behind.

Actions that change **no file** deliberately do not open a swap: the sample
toggle, validator upload and removal, and every sample-interaction mutation commit
directly under the problem row lock, because there is nothing on disk for a failed
commit to leave behind. That is a rule of the design, not an oversight; the
precedent is Web's limits-only save.

---

## `durable_fs.py`

Purpose: make the editor's filesystem writes as durable as the database rows they
are compared against.

Recovery decides from a committed `artifact_generation` whether to keep the new
artifacts or restore the quarantined ones. That comparison is meaningless if the
files never reached the device: after a host crash, a committed generation
pointing at content the kernel still held in cache is the one state recovery
cannot detect, because the fence tells it the Save landed.

- `write_file_durably(target, content)` — writes a temporary sibling, flushes it,
  and renames it over the target. It never opens the target, because it may be a
  hardlink into the live directory (that is how staging is seeded).
- `copy_file_durably(source, target)` — streams the hardlink fallback into a new
  staging file and flushes it without loading a potentially multi-gigabyte case
  into memory.
- `fsync_directory(path)` / `fsync_parents(paths)` — flush the directory entries a
  rename created, once per directory rather than once per artifact. Best-effort:
  a filesystem that cannot flush a directory handle keeps the weaker guarantee it
  already had rather than failing the Save.

Call sites: every test-case write, the staged statement, the staged directory
after materialization, and the promotion and rollback renames.

---

## `editor_urls.py`

Purpose: build problem-editor return URLs that carry a tab, an existing query,
and a row anchor at the same time.

Every route that sends an author back to the tabbed editor states which tab they
land on, a per-row route also states which row, and Arena carries the problem
list's filter state through the editor -- so one URL can need all three parts.
`editor_url(base, tab=..., anchor=...)` merges the query and re-attaches the
fragment last, because the obvious `f"{url}?tab=…#tc-{id}"` is wrong twice: a
second `?` on a URL that already has a query produces a parameter no server
parses, and appending after a fragment puts the parameter inside the fragment,
where it is silently dropped.

---

## `validator_choice.py`

Purpose: resolve the validation strategy named by a creation-chooser route
parameter, so both modules' `/new/{validator_type}` routes agree on what the
segment means.

A problem's strategy is chosen once, on the way into the creation editor, and the
chooser names it **in the URL** rather than in an editable form field -- so no
crafted POST body can select or change one. Both creation POSTs read the route
parameter and ignore any `validator_type` the body carries.

| Symbol | Behavior |
| --- | --- |
| `CHOOSABLE_VALIDATOR_TYPES` | The strategies a chooser may open an editor for: `standard` and `interactive`. |
| `VALIDATOR_CHOICE_UNAVAILABLE_MESSAGE` | The operator-facing message for the reserved strategy. |
| `resolve_validator_choice(raw)` | Return the choosable strategy the segment names. Raise `UnavailableValidatorChoiceError` for `checker` and `UnknownValidatorChoiceError` for anything else. |

Three outcomes must stay distinguishable, which is why this resolves through two
exception types rather than an `Optional`: a choosable strategy opens its editor;
the reserved `checker` value parses but has no runtime in this release, so the
caller redirects back to the chooser with an explanation rather than pretending
the URL does not exist; anything else answers `404`.

The route parameter is deliberately typed `str` at the handler and resolved here.
Typed as the enum, FastAPI answers `422` before the handler runs, and
`shared.error_handlers` renders that as a neutral JSON `{"error": ...}` body --
wrong for an HTML admin page, and it would make the reserved and unknown outcomes
indistinguishable. Each module wraps this in its own
`resolve_choice_or_redirect` (`web/routes/contest_admin_problem_new.py`,
`arena/routes/admin_problem_new.py`), which owns the module's flash category and
chooser URL; Arena's additionally preserves the list-return query state across the
redirect.

---

## `validator_type_guard.py`

Purpose: enforce that a problem's stored validation strategy
(`problems.validator_type` / `arena_problems.validator_type`) is chosen once, at
creation or import, and never changes.

The strategy is the authoritative answer to "what kind of problem is this".
Before it existed, interactive-ness was inferred from the presence of a
custom-validator row, which is wrong in both directions: a problem whose
validator source was removed silently became a standard problem judged by the
token comparator, and a standard problem carrying a stale validator row looked
interactive. An interactive problem also cannot become a standard one after the
fact, because its test cases carry no expected output and its contestants were
shown sample interactions rather than sample cases.

| Function | Behavior |
| --- | --- |
| `raise_if_validator_type_changed(instance)` | Raise `ValidatorTypeImmutableError` when a *persistent* instance's `validator_type` holds a changed value. A transient or pending instance is exempt -- stating the strategy at creation is the one permitted write -- and so is reassigning the identical value, which records no history. |
| `guard_validator_type_immutability(session, problem_types)` | Apply the check to every dirty instance of `problem_types` in a session being flushed. |

Both domains register the guard on SQLAlchemy's `before_flush`:
`web/models/problem.py` folds it into its existing listener, and
`arena/models/arena_problems.py` adds its own. The service layer refuses a
disagreeing value on its update paths as well; both layers are needed, because a
service guard never sees an assignment that bypasses it.

**Stated boundary:** this covers every supported application workflow -- forms,
routes, services, packages, backups, and admin tooling -- but not direct SQL.
Anyone with a `psql` prompt can still change a strategy, and nothing in this
release detects that. A database trigger was considered and deliberately not
adopted: the repository has no trigger precedent, and introducing the first one
as a side effect of this change would set an unreviewed precedent.

---

## `problem_package/`

Purpose:
- own the **entire** problem-package format — reading, writing, staging, and crash recovery — so
  the Arena and Contest domains cannot drift apart on any format decision

Before this package existed, `shared/tc_zip.py` factored out test-case parsing and everything else
(integer coercion, null semantics, string lengths, UTF-8, image resolution, archive safety, export
field sets) was re-implemented independently on each side. The drift was observable: a "no limit"
Contest package silently became 64 KiB on Arena, a binary test case was rejected by one importer
and accepted by the other, and over-long metadata reached the driver as a `DataError` instead of a
message.

Canonical location:
- `shared/services/problem_package/`

| Module | Responsibility |
| --- | --- |
| `constants.py` | `FORMAT_VERSION`, field-length caps, archive ceilings, member regexes |
| `errors.py` | `PackageError(ValueError)`, frozen `PackageWarning(code, message)` |
| `model.py` | the frozen slotted records both domains consume |
| `metadata.py` | `problem.json` parse → validate → defaults |
| `preflight.py` | archive safety scan and member classification |
| `extraction.py` | streaming extraction with SHA-256 accounting and manifest verification |
| `content.py` | statement (Markdown / PDF), explanation, and test-case content validation |
| `testcase_archive.py` | shared multi-case classification, pairing, and bare-ZIP parsing |
| `reader.py` | ZIP path → `StagedPackage` |
| `writer.py` | `ProblemPackage` + profile → ZIP on disk |
| `staging.py` | `PackageStagingArea`, reversible `ArtifactPromotion`, shared path guards |
| `quarantine.py` | `QuarantiningPromotion` — displace-and-restore promotion for content that already exists |
| `promotion.py` | `ArtifactPromoter` — orders an **import's** filesystem writes against the transaction |
| `edit_swap.py` | `EditArtifactSwap` — the same ordering for an **editor Save**, quarantining what it displaces |
| `journal_model.py` | the journal record: kind, entries, promotion state, generation fence |
| `journal.py` | the crash-safe on-disk journal format and the reconciliation loop |
| `journal_recovery.py` | what recovery deletes, keeps, or restores for one stale journal |
| `reconcile.py` | resolving stale journals at startup and before each import |
| `upload.py` | chunked upload spooling, temp export paths, safe download filenames |
| `merge.py` | `append_package_folder` — splices one built package ZIP into a containing archive (contest backups, public problem sets) |

Main entrypoints:
- `read_problem_package(zip_path)` — a context manager yielding a `StagedPackage`: the immutable
  `ProblemPackage` plus the staging area holding its payloads. Leaving the context removes every
  temporary path, which is why the live handle lives *outside* the frozen value object
- `build_package(package, destination, *, profile, require_importable=True)` — writes `"full"`
  (importable, every version-2 key and optional editorial) or `"public"`
  (contestant statement bundle, no `problem.json` or editorial)
  to a path on disk. `require_importable=False` waives the version-2 completeness rules for a
  caller whose package is not a restore source of record; the contest backup exporter and the
  public problem-set exporter use it
- `append_package_folder(dest_path, prefix, package_path)` — copies every member of a built
  per-problem package into the outer archive under `prefix`, chunked so a multi-problem archive's
  peak disk usage stays at the outer archive plus one package
- `ArtifactPromoter` — `stage` → `promote` → commit → `finish`, with `rollback` deleting exactly
  what was promoted when the commit fails
- `EditArtifactSwap` / `commit_with_edit_swap(session, swap)` — the edit-aware counterpart:
  `stage_test_cases` / `stage_file` → `write_journal` → `promote` → commit → `finish`, with
  `rollback` **restoring** what the promotion displaced
- `bump_artifact_generation(session, domain, problem_id)` — the fence value a Save journals
- `EditArtifactSwap.stage_removal(target, root)` — a Save can end with *less* on disk than it
  started with (a Contest statement switched from PDF to Markdown drops the PDF), and that deletion
  is as reversible as a replacement: promotion parks the file in the same quarantine, rollback puts
  it back, and `finish()` deletes it. Journalled as an ordinary entry with an additive, optional
  `removal` flag, so recovery still decides from the quarantine on disk and a journal written
  without the key resolves identically
- `reconcile_import_journals(session, domain=..., testcase_dir=..., statement_dir=...)`
- `spool_upload(upload)` / `temporary_package_path()` / `safe_package_filename(title)`

`pypdf` is declared in `shared/pyproject.toml` rather than Web's: the shared reader owns PDF
statement validation, and a lazy import of a dependency another package declares would make
shared behavior depend on which module happened to be installed.

### The strategy discriminator (format version 2)

`PackageMetadata.validator_type` carries the problem's validation strategy, and is **always**
populated regardless of the package's own version: version 2 states it, and a version-1 package has
it derived from `custom_validator` presence by the parser. Consumers therefore never branch on the
version to learn the strategy, and `ProblemPackage.is_interactive` reads that normalized value
rather than `validator is not None`.

The rules are enforced in two places, and the split is structural rather than stylistic:
`metadata.py` enforces what `problem.json` alone can see (a `standard` problem declares no
validator; an `interactive` one declares a validator), while `reader.py` enforces what only the
extracted archive index can see (a `standard` problem ships no `validator/` members). `checker` is
rejected in `metadata.py`, before any `ProblemPackage` is returned, so neither domain importer
implements that check and the two cannot diverge on it.

See [PROBLEM_PACKAGE_FORMAT.md](PROBLEM_PACKAGE_FORMAT.md) for the wire format.

### Additive editorials in format version 2

`PackageMetadata.editorial` holds an optional declaration naming the fixed
`editorial.md` member and its SHA-256 digest; `ProblemPackage.editorial` holds
the validated Markdown text. The content uses the statement Markdown policy and
512 KiB ceiling.

The editorial digest is intentionally independent of the legacy top-level
manifest. A deployed version-2 reader ignores the unknown metadata property and
safe member, then verifies the unchanged legacy manifest successfully. Updated
readers exclude `editorial.md` from that manifest and verify its nested digest
before either domain persists the text. Full exports include editorials; public
bundles never do.

An undeclared `editorial.md` is ignored with an `undeclared_editorial` warning,
so a legacy package that used the safe filename remains readable without having
its content silently imported as an official editorial. For third-party
compatibility, readers also accept a redundant top-level manifest entry for
`editorial.md` when that digest matches; NOCA exports never write one.

### No archive in RAM, in either direction

Both routes previously read the whole upload into memory with no size cap, then opened the ZIP
twice. Now the route spools the upload to an owned temporary file in 64 KiB chunks while enforcing
the 256 MiB ceiling, the archive is opened **once**, and recognized members are streamed to the
staging area. Exports mirror this: the writer copies stored files into the archive in 1 MiB chunks
at an owned temporary path, and the route serves it through a `FileResponse` whose background task
deletes it. If archive construction fails or is cancelled before the response takes ownership, the
temporary-path context removes the partial archive. Newline normalization and the UTF-8 check are
equally incremental — a `\r` or a partial multi-byte sequence landing on a chunk boundary is carried
into the next chunk.

Scanning, extracting, hashing, and validating are blocking work, so the async routes call
`open_problem_package` through `anyio.to_thread.run_sync` and own closing the staging area
afterwards; `read_problem_package` is the synchronous context-manager form that owns it for you.

### Staging lifecycle and promotion

Arena used to commit rows **before** writing test-case files; Contest wrote files **before**
committing. Either order leaves orphans when the other half fails. Both now do the same thing:

1. the reader extracts validated members into a staging area;
2. before any database mutation, artifacts are prepared in hidden sibling locations under their
   **configured final roots** — note these are *two different roots* on Contest
   (`PROBLEM_STATEMENT_DIR` holds standalone files, `PROBLEM_TESTCASE_DIR` holds per-problem
   directories) — so every promotion is a same-filesystem rename;
3. rows are built and flushed in a caller-owned transaction;
4. staged artifacts are **promoted**, and only then is the transaction committed;
5. a failed commit deletes every promoted artifact, and any remaining staging path is removed on
   every exit path.

`commit_with_promotion` owns that sequence, and its asymmetry is the point: anything that fails
**before** the commit rolls back and deletes exactly what was promoted, while anything that fails
**after** it deletes *nothing*. The rows are durable at that point, so their files must stay; a
post-commit cleanup failure is logged and leaves the journal for reconciliation, which looks the
problem up, finds it, and simply clears the journal. Every filesystem step runs in a worker
thread — copying and renaming a problem's whole test-case directory is blocking I/O.

Validator compile tokens stay in the result and are enqueued **after** the commit.

### Edit-aware artifact swap

An import may delete whatever it finds at its target, because nothing was there before it. An edit
may not: the author's existing test cases are live data. Reusing `ArtifactPromotion` for a Save
would therefore lose them — promote deletes the problem's test-case directory, the commit fails,
rollback deletes the replacement, and the problem is left with **no test-case files at all** while
its rows still describe the old ones. That is strictly worse than the commit-then-write ordering it
replaces, so the edit path is a **sibling** of the import path, added alongside it; import behavior
is unchanged.

`EditArtifactSwap` differs from `ArtifactPromoter` in three ways, and each one is load-bearing:

1. **Quarantine, don't delete.** `QuarantiningPromotion.promote()` renames existing content to a
   hidden sibling (`.noca-pkg-<token>-prev-<name>`, same filesystem by construction) instead of
   removing it. `rollback()` renames it back; `finish()` deletes it only after the commit succeeded.
2. **Journal the quarantine — before promoting, and for every entry.** The quarantine path is
   deterministic and is written into the journal *ahead of* the first rename, because the window
   between "target renamed away" and "journal says where it went" is precisely the unrecoverable
   one. It is recorded even for a target that does not exist yet, because target existence can
   change in between — the satellite routes still write into the live directory — and recovery must
   not be told `None` for a target that turned out to have content. What decides at recovery time is
   therefore **the quarantine's presence on disk**, which cannot be stale; the journaled
   `target_existed` flag only separates the two remaining cases, "promoted over nothing" (remove the
   promoted content) from "the displacement never completed" (the target is still the original, so
   leave it).

   For the same reason, `QuarantiningPromotion.promote()` records the displacement **before** the
   rename that would strand it. Recording it afterwards leaves a window in which the first rename
   succeeded, the second failed, and rollback knows nothing about the quarantine — orphaning the
   author's files in a hidden path while the journal that could have named them is cleared.
3. **Fence on the generation, not on existence.** The journal records the `artifact_generation` the
   problem row will hold once the Save commits, and the Save increments it in the same transaction.
   Recovery compares the stored value: `stored >= expected` means the commit landed (or a later Save
   superseded it, which calls for the same action), so the new artifacts stay and the quarantine is
   dropped; a strictly lower value means the commit was lost, so the quarantined originals are
   renamed back.

A Save materializes the **complete** desired test-case directory in staging —
`copy_testcase_files_into` seeds it with hardlinks to the problem's current files,
falling back to a durable streamed copy where links are unavailable. A Save touching
one case therefore gets one reversible same-filesystem directory swap without copying
unchanged bytes on the normal path. There is **no size or count exception**: a Standard
case is two files plus row metadata, so every definition Save and immediate judgment
action that changes files uses the swap rather than writing into the live directory.

### Promotion journal

Promotion spans multiple roots and mixes files with directories, so a marker written *inside* the
promoted directory cannot describe it. One guarded journal file per import, under
`<testcase_dir>/.noca-import-journals/`, records the problem id and domain, every staged source
path with its final target and configured root, and the promotion state
(`staged` → `promoting` → `promoted` → `committed`). It is `fsync`'d and renamed into place at each
transition and deleted on success. The directory name starts with a dot, which
`get_problem_testcase_dir` forbids in a problem id, so it cannot collide with a problem's files
and needs no configuration of its own.

A journal also records its **kind** and, for an edit, its generation fence and the quarantine path
of each entry. The format is at **version 2**; version 1 — every journal written before the edit
swap existed — is still read, as an import journal with no quarantine and no fence, so an attempt
interrupted by the upgrade itself is still reconciled. Any other version is left in place.

Reconciliation runs **at application startup** (the `web` and `arena` lifespans, alongside the
existing reaper registrations) **and before each import**, not only when another import happens.
Editor Saves journal into the same directory, so both passes already cover them. Journals are
resolved **newest-first**: two lost Saves on one problem quarantine in sequence and carry the same
fence, so undoing them in reverse order is what ends at the author's true original. Two things make
that order dependable: Saves on one problem are serialized by the `artifact_generation` row lock,
taken at the Save's flush before it journals; and the promotion token leads with a nanosecond
timestamp, so when a coarse-granularity filesystem reports both journals with the same `mtime`, the
file-name tiebreak preserves the same order rather than picking one at random. A journal that
raises is logged and skipped rather than deferring every journal behind it — which at the
pre-import call site would otherwise abort the import itself. Each journal is resolved by the signal
its kind names:

- **import** — look up the problem row. If it exists the commit won, so clear the journal; if it
  does not, delete the recorded artifacts.
- **edit** — compare the row's `artifact_generation` against the journal's fence, as described
  above. Existence cannot answer this, because an edited problem exists either way. Recovery takes
  the same problem-row guard as a Save and resolves the complete same-problem journal chain under
  that one guard; this prevents both trampling an in-flight Save and letting a new Save start while
  recovery has restored only an intermediate uncommitted predecessor. An edit journal reached
  without a generation lookup is left in place rather than resolved on the wrong signal.

Restoration, committed-quarantine deletion, and journal deletion flush the parent
directories they mutate before the recovery record is cleared. A second host crash
therefore cannot persist journal removal while losing the filesystem transition the
journal described.

Both problem tables live in the shared schema, so one query answers either question for either
domain.

**Every path read from a journal — staged, target, and quarantine — is re-validated against its
configured root before anything is deleted _or restored_**, so a corrupted or tampered journal can
never direct either outside the problem storage roots. A journal that cannot be parsed is logged and left in place rather than acted on.

### Warnings vs. errors

Anything that would lose data silently is a `PackageError` and refuses the package; anything the
reader resolves on its own is a structured `PackageWarning` carried through to the route and
flashed. This replaces the ad-hoc `skipped_language_ids` list the Contest importer used to return.
See [PROBLEM_PACKAGE_FORMAT.md](PROBLEM_PACKAGE_FORMAT.md) for the full contract.

### What the reader deliberately does not decide

Some checks depend on the *target install* and cannot be made once, centrally, without lying about
what a package means somewhere else. Those stay with the importer, run before anything commits, and
are documented as such in the format reference:

- whether the validator's `language_id` is an active judge language here;
- whether the named categories exist (Arena drops unknown ones, Contest creates them);
- whether the contest allows the languages in `language_limits`;

`scripts/validate_problem_package.py` says so explicitly rather than implying a package it accepts
will import anywhere.

### Relationship to `tc_zip.py`

`shared/services/problem_package/testcase_archive.py` owns multi-case member classification,
logical-duplicate rejection, layout validation, pairing, ordinal remapping, and line-ending
normalization. The full package reader uses the same index over staged paths, while bare bulk
test-case uploads use its byte-oriented parser. `shared/tc_zip.py` re-exports that public parser
for compatibility and keeps only the separate single-case ZIP helpers.

Reused by:
- `arena/services/admin_problem_io_service.py`, `arena/routes/admin_problem_io.py`,
  `arena/routes/problems.py` (public export), `web/services/problem_service/`
  (`importing.py`, `files.py`), `web/routes/contest_admin_problem_io.py`,
  `web/routes/contest_problems.py` (public export),
  `web/services/contest_backup_service/export.py`,
  `shared/services/sample_problem_package.py`, and `scripts/validate_problem_package.py`

---

## `problem_image.py`

Purpose:
- own the problem illustration image contract shared by the Arena and Contest problem domains:
  the fixed file-size and dimension limits, the extension/MIME maps, the upload processor, and
  the staged-image loader, so the two domains cannot drift apart when the rules change

Both domains store the image in the database as base64 text plus its MIME type and an optional
caption (unlike test cases, which live on the filesystem), render it as a `data:` URI with no
serving route, and round-trip it through the problem package ZIP as a root-level `image.<ext>`
member declared by the `image` key of `problem.json`.

Problem illustrations accept GIF, JPEG, PNG, and WebP content. GIF support is scoped to this
service, so the image-processing service's default profile-photo and logo allowlist is unchanged.
Re-encoding preserves animated GIF frames and timing.

Canonical location:
- `shared/services/problem_image.py`

Main entrypoints:
- `MAX_PROBLEM_IMAGE_BYTES` — fixed 2 MiB per-problem file-size limit
- `MAX_PROBLEM_IMAGE_WIDTH` and `MAX_PROBLEM_IMAGE_HEIGHT` — fixed 2048 × 2048-pixel
  limits that keep package validation independent from deployment configuration
- `process_problem_image_upload(image_service, upload) -> (base64, mime)` — validates a form upload
- `load_staged_image(image, image_service) -> (base64 | None, mime | None)` — validates the bytes
  of the image the shared package reader already resolved and staged. Deciding *which* archive
  member is the image (and rejecting a `problem.json` that names one the package does not carry)
  belongs to `problem_package`; what is left here is deciding whether the bytes are an acceptable
  image
- `export_image_filename(mime) -> str` — the `image.<ext>` package member name

Reused by:
- `arena/routes/admin_problem_form_views.py`, `arena/services/admin_problem_io_service.py`,
  `web/routes/contest_admin_problem.py`, `web/routes/contest_admin_problem_edit.py`, and
  `web/services/problem_service/` (`files.py` export, `importing.py` import)

Paired presentation:
- `shared/template/_partials/problem_image_field.html` (admin form field) and
  `shared/template/_partials/problem_image_figure.html` (public display), plus
  `problem-image-preview.js` and the `.noca-problem-*` rules in `common.css`

---

## `balloon_assets.py`

Purpose:
- render the small balloon and star SVG artwork with an optional problem letter and load the
  fixed Gold, Silver, and Bronze medal SVGs as the framework-agnostic source of truth, so any
  runtime can serve identical artwork from its own origin

This module has no web-framework dependency: invalid input raises `ValueError`, and each caller
maps that to its own HTTP error. The SVG templates ship alongside it under
`shared/services/assets/`.

Canonical location:
- `shared/services/balloon_assets.py`

Main entrypoints:
- `normalize_hex_color(color) -> str` — normalize a 3- or 6-digit hex color (optional `#`) to
  lowercase `#rrggbb`, else `ValueError`
- `normalize_letter(letter) -> str` — validate ASCII letters and return the first, uppercased
- `render_balloon_svg(fill_color, letter=None) -> str` / `render_star_svg(fill_color, letter=None) -> str`
  — memoized renderers returning the SVG document string
- `render_medal_svg(band) -> str` — return the Gold, Silver, or Bronze SVG, else `ValueError`
- `medal_band_for_rank(rank, *, gold, silver, bronze) -> MedalBand | None` — map a 1-based
  ranking position to its medal band. Each cutoff is the last rank in its band, tried gold →
  silver → bronze; a cutoff of `0` disables that band and a rank below 1 earns nothing. Used
  by the animator reveal projection (per-site cutoffs) and by Arena's ranking pages
  (`NOCA_ARENA_RANKING_MEDAL_*_CUTOFF`)

Reused by:
- `animator/routes/assets.py` and `web/routes/assets.py` (thin `/assets/balloon|star|medal`
  routes with application-specific cache headers)

---

## `audio_signature.py`

Purpose:
- own, in one place, which audio formats NOCA accepts and how they are named, so the runtime
  that *stores* a clip and the runtime that *serves* it can never disagree

Two runtimes validate the same bytes at different times: Web validates an upload before storing
it, and the animator re-validates the stored payload before serving it to a ceremony projector.
If those two ever accepted different sets, a clip could be stored and then be unplayable — or be
served under a type the uploader never validated. Detection is by **file signature**
(`puremagic`), never from a caller-supplied MIME claim, because the bytes are the only
trustworthy description of content the validating code did not produce.

This module has no web-framework dependency: rejection raises, and each caller maps it to its own
vocabulary.

Canonical location:
- `shared/services/audio_signature.py`

Main entrypoints:
- `detect_audio_mime(content) -> str` — canonical `audio/mpeg` / `audio/ogg` / `audio/wav`
- `SUPPORTED_AUDIO_MIME_TYPES` — detected-to-canonical mapping (MP3, OGG, WAV only)
- `AudioSignatureError` (a `ValueError`) with subclasses `UnrecognizedAudioError` (type could not
  be identified, or empty content) and `UnsupportedAudioError` (identified but not accepted;
  carries `detected_mime`)

Reused by:
- `web/services/user_media_service.py` — maps each subclass to the message its upload form
  already renders
- `animator/services/team_audio_service.py` — maps the base class to `None`, which its route
  answers as `404`: a clip it cannot vouch for is treated as no clip at all

---

## `user_timezone.py`

Purpose:
- resolve an IANA timezone name from an Arena user's `country_code` / `subdivision_code` so both
  the Arena HTTP layer and the rating worker share one mapping without the worker importing from
  the `arena` package

Canonical location:
- `shared/services/user_timezone.py`

Main entrypoint:
- `timezone_name_for_country(country_code, subdivision_code) -> str` — exact subdivision match,
  then a curated country default, then the first `pytz` country timezone; falls back to `"UTC"`

Reused by:
- `arena/services/user_timezone_service.py` (which adds user-object and datetime helpers) and
  `shared/services/arena_badge_data.py` (`timezone_name_for_country`)

---

## `sse_refresh.py`

Purpose:
- own the Server-Sent Events refresh loop shared by the web contest live feed, the
  Arena live feed, and the Arena per-user submission-status stream, so the subtle
  async lifecycle (heartbeat, reconnect, task
  cancellation, generator cleanup) cannot drift between the routes

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
- `arena/routes/user_submission_status.py` (per-user submission-status SSE stream
  using `iter_refresh_events` with `should_emit` filtering on owned submission ids
  and `emit_initial_ping=True`)

Frontend counterpart:
- `shared/static/js/live-feed-core.js` is the shared browser engine for both feeds
  (fetch/SSE/status/debounce/known-row highlight, overflow summary line, trailing-row
  fade); each app mounts it at `/static/shared-js` and supplies only a `renderRow`
  callback via `NocaLiveFeed.init(...)`
- `shared/static/js/submission-status-watcher.js` is the browser counterpart of the
  `arena/routes/user_submission_status.py` stream: it consumes those `refresh` pings,
  refetches the authoritative status snapshot, and falls back to a poll. Both Arena
  live-status pages use it via `NocaSubmissionStatusWatcher.watch(...)` and supply
  only their own DOM updates (see the shared static JS section below)

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
- `noca-math-shield.js`: shields LaTeX math bodies from Marked, and restores
  them afterward. Marked applies CommonMark's generic backslash-escape rule to
  `$…$`/`$$…$$` content exactly like any other text -- `\{` becomes `{`, a
  matrix row's `\\` becomes `\` -- which silently corrupted LaTeX before KaTeX
  ever saw it. `shieldMathSpans(rawMarkdown)` finds each `$…$`/`$$…$$` span
  with a genuine matching close (an inline `$…$` search is confined to one
  line; a `$$…$$` search may cross lines but never a fenced code block; an
  unmatched opening delimiter is left completely untouched, so a stray prose
  dollar such as "Price: $10" never disables Markdown formatting for the rest
  of the document) and replaces its body with an opaque, single-line,
  Private-Use-Area-guarded placeholder token before Marked ever parses the
  source -- opaque so Marked cannot misinterpret any character inside it, and
  single-line so `noca-markdown.js`'s `breaks: true` cannot split a multiline
  body across a `<br>`. `restoreMathSpans(html, spans)` puts every body back
  after `marked.parse()` and `DOMPurify.sanitize()` have both run, HTML-escaping
  only `&`, `<`, and `>` (nothing else needs escaping in text content) and using a
  collision-free global replace since each shielded occurrence gets its own
  token. Used exclusively by `noca-markdown.js`'s `toHtml()`; every page that
  loads `noca-markdown.js` must load this module first (see below), and its
  absence degrades `toHtml()` back to the pre-shielding behavior rather than
  failing.
- `noca-markdown.js`: **the single Markdown rendering pipeline for every NOCA
  surface** -- problem statements (Web and Arena, page and print), sample
  test-case explanations, Arena editorials, legal documents, AI reviews, and
  class batch feedback. It runs, in order: `NocaMathShield.shieldMathSpans()`
  -> `NocaMarkdownDirectives.prepareMarkdown()` -> `marked.parse()` ->
  `DOMPurify.sanitize()` -> `NocaMathShield.restoreMathSpans()` -> DOM
  injection -> Mermaid -> KaTeX `renderMathInElement()` ->
  `NocaMarkdownDirectives.apply()`. Pages never ship their own copy of that
  sequence; they bind declaratively and the module renders every match on
  load:

  ```html
  <!-- external source: entity-escaped inside a text/plain script blob -->
  <div class="noca-markdown" data-noca-markdown="statement-src"></div>
  <script id="statement-src" type="text/plain">{{ value | e }}</script>

  <!-- in place: the element already holds its own decoded source text -->
  <div class="noca-markdown" data-noca-markdown>{{ value | e }}</div>
  ```

  The two modes differ only in entity handling: script-blob content is left
  escaped by the HTML parser and is decoded before parsing, while text held
  directly by an element is already decoded and must not be decoded twice. The
  `noca-markdown` class is also the CSS hook -- `shared/static/css/common.css`
  styles rendered Markdown through `:where(.noca-markdown, .editor-preview)`
  alone, so a new surface gets table borders, cell padding, GFM column
  alignment, and heading/table spacing by carrying the class rather than by
  being added to a hand-maintained selector list. Every optional dependency is
  feature-detected; a page missing Mermaid or KaTeX still renders Markdown, and
  a page missing `marked` leaves its containers untouched rather than blanking
  them. `window.NocaMarkdown` also exposes `toHtml()`, `enhance()`, `render()`,
  and `renderAll()` for callers that own their own element -- which is how the
  EasyMDE preview stays on this pipeline (see `problem-statement-editor-core.js`).

  `_base.html` in both web and arena loads `noca-math-shield.js` immediately
  before this module, so pages that extend it need only the container markup
  plus the vendor libraries they use; the standalone full-HTML pages (problem
  print views, the Arena editorial viewer) include both scripts themselves, in
  that order.
- `problem-edit-unsaved-guard.js`: warns before leaving with unsaved changes --
  the definition editor's Save form, and each judgment page's typed-rows form,
  which is the only state on those pages the server has not already seen.
- `confirm-submit.js`: the single home of the `data-confirm` courtesy -- a form
  carrying the attribute asks before a destructive or wholesale action. The
  listener is delegated on the document, so fragments swapped in later (htmx
  refreshes, reordered list partials) keep it with no per-page wiring. Both
  modules load it from their `_base.html`; no page may include a second copy or
  register its own `data-confirm` submit listener (two live copies would ask the
  same question twice), a coupling pinned by
  `tests/shared/test_confirm_submit_template_coupling.py`.
- `judgment-actions.js`: the two remaining courtesies the judgment-data pages
  need from the browser -- a `data-clears-typed-rows` warning when an upload
  would discard typed rows, and the per-row replace trigger that opens its row's
  hidden file input and submits that row's form. Everything on those pages is an
  ordinary form that posts immediately, so there is no client-side model to
  maintain. (`data-confirm` used to be the third courtesy; it moved to
  `confirm-submit.js` so pages outside the judgment editor get it too.)
- `tc-reorder-sortable.js`: shared drag-to-reorder for the admin test-case list
  (and the Web problem list), driven by `data-reorder-*` attributes and
  `.noca-drag-handle` / `.noca-sortable-*`; posts the move and swaps the refreshed
  list partial named by `data-reorder-target`. Focused handles also move with the
  Up/Down arrows, restore focus after the swap, and announce success or failure.
- `tc-add-row.js`: shared inline "Add test case" rows appended to `#tc-add-rows`
  and submitted by the test-cases page's own Save (`tc_in_N` / `tc_out_N` /
  `tc_explanation_N` / `tc_is_sample_N`).
- `problem-edit-validate.js`: cheap pre-submit checks (required fields, positive
  integers) so the common near-miss never reaches the server with archives
  attached, which a browser cannot re-attach afterwards. Native `invalid` events
  are captured too: a control in a hidden pane opens that pane, gets persistent
  inline feedback, and receives focus. A convenience, never a gate: every rule is
  enforced again server-side with the same pane/field mapping.
- `problem-image-preview.js`: client-side FileReader preview for the problem
  illustration field. Self-initializing on any `input[type=file][data-image-preview]`,
  so including `_partials/problem_image_field.html` is enough to get the preview in
  both the Arena and Contest admin problem forms.
- `problem-statement-editor-core.js`: shared core for the statement editor on the
  problem create/edit forms. Exposes `window.NocaStatementEditor.create()`, which
  builds the EasyMDE editor on `#stmt-md-editor` (restricted toolbar, KaTeX preview
  rendering, `noca:problem-statement-changed` dispatch) and returns a
  `StatementEditor` with `value`/`setValue`/`setEnabled`/`syncToTextarea`/
  `notifyChanged` helpers. Each module loads this first, then its own thin script:
  `arena/static/js/problem-statement-editor.js` only syncs on submit, while
  `web/static/js/problem-statement-editor.js` adds the web-only PDF/MD source
  switching (file input, "Replace with empty Markdown" button, `statement_source`).
- `clipboard.js`: copies plain text through the modern Clipboard API with an
  HTTP-compatible fallback; exposes `window.NocaClipboard.copyText(text)`, which
  always returns a Promise.
- `confetti-celebrate.js`: shared confetti burst used by the web runs page and the
  Arena profile submissions tab. Exposes `NocaConfetti.celebrate(key, options?)`
  (timed two-sided burst, deduped per key) and `NocaConfetti.burst(options)` for an
  un-deduped one-shot; inert when the tsParticles confetti bundle is absent.
- `markdown-directives.js`: applies NOCA's one-line Markdown directives to rendered
  Markdown and EasyMDE previews, converting supported directives into presentation
  classes and removing the directive paragraph; unsupported or misplaced directives
  remain visible so authors can correct them. It also owns `prepareMarkdown()`,
  the pre-parse pass `noca-markdown.js` runs after `noca-math-shield.js`'s math
  shielding. Its `FENCE_PATTERN` export is reused by `noca-math-shield.js` so a
  fenced code block is recognized identically by both passes instead of
  duplicating the regex. Directives are recognized inside `.noca-markdown` and
  `.editor-preview` containers -- the same hook the renderer and the shared CSS
  use, so the three cannot drift apart.
- `print-page.js`: binds any `[data-print-page]` control to the browser's print
  dialog; used by the standalone print-friendly problem pages in web and arena.
- `row-href.js`: makes list/table rows navigable through their row link, skipping
  interactive elements, `.arena-favorite-icon`, and any `[data-no-row-link]`
  opt-out inside the row.
- `si-add-row.js`: shared inline "Add sample interaction" rows for the Web and
  Arena judgment pages on interactive problems; appends rows to `#si-add-rows`
  submitted as `si_transcript_N` / `si_explanation_N` with that page's add form.
- `slugify.js`: URL-slug generation shared by the Contest and Arena modules
  (lowercase, strip diacritics, hyphenate alphanumeric runs); the per-surface
  stop-word policy is passed in, because the modules deliberately disagree.
- `theme-toggle.js`: light/dark theme toggle persisted under the `noca-theme`
  localStorage key.
- `contest-clock-utils.js`: shared display logic for contest countdown clocks
  (`ContestClockUtils` pure formatting helpers); used by web and the animator.
  `formatDuration` / `countdownText` own the wording, `contestPhase` /
  `phaseLabel` name the scheduled phase (upcoming / running / frozen / silence /
  past), and `countdownUrgency(nowMs, startMs, endMs)` returns how pressing the
  remaining time is -- `normal`, `warning` (≤ 30 min left), `critical` (≤ 5 min
  left, through the end moment itself) or `ended` (past it). Urgency is a
  separate axis from phase and is not derivable from it: freeze and
  answer-silence are moments an organiser scheduled, while urgency is only the
  clock running down, so a frozen contest with hours to run is `normal` and a
  never-frozen one in its last minutes is `critical`. An upcoming contest is
  always `normal` -- the wait to start is not the contest's own deadline. Each
  boundary belongs to the more urgent state, because a clock reading exactly
  30:00 is already inside the last half hour. Consumers apply it as a hook
  rather than as text (Web's navbar driver writes `data-urgency` on
  `#contest-countdown` on the one-second tick, which the
  `--noca-urgency-*` tokens colour); the countdown wording remains the
  non-colour cue, so urgency is never signalled by colour alone.
- `noca-echarts-theme.js`: registers the `noca-light` / `noca-dark` ECharts themes
  (axes, legend, tooltip, text, dataZoom, categorical palette) and hands out a
  managed wrapper via `NocaECharts.create(el)`; consumed arena-side.
- `noca-presence.js`: presence and session-keepalive client — heartbeat POST to
  keep the current user marked online, plus online-dot polling over
  `.avatar-wrapper[data-user-id]` elements; consumed by **both** arena (paired
  with `shared/static/css/presence.css`) and web. The heartbeat runs
  unconditionally because it is also what keeps a sliding auth session alive on a
  page that sits open without navigating; `data-presence-enabled="false"`
  disables the dot polling alone (an absent attribute means enabled, so existing
  consumers are unaffected).

  The two consumers configure it differently, through
  `[data-noca-presence]`. Arena points it at both endpoints and runs both jobs.
  Web has no presence domain and configures the keepalive alone: only
  `data-heartbeat-url` is required, and a config with no `data-status-url`
  polls no dots even were presence enabled. Web's element is emitted by
  `_base.html` from `web/template_globals.py::session_heartbeat_config`, aimed at
  `POST /session/heartbeat`, at `NOCA_JWT_EXPIRE_SECONDS / 4` (minimum 60 s).
- `submission-status-watcher.js`: the SSE + poll + reconcile engine behind Arena's
  two live submission-status surfaces (the profile submissions tab and the
  submission detail page), which previously implemented the same protocol twice.
  `NocaSubmissionStatusWatcher.watch(options) -> { stop }` takes `statusUrl`,
  `eventsUrl`, the `ids` to watch, and an `onSnapshot(row)` callback; each page
  supplies only its own DOM updates and its own confetti trigger. Contract:
  - the status snapshot (`arena_user_submissions_status`) is the **sole data
    source** — the SSE channel carries only `refresh` pings, never verdict data;
  - `onSnapshot` drops an id from the watch set **only when it returns an explicit
    `false`**; anything else keeps watching, so a consumer that forgets a `return`
    cannot silently stop its own updates. The watcher tears down once the set
    empties;
  - a `refresh` ping and an SSE (re)connect are debounced (250 ms) and an
    in-flight guard prevents overlapping reconciles;
  - a `401`/`403` stops streaming and polling rather than reconnecting forever;
  - the low-frequency poll (2500 ms) is not just a fallback: intermediate
    `QUEUED`/`DISPATCHED`/`JUDGING` states publish no SSE event. Where
    `EventSource` is unavailable the poll is the only channel and the watcher
    reconciles once immediately;
  - `stop()` and `pagehide` abort an in-flight reconcile (`AbortController`, plus
    post-await re-checks where it is absent), so no callback runs after teardown.

---

## Shared static CSS (`shared/static/css/`)

Stylesheet rules that were byte-for-byte identical between the web and arena
modules live once in `shared/static/css/common.css`, served by both apps through
the `static_shared_css` mount (`/static/shared-css`). Each module's stylesheet
pulls it in with `@import url('/static/shared-css/common.css')` at the top
(consistent with the absolute `/static/...` paths already used in `url(...)`
references), so no per-template `<link>` is required.

`common.css` currently holds the `.noca-icon-btn-group` segmented-button rules, the
shared `live-feed-*` rules / `live-feed-row-flash` keyframes (paired with
`live-feed-core.js`), the `.noca-transcript-*` interactive-transcript rules, and the
`.noca-problem-image` / `.noca-problem-figure` / `.noca-problem-figure-caption` /
`.noca-problem-image-preview` problem-illustration rules (paired with
`problem-image-preview.js` and the two image partials). The directory also holds
`tokens.css` — the shared design tokens, the single source of truth for the NOCA
visual identity across web and arena — plus `presence.css` and
`components-dark.css`, all imported by `common.css`. The remaining module-specific
rules stay in the per-module
stylesheets: `web/static/css/contest.css` and `arena/static/css/arena.css` keep
their own `:root` additions, `.material-symbols-outlined`, and `.live-feed-summary`
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
- `ip_reputation.py` — `IPQualityScoreIPReputationService` and the `IPReputation`
  result type

Main types:
- `NetworkService`
- `IPQualityScoreIPReputationService`
- `IPReputation` — frozen dataclass with `is_crawler`, `mobile`, `recent_abuse`,
  `fraud_score`, `proxy`, `vpn`, `tor`, `active_vpn`, and `active_tor`
- `NetworkServiceError`
- `RequestValidationError`
- `URLValidationError`
- `SSRFProtectionError`
- `ParamsValidationError`
- `HeadersValidationError`

Main entrypoints on `NetworkService`:
- `make_json_request(url, params=None, header=None) -> dict[str, Any]`
- `validate_and_parse_url(url, block_private_networks=False, allowed_schemes=None) -> ParseResult`
- `sanitize_params(params, *, max_params=100, max_depth=5) -> dict[str, Any] | None`
- `sanitize_headers(headers) -> dict[str, str] | None`
- `build_safe_request_kwargs(...) -> tuple[str, dict[str, Any], int]`
- `get_ip_from_request(request) -> str | None`
- `get_trusted_source_port_from_request(request) -> int | None`
- `is_private_network(hostname) -> bool`
- `IPQualityScoreIPReputationService.check(ip_address) -> IPReputation | None`

Reuse this module when:
- calling third-party HTTP JSON endpoints from the web or arena layers
- validating URLs or proxy-forwarded client IPs
- adding SSRF-safe outbound request behavior
- evaluating IPQualityScore IP reputation, proxy, VPN, and Tor signals

Do not reimplement:
- private-network blocking and DNS resolution checks
- request param and header sanitization
- bounded response-size handling for JSON fetches

Notes:
- `make_json_request` streams responses and enforces the `MAX_RESPONSE_SIZE` cap before parsing JSON
- SSRF protection checks both literal IPs and all resolved A/AAAA records for hostnames
- the package preserves the previous `web.services.network_utils` import surface via `NetworkService`
- IPQualityScore IP reputation is configured via `NOCA_IPQUALITYSCORE_APIKEY`. When the key is empty, or when
  a lookup fails, the service returns `None`.

---

## `geolocation.py`

Purpose:
- resolve a client IP address to structured geolocation fields for login history
- disabled gracefully when no API key is configured

Canonical location:
- `shared/services/geolocation.py`
- `web/services/geolocation.py` is a compatibility re-export shim

Main types:
- `GeolocationIP`
- `GeolocationDetails` — frozen dataclass with `country_code`, `subdivision_code`, `district`,
  `city`, `is_eu`, `as_number` (all optional)

Constructor:
- `GeolocationIP(api_key: str | None, network_service: NetworkService, logger=None)`
  - `api_key`: ipgeolocation.io API key; when `None` the service is disabled and `get_details_by_ip` always returns `None`
  - `network_service`: a `NetworkService` instance for the outbound HTTP call

Main entrypoints:
- `get_details_by_ip(ip_address: str) -> GeolocationDetails | None`
  - Parses the ipgeolocation.io v3 response into normalized, type-guarded fields, or `None` on any failure
  - `country_code`: `location.country_code2`, upper-cased; kept only when exactly two alpha chars
  - `subdivision_code`: `location.state_code`, upper-cased; kept only when prefixed by `"{country_code}-"` (e.g. `DE-BY`)
  - `district` / `city`: `location.district` / `location.city`, stripped, empty → `None`
  - `is_eu`: `location.is_eu`, kept only when a real `bool`
  - `as_number`: `asn.as_number`, normalized to a string (accepts `str` or `int`)
  - No arbitrary JSON value ever flows into a column; every field is validated defensively

Notes:
- Replaces the former `get_location_by_ip` string method (removed); callers now store the six structured columns
- Private and loopback IPs (RFC 1918, RFC 4193, etc.) always return `None` without making an HTTP request
- API errors and network failures are logged and return `None` — the caller never raises
- Configured via `NOCA_GEOLOCATION_API_KEY` in both the `web` and `arena` modules
- In `arena/main.py` the instance is exposed on `app.state.geo_service`
- In `web/main.py` the instance is injected into `AuthenticationService`
- Existing login rows are backfilled from their stored `ip_address` by `scripts/backfill_login_geolocation.py`

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
- `EmailValidationService.canonicalize(email) -> str` — strips `+tag` subaddressing
  and dots for alias-collision detection; backs the `email_canonical` column via
  `arena/services/user_registration_service.py` and `arena/models/arena_users.py`

Notes:
- backed by the `email-validator` package with `check_deliverability=False`
- used by `arena/services/user_registration_service.py` and `web` registration flows

---

## `email_reputation.py`

Purpose:
- assess whether an email address is "good" (real, non-disposable, low-fraud) via the
  [IPQualityScore](https://www.ipqualityscore.com/) email validation API
- disabled gracefully when no API key is configured

Canonical location:
- `shared/services/email_reputation.py`

Main types:
- `EmailReputationService`
- `EmailReputation` — frozen dataclass with `valid`, `disposable`, `suspect`,
  `overall_score`, `common`, `fraud_score`, `sanitized_email`

Constructor:
- `EmailReputationService(api_key: str | None, network_service: NetworkService, logger=None)`
  - `api_key`: IPQualityScore API key; when `None` the service is disabled and `check` always returns `None`
  - `network_service`: a `NetworkService` instance for the outbound HTTP call

Main entrypoints:
- `check(email: str) -> EmailReputation | None`
  - Calls `GET https://www.ipqualityscore.com/api/json/email/{key}/{url-encoded email}?timeout=7`
  - Returns a type-guarded `EmailReputation`, or `None` when disabled, on any network/JSON
    failure, or when the response body is not `success: true`
  - Every field is read defensively: booleans default `False`, integers default `0`,
    `sanitized_email` falls back to the submitted address

Notes:
- API errors and network failures are logged and return `None` — the caller never raises
- Configured via `NOCA_IPQUALITYSCORE_APIKEY` in both the `web` and `arena` modules
- Wired into the Arena signup flow: `arena/main.py` builds
  `app.state.email_reputation_service`, `arena/routes/auth_signup.py` passes it to
  `record_signup_reputation(...)`, and `arena/services/signup_reputation_service.py`
  calls `email_reputation_service.check(email)`. Also consumed by
  `scripts/backfill_email_reputation.py`

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
- `wait_for_db` runs early in the six module startup sequences that use a database:
  web, arena, autojudge, aiassistant, rating, and animator (healthmonitor has no DB)
- `wait_for_valkey` runs in web, Arena, autojudge, AI assistant, animator, and
  healthmonitor startup; the rating worker does not use Valkey

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

## `worker_heartbeat.py`

Purpose:
- container-local liveness signal for the queue-consuming workers, which have no HTTP
  surface a Docker healthcheck could probe
- read by each worker's `healthcheck.py` module, which is what the Compose healthcheck runs

Canonical location:
- `shared/services/worker_heartbeat.py`

Main entrypoints:
- `touch_heartbeat(path) -> None` — creates missing parents and refreshes the mtime
- `remove_heartbeat(path) -> None` — deletes it, tolerating an absent file or denied unlink
- `heartbeat_is_fresh(path, *, max_age_seconds, now=None) -> bool` — the probe itself
- `heartbeat_loop(path, *, interval_seconds, stop_event) -> None` — writes once immediately,
  then on the interval until shutdown

Notes:
- the Valkey worker-presence record cannot serve as the healthcheck: it is namespaced by
  worker ID, and a worker with no configured `NOCA_*_WORKER_ID` derives its ID from
  `<fqdn>:<pid>` — a value the separate healthcheck process cannot reconstruct. The file is
  local to the container, so the probe stays independent of Valkey, of PostgreSQL, and of
  the worker's identity
- a filesystem error is reported as stale rather than raised: a healthcheck that cannot read
  the heartbeat has not observed a healthy worker
- used by `rating` (`NOCA_RATING_HEARTBEAT_*`) and `aiassistant` (`NOCA_AI_HEARTBEAT_*`);
  the autojudge predates it and keeps its own equivalent in `autojudge/heartbeat.py`
  (`NOCA_JUDGE_HEARTBEAT_*`)

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

## `animator_access_service.py`

Purpose:
- own the reusable animator access-control domain logic so both Web
  administration and the animator runtime share it without importing each
  other (the animator consumes it today via `animator/dependencies.py` and
  `animator/routes/control.py`)
- update a site's validated medal cutoffs
- update a contest's validated global (contest-wide) medal cutoffs
- manage the lifecycle of scoped operator credentials (site-scoped and
  contest-global) for the reveal engine
- resolve an operator token to its authorized scope in constant time

Canonical location:
- `shared/services/animator_access_service.py`

Design:
- operates over the shared SQLAlchemy Core tables `contests`, `sites`, and
  `site_secrets`; never imports FastAPI or the Web ORM models
- one validator per medal contract, shared by both boundaries that use it (the
  route's Pydantic model and the update function) so they cannot drift: the
  strict per-site one rejects `None`, and `validate_optional_cutoffs` adds the
  all-or-nothing rule that global medals need. `validate_optional_cutoffs`
  mirrors the `ck_contests_global_medal_cutoffs` CHECK exactly, including its
  refusal of a partly filled triple
- accepts any executor exposing `execute` (an `AsyncSession` from web or an
  `AsyncConnection` from a worker), matching the other shared database services
- generates at least 256 bits of entropy with `secrets.token_urlsafe(32)`;
  persists only the fixed-length SHA-256 digest — the plaintext token exists
  solely in the return value of a create operation
- normalizes only transport whitespace on a token; never lowercases or otherwise
  transforms it
- an invalid token resolves to the single generic failure `None`, so callers
  cannot learn whether a contest or site credential exists

Main types:
- `AnimatorAccessError` — raised for invalid medal or credential input
- `SiteSecretMetadata` — digest-free DTO for listing/display (no `secret_digest`)
- `ResolvedScope` — `contest_id` plus `site_id` (`None` for a global secret)

Main entrypoints:
- `generate_operator_token() -> str`
- `normalize_token(raw_token) -> str`
- `digest_token(raw_token) -> str`
- `verify_digest(stored_digest, candidate_digest) -> bool` (constant-time)
- `update_site_medals(executor, *, site_id, contest_id, gold, silver, bronze) -> None`
- `validate_optional_cutoffs(gold, silver, bronze) -> None` — all-or-nothing
  validation for an optional cutoff triple: all three `None` (medals disabled)
  or all three set, positive, and ordered. Raises `AnimatorAccessError` otherwise
- `update_contest_global_medals(executor, *, contest_id, gold, silver, bronze) -> None`
  — writes the contest's global cutoffs; three `None`s clear them
- `list_site_secrets(executor, contest_id, site_id=None) -> list[SiteSecretMetadata]`
- `create_site_secret(executor, *, contest_id, site_id, label) -> str`
- `create_global_secret(executor, *, contest_id, label) -> str`
- `revoke_secret(executor, *, contest_id, secret_id) -> bool` — contest-scoped so
  one contest cannot revoke another's credential by id; returns whether a row was
  removed
- `revoke_all_secrets(executor, *, contest_id) -> int` — removes every global and
  site-scoped credential for one contest and returns the number removed
- `resolve_scope(executor, contest_id, token) -> ResolvedScope | None`

Reuse this module when:
- administering animator site medals or operator secrets from web
- authorizing a reveal operator from the animator runtime

Do not reimplement:
- token generation, digesting, or constant-time verification
- medal-cutoff ordering and positivity validation
- the digest-only persistence and generic-failure scope resolution

---

## `imageprocessing_service/`

Purpose:
- process uploaded/base64 images
- enforce JPEG, PNG, and WebP file types from their content signatures
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

Uploaded files and base64 data URIs are identified from their bytes with
`puremagic`. The service ignores client-provided MIME labels and returns the
canonical detected MIME type. It rejects unknown signatures, unsupported image
types, and signatures that disagree with Pillow's decoded format.

Web and Arena use `multipart_file_size.py` ahead of route parsing to stop
selected image fields as soon as they exceed their configured byte ceiling.
Rules can target all file parts or named fields on mixed upload forms.

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
- `LockKind = Literal["clarification", "task", "review"]` — the public lock-kind alias
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
- `ContestValkeyTargets` — immutable contest, judgment, profiling, validation,
  task, and clarification identifier set for strict cleanup
- `ValkeyRuntime.purge_contest_runtime_state(targets) ->
  ContestValkeyPurgeResult` — removes and verifies every target queue entry, job
  hash, autojudge/workflow lock, buffered command, inflight timestamp, and
  scoreboard cache variant
- `purge_contest_with_client(client, targets) -> ContestValkeyPurgeResult` —
  raw-client strict purge used by the runtime and integration tests
- `ContestValkeyPurgeError` — reports unavailable, failed, or unverifiable
  cleanup without degrading to best-effort behavior
- `create_valkey_pool(valkey_url: str) -> ConnectionPool`
- `enqueue_job(client_or_runtime, job, *, priority) -> None`
- `enqueue_arena_submission_job(client_or_runtime, job) -> None`
- `enqueue_profiling_job(client_or_runtime, job) -> None`
- `dequeue_job_id(client_or_runtime) -> str | None`
- `get_contest_queue_metrics(client_or_runtime, contest_id) -> ContestQueueMetrics | None`
- `remove_from_inflight(client_or_runtime, judgment_id) -> None`
- `publish_verdict(client_or_runtime, event) -> None`
- `publish_submission(client_or_runtime, event) -> None` — publishes a
  `SubmissionEvent` new-submission nudge to `QUEUE_SUBMISSIONS_CHANNEL`
  (`judge:submissions`) so the animator can flash a pending cell and refetch the
  authoritative `/snapshot`; buffered/best-effort like `publish_verdict`
- `ValkeyRuntime.publish_revelation(event) -> bool` and
  `ValkeyRuntime.iter_revelation_events(contest_id, scope, *, on_subscribed=None)
  -> AsyncGenerator[RevealStateChangedEvent]` — reveal-ceremony projection
  pub/sub (see "Reveal ceremony persistence and projection pub/sub")
- `ValkeyRuntime.fenced_save_reveal_state(*, lock_key, state_key, token,
  state_json, ttl_seconds) -> int | None` — token-fenced reveal-state write
  (`1` saved, `0` ownership lost, `None` unavailable)
- `worker_presence_loop(...) -> None` — immediately publishes a worker and
  refreshes its live marker until shutdown
- `list_all_workers(client_or_runtime) -> dict[WorkerClass, list[WorkerPresence]]`
- `remove_worker(...) -> None` — removes a worker until its next heartbeat
- `prune_stale_workers(client_or_runtime, *, worker_class, older_than_days=PRESENCE_RETENTION_DAYS, now=None) -> int`
  and `prune_all_stale_workers(client_or_runtime, *, older_than_days=..., now=None) -> int`
  — delete durable registry records of workers unseen past the cutoff
- `build_command(...)`, `publish_command(...)`, `verify_command(...)`, `claim_nonce(...)`, `worker_command_loop(...)`, `WorkerCommandType`, `LivePauseFlag` — signed worker pause/resume command transport (see "Worker pause/resume commands")
- `ValkeyRuntime.set_reporting(key, value, *, ex) -> bool` — atomic `SET … EX` reporting delivery success for auditing
- `ValkeyRuntime.get_and_delete(key) -> str | None` — atomically consumes a
  string key with `GETDEL`
- Queue key constants: `QUEUE_PENDING_KEY`, `QUEUE_PRIORITY_KEY`, `QUEUE_INFLIGHT_KEY`, `QUEUE_INFLIGHT_TIMES_KEY`, `QUEUE_JOB_HASH_PREFIX`, `QUEUE_RESULTS_CHANNEL`, `QUEUE_SUBMISSIONS_CHANNEL`

Worker presence:
- `WorkerClass` defines `autojudge`, `rating`, `aiassistant`, `web`, `arena`,
  and `animator`. The first three are worker classes shown on the Arena admin
  dashboard; `web`, `arena`, and `animator` are presence-only HTTP server
  classes published from each server's lifespan and read by the health monitor.
  They never
  appear in the Arena dashboard worker cards or pause UI (the dashboard
  iterates an explicit class tuple, and `_resolve_class` rejects them).
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
- The two durable hashes are bounded by a **stale-record prune pass**. Live
  markers expire on their own, but `seen` and `last-jobs` never would: because
  `resolve_worker_id` falls back to `<fqdn>:<pid>` when `NOCA_*_WORKER_ID` is
  unset, every restart would otherwise leave one permanent field behind.
  `prune_stale_workers` deletes the `seen` and `last-jobs` fields of workers
  whose `last_seen_at` is older than `PRESENCE_RETENTION_DAYS` (**7 days**, a
  shared constant with no environment variable), and also deletes unparseable
  registry values, which would otherwise make `list_workers` raise.
- The pass never prunes a worker that still holds a live marker, so a legacy
  start-timestamp-only record of a long-running worker survives. Deletion is a
  **compare-and-delete** Lua script keyed on the value the pass read, so a
  heartbeat landing between the read and the delete keeps its fresh record.
  Pruning is harmless in any case: a returning worker re-registers on its next
  heartbeat.
- Two callers run it, and both are safe to run concurrently: the health monitor
  reaper loop every `NOCA_HEALTHMON_REAPER_INTERVAL`, and every
  presence-publishing module once at shutdown, right after `mark_worker_offline`
  and inside the same suppressed guard. The shutdown call is what bounds growth
  in deployments that do not run the optional health monitor image.

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

Valkey Pub/Sub is independent of logical databases. Pytest therefore prefixes
these channel names with `noca:test:<run>:<worker>:` before importing the shared
constants. This prevents serial tests on DB 15 and parallel workers on DBs 1–14
from publishing messages to applications listening on the production channel
names in DB 0.

- `QUEUE_RESULTS_CHANNEL = "judge:results"` — contest (web) verdicts. Produced by autojudge/web; consumed by `ValkeyRuntime.iter_verdict_events()` (web runs SSE and the public contest live feed).
- `QUEUE_SUBMISSIONS_CHANNEL = "judge:submissions"` — contest new-submission nudges. Produced by the web submit route via `publish_submission` after the submission commits; consumed by `ValkeyRuntime.iter_submission_events()` (the animator live feed). `SubmissionEvent` (`shared/queue_schema.py`) is the `{submission_id, contest_id, team_id, problem_id}` payload (all fields required so malformed messages fail validation at parse). It is a low-latency signal only: the animator flashes the pending cell and refetches the authoritative `/snapshot`. Ordering vs. `judge:results` is **not** guaranteed; snapshot reconciliation corrects a verdict that arrives before its submission signal.
- `ARENA_RESULTS_CHANNEL = "arena:results"` — Arena verdicts. Produced **only** by the autojudge worker via `publish_arena_verdict_with_client` (exported as `_publish_arena_verdict_with_client`); consumed by `ValkeyRuntime.iter_arena_verdict_events()` (Arena public live feed). The channel name has a single source of truth in this constant so the autojudge producer and Arena subscriber cannot drift.
- `ArenaVerdictEvent` (`shared/queue_schema.py`) is the minimal `{submission_id, judgment_id, verdict}` payload on `arena:results`. It is a "changed" signal only: the Arena live feed refetches a server-side snapshot rather than rendering event fields. There is deliberately no runtime publish path / `PendingCommand` operation for it, since nothing publishes Arena verdicts through `ValkeyRuntime`.

Reveal ceremony persistence and projection pub/sub (`revelation.py` + `reveal_schema.py`):
- **Validated key/channel builders.** `reveal_state_key(contest_id, scope)`, `reveal_lock_key(contest_id, scope)`, `reveal_controller_key(contest_id, scope)`, and `revelation_channel(contest_id, scope)` build every key/channel from two components validated by `validate_component` against `^[A-Za-z0-9_-]{1,64}$`. The guard forbids `:` (so a component can never inject an extra key segment or a different channel) and matches the shapes actually used — contest UUIDs and the `scope` value, which is a site id or the `GLOBAL_SCOPE = "global"` constant. An invalid component raises `InvalidRevelationScopeError` before any Valkey call. Key prefixes: `REVEAL_STATE_KEY_PREFIX = "animator:reveal"`, `REVEAL_LOCK_KEY_PREFIX = "animator:reveal:lock"`, `REVEAL_CONTROLLER_KEY_PREFIX = "animator:reveal:controller"`, `REVELATION_CHANNEL_PREFIX = "revelation:events"`. The controller key holds one ceremony scope's active controller id — an opaque per-panel value only; it never carries tokens, token digests, or client addresses.
- **Fenced state write.** `fenced_save_state_script()` returns the single Lua source used by `ValkeyRuntime.fenced_save_reveal_state(lock_key, state_key, token, state_json, ttl_seconds)`: it writes the state with `EX` **only while the lock still holds the caller's token**, returning `1` on a fenced write, `0` on lost ownership, and `None` when Valkey is unavailable. This is what lets the animator reveal store keep a single writer per scope safely even if a lock lease expires; the lock's compare-and-delete release only guards *release*, not the write.
- **`RevealStateChangedEvent`** (`shared/reveal_schema.py`) is a versioned (`event_version: Literal[1]`), `extra="forbid"` **invalidation nudge**, not a projection payload: `{contest_id, scope, command, phase, focused_team_id, revealed_count, frozen_count, published_at}`. `shared` cannot import the animator's derived team/problem views, and broadcasting them would give subscribers a second, race-prone source of truth — so every event means only "the ceremony under this `contest_id`/`scope` changed; refetch the authoritative projection/state." Consumers must **never** render the event's own fields as authoritative state. Missed events are harmless because state is always reloadable. `RevealPhase` and `RevealCommand` literals live here too; `animator.models.reveal_session` re-imports `RevealPhase` rather than redeclaring it.
- **Publish semantics.** `ValkeyRuntime.publish_revelation(event) -> bool` and the raw `publish_revelation_with_client(client, event) -> int`. The bool `True` means **the `PUBLISH` command reached Valkey**, explicitly *including* the case where it reached zero subscribers (Valkey's integer subscriber count, `0` included, is success); `False` is returned only on a recoverable transport error or when no client is connected. Unlike `publish_verdict`, revelation publishes are deliberately **not** buffered through `PendingCommand`: replaying a stale ceremony frame after a reconnect is worse than dropping it, since every event is only an invalidation signal.
- **Subscriber.** `ValkeyRuntime.iter_revelation_events(contest_id, scope, *, on_subscribed=None) -> AsyncGenerator[RevealStateChangedEvent]` follows the verdict-channel own-client / reconnect-return style and validates each frame inside a per-message guard, so a malformed or foreign-version payload is logged and skipped rather than escaping to the caller.
- **Subscription-established signal.** The optional `on_subscribed` callback fires exactly once, immediately after the `SUBSCRIBE` completes — the instant from which no publication can be missed. A consumer that must tell *its* clients "you are covered now" cannot derive that moment from the first yielded event, because a quiet channel may never produce one; the animator's spectator SSE stream uses it to emit its `reveal_ready` event, closing the window between an SSE response starting (which fires the browser's `open`) and the subscription actually existing. When the runtime has no client or subscription setup fails, the generator ends without invoking the callback; consumers must observe termination separately and must not announce coverage.

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
  machine values: `staged` (accepted, waiting for the batch flusher window),
  `preparing`, `submitted`, `polling`, `expiring` (transient in-transaction
  claim sentinel), `completed`, `failed`, `expired`, and `cancelled`.

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

## `health_rate_limit.py`

Purpose:
- shared public `/health` endpoint rate limiting for Web, Arena, and the
  animator (`animator/routes/health.py` enforces it on `/health`)
- Valkey-backed fixed-window counters with a process-local fallback when
  Valkey is unavailable
- trusted CIDR bypass for local container and load-balancer health checks

Canonical location:
- `shared/services/health_rate_limit.py`

Main entrypoints:
- `HealthRateLimitSettings` — compact settings object consumed by route
  dependencies
- `InMemoryHealthRateLimiter` — process-local fallback fixed-window limiter
- `enforce_health_rate_limit(request, *, module, settings, fallback_limiter) -> None`
  — raises `HTTPException(429)` with `Retry-After` when a public caller exceeds
  the configured `/health` limit

---

## `scoreboard_cache.py`

Purpose:
- scoreboard cache invalidation helpers shared by web, the autojudge worker, and
  contest purge

Canonical location:
- `shared/services/scoreboard_cache.py`

Notes:
- arena computes rankings on demand without a scoreboard cache; the current
  consumers are web, autojudge (`autojudge/submission_job.py` calls
  `invalidate_scoreboard_cache` to invalidate on verdict), and
  `shared/services/valkey_service/contest_purge.py`

---

## `scoreboard_projection.py`

Purpose:
- single owner of the ICPC scoreboard semantics: snapshot DTOs, cache serialization, and the pure `compute_icpc` calculation
- consumed by the web scoreboard service and by the animator runtime
  (`animator/services/reveal_engine.py`, `reveal_projection.py`, `reveal_loader.py`,
  `contest_feed_service.py`), without importing `web` models

Canonical location:
- `shared/services/scoreboard_projection.py`

Key types and functions:
- `ProblemResult`, `TeamStanding`, `ScoreboardSnapshot` — scoreboard DTOs
- `ContestScoringInput`, `TeamInput`, `ProblemInput`, `SubmissionInput`, `JudgmentInput` — structural input protocols (read-only properties) so callers adapt their own ORM or value records instead of this module importing them
- `ordinal_to_label(ordinal)` — 1-based problem ordinal to label (`A`..`Z`, `AA`, ...)
- `submission_sort_key(submission)` — the canonical `(timestamp_seconds,
  created_at, id)` ordering key. This module is the single owner of that order:
  `compute_icpc` sorts with it, and the animator's frozen reveal universe
  re-exports and uses the same function, so the two cannot drift. `created_at` is
  normalized for comparison only (naive read as UTC, `None` sorts first), so a
  `SubmissionInput` with the protocol-permitted `created_at=None` cannot raise
  `TypeError` mid-sort
- `bucket_visible_pending_submissions(submissions, judgments, *, freeze_at_seconds,
  viewer_sees_frozen)` — groups unresolved submissions by team/problem using the
  same freeze-visibility rule consumed by `compute_icpc` and animator pending lists
- `penalizing_verdicts(accept_pe, ce_adds_penalty)` — returns the canonical
  ordered `Verdict` members that count as failed attempts. Both `compute_icpc`
  and participant-facing rules summaries consume this helper, so displayed
  rules cannot drift from scoreboard behavior
- `compute_icpc(contest, teams, problems, submissions, judgments, freeze_at_seconds, viewer_sees_frozen)` — pure standings calculation; honors per-contest `wa_penalty`, `accept_pe`, and `ce_adds_penalty`, the strict freeze predicate (`timestamp_seconds > freeze_at_seconds`), pending cells, position-based tied ranks, and first-balloon marking ordered by `(timestamp_seconds, created_at, id)`
- `snapshot_to_dict(snapshot)` / `snapshot_from_dict(data)` — JSON-compatible cache serialization; tolerant of legacy payloads missing `team_fullname` or `is_first_balloon`

Notes:
- web behavior is unchanged: `web/services/scoreboard/` keeps only query, cache, and orchestration code and re-exports the DTOs from here
- this module must never import from `web/`; inputs arrive as `Sequence`/`Mapping` of the structural protocols above

---

## `security_headers.py`

Purpose:
- apply the shared browser security-header baseline to Web, Arena, health
  monitor, and animator HTTP responses

Canonical location:
- `shared/services/security_headers.py`

Key types and functions:
- `SecurityHeaderSettings` — runtime flags for header enablement, CSP
  report-only mode, and HSTS
- `SecurityHeadersMiddleware` — ASGI middleware registered by Web, Arena,
  healthmonitor (`healthmonitor/main.py`), and animator (`animator/main.py`)
- `apply_security_headers(headers, settings=...)` — testable header mutation
  helper

Notes:
- the middleware sets `X-Content-Type-Options`, `Referrer-Policy`,
  `X-Frame-Options`, `Permissions-Policy`, `Cross-Origin-Opener-Policy`,
  `Cross-Origin-Resource-Policy`, and CSP
- CSP starts in report-only mode when `NOCA_CSP_REPORT_ONLY=true`
- HSTS is enabled only when secure cookies are enabled or the app runs in
  production

---

## `auth_rate_limit.py`

Purpose:
- provide Valkey-backed authentication throttling shared by Web and Arena

Canonical location:
- `shared/services/auth_rate_limit.py`

Key types and functions:
- `AuthRateLimitSettings` — shared throttle settings
- `InMemoryAuthRateLimiter` — process-local fallback when Valkey is not
  available (defined in `auth_rate_limit_fallback.py`, re-exported here)
- `build_auth_throttle_identity(...)` — builds IP and account throttle keys
- `check_auth_throttle(...)`, `record_auth_failure(...)`, and
  `reset_auth_throttle(...)` — lifecycle helpers for auth flows

Notes:
- keys include module, auth action, ASGI client IP, and an HMAC hash of the
  normalized account identifier
- helpers intentionally use `request.client.host`; they don't trust raw
  `X-Forwarded-For`
- **fail-open**: every Valkey call is wrapped so that a `ValkeyError`/`OSError`
  is logged and falls back to the in-memory limiter — a Valkey outage never
  500s a login page
- Web applies this to `/login` and `/c/{slug}/login`; Arena applies it to
  login, 2FA, password reset, and signup, plus IP-scoped abuse caps on the
  email-resend actions (`resend_activation`, `resend_parental_consent`,
  `update_parental_email`) via `arena.routes.auth_common.enforce_resend_throttle`

---

## `security_events.py`

Purpose:
- persist cross-module security events for admin review and audit history

Canonical location:
- `shared/services/security_events.py`

Key types and functions:
- `record_security_event(...)` — insert a security event from a session or
  connection; accepts `actor_user_id` (opaque id) and `actor_label` (human-readable
  login, e.g. email/username) snapshotted at event time, plus optional
  `request_id`
- `record_request_security_event(...)` — insert an event with request IP and
  user-agent metadata; also forwards `actor_user_id`, `actor_label`, and
  `X-Request-ID`
- `list_recent_security_events(session, limit=50, module=None, event_type=None,
  modules: Sequence[str] | None = None)`
  — return recent events for callers that need a bounded list
- `list_security_events_paginated(session, page=..., per_page=..., module=None,
  modules=None, event_type=None)` — return all retained matching events through
  page-based browsing
- `list_security_event_filter_values(session, module=None, modules=None)` —
  distinct modules and event types present, for building filter dropdowns
- `delete_security_events_older_than(session, retention_days=..., modules=None)`
  — retention cleanup; `modules` restricts deletion to the caller's owned
  module set so an independently deployed Web-only or Arena-only site prunes
  exactly its own rows

Notes:
- events are stored in the `security_events` table
- rows carry both an opaque `actor_user_id` and a human-readable `actor_label`
  (the login, snapshotted at event time so it survives account rename/deletion);
  the viewers render `actor_label` in a "User" column, falling back to a truncated
  `actor_user_id`. `auth_*` / `parental_*` callers pass both whenever the user is
  in scope (login success/failure, 2FA, activation, parental-consent flows)
- callers record auth lockouts, repeated auth failures, existing-account signup
  attempts, AI response redactions, suspicious token/session mismatches, and
  admin actions (via `admin_audit.py`)
- rows store `client_ip` and nullable `source_port`; the port comes from the
  ASGI client port or from the trusted header configured by
  `NOCA_SOURCE_PORT_HEADER`
- rows store nullable `request_id` from `X-Request-ID`; the bundled Caddyfile
  overwrites that header with Caddy's request UUID and appends the same value to
  the Caddy access log
- viewers are scoped to the owning runtime's modules (same split as the
  reaper): Arena `/admin/dashboard/security-events` shows
  `module in (arena, aiassistant)`; Web `/uberadmin/security-events` shows
  `module=web` only, with an event-type filter
- both viewers offer a "Download as CSV" export served by
  `security_events_export.py`

---

## `security_events_export.py`

Purpose:
- stream the whole security-event log of one viewer's module scope as a CSV
  download

Canonical location:
- `shared/services/security_events_export.py`

Key types and functions:
- `stream_security_events_csv(session, module=None, modules=None)` — async
  iterator of CSV text chunks, newest first, starting with a UTF-8 BOM and the
  `CSV_HEADER` row
- `csv_filename(prefix, now=None)` — timestamped attachment filename
- `CSV_HEADER` — the exported column order (every stored column, with the JSON
  metadata serialized into a single cell)

Notes:
- the export intentionally ignores the viewer's on-screen `event_type`/`module`
  filters: an operator downloading the log wants the complete history. The
  viewer's module *ownership* scope is kept, because that is an authorization
  boundary rather than a user filter — Web exports `module=web`, Arena exports
  `module in (arena, aiassistant)`
- rows are read in keyset-paginated batches ordered by `(created_at, id)`
  descending, so neither the service nor the response body materializes the full
  log
- cell values are flattened to one line and a leading `=`, `+`, `-`, or `@` is
  prefixed with an apostrophe, so attacker-influenced fields (user agent,
  metadata) cannot become spreadsheet formulas
- the routes open their own session for the stream rather than using the
  request-scoped dependency, because FastAPI closes `yield` dependencies before
  a streaming body is consumed

---

## `security_events_reaper.py`

Purpose:
- periodic retention cleanup of the shared `security_events` table, run
  independently by each HTTP runtime so a Web-only or Arena-only deployment
  still prunes its own events

Canonical location:
- `shared/services/security_events_reaper.py`

Key types and functions:
- `run_security_events_reaper(session_factory, *, poll_interval_seconds,
  retention_days, modules, stop_event, logger)` — self-contained loop (no
  imports from `web`/`arena`): runs one cleanup cycle immediately, then repeats
  every `poll_interval_seconds` until `stop_event` is set

Notes:
- started from each app's lifespan when `SECURITY_EVENTS_RETENTION_DAYS > 0`:
  Web passes `modules=["web"]`, Arena passes `modules=["arena", "aiassistant"]`
  (the AI worker only ever runs alongside Arena, so Arena owns its rows)
- delegates row deletion to
  `security_events.delete_security_events_older_than(...)`
- each cycle opens a fresh session and commits; a failure is logged and the
  loop continues

---

## `admin_audit.py`

Purpose:
- record privileged admin actions as `security_events` rows so the audit trail
  reuses the existing actor/module/IP/user-agent columns and viewers instead of
  a dedicated table

Canonical location:
- `shared/services/admin_audit.py`

Key types and functions:
- `record_admin_action(session, request, *, module, actor_user_id, actor_label,
  action, target_type, target_id, detail=None, severity: str = "info")` — write one
  `event_type="admin_action"` row with the actor's human-readable login and a
  structured `{"action", "target_type", "target_id", "detail"}` metadata
  payload (`detail` is included only when not `None`)

Notes:
- the audit row is written on the caller's session so, wherever the mutation
  commits in the same transaction, they commit atomically
- callers snapshot the Web username or Arena email in `actor_label`; the event
  viewers show this login instead of the opaque actor ID
- migration `202607220001` backfills missing labels on existing admin-action
  rows when the referenced actor still exists
- currently wired to destructive/privilege actions: Arena user role change,
  activate/deactivate, disable-2FA, `toggle_email_confirmed`
  (`arena/routes/admin_users_actions.py`, with `severity="warning"`), and
  problem/affiliation/category deletes;
  Web uberadmin enable/disable, contest problem/user deletes, and contest
  start-now/end-now state changes, plus animator settings and credential
  changes and sensitive contest backup exports
- escape hatch: promote to a typed table later if query needs outgrow the JSON
  shape

---

## `testcase_pending_ops.py`

- `shared/services/testcase_pending_ops.py`

What one test-case action asks for, as data the planner can apply. Every action on the
judgment-data pages -- replace this case, delete that one, add these, reorder them, replace them
all -- is a `PendingTestCaseOps` with exactly one field set, applied immediately.

It briefly described something larger: a single Save carrying every mutation at once, with a form
parser, an uploads reader and rules refusing contradictory combinations. Separate pages retired all
of that -- with one action per request there is nothing to combine -- and `validator_action_ops.py`
and `testcase_form_echo.py` went with it.

- `PendingTestCaseOps` — the one-field-per-action description: `removals`, `sample_toggles`,
  `added`, `replacements`, `bulk_cases`, `order`
- `CaseContent` — one case's content, plus `states_metadata`: whether the *submitter* stated the
  case's sample flag and explanation, rather than merely supplying content alongside them. An
  inline edit form states both (it shows the checkbox and the explanation box, so clearing either
  is a decision); a single-case ZIP states neither, and a replacement that does not state them
  keeps what the row holds
- `parse_inline_added_cases(form, *, interactive)` — the one form parser still needed: the rows an
  author typed and has not saved. Strategy-aware from the **stored** `ProblemValidatorType`, never
  from validator presence, so an interactive problem's rows carry input only and are never samples
- `SubmittedCaseRow` / `submitted_case_rows(form)` — retain the raw text, sparse row index, sample
  choice and row-level error after a rejected POST; `first_oversized_submitted_case(...)` points the
  response at the exact input/output field instead of redirecting to an empty page
- `case_content_from_single_archive(...)` / `case_contents_from_bulk_archive(...)` — shared
  adapters from the canonical ZIP parsers to `CaseContent`, used by both Web and Arena so strategy
  handling, explanations, and sample defaults cannot drift

Where the retired parser's refusals live now: an archive is parsed by the route that receives it,
an action naming a row that no longer exists is refused under the row lock by
`judgment_case_action.py`, and a validator on a standard problem is refused by the validator
routes.

## `interaction_pending_ops.py`

- `shared/services/interaction_pending_ops.py`

The shared parser and rejected-form model for sample-interaction rows typed on the Web and Arena
judgment pages. `SubmittedInteractionRow` retains each raw transcript, explanation, and browser row
index. `parse_pending_interactions(form)` returns `PendingInteraction` values and raises
`SubmittedInteractionError` with that same index when a transcript is malformed, so both adapters
can return HTTP 422, keep every submitted row, and focus the exact invalid transcript before any
interaction is written.

- `problem_save_errors.py` — `PendingOpsError`, `require_supported_strategy(...)` and
  `unsupported_strategy_message(...)`: the refusal vocabulary both families share, including the
  one the retained satellite endpoints use because they reduce the strategy to a boolean

## `testcase_save_plan.py`

- `shared/services/testcase_save_plan.py`

From those operations to a complete staged directory, in two halves so the interesting one needs
no filesystem.

- `build_desired_cases(current, ops, *, interactive)` — pure: the final ordered list, each entry
  carried from the current directory, replaced by an upload, or new. Ordinals are renumbered
  contiguously, so removing case 2 of 3 leaves 1 and 2
- `materialize(desired, staged_dir, *, interactive)` — applies that decision inside the seeded
  staging directory, parking every carried file under a temporary name first so a reorder cannot
  overwrite a file it still needs. Returns the on-disk sizes the rows must record. A carried case
  with no input, or a carried Standard case with no expected output, raises `MissingCarriedCase`
  instead of converting storage corruption into committed empty or incomplete judge data

## `problem_editor_save.py`

- `shared/services/problem_editor_save.py`

The ordering every editor Save follows, written once because getting it wrong is the failure the
whole change exists to prevent.

- `lock_problem_row(session, domain, problem_id)` — `SELECT ... FOR UPDATE` **before** the Save
  reads a single test case. Two Saves that both snapshot the live directory would each stage a
  complete replacement, and the loser would silently reinstate what the winner replaced. A create
  has no row to lock: it inserts and flushes first
- `open_save_swap(session, *, domain, problem_id, testcase_dir)` — advances the generation fence
  and opens the swap
- `stage_test_cases(swap, desired, *, interactive)` — builds the complete directory in staging,
  with no exception for a single small case
- `abandon_swap(session, swap)` — the window `commit_with_edit_swap` does not cover: a failure
  between staging and promotion rolls back and drops the staging paths, with no quarantine to
  restore because nothing was promoted. The cleanup is cancellation-shielded so both operations
  complete even when request cancellation caused the failure

## `testcase_files.py`

Purpose:
- generic, model-free filesystem helpers for problem test cases, shared by Web, Arena, and the autojudge worker

Canonical location:
- `shared/services/testcase_files.py`

Key functions / constants:
- `CONTEST_TC_SUBDIR = "contest"`, `ARENA_TC_SUBDIR = "arena"` — domain subdirectories under the shared `NOCA_PROBLEM_TESTCASE_DIR` root
- `get_problem_testcase_dir(problem_id, testcase_dir)` — validate a problem id
  and resolve its guarded storage directory
- `get_testcase_path(problem_id, ordinal, ext, testcase_dir)` — resolve `<testcase_dir>/<problem_id>/NNN.in|out`
- `save_testcase_files(problem_id, ordinal, in_bytes, out_bytes, testcase_dir) -> (in_size, out_size | None)` — normalize to LF and write a case **directly in the live directory**, returning on-disk byte sizes. Not atomic and not undoable: this is the satellite routes' historical behavior, and the editor's Save path must not use it
- `write_testcase_files_into(base, ordinal, in_bytes, out_bytes)`, `delete_testcase_files_in(base, ordinal)`, `reorder_testcase_files_in(base, ordinal_map)` — the same operations against an already-resolved directory, which is how a Save writes into its staging copy
- `copy_testcase_files_into(problem_id, testcase_dir, destination) -> int` — seed a staging directory with the problem's current files, using hardlinks where supported and durable streamed copies as the fallback, so a Save materializes the complete desired directory without mutating live inodes
- `read_testcase_preview`, `read_testcase_full`, `read_testcase_sizes`
- `delete_testcase_files`, `delete_all_testcase_files`, `renumber_testcase_files`, `reorder_testcase_files`

Notes:
- callers pass the domain-specific root (`settings.PROBLEM_TESTCASE_DIR`, already resolved to `<root>/contest` for Web and `<root>/arena` for Arena); the helper is domain-agnostic
- an **interactive** (custom-validator) problem's cases have no expected output: pass `out_bytes=None`, which writes the `.in` only, removes any stale `.out`, and reports an output size of `None` (stored as a null `output_size_bytes`). The other helpers already tolerate a missing `.out`
- problem ids must be UUID/slug-like path segments; resolved paths must remain
  under the configured test-case root before read, write, rename, or delete
- the single-source-of-truth inline-edit threshold `MAX_INLINE_TESTCASE_BYTES` (10 KB) and the single-case ZIP helpers (`parse_single_testcase_zip`, `build_single_testcase_zip`, `SingleTestCase`) live in `shared/tc_zip.py`; the ZIP parsers take `require_output=False` for an interactive problem, where the archive carries `input.txt` / `in/NNN.in` alone

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
  scripts (`tc-reorder-sortable.js`, `tc-add-row.js`, `judgment-actions.js`) complete the
  unified test-case editing UI, with row-action confirmations (`data-confirm`) handled by
  the globally loaded `confirm-submit.js`

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
- `paginate_arena_notifications(executor, *, user_id, page=1, per_page=25)`
- `delete_arena_notification(executor, *, notification_id, user_id) -> bool`
- `mark_all_arena_notifications_read(executor, *, user_id) -> int` — used by
  `arena/routes/notifications.py`
- `delete_all_arena_notifications(executor, *, user_id) -> int`
- `DEFAULT_NOTIFICATION_LIMIT = 20` — public constant behind the topbar list cap

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
| `CUSTOM_VALIDATOR_DISABLED` | `code_off` |
| `TEACHER_FEEDBACK_POSTED` | `rate_review` |
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
- the heartbeat endpoint carries a second, load-bearing responsibility: it runs
  `get_current_arena_user`, which marks the request eligible for sliding-session
  cookie rotation, *before* the `PRESENCE_ENABLED` early return. This is why
  `arena/main.py::_heartbeat_config` emits the client configuration for every
  logged-in user rather than only when presence is enabled — gating it on the
  flag made Arena session lifetime depend on whether the green-dot feature
  happened to be on, and ended long problem edits at the login page with the
  form discarded. Cadence is `PRESENCE_HEARTBEAT_SECONDS` when presence is
  enabled and `JWT_EXPIRE_SECONDS / 4` (minimum 60 s) otherwise

---

## `session_keepalive.py`

Purpose:
- the arithmetic behind sliding sessions, shared by Web and Arena

Canonical location:
- `shared/session_keepalive.py`

Main entrypoints:
- `refresh_window_seconds(token_lifetime_seconds)` — the remaining lifetime at
  which a valid token starts being rotated (half the lifetime, never zero)
- `derived_keepalive_seconds(token_lifetime_seconds)` — the browser cadence when
  nothing else sets one: a quarter of the lifetime, floored at
  `KEEPALIVE_MIN_INTERVAL_SECONDS` (60)
- `keepalive_lands_inside_refresh_window(...)` — whether a cadence actually
  rotates the cookie
- `keepalive_window_error(...)` — the operator-facing refusal message, whose
  remedy adapts to whether the cadence is configured or derived

Consumers:
- web: `web/config.py` (startup validator), `web/template_globals.py`,
  `web/services/authentication_service.py`
- arena: `arena/config.py` (startup validator), `arena/template_globals.py`,
  `arena/services/session_service.py`

Notes:
- the window was previously written out once per module, in each module's
  session service. That is why this exists: a startup validator has to reject a
  cadence landing *outside* the window, so the validator and the middleware must
  agree on where the window starts. Two copies of `lifetime // 2` are two chances
  to disagree, and a disagreement would be silent — the validator would accept a
  configuration whose heartbeat rotates nothing
- each module keeps its own `session_keepalive_seconds` config property, since
  the *effective* cadence differs: Arena's follows the presence heartbeat when
  the green dot is on, Web's is always derived

---

## `pagination_service.py`

Purpose:
- shared pagination helpers for server-rendered pages

Canonical location:
- `shared/services/pagination_service.py`

Main entrypoints:
- `Pagination[T]` — frozen, template-friendly pagination result (`items`, `page`,
  `per_page`, `total`) with derived `pages`, `has_prev`/`has_next`,
  `prev_num`/`next_num`, `first`/`last`, and an `iter_pages()` window helper
- `clamp_page(page, *, total, per_page) -> int` — clamp a page to the available
  range for a known total
- `parse_page(value, *, default=1) -> int` — parse a raw request page value,
  clamped to `[1, MAX_PAGE]`
- `effective_per_page(value, *, allowed, default) -> int` — validated page size; a
  member of `allowed` or `default`

Consumers:
- web: `web/routes/uberadmin_security.py`, `web/services/solution_test_service.py`
- arena: `arena/routes/admin_dashboard_history.py`
- shared-internal: `shared/services/security_events.py`

---

## `multipart_file_size.py`

Purpose:
- fail-fast multipart file-size enforcement for NOCA ASGI applications

Canonical location:
- `shared/services/multipart_file_size.py`

Main entrypoints:
- `MultipartFileTooLargeError` — HTTP 413 raised as soon as a selected file part
  exceeds its configured ceiling
- `MultipartFileSizeRule` — frozen rule matching one route method/path pattern and
  defining which file fields share its byte ceiling (`field_names=None` limits
  every file part)
- `MultipartFileSizeLimitMiddleware` — ASGI middleware that tracks selected file
  bytes through `python_multipart` while the body streams, without buffering the
  complete request

Consumers:
- web (`web/main.py`, `web/error_handlers.py`) and arena (`arena/main.py`,
  `arena/error_handlers.py`, `arena/image_upload_limits.py`) — mounted as ASGI
  middleware in both apps, ahead of route parsing

---

## Arena module — shared service instances

The arena module initializes its own instances of the shared services listed below. All
instances live on `app.state` and are created in `arena/main.py`'s lifespan in the order shown.

### `SecretsManager` (startup)

Purpose:
- transparent Fernet encryption/decryption for `EncryptedString` columns (OTP secrets)

Canonical location:
- `secrets_manager` git dependency (`SecretsManager @
  git+https://github.com/dclobato/secrets-manager.git@v1.0.0` in
  `shared/pyproject.toml`)
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

Canonical location: `jwtservice` git dependency (`jwtservice @
git+https://github.com/dclobato/jwtservice.git@v2.1.0` in `arena/pyproject.toml`);
`app.state.jwt_service`

Notes:
- the issuer comes from `NOCA_ARENA_APP_NAME` (default `"noca-arena"`) and must differ from the web module's `NOCA_WEB_APP_NAME` (default `"noca"`), preventing cross-server token acceptance
- uses the same `NOCA_JWT_SECRET_KEY`, `NOCA_JWT_ALGORITHM`, `NOCA_JWT_EXPIRE_SECONDS`, and
  `NOCA_JWT_REFRESH_MAX_SESSION_SECONDS` env vars as the web module by default; configure
  separate values for stronger isolation. Both modules now apply them the same way: every
  active session slides at half-life, capped only by `NOCA_JWT_REFRESH_MAX_SESSION_SECONDS`
  (`0` disables the cap)

### `EmailService` (arena instance)

Purpose:
- transactional email delivery for arena user flows (account activation, password reset, 2FA)

Canonical location: `shared/services/email_service.py`; `app.state.email_service`

Notes:
- configured via the same `NOCA_SEND_EMAIL`, `NOCA_EMAIL_PROVIDER`, and `NOCA_SMTP_*` env vars as the web module
- see the `email_service.py` section above for full API reference
# Custom validator lifecycle

`shared.services.custom_validator` validates the 256 KiB UTF-8 upload contract,
creates candidate tokens and queue payloads, promotes matching candidates,
retains bounded compilation failures, clears revisions, parses package
metadata, and supplies domain-neutral status and current-source views for both
frontends.

`current_validator_source(record)` returns the source authors can inspect or
download: the active revision when present, otherwise the staged candidate. It
returns `None` when no complete source/language pair exists.

`shared.services.valkey_service.enqueue_custom_validator_validation_job`
stores validation job metadata and pushes the validation identifier onto the
profiling-priority queue after the owning database transaction commits.
# Sample interactions

`shared.services.sample_interactions` owns everything about the worked examples an
**interactive** problem shows instead of sample test cases: a transcript of the
conversation a correct program has with the validator. The module is domain-neutral
(no ORM imports); the Web and Arena services own only the SQL.

Transcripts are stored in the same JSON shape the judge records for a real
interactive attempt (`submission_interactive_attempts.transcript`), so one renderer
serves both — the shared `_partials/transcript_table.html`.

| Function | Description |
|---|---|
| `parse_interaction_text(text)` | Parse the authoring plain-text format into transcript JSON. Every line must start with the exact two-character prefix `"> "` (validator → contestant) or `"< "` (contestant → validator); everything after the prefix is preserved verbatim. CRLF/CR are normalized first. Bare `>` / `<`, leading whitespace, and blank lines raise `InteractionParseError` naming the 1-based line number. |
| `transcript_to_text(transcript)` | The inverse, used to pre-fill the edit textarea. |
| `transcript_preview(transcript, *, limit=60)` / `transcript_line_count(transcript)` | Admin list-row summaries. |
| `validate_transcript_json(obj)` | Structural validation of a transcript read from a package. |
| `parse_packaged_interactions(*, archive_names, read_file)` | Read `interaction/NNN.interaction` + optional `interaction/NNN.explain` members, remap ordinals contiguously from 1, and enforce the cap. |
| `build_interaction_files(interactions)` | Build the `interaction/` archive members for an export, numbered sequentially in display order. |
| `interactive_testcase_violation(*, total_cases, sample_cases)` | The invariant for a problem with a configured validator: **zero public test cases and at least one secret one**. Returns a message or `None`. |
| `SampleInteractionRowView` | Template-safe row model for the shared admin list partial, mirroring `TestCaseRowView`. |

`MAX_SAMPLE_INTERACTIONS = 5` caps how many a problem may hold. Hidden interactions
(kept through a validator removal) count towards the cap, so a later un-hide can
never exceed it.

The cap is judged against the row count a save *produces*, not the one it starts
from: the per-domain pending appliers apply removals before additions and validate
`existing − removed + added` up front, so a full problem can swap an interaction in
one edit, and a save that would still overflow fails before mutating anything.

# Sample problem package

`shared.services.sample_problem_package.build_sample_problem_package(destination)` writes the
reference "A + B" import package (statement, three test cases with an explanation — one of them
public — global limits, and `python3` / `rust` per-language limits) offered for download from both
import pages. It writes to a caller-owned path, exactly as a real export does, and the routes
stream it with a `FileResponse` that deletes the file afterwards.

It is generated from code rather than committed as a binary so it cannot drift from the reader,
and it goes through the **shared writer**, so it carries every version-1 key and a valid `sha256`
manifest — what a real export looks like. Its fields deliberately span both domains (Arena's
`source` / `license` / `statement_language`, the Contest's `color` / `language_limits`), because
the format is their union and each importer keeps what its own schema can store. Round-trip tests
import it through both real importers.
