# Changelog

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
