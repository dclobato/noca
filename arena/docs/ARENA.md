# Arena Module — Current Implementation Snapshot

## Overview

Arena is now a real, standalone FastAPI application inside NOCA, not just a design plan. It owns its
own users, problem catalog, admin UI, and rating data, while still reusing shared infrastructure from
`shared/`, the `autojudge` worker for judging, and the `rating` worker for periodic rating
recomputation.

The currently implemented surface is strongest in these areas:

- account lifecycle and authentication
- user profile, security, location, and affiliation management
- admin management of users, categories, affiliations, problems, and test cases
- public problem browsing and sample test-case download
- rating computation infrastructure and rating-history visualization

The biggest missing piece today is the contestant-facing submission and rankings UI. The
backend data model and judging pipeline for submissions already exist, and public problem browsing
is implemented, but Arena does not yet expose submission or rankings routes.

---

## Runtime architecture

`arena/main.py` boots the Arena app with OpenAPI/docs disabled and wires these runtime services during
lifespan startup:

1. `SecretsManager` for encrypted TOTP secret storage (`EncryptedString`)
2. async SQLAlchemy engine + session factory
3. Arena Valkey runtime
4. Valkey-backed JWT revocation store
5. Arena JWT service (`issuer = "noca-arena"`)
6. shared email service
7. shared image processing service
8. QR code service for TOTP onboarding
9. IP geolocation service for login history
10. reverse-geocoder client state for profile location detection
11. Jinja environment with Arena globals and filters
12. a poller that mirrors rating-worker metadata from Valkey into app state

Arena mounts:

- `/static/css` from `arena/static/css`
- `/static/shared-css` from `shared/static/css`
- `/static/js` from `arena/static/js`
- `/static/shared-js` from `shared/static/js`
- `/static/vendor` from `shared/static/vendor`
- `/static/webfonts` from `shared/static/webfonts`

The app then includes routers from:

- `root`
- `auth`
- `legal`
- `help`
- `users`
- `user_security`
- `problems`
- `affiliations`
- `admin_categories`
- `admin_users`
- `admin_affiliations`
- `admin_problems`
- `admin_problem_tc`
- `admin_problem_api`

---

## Authentication and account lifecycle

Arena authentication is production-grade and already covers more than simple email/password login.

### Session model

- Session cookie: `arena_access_token`
- Session validation happens in `ArenaAuthMiddleware`
- Default LOGIN JWT lifetime: 1 hour
- "Remember me" opt-in sessions keep a 30-day persistent cookie, use 1-hour LOGIN JWTs, and rotate those tokens at half-life while the user remains active
- Remembered sessions stop rotating after an absolute 30-day cap from the original login time
- `get_current_arena_user` re-checks the token against live DB state using `user.get_token_id()`
- Any password change or admin-triggered session invalidation forces logout on the next request
- Logout revokes the JWT through Valkey

### Account gates

Arena currently enforces all of these before a user can keep a logged-in session:

- account must be active
- email must be confirmed
- date of birth must be present
- users under 13 are blocked
- users aged 13-17 require parental/legal-guardian consent

### Implemented auth flows

The `/auth/*` routes currently implement:

| Flow | Current behavior |
|---|---|
| Login | email/password login, password-age warning, and optional remember-me sessions that rotate 1-hour LOGIN JWTs while active, capped at 30 days total |
| Email confirmation | activation link via `/auth/activate` |
| Parental consent | guardian email flow via `/auth/parental-consent` |
| Terms gate | users who have not accepted ToS/Privacy are redirected to `/auth/accept-terms` after login |
| 2FA login | pending 2FA token in session, then TOTP or backup-code verification |
| Forced password change | separate pending password-change token flow |
| Password reset | request link + reset by JWT token |
| Recovery actions | resend activation, resend parental consent, update guardian email, regularize date of birth |

### 2FA and recovery

Arena supports:

- TOTP enrollment with QR code
- encrypted-at-rest OTP secret storage
- backup code generation, regeneration, and one-time display
- backup code consumption during login
- user-driven 2FA disable (password-confirmed)
- admin-driven 2FA disable with session invalidation

### Login history

Every successful login can record:

- timestamp
- IP address
- geolocated location string
- user agent
- login mode

The login service writes these records, and Arena admins can browse a user's
filtered, paginated history from the **Login History** tab on the admin user
profile.

---

## Current route surface

### Public and account-facing routes

| Area | Paths |
|---|---|
| Dashboard | `/`, `/dashboard` |
| Authentication | `/auth/login`, `/auth/logout`, `/auth/signup`, `/auth/activate`, `/auth/parental-consent`, `/auth/password-reset`, `/auth/2fa`, `/auth/change-password`, `/auth/accept-terms` |
| Problem browsing | `/problems`, `/problems/{arena_number}`, `/problems/{arena_number}/rating-history`, `/problems/{arena_number}/sample-testcases.zip`, `/problems/{arena_number}/submit` |
| Classes | `/classes`, `/classes/new`, `/classes/{class_id}`, `/classes/{class_id}/edit`, `/classes/{class_id}/members`, registration and membership actions under `/classes/*` |
| Affiliation assets | `/affiliations/{affiliation_id}/logo` |
| Profile pages | `/user/profile`, `/user/{user_id}/avatar`, `/user/{user_id}/photo` |
| Profile JSON APIs | `/user/profile/subdivisions`, `/user/profile/location`, `/user/profile/location/detect`, `/user/profile/affiliations/search`, `/user/profile/affiliation`, `/user/profile/rating-history` |
| User security | `/user/profile/2fa/setup`, `/user/profile/2fa/confirm`, `/user/profile/2fa/disable`, `/user/profile/backup-codes`, `/user/profile/backup-codes/regenerate` |
| Help | `/help/rating`, `/help/languages` |
| Legal | `/legal/terms`, `/legal/privacy` |
| Submissions | `/submissions/{submission_id}`, `/submissions/{submission_id}/request-ai-review` |
| Notifications (JSON) | `/arena/notifications`, `/arena/notifications/{notification_id}/read` |

### Admin routes

| Area | Paths |
|---|---|
| Affiliation management | `/admin/affiliations`, `/admin/affiliations/new`, `/admin/affiliations/{id}/edit`, `/admin/affiliations/{id}/delete` |
| Category management | `/admin/categories`, `/admin/categories/new`, `/admin/categories/{id}/edit`, `/admin/categories/{id}/delete` |
| User management | `/admin/users`, `/admin/users/{id}`, plus role/activation/password/photo/2FA/name/location/affiliation actions |
| Problem management | `/admin/problems`, `/admin/problems/new`, `/admin/problems/{id}/edit`, `/admin/problems/{id}/toggle-enabled` |
| Test case management | add/edit/delete test cases and ZIP replace under `/admin/problems/{problem_id}/testcases/*` |
| Admin JSON APIs | category search and problem rating history endpoints under `/admin/problems/*` |

### Role rules

- `ARENA_ADMIN`: full admin surface; may always add/edit problems on the Arena problem base
- `ARENA_JUDGE`: may manage classes and problem sets by default; managing the Arena problem
  base requires the `can_edit` grant (see below)
- `ARENA_USER`: standard authenticated user

**Problem-base editing (`can_edit`)** is a per-user boolean flag (default `False`) on
`arena_users`, separate from role. Adding/editing problems on the Arena problem base requires
`ARENA_ADMIN` **or** `can_edit=True` (enforced by `require_arena_problem_editor`); a plain
`ARENA_JUDGE` without `can_edit` is rejected. Non-admin editors remain scoped to problems they
own. An admin grants/revokes `can_edit` for any user (judge or regular user) from
the user-management UI (`POST /admin/users/{id}/toggle-can-edit`); the flag does not affect
class or problem-set management, which judges keep by default.

Judges do **not** get the user-admin surface; that remains admin-only.

---

## UI and current functional surface

### Dashboard

`/dashboard` currently renders four cards:

- Random Problems
- Top Users
- Top Countries
- Top Leagues

Only **Top Users** is backed by live database data today (`leaderboard_service.get_top_rated_users`).
The other three cards are still template placeholders/mock content.

### User profile

The profile page is already rich and practical. It includes:

- photo/avatar upload with client-side crop flow
- personal data summary
- user rating and 24-month rating history chart
- editable location and affiliation
- security tab for password change, 2FA enable/disable, and backup-code regeneration
- solved-problems and attempted-problems tabs with independent pagination

### Help and legal pages

- `/help/rating` documents the current Arena rating formula and configured interval/factor metadata
- `/help/languages` reads active languages from the shared `languages` table and explains verdicts
- legal pages render markdown documents from `arena/template/legal/`

### Admin UI

The Arena admin area is already substantial:

- paginated user list with search and role filtering
- user profile inspection with direct moderation actions
- affiliation CRUD with name/URL/country/subdivision validation, optional logo upload (1:1 crop, max 2 MB), and explicit user-detach on delete
- category CRUD with validated slugs/colors and linked-problem counts
- problem list with search, owner filter, category filter, rating sort, and enable/disable toggle
- problem form with separate owner and author metadata, optional public license, markdown statement
  editor, LaTeX and Mermaid support, optional image upload, and category assignment
- per-problem rating-history chart
- test case CRUD plus ZIP bulk replace

---

## Services currently present in `arena/`

| Service | Purpose |
|---|---|
| `arena_auth_service.py` | login/logout, login history, pending flow tokens |
| `arena_password_service.py` | password reset and basic profile updates |
| `user_service.py` | registration, email confirmation, parental consent, activation, session invalidation, ToS acceptance |
| `user_2fa_service.py` | TOTP setup/verification/disable logic |
| `backup2fa_service.py` | backup-code generation, consumption, cleanup |
| `qrcode_service.py` | QR code rendering for TOTP setup |
| `token_service.py` | Arena JWT configuration and action enum |
| `profile_location_service.py` | countries/subdivisions, reverse geocoding, affiliation search/update |
| `leaderboard_service.py` | top-rated user query for the dashboard |
| `user_progress_service.py` | solved/attempted problem lists for profiles |
| `pagination_service.py` | reusable pagination primitives |
| `admin_user_service.py` | admin-side user listing and moderation actions |
| `admin_category_service.py` | category validation and CRUD |
| `admin_affiliation_service.py` | affiliation validation and CRUD with explicit user-detach on delete |
| `admin_problem_service.py` | problem CRUD, filtering, category binding, and owner lookup |
| `admin_problem_tc_service.py` | test case CRUD and ZIP replacement |
| `problem_tc_export_service.py` | ZIP export of sample test cases |
| `session_service.py` | login-session token rotation for remember-me flows |
| `submission_list_service.py` | paginated submission history for user profiles |
| `submission_service.py` | submission row creation + autojudge job payload generation |
| `arena_class_service.py` | class creation/update, UI listings, teacher autocomplete, and class discovery |
| `arena_class_membership_service.py` | class membership (dated history) and registration-request workflow |
| `valkey_service.py` | Arena-specific Valkey runtime wiring |

---

## Data model

Arena now has a complete shared schema namespace in `shared/db_schema/arena/`.

### User and identity tables

| Table | Notes |
|---|---|
| `arena_users` | Arena identity, auth state, password/session state, rating summary, location, affiliation |
| `arena_affiliations` | externally managed affiliation catalog with optional rating |
| `arena_backup_2fa` | one-time recovery codes |
| `arena_login_history` | Immutable login audit records with a sequential BIGINT primary key and a `(arena_user_id, dta_login)` browsing index |

Notable user fields already in use:

- `session_version` for JWT invalidation
- `precisa_trocar_senha` for forced password changes
- `usa_2fa` and encrypted `_otp_secret`
- `user_rating` and `solved_problems`
- `country_code`, `subdivision_code`, `affiliation_id`
- `preferred_language_id` for the programming-language preference
- `prefered_language` for the user locale (`en-US` or `pt-BR`)

### Problem tables

| Table | Notes |
|---|---|
| `arena_problem_categories` | flat category taxonomy with color badges |
| `arena_problem_category_map` | many-to-many problem/category link |
| `arena_problems` | public number, limits, statement, owner, author, license, image, and enabled flag |
| `arena_test_cases` | DB-stored input/output text, ordered by `ordinal`, with `is_sample` |
| `arena_problem_ratings` | problem difficulty statistics and current rating |

Important problem-model behavior:

- new problems are created disabled
- `owner_id` identifies the managing user; `author` stores an optional external name, while
  `author_is_owner` resolves authorship from the owner's fullname
- `license` stores optional public license information of at most 256 characters
- statement content is validated as Arena-safe markdown
- problem images are optional and stored inline as base64 + MIME type
- categories are replace-updated through the junction table

### Submission and judging tables

| Table | Notes |
|---|---|
| `arena_submissions` | one row per submission attempt |
| `arena_submission_judgments` | autojudge-only judgment lifecycle |
| `arena_submission_test_results` | at most one row per judgment: the first non-AC test case |
| `arena_problem_solvers` | source of truth for first accepted solve per `(user, problem)` |
| `arena_problem_tried` | latest attempt timestamp per `(user, problem)` |

Arena intentionally uses an **autojudge-only** model:

- no human confirmation step
- no verdict override table
- no judgment audit trail table
- `final_verdict` is written directly from the autojudge result

### Class tables

| Table | Notes |
|---|---|
| `arena_classes` | a class owned by an assigned teacher (`ARENA_JUDGE`); `name`, optional `description`, `starts_on`/`finishes_on` (date only), `CHECK finishes_on >= starts_on`, `allow_self_registration` (default False) |
| `arena_class_memberships` | dated membership-status history; composite PK `(class_id, user_id, event_date)` so same-day flips overwrite; current status is the latest `event_date` row (`ACTIVE`/`REMOVED`) |
| `arena_class_registration_requests` | self-service join requests (`PENDING`/`APPROVED`/`DENIED`) with decider audit and optional `denial_reason`; partial unique index forbids duplicate pending requests per `(class, user)` |
| `arena_problem_sets` | **placeholder** registering the class → problem-set relationship; no problem-set behavior implemented yet |

Class behavior notes:

- a teacher direct-assign and an approved registration request both produce an `ACTIVE` membership
- only classes with `allow_self_registration = True` are listed for discovery and accept self-service requests
- only the assigned teacher (or an `ARENA_ADMIN`) may assign/remove others and decide requests
- a user may always remove themselves; members and the teacher/admin may list members with current rating

### Rating history tables

| Table | Notes |
|---|---|
| `arena_problem_rating_history` | append-only problem rating snapshots |
| `arena_user_rating_history` | append-only user rating snapshots |
| `arena_affiliation_rating_history` | append-only affiliation rating snapshots |

These history tables power the user and problem rating-history charts and are kept to a rolling
24-month window by the rating services.

---

## Ratings

Arena ratings are no longer just a concept; they are actively supported by a dedicated worker.

### Worker chain

The `rating` package runs three sequential loops:

1. recompute all problem ratings
2. recompute all user ratings from the fresh problem ratings
3. recompute all affiliation ratings from the fresh user ratings

This runs in a dedicated `noca-rating` process so multiple Arena web replicas do not compete for the
same scheduler responsibility.

### Arena web integration

The rating worker publishes:

- next scheduled update timestamp
- interval display text
- affiliation factor

to Valkey. Arena polls those keys into app state so templates can display scheduler metadata without
doing Valkey I/O per request.

### Current leaderboard behavior

`leaderboard_service.get_top_rated_users()` currently powers the dashboard card and:

- excludes inactive users
- excludes unconfirmed users
- excludes staff accounts (`ARENA_ADMIN`, `ARENA_JUDGE`)
- ranks by `user_rating`, then solved count, then creation time

---

## Judging and submissions backend

Arena already has a working backend path for submissions even though the public UI is still missing.

### What already exists

- `arena/services/submission_service.py` validates problem/language/test-case availability
- it creates `arena_submissions` and `arena_submission_judgments`
- it updates `arena_problem_tried` and the aggregate `attempted_users` counter (excluding
  `ARENA_ADMIN` and the problem owner; `ARENA_JUDGE` and regular users count)
- it emits an `ArenaSubmissionJob`
- `autojudge/arena_submission_job.py` compiles and judges Arena submissions
- the worker stores the first non-AC test result only
- first AC updates `arena_problem_solvers` for everyone (personal solved marker); problem rating
  counters (`solved_users`, `total_tries_before_solve`) are incremented only for submissions that
  count toward the problem — that is, excluding `ARENA_ADMIN` and the problem owner (`ARENA_JUDGE`
  and regular users count). The shared rule lives in
  `shared/services/arena_query_helpers.py` (`counts_toward_problem_rating` / `is_excluded_from_problem_rating`)

### What is still missing in Arena web

- public rankings pages
- AI review is implemented (submission detail page with request endpoint) but not yet surfaced in rankings or user profile tabs beyond the notification badge

---

## What is notably incomplete today

- public rankings pages are not implemented yet
- dashboard cards for top countries and top leagues are still static placeholders
- login history is available to administrators from each admin user profile

---

## Bottom line

Arena is no longer a future-phase design. It is already a working FastAPI module with robust auth,
profile/security flows, admin management for users/affiliations/categories/problems, public problem
browsing, shared judging/rating infrastructure, and a complete Arena-specific database schema.

What remains is mostly public rankings and per-user submission history tabs.
