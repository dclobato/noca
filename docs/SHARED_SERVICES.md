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

Main entrypoint:
- `active_arena_judgment_subquery() -> Subquery` — most-recent non-`SUPERSEDED`
  judgment timestamp per submission (`submission_id`, `max_created_at`); join it back
  against `arena_submission_judgments` on `(submission_id, created_at)` to pick the
  active row

Reused by:
- `arena/services/live_feed_service.py`, `arena/services/submission_list_service.py`,
  `shared/services/arena_stats.py`, and `shared/services/arena_badges.py`

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
| `staging.py` | `PackageStagingArea`, reversible `ArtifactPromotion` |
| `promotion.py` | `ArtifactPromoter` — orders filesystem writes against the transaction |
| `journal.py` | the crash-safe import journal |
| `reconcile.py` | resolving stale journals at startup and before each import |
| `upload.py` | chunked upload spooling, temp export paths, safe download filenames |

Main entrypoints:
- `read_problem_package(zip_path)` — a context manager yielding a `StagedPackage`: the immutable
  `ProblemPackage` plus the staging area holding its payloads. Leaving the context removes every
  temporary path, which is why the live handle lives *outside* the frozen value object
- `build_package(package, destination, *, profile)` — writes `"full"` (importable, every version-1
  key) or `"public"` (contestant statement bundle, no `problem.json`) to a path on disk
- `ArtifactPromoter` — `stage` → `promote` → commit → `finish`, with `rollback` deleting exactly
  what was promoted when the commit fails
- `reconcile_import_journals(session, domain=..., testcase_dir=..., statement_dir=...)`
- `spool_upload(upload)` / `temporary_package_path()` / `safe_package_filename(title)`

`pypdf` is declared in `shared/pyproject.toml` rather than Web's: the shared reader owns PDF
statement validation, and a lazy import of a dependency another package declares would make
shared behavior depend on which module happened to be installed.

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

### Import journal

Promotion spans multiple roots and mixes files with directories, so a marker written *inside* the
promoted directory cannot describe it. One guarded journal file per import, under
`<testcase_dir>/.noca-import-journals/`, records the problem id and domain, every staged source
path with its final target and configured root, and the promotion state
(`staged` → `promoting` → `promoted` → `committed`). It is `fsync`'d and renamed into place at each
transition and deleted on success. The directory name starts with a dot, which
`get_problem_testcase_dir` forbids in a problem id, so it cannot collide with a problem's files
and needs no configuration of its own.

Reconciliation runs **at application startup** (the `web` and `arena` lifespans, alongside the
existing reaper registrations) **and before each import**, not only when another import happens:
for each journal, look up the problem row — if it exists, the commit won, so clear the journal; if
it does not, delete the recorded artifacts. Both problem tables live in the shared schema, so one
query answers this for either domain.

**Every path read from a journal is re-validated against its configured root before anything is
deleted**, so a corrupted or tampered journal can never direct a delete outside the problem
storage roots. A journal that cannot be parsed is logged and left in place rather than acted on.

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
  `shared/services/arena_badges.py`

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
`problem-image-preview.js` and the two image partials). Module-specific design tokens stay in the per-module
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
- `validate_and_parse_url(url, *, block_private_networks=False, allowed_schemes=None) -> ParseResult`
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
- Not yet wired into any signup flow; the service and its tests exist standalone

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

## `animator_access_service.py`

Purpose:
- own the reusable animator access-control domain logic so both Web
  administration and the future animator runtime share it without importing each
  other
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
- `create_valkey_pool() -> ConnectionPool`
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
- `QUEUE_RESULTS_CHANNEL = "judge:results"` — contest (web) verdicts. Produced by autojudge/web; consumed by `ValkeyRuntime.iter_verdict_events()` (web runs SSE and the public contest live feed).
- `QUEUE_SUBMISSIONS_CHANNEL = "judge:submissions"` — contest new-submission nudges. Produced by the web submit route via `publish_submission` after the submission commits; consumed by `ValkeyRuntime.iter_submission_events()` (the animator live feed). `SubmissionEvent` (`shared/queue_schema.py`) is the `{submission_id, contest_id, team_id, problem_id}` payload (all fields required so malformed messages fail validation at parse). It is a low-latency signal only: the animator flashes the pending cell and refetches the authoritative `/snapshot`. Ordering vs. `judge:results` is **not** guaranteed; snapshot reconciliation corrects a verdict that arrives before its submission signal.
- `ARENA_RESULTS_CHANNEL = "arena:results"` — Arena verdicts. Produced **only** by the autojudge worker via `publish_arena_verdict_with_client` (exported as `_publish_arena_verdict_with_client`); consumed by `ValkeyRuntime.iter_arena_verdict_events()` (Arena public live feed). The channel name has a single source of truth in this constant so the autojudge producer and Arena subscriber cannot drift.
- `ArenaVerdictEvent` (`shared/queue_schema.py`) is the minimal `{submission_id, judgment_id, verdict}` payload on `arena:results`. It is a "changed" signal only: the Arena live feed refetches a server-side snapshot rather than rendering event fields. There is deliberately no runtime publish path / `PendingCommand` operation for it, since nothing publishes Arena verdicts through `ValkeyRuntime`.

Reveal ceremony persistence and projection pub/sub (`revelation.py` + `reveal_schema.py`):
- **Validated key/channel builders.** `reveal_state_key(contest_id, scope)`, `reveal_lock_key(contest_id, scope)`, and `revelation_channel(contest_id, scope)` build every key/channel from two components validated by `validate_component` against `^[A-Za-z0-9_-]{1,64}$`. The guard forbids `:` (so a component can never inject an extra key segment or a different channel) and matches the shapes actually used — contest UUIDs and the `scope` value, which is a site id or the `GLOBAL_SCOPE = "global"` constant. An invalid component raises `InvalidRevelationScopeError` before any Valkey call. Key prefixes: `REVEAL_STATE_KEY_PREFIX = "animator:reveal"`, `REVEAL_LOCK_KEY_PREFIX = "animator:reveal:lock"`, `REVELATION_CHANNEL_PREFIX = "revelation:events"`.
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

## `health_rate_limit.py`

Purpose:
- shared public `/health` endpoint rate limiting for Web and Arena
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
- scoreboard cache invalidation helpers shared between web and future arena ranking cache needs

Canonical location:
- `shared/services/scoreboard_cache.py`

Notes:
- arena computes rankings on demand without a scoreboard cache; this module is currently web-only but lives in shared for future use

---

## `scoreboard_projection.py`

Purpose:
- single owner of the ICPC scoreboard semantics: snapshot DTOs, cache serialization, and the pure `compute_icpc` calculation
- consumed by the web scoreboard service today and by the animator runtime later, without importing `web` models

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
- `compute_icpc(contest, teams, problems, submissions, judgments, freeze_at_seconds, viewer_sees_frozen)` — pure standings calculation; honors per-contest `wa_penalty`, `accept_pe`, and `ce_adds_penalty`, the strict freeze predicate (`timestamp_seconds > freeze_at_seconds`), pending cells, position-based tied ranks, and first-balloon marking ordered by `(timestamp_seconds, created_at, id)`
- `snapshot_to_dict(snapshot)` / `snapshot_from_dict(data)` — JSON-compatible cache serialization; tolerant of legacy payloads missing `team_fullname` or `is_first_balloon`

Notes:
- web behavior is unchanged: `web/services/scoreboard/` keeps only query, cache, and orchestration code and re-exports the DTOs from here
- this module must never import from `web/`; inputs arrive as `Sequence`/`Mapping` of the structural protocols above

---

## `security_headers.py`

Purpose:
- apply the shared browser security-header baseline to Web and Arena HTTP
  responses

Canonical location:
- `shared/services/security_headers.py`

Key types and functions:
- `SecurityHeaderSettings` — runtime flags for header enablement, CSP
  report-only mode, and HSTS
- `SecurityHeadersMiddleware` — ASGI middleware registered by Web and Arena
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
- `list_recent_security_events(session, limit=50, module=None, event_type=None)`
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
  action, target_type, target_id, detail=None)` — write one
  `event_type="admin_action"` row with the actor's human-readable login and a
  structured `{"action", "target_type", "target_id", "detail"}` metadata
  payload

Notes:
- the audit row is written on the caller's session so, wherever the mutation
  commits in the same transaction, they commit atomically
- callers snapshot the Web username or Arena email in `actor_label`; the event
  viewers show this login instead of the opaque actor ID
- migration `202607220001` backfills missing labels on existing admin-action
  rows when the referenced actor still exists
- currently wired to destructive/privilege actions: Arena user role change,
  activate/deactivate, disable-2FA, and problem/affiliation/category deletes;
  Web uberadmin enable/disable, contest problem/user deletes, and contest
  start-now/end-now state changes, plus animator settings and credential
  changes and sensitive contest backup exports
- escape hatch: promote to a typed table later if query needs outgrow the JSON
  shape

---

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
- `save_testcase_files(problem_id, ordinal, in_bytes, out_bytes, testcase_dir) -> (in_size, out_size | None)` — normalize to LF and write a case, returning on-disk byte sizes
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
- the issuer comes from `NOCA_ARENA_APP_NAME` (default `"noca-arena"`) and must differ from the web module's `NOCA_WEB_APP_NAME` (default `"noca"`), preventing cross-server token acceptance
- uses the same `NOCA_JWT_SECRET_KEY`, `NOCA_JWT_ALGORITHM`, and `NOCA_JWT_EXPIRE_SECONDS` env vars as the web module by default; configure separate values for stronger isolation

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
