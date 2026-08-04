# Changelog

Todas as mudanças relevantes deste projeto são documentadas aqui.
O formato segue o [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/)
e o projeto adota o [Versionamento Semântico](https://semver.org/lang/pt-BR/).

## [15.2.0] - 2026-08-04

### Features

- **arena:** Overhaul UI and authentication flows
- **arena:** Back ranking and student search with FTS and trigram indexes
- **arena:** Refine class management UI
- **arena:** Refine submission detail result view
- **arena:** Polish report, ranking, and auth UI
- **arena:** Polish batch feedback review layout
- **arena:** Add class-wide problem-set report
- **arena:** Align profile completion with auth flow

### Bug Fixes

- **arena:** Preserve report context on submission details
- **arena:** Count each submission once in problem-set reports
- **arena:** Show problem link underlines on hover
- **arena:** Prevent class detail table overflow



## [15.1.0] - 2026-08-02

### Features

- **aiassistant:** Display model name on worker startup
- **arena:** Record and filter the problem statement language
- **scripts:** Add container image retention cleanup for Docker Hub and GHCR
- **ci:** Let the language publish target one registry or both
- **ci:** Retry publishes, verify released tags, and select registries
- **arena:** Replace problem search ILIKE with full-text search
- **arena:** Autocomplete problem authors and sources

### Bug Fixes

- **ci:** Publish one registry per pass and drop judge provenance

### Performance

- **arena:** Optimize problem list queries



## [15.0.1] - 2026-08-01

### Features

- **animator:** Add an Android reveal-ceremony operator remote
- **animator-remote:** Derive the APK version from the workspace version
- **presence:** Prune stale worker-presence records from Valkey

### Bug Fixes

- **animator-remote:** Build on any JDK instead of demanding a JDK 17
- **animator-remote:** Pin AndroidX to versions AGP 8.13.2 can build
- **animator-remote:** Keep the controls clear of the system bars
- **animator-remote:** Restore the saved token before prompting for one
- **animator-remote:** Make the manifests well-formed XML
- **clipboard:** Support copying over HTTP
- **logging:** Redact URL-embedded API keys from log output



## [15.0.0] - 2026-07-31

### ⚠ Breaking Changes

- **animator:** Serve routes at the root, dropping the /animator prefix
- **animator:** Rename the reveleitor projector to ceremony

  GET /c/{slug}/reveleitor is removed and now returns 404.
  Use GET /c/{slug}/ceremony instead. The route name animator_reveleitor_page
  becomes animator_ceremony_page.

### Features

- **markdown:** Add shared rendering directives
- **animator:** Phase-01 add animator gate and site medal/secret schema
- **animator:** Phase-02 site medal and operator-secret services
- **animator:** Phase-03 web contest-admin animator settings page
- **animator:** Phase-04 scaffold standalone noca-animator runtime
- **animator:** Redesign contest-admin animator settings page
- **animator:** Phase-05 contest metadata and scoreboard snapshot feeds
- **animator:** Phase-06 live scoreboard presentation page
- **animator:** Phase-07 live SSE event stream
- **animator:** Phase-08 animated live scoreboard
- **animator:** Render balloon/star artwork with letters on scoreboard
- **animator:** Live pending-submission list and cell flash
- **animator:** Animate live connection badge
- **animator:** Model reveal sessions and frozen projections
- **animator:** Implement the reveal state machine
- **animator:** Persist and publish reveal sessions in Valkey [Phase-11]
- **animator:** Show connection outage duration
- **animator:** Add live activity ticker
- **animator:** Add container deployment
- **animator:** Expose authenticated reveal control API (Phase 12)
- **web:** Show medal icons in animator settings
- **animator:** Add reveal spectator APIs and team photos (Phase 13)
- **animator:** Deliver reveal ceremony interfaces (Phase 14)
- **animator:** Add contest clock to launcher
- **animator:** Add footer to presentation pages
- **animator:** Clarify reveal control actions
- **animator:** Render reveal medals as team watermarks
- **animator:** Phase 18 — package and wire production deployment
- **scripts:** Freeze the InterIF 2026 seed scoreboard and close answers early
- **animator:** Show site name on smaller line below contest title
- **animator:** Phase 19 — harden reveal recovery and concurrency
- **config:** Make HOST/PORT configurable for web, arena and healthmonitor
- **animator:** Add contest presentation index

### Bug Fixes

- **audit:** Show usernames for admin actions
- **web:** Remove InterIF fixture language on cleanup
- **audit:** Label contest backup export actors
- **animator:** Recover SSE after terminal disconnects
- **migrations:** Merge animator and master revision heads
- **animator:** Recover reveal clients after disconnects
- **animator:** Unify FLIP motion timing
- **animator:** Improve reveleitor team media modal
- **animator:** Default the brand name to "NOCA Animator"
- Use BRAND_NAME for email sender, APP_NAME for arena JWT issuer
- **healthmonitor:** Repair the heatmap grid column template
- **email:** Encode only the display name in RFC 5322 addresses

### Refactoring

- **scoreboard:** Phase-00 extract shared projection service
- **animator:** Remove site style configuration
- **[BREAKING]** **animator:** Serve routes at the root, dropping the /animator prefix
- **[BREAKING]** **animator:** Rename the reveleitor projector to ceremony

### Documentation

- **animator:** Move team audio playback into phase 14
- **animator:** Move phases 15-17 to the optional backlog
- Update full test timeout guidance
- **animator:** Add controller lease backlog phase

## [14.3.0] - 2026-07-26

### Features

- **arena**: Assign problems to teacher problem sets — a judge-only accordion on
  the problem detail page groups current assignments, links each set to its
  management page, and offers eligible targets from owned ongoing classes through
  safely serialized dependent selectors. A dedicated assignment service and POST
  route revalidate exact role, ownership, class dates, deadline, problem
  existence and non-membership; problem-list return state is preserved, GET and
  POST eligibility use consistent UTC dates, and zero-row duplicate races are
  reported as stale-selection warnings rather than false success. The
  create-problem-set start field defaults to two minutes ahead in the teacher's
  timezone so minute-precision submissions do not immediately fail past-time
  validation
- **languages**: Add Scala 3.3.8 LTS, OCaml 4.14.4 and PHP 8.5.8 as judge
  languages, bringing the registry to 21. Scala compiles with `scalac` and folds
  the Scala runtime jars into a self-contained `solution.jar`, so its run image is
  the same plain Temurin JRE that Kotlin uses and the sandbox needs only the
  existing JVM binds. OCaml is built from source (Debian bookworm ships only
  4.14.1) and compiles natively with `ocamlopt`, so the run image carries no OCaml
  runtime and needs no sandbox binds, matching the Go/Rust model. PHP is
  interpreted, syntax-checked with `php -l`, and runs from source. Each language
  ships editor stubs, highlight.js/Ace modes, a devicon, a stdout flush hint, and
  sample solutions for both the token-compared and custom-validator problems

### Fixes

- **smoke-test**: Point `scripts/autojudge/smoke_test_judge.py` at
  `sample_question/token_validator/`. The sample tree had been reorganized into
  `token_validator/` and `custom_validator/` subdirectories, leaving the script
  resolving every source, input and expected-output path against a directory that
  no longer held them

## [14.2.0] - 2026-07-25

### Features

- **solution-tests**: Add non-scoring solution tests for judges and admins —
  JUDGE, ADMIN and UBERADMIN actors can run a candidate solution against any
  problem in an active contest through the real compiler, sandbox, limits, test
  cases and custom validator, with zero effect on the competition. Runs live in
  their own `solution_test_runs` / `solution_test_case_results` tables so
  leakage into standings, balloons, Runs, reports, feeds and exports is
  structurally impossible; the worker entrypoint takes no Valkey handle, so it
  cannot publish verdict events, invalidate the scoreboard cache, or create
  balloon tasks. Interactive attempts reach these tables through a new
  `attempt_target` axis independent of `domain`. Jobs share the contestant
  queues and the `priority=contest.is_running` rule, are retained for the life
  of the contest, and are excluded from contest backups. Rate limiting is an
  independent per-actor budget keyed on a namespaced advisory lock
- **web**: Add an UberAdmin contest backup export/import — export a finished or
  inactive contest to a portable ZIP (problems, users, submissions, full
  judgment history, clarifications, staff tasks, sites, and optional
  media/password hashes) and restore it under a new name and slug as a faithful
  historical replay, with verdicts, timings and timestamps written verbatim and
  no re-judging. Bounded archive parsing (ZIP-bomb and path-traversal guards,
  size ceilings), fail-closed reference-graph integrity checks, and a
  one-transaction restore with filesystem rollback. Password-hash export
  requires password reconfirmation, and each sensitive opt-in records its own
  admin-action audit event
- **web**: Permanently remove inactive contests — an UberAdmin-only workflow
  that deletes a contest across PostgreSQL, Valkey and the problem-artifact
  filesystem. A verified Valkey purge runs before any destructive change,
  problem artifacts move to a reversible same-root quarantine, and the contest
  graph is deleted in one transaction alongside a sanitized `contest_deleted`
  audit event; a pre-commit failure rolls back and restores the quarantine
- **web**: Add user photo and audio media storage — contest-user photo payloads
  move to a dedicated `users_media` table (public photo and avatar URLs
  unchanged), with validated MP3/OGG/WAV clip uploads, authenticated preview
  endpoints, admin management, staged previews, cache-safe media versions and
  past-contest mutation guards
- **web**: Allow general clarifications and announcements with no problem
  attached — `clarifications.problem_id` is now nullable, both contest forms
  default to a "General" option, and the UI labels such rows as General.
  Contest scoping for clarifications now goes through the author
  (`team_id` → `users.contest_id`) instead of through the problem, so the
  reaper, dashboards, counters, timeline export, backup export, and contest
  removal all cover general rows
- **scoreboard**: Add site filtering — a validated `site_id` query filter trims
  scoreboard snapshots on the backend while preserving global ranks and the
  shared contest-wide cache. Assigned users get "All sites" / "My site only"
  buttons, unassigned users and uberadmins keep the site combobox, and
  single-site contests hide filtering entirely
- **images**: Enforce upload type and size limits — JPEG, PNG and WebP are
  detected from file signatures with `puremagic` instead of client-provided
  MIME types, and a shared streaming multipart limiter stops oversized uploads
  before route processing across Web and Arena. Adds
  `NOCA_IMAGE_MAX_FILE_SIZE` (2 MiB default, 5 MiB hard ceiling)
- **problems**: Support portable GIF illustrations — a deployment-independent
  problem-image contract with fixed 2 MiB and 2048×2048 limits applied to both
  manual uploads and package imports, so an exported problem stays valid across
  installations with different general image settings. Animated GIFs are
  re-encoded with all frames and timing intact and round-trip through problem
  packages
- **web**: Enforce a configurable audio upload limit —
  `NOCA_AUDIO_MAX_FILE_SIZE` (2 MiB default, 5 MiB hard ceiling) with a
  dedicated streaming multipart rule returning HTTP 413 before route
  processing, independent of the photo limit and displayed on the forms
- **web**: Label balloon and star assets with optional ASCII letter path
  segments, rendering the first letter uppercase with black or white text
  chosen by WCAG contrast, and show the labeled assets in scoreboard headers,
  problem details and task modals
- **shared**: Add `shared/services/balloon_assets.py`, a framework-agnostic
  balloon/star SVG renderer (templates, hex/letter normalization, WCAG contrast
  text color, memoized rendering) as the cross-module source of truth
- **web**: Improve contest reports — merge the two time-window charts into one
  stacked AC vs non-AC bar, add client-side Team/Total/AC ordering to "Runs by
  Team and Problem" ranked by a sample-size-aware Wilson score, add captions to
  all nine reports, and add a reusable balloon-thumbnail class
- **web**: Make clarification and runs tables sortable by time and problem via
  a server-side `sort_by` query param, preserved across HTMX polling
- **web**: Make clarification rows full width and clickable, opening the detail
  modal on click or Enter/Space
- **web**: Highlight the viewer's own scoreboard row, auto-scroll it into view,
  and add a floating back-to-top button
- **web**: Allow editing contest user credentials (email and password) after
  the contest ends, via a credentials-only service that leaves profile fields
  frozen

### Bug Fixes

- **autojudge**: Fence worker writes on a per-attempt claim — the Valkey lock
  did not make a run single-writer, so a slow-but-alive worker crossing the
  reaper's stale threshold could execute concurrently with its replacement and
  bury a committed verdict behind a duplicate-key `FAILED`. Every
  `set_*_dispatched` now stamps an attempt-scoped `attempt_token` on all four
  worker-owned run tables, and each later write is fenced on the token it
  stamped; result inserts fold the ownership test into the INSERT's source. A
  lost claim raises `JudgmentOwnershipLost` and aborts dispatch quietly instead
  of persisting `FAILED`. Adds a nullable `attempt_token` column to
  `submission_judgments`, `arena_submission_judgments`, `profiling_runs` and
  `solution_test_runs`
- **autojudge**: Prevent duplicate reconciliation dispatch — atomically
  revalidate inflight membership, stale deadlines, tombstones and lock
  ownership before recovery mutates queue state, and use attempt-scoped lock
  tokens so an older worker cannot remove a replacement worker's claim
- **web**: Keep every Runs column in view — the auto table layout let unsized
  Problem and Team columns push the last columns past the viewport. The table
  now uses a fixed layout with explicit widths from the shared `.noca-col-*`
  scale, and J1/J2/My-verdict columns are hidden in autojudge-only contests
  where they can never hold a value
- **audit**: Record usernames for security events — admin-action producers must
  now snapshot a human-readable actor label alongside the opaque user ID (Web
  records usernames, Arena records normalized email logins)
- **web**: Dismiss queued submission alerts after verdict by tagging the
  redirect with the queued submission ID and including submission IDs only in
  live-visibility SSE payloads, keeping scoreboard-frozen team payloads fully
  redacted
- **web**: Scope the report language tables to the contest's registered
  languages instead of every globally active language
- **web**: Name the team in balloon timeline events so the recipient is
  identifiable in the users-per-site report
- **web**: Make the reports activity chart responsive with a reusable
  full-width layout class and an accessible container label
- **web**: Add `Cache-Control` headers to the balloon and star SVG asset routes
  so the navbar logo and scoreboard balloons stop re-fetching on navigation
- **ui**: Show the configured media upload limits on Arena and Web upload
  surfaces instead of duplicating stale defaults in markup
- **shared**: Add the `judge:submissions` channel definitions used by the web
  submit route, restoring the web runtime import
- **web**: Avoid a contest service import cycle
- **web**: Seed general InterIF clarifications with a null problem ID, and make
  the InterIF seed backup-compatible by generating Markdown statement stubs and
  using real registry languages

### Performance

- **web**: Filter contest runs on the server — problem, team,
  autojudge-verdict and final-verdict predicates are applied in the submission
  query while preserving role-based visibility and SQL sort modes; the obsolete
  client-side filtering script is removed
- **web**: Poll the contest clock instead of streaming it — the clock endpoint
  returns a finite JSON snapshot for every request, freeing a persistent
  connection per tab from the browser's small per-origin pool
- **db**: Tune autovacuum for high-churn tables with a per-table migration for
  the submission/judging pipeline and the append-then-bulk-delete log tables

### Build

- **containers**: Fix missing scripts per image and split schema stewardship —
  `web` and `arena` remain stewards running `run_migrations.py`, while
  `autojudge`, `rating` and `aiassistant` become pure consumers running the new
  `scripts/wait_for_migrations.py`, bounded by
  `NOCA_WAIT_FOR_MIGRATIONS_TIMEOUT` (default 300s)
- Ignore missing type stubs for the untyped `markdown_sanitize` dependency

### Documentation

- **animator**: Add a phased implementation roadmap splitting the unified
  design into 22 dependency-ordered plans, and align the unified plan with the
  codebase and reveal engine
- **bootstrap**: Correct the development setup commands — use the tracked
  environment template, create storage directories safely, restore the
  advisory-lock migration runner, and drive privileged account creation from
  configured credentials instead of hard-coded secrets

## [14.1.0] - 2026-07-18

### Features

- **web**: Add bulk language selection controls (select-all / clear-all) to the
  contest creation and metadata edit templates, backed by a shared static
  script instead of inline JavaScript
- **arena**: Record signup IP and email reputation and notify admins — a new
  `arena_user_reputation` table stores one snapshot per user (signup IP always
  recorded, plus IP/email fraud scores and full JSON reports), a post-signup
  background task emails every `ARENA_ADMIN` a report, and admins review it on a
  new Reputation tab; adds `scripts/backfill_email_reputation.py`
- **shared**: Add an IPQualityScore IP reputation service returning proxy, VPN,
  Tor, crawler, mobile, abuse, and fraud-score signals
- **shared**: Add an IPQualityScore email-reputation service returning
  validity, disposable, suspect, fraud-score, and sanitized-email signals,
  gated on `NOCA_IPQUALITYSCORE_APIKEY`

### Bug Fixes

- **arena**: Show the output diff for PE verdicts — the test-result partial
  gated the output-mismatch comparison on WA only, so PE verdicts rendered as
  if passing; PE is now treated like WA via a shared `is_output_mismatch` flag
- **ui**: Keep problem print and sample test-case blocks readable in dark mode,
  and always render the web problem print view in light mode
- **web**: Avoid the deprecated `datetime.utcnow` in the queue time helper by
  deriving naive UTC from the aware timestamp

## [14.0.1] - 2026-07-17

### Bug Fixes

- **deploy**: Stop the Caddy header delete from wiping `X-Request-ID`, so
  `security_events.request_id` stays correlatable with Caddy access logs

### Documentation

- **crypto**: Ship `scripts/secrets_config.py` in the arena and aiassistant
  images and document the in-container `.env.crypto` bootstrap/rotation
  procedure in `CONFIG.md`, with pointers from `BOOTSTRAP.md` and `BACKUP.md`

## [14.0.0] - 2026-07-16

### ⚠ BREAKING CHANGES

- **validators**: Custom validators are now parametrized by test-case input:
  one container pair judges a whole submission and the judge replays the
  conversation once per test case, writing that case's input to the
  validator's stdin before the two sides talk. Existing validators must be
  rewritten to read their test-case input from stdin first
- **validators**: Interactive problems' test cases now carry **input and
  explanation only** (no expected output) and every case is **secret**;
  problem packages, ZIPs, and downloads for validator problems ship `.in`
  files alone. Packages built for the previous format must be regenerated
- **validators**: A problem with a configured validator must have zero public
  test cases and at least one secret one; staging a validator demotes public
  cases and the sample toggle is refused while a validator is configured
- **validators**: Removing a validator now requires an explicit
  `keep_interactions=true|false` choice on the removal endpoints

### Features

- **problems**: Add sample interactions for interactive problems:
  author-written transcripts (up to five per problem) rendered on the problem
  page, carried in packages as `interaction/NNN.interaction` + `.explain`
- **problems**: Preview the first 10 lines of a sample interaction
- **problems**: Add a print-friendly problem view to Arena and Web
- **validator**: Expose the effective problem limits and submitted language to
  custom validators as environment variables (`PROBLEM_TIME_LIMIT`,
  `PROBLEM_OUTPUT_LIMIT`, `PROBLEM_MEMORY_LIMIT`, `PROBLEM_PID_LIMIT`,
  `USER_LANGUAGE`, plus `PER_LANGUAGE_LIMITS` for Web contests)
- **validator**: Add a highlighted validator source viewer and name exported
  validator source files by language
- **web**: Require a chief judge whenever a contest has judges, and let admins
  and the chief judge work tasks, clarifications, and verdicts
- **arena**: Add a statistics tab to self and admin user profiles
- **arena**: Add admin force-rejudgment on the submission detail page
- **arena**: Restructure the admin sidebar/dashboard and add a Help menu
- **aiassistant**: Include interactive context in AI reviews
- **aiassistant**: Add configurable OpenAI reasoning effort
- **healthmonitor**: Add the health-monitoring module with public status and
  30-day uptime dashboards (port 8002)
- **audit**: Record request correlation metadata (client IP, source port,
  request ID) on security events
- **ui**: Unify web/arena identity, add dark mode, and centralize the NOCA
  brand; replace the arena module icon

### Bug Fixes

- **autojudge**: Stop the interactive exit race from failing correct solutions
- **validator**: Show runtime-failed validators as configured
- **arena**: Load language help on the right tab
- **arena**: Stop highlighting the Problems nav on the dashboard
- **templates**: Fix the explanation of the validator flow
- **ui**: Make monochrome devicons visible in dark mode

### Refactoring

- **web,arena**: Share the row-href script and make run rows clickable
- **arena**: Drop the unused require_arena_judge_or_admin dependency

### Documentation

- **packages**: Add the problem package format specification
- **custom-validator**: Port the sample validator to every judge language
- **healthmonitor**: Add the module to reinstall docs, ops scripts, and module
  lists

### Tests

- **arena**: Lock in readiness checks surviving a removed validator

## [13.3.0] - 2026-07-13

### Features

- **validators**: Add custom interactive validators for Contest and Arena
  problems: staged candidate revisions compiled and validated by the Autojudge,
  interactive judging that pipes the contestant and the validator together, and
  the exit-code-to-verdict mapping
- **validators**: Support zero-test-case validator problems, render the
  interactive attempt transcript, and polish the surrounding UI
- **transcript**: Tell the two sides of an interactive attempt apart in the
  rendered transcript
- **arena**: Show custom validator markers on problem listings
- **arena**: Document per-language stdout flushing on the languages help page
- **problems**: Add illustration images to Contest problems
- **import**: Share the package-format documentation and offer a sample package

### Bug Fixes

- **arena**: Add the privacy link to the footer and resolve both legal links by
  route name
- **web**: Handle empty login form credentials

### Refactoring

- **arena**: Rebuild the problem editor around six cards and a single Save

### Documentation

- **arena**: Explain interactive problems and custom validators in the judgment
  verdicts help tab
- **web**: Document problem image exports

## [13.2.1] - 2026-07-10

### Features

- **login-history**: Record structured IP geolocation
- **arena**: Block mailbox-alias duplicate sign-ups

### Bug Fixes

- **security-events**: Record actor login on remaining auth events

## [13.2.0] - 2026-07-09

### Features

- **judge**: Add Perl language support with registry defaults, syntax-check
  compilation, runtime commands, editor highlighting, starter code, judge
  compile/run images, build targets, language reference docs, and smoke-test
  sample coverage

### Documentation

- **skills**: Document that the bump-version workflow runs mypy, lint, and
  formatting before proceeding with the release commit

## [13.1.6] - 2026-07-09

### Features

- **admin-submissions**: Add a status filter to the dashboard submissions list
- **security-events**: Record and display the actor login for authentication
  events
- **autojudge**: Persist exit signals and retry suspicious sandbox kills

### Bug Fixes

- **geolocation**: Deduplicate repeated location field names

## [13.1.5] - 2026-07-08

### Bug Fixes

- **autojudge**: Raise the isolate `num_boxes` limit to 1000 in run images

### CI

- **containers**: Publish images to both registries in a single build

## [13.1.4] - 2026-07-08

### Features

- **arena**: Enrich the admin user profile with earned badges, notifications and
  submissions
- **arena**: Show the submission owner on the submission detail page
- **security**: Audit authentication and email lifecycle events

### Bug Fixes

- **autojudge**: Strip NUL bytes from captured output before writing to the
  database
- **autojudge**: Allocate a unique isolate box-id per container

## [13.1.3] - 2026-07-07

### Features

- **arena**: Gate Arena rating, statistics, public solver counts and the
  first-solver badge by problem ownership instead of role — every user's
  submissions count for problems they do not own, regardless of role, and only
  the problem owner is excluded; drop the vestigial role argument, the now-dead
  `arena_users` joins, and the unused `only_users` flag
- **arena**: Replace the dashboard "Random Problems" card with a "Latest
  Problems" card listing the 10 most recently created or edited enabled problems
  (ordered by `updated_at`, showing relative edit time), rename the "Top Users"
  card to "Leaderboard" with the top 10 users, and let both cards grow to their
  content

### Documentation

- **arena**: Add the badge catalog documenting each Arena badge awarded by the
  rating worker, including image filename and achievement description

## [13.1.2] - 2026-07-03

### Bug Fixes

- **security**: Allow Cloudflare's Web Analytics beacon in the CSP
  (`static.cloudflareinsights.com` in `script-src`, `cloudflareinsights.com` in
  `connect-src`) so enforcing mode does not block the edge-injected script, and
  document the reverse-proxy trust failure mode (`NOCA_FORWARDED_ALLOW_IPS`) that
  breaks `request.url_for()` scheme and client-IP handling behind a containerized
  proxy; fetch the Bootstrap CSS source map to silence the DevTools 404
- **rating**: Exclude `ARENA_ADMIN`/`ARENA_JUDGE` from the first-solver badge

## [13.1.1] - 2026-07-03

### Bug Fixes

- **shared**: Type security-event row mappings with SQLAlchemy's `RowMapping`
  so the all-module mypy CI job passes

## [13.1.0] - 2026-07-03

### Features

- **security**: Add Valkey-backed auth throttling with a fail-open in-memory
  fallback for Web and Arena login, password reset, signup, 2FA, activation,
  and consent flows
- **security**: Add the `security_events` audit log, module-scoped admin
  viewers, retention reapers, and privileged admin-action recording for Web
  and Arena
- **security**: Add shared browser security headers, production secure-cookie
  validation, proxy-correct client IP handling, default-deny Web auth,
  testcase path guards, and AI review guardrails
- **arena**: Add 10 new gamification badges for language breadth, first-solver
  activity, hand-in timing, burst solving, trimming attempts, and related
  achievements
- **arena**: Add compile logs, failing testcase context, expected output,
  stderr, and existing AI review context to teacher batch-feedback reviews

### Bug Fixes

- **arena**: Hide signup account enumeration by returning the same success
  response for existing accounts and sending an out-of-band account-exists
  email
- **arena**: Unify needs-feedback logic across problem-list and student-report
  pages

### Documentation

- **aiassistant**: Update AI assistant and AI review flow documentation for the
  current worker behavior
- **config**: Document the new security-event retention settings and related
  Web/Arena service routes

## [13.0.0] - 2026-07-01

### BREAKING CHANGES

- **containers**: Upgrade judge runtimes (Node 24, Temurin 25, Ruby 4.0, Rust 1.96, .NET 10, Lua 5.5). Contestant-visible language runtimes change major/minor versions across the board (Node 22->24, JDK/Temurin 21->25, Ruby 3.3->4.0, Rust 1.94->1.96, .NET 8->10, Lua 5.4->5.5). Existing accepted solutions that rely on removed/changed standard-library behavior, deprecated APIs, or version-specific compiler/runtime quirks may need resubmission. Judge images must be rebuilt and `shared/language_configs.py` reseeded via `scripts/bootstrap_languages.py` before deploying.

### Features

- **arena**: Add teacher batch feedback page for problem-set problems
- **arena**: Redirect batch feedback to problem list, allow removing teacher feedback, show source line numbers

### Bug Fixes

- **arena**: Base needs-feedback badge on most recent non-AC submission
- **arena**: Render KaTeX CSS on batch feedback page and correct needs-feedback count

### Build & Infrastructure

- **ci**: Authenticate setup-uv GitHub API calls to avoid rate limits

## [12.4.1] - 2026-07-01

### Bug Fixes

- **static**: Shorten CSS/JS cache lifetime to bound rollout staleness
- **tests**: Fix contest admin template path tests

### Build & Infrastructure

- **ci**: Add validation and image publishing workflows
- **ci**: Add source labels to container images

## [12.4.0] - 2026-07-01

### Features

- **arena**: Lock down Arena pages with default-deny access control
- **health**: Rate limit public health endpoints
- **rating**: Gate problem-difficulty pivot by per-problem attempt count
- **rating**: Snapshot problem-difficulty distribution as a histogram

## [12.3.2] - 2026-06-28

### Bug Fixes

- **ui**: Stack sample test case blocks
- **autojudge**: Move `-lm` after source file and add `-D_GNU_SOURCE` for C
- **aiassistant**: Map unsupported file extensions before OpenAI upload

### Performance

- **judge**: Add JVM determinism flags to Java run command

## [12.3.1] - 2026-06-27

### Bug Fixes

- **arena**: Use standalone error template and silence DB-down log flood
- **arena**: Eagerly load affiliation on public profile to avoid async lazy-load error
- **aiassistant**: Refund platform credit on batch failure and add AI Usage status dashboard

### Refactoring

- **arena**: Rename AI Credits route and template to AI Usage

## [12.3.0] - 2026-06-22

### Features

- **arena**: Add public Arena user profile page with precomputed statistics (verdict distribution, language breakdown, recent activity)
- **arena**: Add `public_profile` opt-in flag for Arena users to control profile visibility
- **arena**: Add problem-count badges for 10, 25, 100, and 500 distinct solved problems
- **arena**: Show earned gamification badges on user profile pages
- **arena**: Link admin problem numbers to their detail pages
- **arena**: Add user badge persistence for gamification (append-only `arena_user_badges` ledger)
- **rating**: Award Arena gamification badges from a dedicated badge-assignment loop in the rating worker

## [12.2.0] - 2026-06-21

### Features

- **ops**: Add unattended backup and restore workflows for PostgreSQL, Valkey,
  deployment configuration, and bind-mounted data
- **ops**: Add local backup retention, Restic uploads, remote snapshot
  retention, and snapshot-based restores
- **arena**: Add Valkey-backed online presence indicators, heartbeat and status
  endpoints, and an authenticated footer counter
- **arena**: Stream owner-scoped profile submission status updates with polling
  fallback and accepted-submission celebrations

### Bug Fixes

- **ops**: Require Restic credentials and repository configuration through
  environment variables
- **ops**: Archive deployment configuration file contents instead of dangling
  relative symlinks

## [12.1.0] - 2026-06-20

### Features

#### Test Cases
- **tc**: Unify the test-case editing UI across the web and arena modules for a consistent experience
- **tc**: Accept `.sol` as an alternative output extension in test case ZIP uploads
- **web**: Add a quick-submit form to the problem detail page

### Bug Fixes

- **submissions**: Reject binary source uploads
- **arena**: Restore sample test-case content on the problem detail page
- **arena**: Read sample test case content from the filesystem in problem detail
- **arena**: Offload test case filesystem reads to a thread to avoid blocking the event loop
- **web**: Persist test case sizes during problem import

### Styles

- **web**: Update the navbar balloon brand color

## [12.0.2] - 2026-06-19

### Bug Fixes

- **arena**: Fix lifespan test mocks to cover `ensure_sem_afiliacao` — tests for JWT issuer and image service avatar size were failing because the new startup seed call was not mocked; mock added to `_configure_lifespan_mocks`

### Features

#### Arena — Affiliations
- **arena**: Add `exclude_from_ranking` flag to affiliations — admins can mark an affiliation so its members are excluded from the ranking; flag is editable on the affiliation admin page
- **arena**: Add "No affiliation" checkbox to affiliation change modal — users can opt out of any affiliation; backend stores this as a null affiliation reference
- **arena**: Upsert "Sem afiliação" affiliation on startup — the built-in no-affiliation entry is created or refreshed with `exclude_from_ranking=True` on every startup, ensuring it is always present and correctly configured

#### Arena — Profile
- **arena**: Prompt profile completion after login — users who have not set an affiliation are prompted to complete their profile on the next login

### Bug Fixes (continued)

- **arena**: Exclude inactive users from class members list and problem set report

## [12.0.1] - 2026-06-19

### Bug Fixes

#### AI Assistant
- **aiassistant**: Clean up terminal Valkey jobs — atomic terminal cleanup removes duplicate pending/inflight entries, dispatch timestamps, and `ai:job` metadata hashes; cleanup is buffered through `ValkeyRuntime` so it is replayed after recoverable outages; applied after successful online reviews, durable batch staging, idempotent exits, non-retryable failures, and retry-limit discards

### Features

#### AI Assistant
- **aiassistant**: Add flush-now and poll-now trigger commands — two one-shot admin dashboard commands wake the batch flusher and batch poller immediately instead of waiting for the next scheduled window; commands use the existing HMAC-signed Valkey transport; trigger events interrupt inter-cycle sleep via `interruptible_sleep`; an `arena_worker_command_audit` row is committed before publishing and updated with the transport outcome; buttons appear only on aiassistant worker cards when `pause_enabled`

### Fixes

- **docs**: Fix `MIGRATION.md` step 5 that omitted `rating` and `aiassistant` from the image pull/build command, causing those containers to restart-loop with "Can't locate revision" after a 12.0.0 deployment

## [12.0.0] - 2026-06-19

### Breaking Changes

- **arena**: Move Arena test-case storage from database to shared filesystem — `input_content`/`output_content` columns dropped; test-case content is now stored under `NOCA_PROBLEM_TESTCASE_DIR/arena/<problem_id>/`; inline editing is gated to ≤10 KB; larger cases use ZIP download/replace round-trip
- **arena**: Separate problem ownership from authorship — problems now have a distinct `owner` (who can manage it) and `author` (credit field); existing data migrated; `can_edit` flag added for granular editing control

### Breaking Changes

- **arena**: Move Arena test-case storage from database to shared filesystem — `input_content`/`output_content` columns dropped; test-case content is now stored under `NOCA_PROBLEM_TESTCASE_DIR/arena/<problem_id>/`; inline editing is gated to ≤10 KB; larger cases use ZIP download/replace round-trip
- **arena**: Separate problem ownership from authorship — problems now have a distinct `owner` (who can manage it) and `author` (credit field); existing data migrated; `can_edit` flag added for granular editing control

### Features

#### Arena — AI Review
- Show AI review turnaround time on submission detail
- Show AI batch turnaround statistics on admin dashboard
- Confirm AI review requests before submission
- Show pending batch job count on AI assistant worker card
- Adapt AI review turnaround display units (seconds/minutes/hours)

#### Arena — Classes & Problem Sets
- Enforce problem set dates to fall within the class period
- Inline problem sets on class detail page
- Combine description editing with problem-set schedule update
- Notify teachers when students request class registration
- Add best-effort email notifications for class membership events
- Add problem removal request and class membership notifications
- Improve class and problem-set page UI
- Improve problem set list and report pages

#### Arena — Problems
- Add optional license field to Arena problems
- Add prev/next problem navigation on problem detail page
- Add danger zone to problem edit page (delete and rejudge actions)
- Link category name to filtered problem list
- Add AC rate and solved count/status columns to problem list
- Sort problems by solver count
- Make table rows clickable in problem and class problem-set lists
- Improve problem detail editor UX
- Add resizable problem workspace

#### Arena — Users & Profiles
- Add ranking_visible flag to Arena users
- Allow profile date of birth updates
- Add submission heatmap to user profile; improve heatmap and add to admin user profile
- Show affiliation logos in user ranking pages
- Show avatars in class student tables
- Replace emoji country flags with SVG images
- Improve user management profiles

#### Arena — Admin Dashboard
- Add Admin Dashboard sub-nav with AI Credits Usage page
- Add global login history and submission list admin pages
- Add admin user login history
- Enhance admin dashboard submission, login and credit views
- Show user origin in live feed
- Show pagination controls above tables, not only below
- Include problem title in export filename

#### Arena — Workers & Infrastructure
- Add authenticated worker pause/resume control (HMAC-signed Valkey nudge, PostgreSQL authoritative)
- Add worker presence dashboard
- Add worker status page
- Add last-job column to worker dashboard
- Add HTMX OOB flash support, worker dashboard CSS, and queue metric helpers
- Show pending batch jobs count on AI assistant worker card
- Require admin password confirmation for sensitive user actions
- Redirect expired HTMX sessions
- Show max output size on submission detail
- Add security event email notifications
- Show supported languages card on dashboard
- Add teacher feedback on submissions
- Add can_edit flag for granular problem-base editing

#### AI Assistant
- Accumulate platform-key AI reviews into windowed OpenAI Batch API jobs
- Expire stale OpenAI batch reviews with automatic credit refund
- Publish batch turnaround statistics to Valkey for Arena dashboard

#### Languages
- Add Swift as a judged language
- Add Ruby and Bash as supported judge languages

#### Rating
- Bimodal contrast algorithm for Arena problem difficulty
- Drop solve-velocity age component; add contrast chart
- Exclude `ARENA_JUDGE` and `ARENA_ADMIN` roles from all rating calculations

#### Runtime & Infrastructure
- Centralize branded error handling across all modules
- Handle backend outages (DB/Valkey down) gracefully
- Add wait-for-db/Valkey readiness checks at startup across all modules

#### Assets & UI
- Replace devicon CSS icons with self-hosted SVGs (assets and live-feed)
- Unify Flatpickr date pickers via shared init script
- Make arena-table denser; restore density on worker table

#### Email
- Add mbox audit log of all delivered emails
- Report mbox logging configuration on startup

### Bug Fixes

- **arena**: Correct language icon sizing in submission detail and dashboard
- **arena**: Correct problem-set report visibility and student verdicts
- **arena**: Correct route name for class membership notifications
- **arena**: Distinguish problem difficulty from user rating
- **arena**: Exclude admin and problem author from rating, counting, and statistics
- **arena**: Exclude staff from public problem stats
- **arena**: Exclude teacher's own class from open-registration list; guard service endpoint
- **arena**: Hide stop-now button and guard route when problem set is closed
- **arena**: Improve class management UI
- **arena**: Record completed 2FA and backup-code logins in history
- **arena**: Remove duplicate CSS, fix ZIP extraction
- **arena**: Resolve two class-page bugs
- **arena**: Restore notification click navigation and set missing target URLs
- **arena**: Skip past-date validation when class dates are unchanged on edit
- **arena**: Suppress feedback badge when student has an AC submission
- **email**: Encode plain-text email parts as quoted-printable instead of base64
- **judge**: Normalize CRLF line endings in test-case content before comparison
- **rating**: Pin zero-attempt problems to display rating of 5.0
- **shared**: Add ace/highlightjs language mappings; fix devicon icons for Ruby and Bash
- **workers**: Silence reload shutdown tracebacks
- **db**: Cascade delete from test cases to result rows; warn on TC edit when submissions exist

### Refactoring

- **aiassistant**: Split `batch_poller` into three focused modules
- **arena**: Extract problem admin form presentation helpers
- **arena**: Remove redundant problem-set list route
- **arena**: Replace tabbed class list with dedicated sub-routes
- **arena**: Split `admin_users` routes and eliminate nav-state repetition
- **arena**: Split `arena_users` model and dedupe location helpers
- **autojudge**: Split `worker.py` into `dispatch`, `reconcile`, and `decode` modules
- **db**: Drop unused generic timestamp columns and reconcile schema drift
- **shared**: Split `language_registry` into three focused modules

### Performance

- **auth**: Use bigint IDs for login history table for improved scalability

### Build & Infrastructure

- **containers**: Upgrade judge isolate to 2.6
- **containers**: Introduce `assets-base` image to fetch vendor assets once per release
- **containers**: Drop PowerShell build script; make assets platform-configurable
- **containers**: Harden `uv sync` against PyPI timeouts
- **build**: Harden multi-platform push builds for reliability
- **swift**: Fetch static Swift SDK via curl with retry, then drop curl from image

## [11.8.0] - 2026-06-18

### Features

#### AI Assistant
- Accumulate platform-key AI reviews into windowed OpenAI Batch API jobs
- Expire stale OpenAI batch reviews with automatic credit refund
- Publish batch turnaround statistics to Valkey for Arena dashboard

#### Arena — AI Review
- Show AI review turnaround time on submission detail
- Show AI batch turnaround statistics on admin dashboard
- Adapt AI review turnaround display units (seconds/minutes/hours)
- Show pending batch job count on AI assistant worker card

#### Arena — Problems
- Improve problem detail editor UX
- Make table rows clickable in problem and class problem-set lists

#### Arena — Classes & Problem Sets
- Improve class and problem-set page UI

### Bug Fixes

- **arena**: Correct language icon sizing in submission detail and dashboard
- **arena**: Distinguish problem difficulty from user rating
- **arena**: Skip past-date validation when class dates are unchanged on edit
- **arena**: Resolve two class-page bugs

### Refactoring

- **arena**: Remove redundant problem-set list route

## [11.7.2] - 2026-06-17

### Features

#### Arena
- Replace CSS devicon icons with self-hosted SVGs in live feed and assets
- Add supported languages card on dashboard

#### Languages
- Add Ruby and Bash as supported judge languages

### Bug Fixes

- **arena**: Exclude admin and problem author from rating, counting, and statistics
- **arena**: Fix Swift icon
- **email**: Encode plain-text email parts as quoted-printable instead of base64

## [11.7.1] - 2026-06-16

### Build & Infrastructure

- **swift**: Fetch static Swift SDK via curl with retry, then drop curl from image
- **build**: Harden multi-platform push builds for reliability

## [11.7.0] - 2026-06-16

### Features

#### Languages
- Add Swift as a judged language

#### Arena
- Adjust activity heatmap cell size

## [11.6.2] - 2026-06-16

### Features

#### Arena
- Sort problems by solver count
- Add best-effort email notifications for class membership events

### Bug Fixes

- **arena**: Restore notification click navigation and set missing target URLs
- **arena**: Exclude staff from public problem stats

## [11.6.1] - 2026-06-15

### Features

#### Arena — Admin Dashboard
- Enhance admin dashboard submission, login and credit views

#### Arena
- Record completed 2FA and backup-code logins in history

#### UI
- Unify Flatpickr date pickers via shared init script

## [11.6.0] - 2026-06-15

### Features

#### Arena — Admin Dashboard
- Add global login history and submission list admin pages

#### Rating
- Exclude `ARENA_JUDGE` and `ARENA_ADMIN` roles from all rating calculations

### Bug Fixes

- **rating**: Pin zero-attempt problems to display rating of 5.0

## [11.5.2] - 2026-06-14

### Features

#### Arena — Admin Dashboard
- Add Admin Dashboard sub-nav with AI Credits Usage page
- Add admin user login history

#### Arena — AI Review
- Confirm AI review requests before submission

#### Arena — Classes & Problem Sets
- Improve problem set list and report pages
- Enforce problem set dates to fall within the class period
- Inline problem sets on class detail page
- Add prev/next problem navigation on problem detail page

### Bug Fixes

- **arena**: Exclude teacher's own class from open-registration list; guard service endpoint
- **arena**: Hide stop-now button and guard route when problem set is closed

### Performance

- **auth**: Use bigint IDs for login history table for improved scalability

## [11.5.1] - 2026-06-13

### Features

#### Arena — Problems
- Link category name to filtered problem list
- Add AC rate, solved count, and solved status columns to problem list
- Add ranking_visible flag to Arena users

#### Arena
- Improve submission heatmap and add to admin user profile

#### Build
- Harden `uv sync` against PyPI timeouts

#### UI
- Make arena-table denser; restore density on worker table

## [11.5.0] - 2026-06-12

### Features

#### Arena
- Add submission heatmap to user profile
- Add resizable problem workspace
- Add security event email notifications

#### Email
- Add mbox audit log of all delivered emails

### Bug Fixes

- **judge**: Normalize CRLF line endings in test-case content before comparison
- **email**: Use quoted-printable encoding for plain-text parts
- **arena**: Suppress feedback badge when student has an AC submission

## [11.4.0] - 2026-06-12

### Features

#### Arena — Workers & Infrastructure
- Add authenticated worker pause/resume control (HMAC-signed Valkey nudge, PostgreSQL authoritative)
- Add worker presence dashboard
- Add worker status page
- Add last-job column to worker dashboard
- Add HTMX OOB flash support, worker dashboard CSS, and queue metric helpers

#### Arena
- Show user origin in live feed

### Bug Fixes

- **arena**: Redirect expired HTMX sessions

### Refactoring

- **arena**: Replace tabbed class list with dedicated sub-routes
- **db**: Drop unused generic timestamp columns and reconcile schema drift

## [11.3.0] - 2026-06-11

### Features

#### Arena
- Add teacher feedback on submissions
- Show avatars in class student tables
- Show max output size on submission detail

#### Rating
- Bimodal contrast algorithm for Arena problem difficulty
- Drop solve-velocity age component; add contrast chart

#### Email
- Add mbox audit log of all delivered emails
- Report mbox logging configuration on startup

### Bug Fixes

- **arena**: Correct route name for class membership notifications
- **arena**: Correct problem-set report visibility and student verdicts

## [11.2.0] - 2026-06-08

### Features

#### Arena
- Include problem title in export filename
- Show pagination controls above tables, not only below

### Bug Fixes

- **db**: Cascade delete from test cases to result rows; warn on TC edit when submissions exist

### Build & Infrastructure

- **containers**: Upgrade judge isolate to 2.6
- **containers**: Introduce `assets-base` image to fetch vendor assets once per release
- **containers**: Drop PowerShell build script; make assets platform-configurable

## [11.1.0] - 2026-06-06

### Features

#### Arena — Workers & Infrastructure
- Centralize branded error handling across all modules
- Handle backend outages (DB/Valkey down) gracefully
- Add wait-for-db/Valkey readiness checks at startup across all modules

#### Arena — Users & Profiles
- Replace emoji country flags with SVG images
- Allow profile date of birth updates
- Improve user management profiles

#### Arena — Admin
- Require admin password confirmation for sensitive user actions
- Show affiliation logos in user ranking pages
- Add danger zone to problem edit page (delete and rejudge actions)

#### Arena — Classes & Problem Sets
- Notify teachers when students request class registration
- Combine description editing with problem-set schedule update
- Add problem removal request and class membership notifications

#### Arena
- Add can_edit flag for granular problem-base editing

### Bug Fixes

- **arena**: Remove duplicate CSS; fix ZIP extraction
- **arena**: Improve class management UI
- **workers**: Silence reload shutdown tracebacks

### Refactoring

- **arena**: Split admin_users routes and eliminate nav-state repetition
- **arena**: Split arena_users model and dedupe location helpers
- **arena**: Extract problem admin form presentation helpers
- **autojudge**: Split `worker.py` into `dispatch`, `reconcile`, and `decode` modules
- **aiassistant**: Split `batch_poller` into three focused modules
- **shared**: Split `language_registry` into three focused modules

## [11.0.0] - 2026-06-05

### Features

#### Arena — Classes & Problem Sets
- Add Class and Problem Set concepts
- Teacher drill-down into student submissions from problem-set report
- Manage problem-set schedule inline; add stop-now action

#### Arena
- Display datetimes in user timezone
- Replace native date/time pickers with Flatpickr v4
- Add public live submission feed
- Custom HTML 404 page with random illustration
- Make Arena live feed configurable and mask identity

#### Logging
- Log resolved settings at module startup

### Refactoring

- **css**: Split arena.css and contest.css into themed partials
- **static**: Extract shared CSS to common.css; rename app.css to contest.css
- **static**: Consolidate duplicated JS into shared module
- **arena**: Split services into cohesive service modules
- **live-feed**: Extract shared SSE and query helpers

## [10.3.0] - 2026-06-03

### Features

#### Arena
- Add per-team (web) and per-user (arena) submission rate limiting

#### Problems
- Auto-assign predefined balloon color on import; consolidate palette
- Add quick sample/secret toggle on test-case lists

#### UI
- Rebrand web UI copy from NOCA to Noca Contest
- Replace text action labels with icon btn-group on test-case lists

## [10.2.0] - 2026-06-02

### Features

#### Arena
- Add notes field to Arena problems; remap import `author` → `source`

#### Test Cases
- Add optional explanation field to test cases

### Bug Fixes

- **autojudge,rating**: Suppress verbose tracebacks on transient DB connection failures in background loops

## [10.1.0] - 2026-06-01

### Features

#### Arena
- Add navbar logo branding and favicon assets
- Add per-problem statistics page
- Show problem author link to admins on edit form
- Add Edit button on problem detail page for admins and authors
- Add problem ZIP import and export

#### Shared
- Update default language stubs to echo input

### Performance

- **web**: Offload ZIP export assembly to background thread

## [10.0.0] - 2026-05-31

### Breaking Changes

- **config**: Add `NOCA_LOG_LEVEL` support across all runtime modules — log-level configuration is now unified under this variable

### Features

#### Arena
- Replace solution textarea with Ace code editor
- Add favorite problems feature
- Redirect browsers to login on 401/403 with next-URL round-trip
- Add bootstrap script to create initial Arena admin user

### Bug Fixes

- **arena**: Inline pending test cases on problem edit; default sort by number

## [9.1.0] - 2026-05-30

### Features

#### Arena — AI Review
- Add platform-funded batch review path via OpenAI Batch API
- Notify users when AI reviews complete
- Include problem image in AI review context

#### Arena — Users & Profiles
- Add public ranking pages for users and affiliations
- Add user preferred language locale
- Merge Personal Data and Security into unified profile tab

#### Arena — Admin
- Add AI credit transactions ledger and admin top-up
- Add admin email confirmation and parental consent toggles
- Add category search filter; refactor admin list UI; improve slug generation
- Add mark-all-as-read for notifications

#### Arena
- Add submission detail view, submissions tab, AI review status, and edit-and-retry
- Add solve-velocity age factor to problem difficulty rating
- Add AI backend credits gate to AI review endpoint

#### Rating
- Publish scheduler metadata from worker

#### Build
- Centralize workspace version in root pyproject.toml
- Pin dependency versions

### Bug Fixes

- **config**: Rename `URL_BASE` to `WEB_URL_BASE` and `ARENA_URL_BASE` for module clarity
- **queue**: Recover AI review and judge jobs lost between DB commit and Valkey enqueue
- **arena**: Eagerly load affiliation on admin user fetch to prevent async lazy-load error
- **aiassistant**: Fix batch idempotency guard bypassed by terminal job rows

### Refactoring

- **config**: Namespace environment variables by module

## [9.0.0] - 2026-04-25

### Breaking Changes

- **license**: Change license from AGPL to NOCA NC License

### Features

#### Languages
- Add compiler/interpreter version field to language registry

### Build & Infrastructure

- Add copyright notice headers across script codebase
- Document multiplatform image building

## [8.2.0] - 2026-04-24

### Features

#### Auth
- Add Valkey-backed JWT revocation store on logout
- Clear invalid JWT cookies on next request

#### Uberadmin
- Add management interface with list, edit, and enable/disable actions
- Support inactive contest management

### Bug Fixes

- **autojudge**: Improve startup diagnostics for config errors and missing migrations

## [8.0.0] - 2026-04-22

### Features

#### Autojudge
- Add Prometheus telemetry exposition

### Bug Fixes

- **makefile**: Sync Makefile behavior on Linux and Windows

## [7.0.0] - 2026-04-20

### Features

#### Arena
- Add public Arena platform: signup flow, login/logout, 2FA, forced password reset, OTP-protected accounts
- Add Arena problem browsing, detail page, and sample test-case download
- Add admin CRUD for problems, test cases, and categories
- Add admin user management
- Add Arena submission judging via shared autojudge worker
- Add periodic problem difficulty and user rating computation
- Add user profile with location, affiliation, and rating history chart
- Add affiliation management with logo upload
- Add ToS/PP acceptance tracking, legal pages, and login gate
- Add LGPD parental consent age gate
- Add remember-me-aware session tokens and middleware refresh
- Add durable user notifications with Material Symbols icons
- Add AI code review infrastructure for Arena submissions
- Add photo crop on signup and profile photo change
- Add Arena rating history chart and split help pages
- Add Class and Problem Set scaffolding
- Add live feed with configurable identity masking
- Add rating help and languages help tabs

#### Languages
- Add Go, Rust, Lua, Haskell, Prolog, and Fortran language support

#### Autojudge
- Add startup banner; lazy-warm container pools on first submission per language
- Hot-reload worker on file changes in development

#### Rating
- Extract Arena rating into dedicated single-replica worker

#### Build
- Introduce shared base Docker images; reorganize Docker infrastructure
- Add Arena Docker compose service and aiassistant image
- Convert repo to uv workspace with per-module pyprojects

#### UI
- Add first balloon highlighting

### Bug Fixes

- **arena**: Password change fixes, flash rendering, JWT session refresh
- **arena**: Preflight crypto environment at startup
- **arena**: Fix dependencies for Fortran run container
- **valkey**: Make dequeue priority selection atomic
- **problem**: Avoid ordinal collisions on test-case removal
- **problem**: Warn before leaving unsaved problem edits
- **web**: Return to edited test case after save

### Refactoring

- **web**: Reorganize templates into role-based subdirectories
- **web**: Split oversized services and route modules into focused files
- **valkey**: Split Valkey service into focused modules
- **shared**: Split `db_schema` into package modules
- **autojudge**: Split large modules into focused single-responsibility files
- **static**: Organize utilities by module
- **arena**: Modularize dashboard cards

### License

- Update license to AGPL

## [6.7.0] - 2026-04-18

### Features

#### Auth
- Add sliding JWT session refresh

### Bug Fixes

- **auth**: Force full-page login redirect for HTMX requests
- **runs**: Repair team verdict refresh and AC confetti
- **autojudge**: Prevent lost jobs during worker recovery

## [6.6.0] - 2026-04-18

### Features

#### Runs
- Enrich final-verdict SSE updates and celebrate accepted runs with confetti

#### Dashboard
- Auto-refresh contest counters

#### Docs
- Add first version of the NOCA user manual

### Bug Fixes

- **types**: Replace mypy ignores with type-safe annotations

## [6.5.0] - 2026-04-13

### Features

#### Contest
- Allow editing contest languages before start
- Allow running contest limit edits

### Refactoring

- **logging**: Improve log messages for clarity and consistency

## [6.4.0] - 2026-04-13

### Features

#### Contest
- Sort live contests by remaining time on public dashboard
- Validate metadata duration doesn't set end time in the past
- Add end-now action with password confirmation

#### UI
- Add light/dark theme toggle
- Show balloon images in problem list and problem detail header

#### Dashboard
- Add pending-item counters to dashboard cards

### Bug Fixes

- **contest**: Fix uberadmin login link on contest login page
- **contest**: Simplify uberadmin contest card to single administration link
- **timeline**: Filter events past contest end and widen table columns
- **contest-timing-timeline**: Handle zero-percentage segments and relax boundary check

## [6.3.2] - 2026-04-12

### Refactoring

- **containers**: Extract service healthchecks

## [6.3.0] - 2026-04-11

### Features

#### Autojudge
- Support flat judge image naming

## [6.2.2] - 2026-04-11

### Features

#### Exports
- Add contest timeline export

## [6.2.1] - 2026-04-11

### Features

#### Contest
- Add contest timing timeline with dynamic progress visualization
- Enhance contest metadata forms with improved layout and radio options for settings

#### Admin
- Show profiling queue metrics on counters page

### Refactoring

- **web**: Extract contest tile macro and polish footer UI
- **web**: Extract flash message rendering macro

## [6.2.0] - 2026-04-10

### Features

#### Problem Editor
- Add LaTeX and Mermaid syntax support with help modal

## [6.1.1] - 2026-04-09

### Features

#### Problem
- Integrate KaTeX for rendering LaTeX equations in problem statements

### Bug Fixes

- **html**: Add extra_script block for JavaScript inclusion in multiple templates
- **migrations**: Fix downgrade revision

## [6.1.0] - 2026-04-09

### Bug Fixes

- **judge**: Move repetitions to per-language limits
- **autojudge**: Allow configuring run-container AppArmor profile

## [5.0.1] - 2026-04-09

### Bug Fixes

- **autojudge**: Sync canonical judge images at startup

## [5.0.0] - 2026-04-09

Initial public release of NOCA.
