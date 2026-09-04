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
5. Arena JWT service (`issuer = settings.APP_NAME`, from `NOCA_ARENA_APP_NAME`)
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
- **Every** session slides: the middleware rotates the LOGIN JWT at half-life for as long as the user keeps making requests, so an active user is never logged out mid-task
- The client heartbeat in `shared/static/js/noca-presence.js` is what keeps a page that sits open without navigating (a long problem edit) inside that rotation window. It runs for every logged-in user regardless of `NOCA_ARENA_PRESENCE_ENABLED`, which governs only the green-dot refresh
- "Remember me" governs cookie **persistence** only: a 30-day `max_age` so the session survives a browser restart, versus a browser-session cookie that ends with the browser. It does not affect whether or for how long a session slides
- `NOCA_JWT_REFRESH_MAX_SESSION_SECONDS` optionally caps total session length for every session; `0` (the default) disables the cap
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

### Guardian consent withdrawal (LGPD art. 8 §5)

A parent or legal guardian may withdraw consent at any time, through a signed link in the
confirmation email they receive once they grant it. Opening the link renders a confirmation
page; the withdrawal itself is a `POST`, because mail scanners and link prefetchers follow
links in email and a `GET` that suspends an account would be triggered by a robot.

**Granting follows the same shape.** The link in the consent invitation opens a review
page that explains what consent enables, shows only the child's masked address, and
mutates nothing -- not even throttle accounting; only the guardian's explicit `POST`
records the grant, re-validating the token under a row lock at submission time. On both
pages the secondary action returns to the public dashboard without touching the account,
and the two primary buttons carry distinct copy so the actions cannot be confused. A
robot following either emailed link can therefore neither grant nor withdraw consent.

**Withdrawal suspends; it does not erase.** The account is deactivated, live sessions end
immediately, and both public-identity opt-ins are cleared — but submissions, verdicts,
badges, ratings and class memberships are all kept, and the account works again as soon as
consent is granted once more. Erasure is a separate right with its own flow. Two paths
restore an account: the child attempts a login, lands on the pending-parental screen, and
triggers a fresh consent email; or an administrator grants consent from the user profile.

`ranking_visible` is deliberately left alone. The public user ranking already drops the row
through `ativo`, while the affiliation aggregation filters on `ranking_visible` alone — so
a suspended user still counts toward their institution's rating, and clearing the flag
would move a third party's score as a side effect of one family's decision.

**One live link at a time.** Every consent transition bumps `arena_users.consent_generation`,
and a link carries the epoch it was minted against, so a used link cannot withdraw twice and
a former guardian loses authority the moment the guardian address changes. A link also goes
inert on its own the morning the holder turns 18, because the age check is evaluated per
request. Every refusal — unknown account, stale epoch, consent never granted, holder now an
adult, malformed token — renders one identical page, so the link is never an oracle for
whether an account exists.

Administrators toggle the same consent state from the user profile, and it is the *same*
operation: password-confirmed, audited, and with exactly the effect the guardian's own link
has. An admin cannot revoke more gently than a guardian can.

### The minor shield: pseudonymous public display

Every account carries a globally unique handle (`arena_users.username`, e.g.
`coruja-serena-042`), and which name a public surface renders — that handle or the legal
name in `arena_users.nome` — turns on age:

- An **adult** is published under their **legal name by default**
  (`full_name_public` is set at signup, and the migration set it for every adult that
  already existed). They may switch to the handle at any time. Pseudonymity is offered to
  them, not imposed: silently retracting the name someone was already listed under is its
  own kind of surprise.
- A user aged **13-17** is published under the handle, and **cannot** change that. So is an
  account whose date of birth is unknown: the shield **fails closed**.

The surfaces this governs are the dashboard leaderboard, the two ranking pages, the public
profile page, and the problem-statistics solver credits.

Three properties are worth stating plainly, because each was a deliberate choice:

- **The shield never removes anyone from the ranking.** It changes the name a participant
  appears under, not whether they appear. `ranking_visible` remains the user's own separate
  choice, and `_eligible_users_where()` carries no age predicate.
- **Age is evaluated per request**, by `shared.age_check`. A shielded account stops being
  shielded on the morning of its eighteenth birthday, with no scheduler and no stored expiry.
  Turning 18 only *unblocks* the opt-in; it never turns a flag on.
- **Search is shielded too.** The public ranking search will not match a shielded user by
  their real name, only by their handle. Rendering a pseudonym while still answering "is this
  real name in the ranking?" would be a confirmation oracle that reconstructs the secret. The
  teacher-scoped class autocompletes are untouched: looking a student up by the name on the
  roll has a legitimate basis.

**The shield has a write half as well as a read half, and they are not
alternatives.** Masking a stored `public_profile = true` at read time would
publish that profile on the owner's eighteenth birthday, when the mask lifts and
nobody has chosen anything. So the flags are also refused on the way in and
cleared on the way through:

- `POST /user/profile/personal-data` answers `400 {"error": "age_shielded"}` when
  a shielded account asks to enable either opt-in, evaluated against the
  *submitted* date of birth and checked before any field is written. Clearing
  either flag is always allowed.
- `admin_user_service.toggle_public_profile()` refuses the same accounts. An
  administrator may not override the age shield: it is a legal control, not a
  moderation control, and an admin path weaker than the user path it mirrors is
  just a way around it. There is deliberately no admin toggle for
  `full_name_public` at all — publishing someone's legal name on their behalf is
  not an administrative act.
- Every date-of-birth path (`update_date_of_birth`,
  `regularizar_data_nascimento`) clears both flags when the account lands in the
  shielded band.

The invariant this establishes is that **no shielded row ever persists
`public_profile = true` or `full_name_public = true`**. The profile page shows
both opt-ins `disabled` with a plain-language explanation rather than hiding
them, per LGPD transparency; that `disabled` attribute is an affordance only,
since every write path re-derives the rule server-side.

The rule is owned by exactly one module, `arena/services/user_visibility_service.py`; see
[SERVICES.md](SERVICES.md). Problem **author credit** is deliberately outside the shield —
that name is published only where its owner opted into authorship credit for a problem they
wrote, and `hide_author_show_source` is the opt-out.

### Usernames

Every Arena account carries a unique lowercase handle in `arena_users.username`,
assigned at signup by drawing an `animal-adjetivo-NNN` pair from the shared word
lists (`shared/services/random_username_service.py`) and checking it against the
table. It is the name Arena publishes on every public surface — a 13-17
year-old must not have their legal name on a public page — and users change it
themselves from the Personal & Security profile tab.

Rules worth knowing:

- **The handle is not a login identifier.** Email remains the only way to log
  in. Accepting a handle at `/auth/login` would make the pseudonym an
  account-enumeration vector.
- **The stored form is the display form.** Handles are canonicalized (NFKC,
  casefold) at every write path, so they carry no display casing. Arena keeps
  no second canonical column and no functional `lower()` index — one place for
  the invariant, at the cost of casing.
- **Changes are rate-limited** by `NOCA_ARENA_USERNAME_CHANGE_COOLDOWN_DAYS`
  (default 30), enforced against `dta_troca_username` by
  `POST /user/profile/username`. Unlimited churn would let an observer correlate
  a shielded user's old and new handles across the ranking and undo the
  pseudonymity. Resubmitting the handle already held does not restart the
  window. An **administrator** bypasses the cooldown through
  `POST /admin/users/{id}/change-username`, which re-confirms their password and
  writes both an admin-audit row and a `username_changed` security event naming
  the old and the new handle — the cooldown protects a user from their own
  churn, not from a rename made in response to a report. Whether that rename
  *restarts* the user's window or clears it is the admin's own choice on the
  form, defaulting to restarting: a handle taken down after a report must not be
  restored a moment later, while a typo fixed on request should not cost the user
  a month of not choosing their own name.
- `arena_users.full_name_public` is the **adult** opt-in to publish the legal
  name instead of the handle, offered on the same profile tab and refused for
  every shielded account. `consent_generation` is the parental-consent epoch;
  the date-of-birth paths bump it, and the guardian-revocation flow that reads
  it is still to come.

**The fallback avatar is seeded on the handle, not the email address.** This is
a one-time visible change: every existing user's generated avatar differs after
the migration. It closes a confirmation oracle — the generator is deterministic
and the avatar is public, so an email seed let anyone render a guessed address
and compare it against a user's image to test whether that person holds that
mailbox. Web has always seeded on its username; Arena now matches.

### Implemented auth flows

The `/auth/*` routes currently implement:

| Flow | Current behavior |
|---|---|
| Login | email/password login, password-age warning, and sliding sessions that rotate 1-hour LOGIN JWTs while the user is active; remember-me additionally persists the cookie across browser restarts for 30 days |
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
- structured geolocation resolved from the IP (country, subdivision, district,
  city, EU flag, AS number)
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

`/dashboard` currently renders three cards, all backed by live database data:

- Languages (active language registry)
- Latest Problems — the 10 most recently created or edited enabled problems,
  showing relative "updated" time (`problem_browse_service.get_latest_problems`)
- Leaderboard — the top 10 rated users (`leaderboard_service.get_top_rated_users`)

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
- `/help/languages` reads active languages from the shared `languages` table, shows stdout flush hints for custom-validator problems, and explains verdicts
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
| `geocode_service.py` | per-user cap, cached 0.001-degree cells, and the fail-closed deployment-wide pacing/budget gate in front of the reverse geocoder |
| `leaderboard_service.py` | top-rated user query for the dashboard |
| `user_progress_service.py` | solved/attempted problem lists for profiles |
| `pagination_service.py` | reusable pagination primitives |
| `admin_user_service.py` | admin-side user listing and moderation actions |
| `admin_category_service.py` | category validation and CRUD |
| `admin_affiliation_service.py` | affiliation validation and CRUD with explicit user-detach on delete |
| `admin_problem_service.py` | problem CRUD, filtering, category binding, and owner lookup |
| `admin_problem_tc_service.py` | test case CRUD and ZIP replacement |
| `problem_tc_export_service.py` | ZIP export of sample test cases |
| `session_service.py` | sliding login-session token rotation and safe login redirects |
| `submission_list_service.py` | paginated submission history for user profiles |
| `submission_service.py` | submission row creation + autojudge job payload generation |
| `arena_class_service.py` | class creation/update, UI listings, teacher autocomplete, and class discovery |
| `arena_class_membership_service.py` | class membership (dated history) and registration-request workflow |
| `valkey_service.py` | Arena-specific Valkey runtime wiring |
| `../template_globals.py` | the single definition of what Arena templates may read, shared by `arena/main.py` and the test app builders |

---

## Data model

Arena now has a complete shared schema namespace in `shared/db_schema/arena/`.

### User and identity tables

| Table | Notes |
|---|---|
| `arena_users` | Arena identity, auth state, password/session state, rating summary, location, affiliation |
| `arena_affiliations` | externally managed affiliation catalog with a rating and precomputed total of member solves |
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
| `arena_problems` | public number, limits, statement, optional editor-only editorial with a release policy (`never`/`always`/`after_ac`, default `never`, not yet enforced), owner, author, license, image, and enabled flag |
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
3. recompute all affiliation ratings and member-solve totals from the fresh user ratings

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
- excludes users hidden from public rankings (`ranking_visible=False`)
- includes every Arena role that otherwise meets the eligibility rules
- ranks by `user_rating`, then solved count, then creation time

---

## Judging and submissions backend

Arena already has a working backend path for submissions even though the public UI is still missing.

### What already exists

- `arena/services/submission_service.py` validates problem/language/test-case availability
- it creates `arena_submissions` and `arena_submission_judgments`
- it updates `arena_problem_tried` and the aggregate `attempted_users` counter, excluding
  only the problem owner
- it emits an `ArenaSubmissionJob`
- `autojudge/arena_submission_job.py` compiles and judges Arena submissions
- the worker stores the first non-AC test result only
- first AC updates `arena_problem_solvers` for everyone (personal solved marker); problem rating
  counters (`solved_users`, `total_tries_before_solve`) are incremented only for submissions that
  count toward the problem — that is, excluding only the problem owner. The shared rule lives in
  `shared/services/arena_query_helpers.py` (`counts_toward_problem_rating` / `is_excluded_from_problem_rating`)

### What is still missing in Arena web

- AI review is implemented (submission detail page with request endpoint) but not yet surfaced in rankings or user profile tabs beyond the notification badge

---

## What is notably incomplete today

- dashboard cards for top countries and top leagues are still static placeholders
- login history is available to administrators from each admin user profile

---

## Bottom line

Arena is no longer a future-phase design. It is already a working FastAPI module with robust auth,
profile/security flows, admin management for users/affiliations/categories/problems, public problem
browsing, shared judging/rating infrastructure, and a complete Arena-specific database schema.

What remains is mostly public rankings and per-user submission history tabs.
