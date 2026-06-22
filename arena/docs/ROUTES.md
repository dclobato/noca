# NOCA Arena Routes

> For `url_for()` endpoint names and path parameters, see [URL_FOR_REFERENCE.md](URL_FOR_REFERENCE.md).

## Health (`arena/routes/health.py`)

The health endpoint reports whether Arena's required runtime backends are
available.

| Method | URL | Description |
|--------|-----|-------------|
| `GET` | `/health` | Reports PostgreSQL, Valkey, and Arena service health. Returns `200` with `status: "ok"` or `503` with `status: "degraded"`. |

## Auth UI (`arena/routes/auth.py`)

| Method | URL | Description |
|--------|-----|-------------|
| `GET` | `/auth/login` | Arena login page — email/password form with "Remember me" and "Forgot password?". Accepts optional `next`, stores it in a hidden form field, and uses only same-origin path targets. |
| `POST` | `/auth/login` | Processes login credentials; on password-only success records login history as `password` and sets an `arena_access_token` cookie (with `extra_data.tid = get_token_id()`). Default sessions issue a fixed 1-hour LOGIN JWT. Checking "Remember me" keeps the 1-hour token lifetime, adds a 30-day persistent cookie, and marks the session for middleware-driven token rotation up to an absolute 30-day cap. Redirects to safe `next` when present, otherwise dashboard. Handles inactive/unconfirmed accounts by re-rendering the login page with a resend-activation button. If login enters 2FA or forced password change, the safe `next` target is preserved in the pending flow token. |
| `POST` | `/auth/resend-activation` | Re-sends the email confirmation link for the account whose ID is stored in the Starlette session by the login handler. Consumes the session key on first use. |
| `POST` | `/auth/resend-parental-consent` | Re-sends the parental consent email for a pending minor account stored in the Starlette session by the login handler. |
| `POST` | `/auth/update-parental-email` | Stores or replaces the parent/legal guardian email for a pending account and sends a consent link. |
| `POST` | `/auth/update-date-of-birth` | Regularises a legacy account missing date of birth before login. |
| `GET` | `/auth/change-password` | Render the forced-password-change page. Guards the route by requiring a valid `pending_pw_change_token` in the Starlette session. Redirects to login if the token is absent. |
| `POST` | `/auth/change-password` | Process the forced-password-change form. Validates the `pending_pw_change_token`, enforces password policy, and on success redirects to the pending flow's safe `next` URL or dashboard. When the pending flow came from a remembered login, the replacement token preserves the remembered-session metadata so middleware rotation and the original 30-day absolute cap continue to apply. |
| `GET` | `/auth/2fa` | Arena two-factor authentication page — 6-digit OTP entry. Requires a valid `pending_2fa_token` in the Starlette session. |
| `POST` | `/auth/2fa` | Process the 2FA verification form. Validates the PENDING_2FA session token, verifies the supplied TOTP or backup code, records completed login history as `2fa` or `backup_code`, and on success issues a LOGIN JWT cookie before redirecting to the pending flow's safe `next` URL or dashboard. When the original login used "Remember me", the issued LOGIN token preserves the remembered-session metadata so middleware rotation and the original 30-day absolute cap continue to apply. |
| `GET` | `/auth/signup` | Arena sign-up page — registration form with full name, date of birth, email, password, profile photo, and terms agreement. |
| `POST` | `/auth/signup` | Creates an Arena user account, records Terms of Service and Privacy Policy acceptance timestamp, sends an activation email, and redirects to login with a flash message. |
| `GET` | `/auth/activate` | Validates an email-confirmation JWT and activates the account. |
| `GET` | `/auth/parental-consent` | Validates a parental-consent JWT and activates the account when email confirmation is also complete. |
| `GET` | `/auth/password-reset` | Password-reset request page, or new-password form when a reset token is present. |
| `POST` | `/auth/password-reset` | Sends a password-reset email or resets the password for a valid reset token. |
| `POST` | `/auth/logout` | Revokes the current `arena_access_token` JWT in the Valkey revocation store, deletes the cookie, and redirects to the dashboard with a success flash message. Idempotent: safe to call when no session is active. |
| `GET` | `/auth/accept-terms` | Render the Terms of Service and Privacy Policy acceptance form. Requires a `pending_tos_uid` session key set by the login handler; redirects to login if absent. |
| `POST` | `/auth/accept-terms` | Process the ToS/PP acceptance form. Validates checkbox presence, updates `aceitou_termos_privacidade` and `dta_aceitacao_termos_privacidade` for the pending user, commits, and redirects to login with a success message. |

## Help (`arena/routes/help.py`)

| Method | URL | Description |
|--------|-----|-------------|
| `GET` | `/help/rating` | Rating system help page. Explains how problem difficulty, user score, and affiliation ratings are computed, with the mathematical formulas and the configured rating update interval. No authentication required. |
| `GET` | `/help/languages` | Languages and verdicts help page. Lists all active languages (name, version, compile/run commands) from the database and explains every possible judgment verdict. No authentication required. |

## Legal (`arena/routes/legal.py`)

| Method | URL | Description |
|--------|-----|-------------|
| `GET` | `/legal/terms` | Public Arena Terms of Service page. Renders the copied markdown document from `arena/template/legal/terms_of_service.md` as sanitized HTML in the browser. |
| `GET` | `/legal/privacy` | Public Arena Privacy Policy page. Renders the copied markdown document from `arena/template/legal/privacy_policy.md` as sanitized HTML in the browser. |

## Public Problem Browsing (`arena/routes/problems.py`)

No authentication required. The `current_user` dependency is optional — when the user is
logged in their solve/attempt status is shown on the detail page.

| Method | URL | Description |
|--------|-----|-------------|
| `GET` | `/problems` | Paginated public problem list (fixed 25 per page). Supports `search` (number, title, source, author name), `sort_by` (`number_asc` default, `number_desc`, `title_asc`, `title_desc`, `solvers_asc`, `solvers_desc`, `rating_asc`, `rating_desc`), `category_slugs` (AND semantics, repeatable), `page`. |
| `GET` | `/problems/{arena_number}` | Problem detail page. Shows statement (Markdown + Mermaid + KaTeX), sample test cases, resource limits, language selector with submit form (enabled for authenticated users). Optional query params carry list state back for the "Back to list" button: `back_page`, `back_search`, `back_sort_by`, `back_category_slugs`. If logged in, shows whether the user solved or attempted the problem and a toggleable favorite heart. When the problem belongs to an accepting problem set from a class the user is registered to, shows a banner (naming the set, class, and deadline) and a "submit for the problem set" checkbox bound to the most urgent such set. |
| `GET` | `/problems/{arena_number}/rating-history` | JSON endpoint returning the problem's rating history for the last 24 months as `{"history": [{"ts": ISO8601, "rating": float}]}` (display-scale, e.g. `7.3`) in chronological order. Used by the ECharts sparkline on the detail page. |
| `GET` | `/problems/{arena_number}/statistics` | Public per-problem statistics page. Charts (rating line, verdict + language doughnuts, wall-time distribution stacked bar) and per-language avg±stddev wall-time / peak-memory tables. All data is loaded client-side from the statistics JSON endpoint and the rating-history endpoint. No authentication required. |
| `GET` | `/problems/{arena_number}/statistics.json` | JSON endpoint returning the precomputed statistics payload (verdicts, languages, time/memory stats, wall-time histogram, `computed_at`). Returns `{}` when statistics have not been computed yet. Snapshots are produced periodically by the rating worker. No authentication required. |
| `GET` | `/problems/{arena_number}/sample-testcases.zip` | Download a ZIP archive (Layout A: `in/001.in` + `out/001.out`) of the public sample test cases. Returns 404 when the problem is disabled/missing or has no sample test cases. No authentication required. |
| `POST` | `/problems/{arena_number}/submit` | Submit a code solution for an Arena problem. Requires authentication; guests are redirected to login with `next` set to the problem detail page. Accepts optional form field `problem_set_id`: when present (the user ticked the problem-set checkbox), the submission is tied to that set and becomes visible to the set's teacher — the service validates the set is accepting, contains the problem, and the user is an active member of its class; when absent, the submission is private. Creates `arena_submissions` and `arena_submission_judgments` (QUEUED) rows, commits, then enqueues the autojudge job. On success redirects to the user's profile Submissions tab; on error (invalid language, empty code, no test cases, invalid problem-set tie) flashes an error and redirects back to the problem detail page. |
| `POST` | `/problems/{arena_number}/favorite` | Toggle favorite status for the current user. Returns `{"is_favorite": true/false}`. Guests receive 401 JSON; unknown/disabled problems return 404. |
| `POST` | `/problems/{arena_number}/request-removal` | Submit a removal request for a problem the current user owns but cannot edit. Requires authentication; guests are redirected to login. Returns 403 if the user is not the owner or already has edit rights. Sends a durable `PROBLEM_REMOVAL_REQUEST` notification to every ARENA_ADMIN user linking to the admin edit page; `source_ref` idempotency prevents duplicate notifications on repeated requests. Flashes a success message and redirects to the problem detail page. |

## Root / Dashboard (`arena/routes/root.py`)

| Method | URL | Description |
|--------|-----|-------------|
| `GET` | `/` | Redirects (302) to `/dashboard`. |
| `GET` | `/dashboard` | Public Arena dashboard. Displays two information cards: Random Problems and rating-backed Top Users. No authentication required. |
| `GET` | `/status` | Authenticated system-status page. Shows aggregate AutoJudge, Rating, and AI Assistant availability. A worker class is available when at least one worker is online and unpaused, unavailable otherwise, and unknown when status retrieval fails. Guests are redirected to login with `next=/status`. |
| `GET` | `/favicon.ico`, `/favicon-{16,32,48,96,180,192,512}.png`, `/mstile-150x150.png`, `/site.webmanifest`, `/browserconfig.xml` | Root-level favicon assets served from `arena/static/favicon/` via an allowlist with public cache headers (`max-age=86400`). Referenced by the favicon `<link>`/`<meta>` block in the base templates. |

## Live Feed (`arena/routes/live.py`)

Public, no-login live submission feed. The SSE channel only signals "changed"; the JSON
snapshot is the sole data source. Real-time updates depend on the autojudge worker
publishing `ArenaVerdictEvent` to the shared `arena:results` Valkey channel.

| Method | URL | Description |
|--------|-----|-------------|
| `GET` | `/live` | Public page. Renders the live feed table shell and loads `live-feed.js`. No authentication required. |
| `GET` | `/live/feed.json` | JSON snapshot `{"live_feed_limit": int, "has_more": bool, "submissions": [...]}` of the latest finalized submissions across all users, newest first. The row count is capped by `NOCA_ARENA_LIVE_FEED_LIMIT`; `has_more` is `true` when older finalized submissions exist beyond that cap. Each row includes nullable affiliation and country names, affiliation logo and user-country flag URLs, a server-built `problem_url`, language icon, and verdict label/badge class. |
| `GET` | `/live/events` | SSE stream (`text/event-stream`). Subscribes to `arena:results` via `iter_arena_verdict_events`, emits a generic `refresh` ping per event plus heartbeat pings; carries no verdict data. |

## Notifications (`arena/routes/notifications.py`)

All routes require an authenticated Arena user. Notification rows are durable
database records created by worker-side producers, such as `autojudge`.

| Method | URL | Description |
|--------|-----|-------------|
| `GET` | `/arena/notifications` | JSON endpoint returning the latest 20 notifications for the current user, plus the current unread count. |
| `POST` | `/arena/notifications/read-all` | Marks all unread notifications as read for the current user and returns the updated unread count. |
| `POST` | `/arena/notifications/{notification_id}/read` | Marks one current-user notification as read and returns the updated unread count. Returns 404 for missing or foreign notifications. |

## Presence (`arena/routes/presence.py`)

All routes require an authenticated Arena user; guests receive `401` and no
presence data. Presence lives entirely in Valkey (best-effort, short TTL, no
database writes) and powers the green online-dot on user avatars.

| Method | URL | Description |
|--------|-----|-------------|
| `POST` | `/arena/presence/heartbeat` | Marks the current user online for `NOCA_ARENA_PRESENCE_TTL_SECONDS`. Returns `{"ok": true, "enabled": true}` (or `{"ok": false, "enabled": false}` when presence is disabled). |
| `POST` | `/arena/presence/status` | Body `{"ids": [...]}` (max 500 distinct ids honoured). Returns `{"enabled": true, "online": [id, ...]}` — only the online ids from the batch; any queried id absent from the list is offline. The auth check runs before the body is parsed or Valkey is touched. |

## Classes (`arena/routes/classes.py`)

All routes require an authenticated Arena user unless noted. Browser GET requests
redirect guests to login with `next` set to the current page.

| Method | URL | Description |
|--------|-----|-------------|
| `GET` | `/classes` | Classes landing page. Shows choice cards for Registered classes, Open for registration, and Manage classes (the last only for judges/admins). |
| `GET` | `/classes/registered` | Registered classes page. Lists classes the current user is enrolled in or has a pending/denied request for. 25 per page; supports `search`, `sort`, `dir`, and `page` query params. |
| `GET` | `/classes/open` | Open-for-registration page. Lists classes accepting self-registration that the user has not yet joined. 25 per page; supports `search`, `teacher_id`, `sort`, `dir`, and `page` query params. |
| `GET` | `/classes/manage` | Manage classes page (judges/admins only; returns 403 for other roles). Lists classes the user manages. 25 per page; supports `search`, `sort`, `dir`, and `page` query params. |
| `GET` | `/classes/new` | Judge/admin class creation form. Uses native date inputs. Admins select an assigned judge through autocomplete; judges become the assigned teacher automatically. |
| `POST` | `/classes/new` | Creates a class. Rejects non-judge/admin users and past dates. Redirects to `/classes/manage#class-{id}` on success. |
| `GET` | `/classes/teachers/autocomplete` | Authenticated JSON endpoint returning up to 10 `ARENA_JUDGE` matches for `?q=` as `{"teachers": [{"id": "...", "label": "Full name <email>"}]}`. Admins see all judges; regular users see judges from their affiliation. |
| `GET` | `/classes/{class_id}` | Placeholder class detail page showing description, dates, assigned teacher, affiliation, and active member count. When self-registration is open, prospective students see an "Ask registration" button. Active members see a green "Registered" indicator instead. |
| `POST` | `/classes/{class_id}/request-registration` | Creates a pending self-registration request for the current user. Creates a `CLASS_REGISTRATION_REQUEST` in-app notification for the teacher and sends a best-effort email to the teacher. Redirects to `/classes/registered` on success. |
| `GET` | `/classes/{class_id}/edit` | Teacher/admin edit form. Start date can be changed only before the class starts. End date can be changed only before the class has finished. |
| `POST` | `/classes/{class_id}/edit` | Updates class metadata, self-registration, dates, and, for admins, assigned teacher. Redirects to `/classes/manage#class-{id}` on success. |
| `GET` | `/classes/{class_id}/problem-sets` | Teacher/admin problem-set management list for one class. Fixed at 25 rows per page. Ordered by deadline descending by default and supports sorting by name, start date, and deadline. Shows per-set counts for problems, students with zero AC submissions, and students with no submissions at all. Includes the modal flow to create a new set with name, notes/description, start date/time, and deadline. |
| `POST` | `/classes/{class_id}/problem-sets` | Teacher/admin action that creates a new problem set, stores its notes/description, applies the optional start/deadline window, and redirects to the manage-problems page on success. |
| `GET` | `/classes/{class_id}/problem-sets/{set_id}/problems` | Teacher/admin manage-problems page for a problem set. Lists the set's problems ordered by Arena number with links to the public problem detail page, plain-text categories, and display rating. Includes an inline form for notes/description, start date/time, and deadline, pre-filled with current values. The Update button saves all three fields. The page also includes a "Stop accepting submissions now" quick-close button, an add-problems autocomplete with pending pills, and remove buttons with confirmation modal. |
| `POST` | `/classes/{class_id}/problem-sets/{set_id}/problems` | Teacher/admin action that adds one or more problems to the set by repeated `problem_refs` form fields (selected from the autocomplete). Redirects back to the manage-problems page. |
| `POST` | `/classes/{class_id}/problem-sets/{set_id}/problems/{problem_id}/remove` | Teacher/admin action that removes one problem from the set after modal confirmation. Related set-tied submissions for that problem become private again. |
| `POST` | `/classes/{class_id}/problem-sets/{set_id}/schedule` | Teacher/admin action that updates the notes/description and start/deadline window together. Blank notes are stored as `NULL`. Validation: if start date changes it must be in the future; deadline must be after start date when both are set, or in the future when start date is absent. Redirects back to the manage-problems page. |
| `POST` | `/classes/{class_id}/problem-sets/{set_id}/stop-now` | Teacher/admin action that immediately closes the problem set by setting its deadline to the current time. No form fields. Redirects back to the manage-problems page. |
| `POST` | `/classes/{class_id}/problem-sets/{set_id}/delete` | Teacher/admin action that deletes a problem set after double confirmation and password verification. Redirects back to the problem-set list without a hash because the deleted row no longer exists. |
| `GET` | `/classes/{class_id}/problem-sets/{set_id}/report` | Teacher/admin report page for one problem set. Lists all active class members ordered by full name and one column per set problem (problem number links to the public problem detail page). Each cell shows the best verdict badge from set-tied submissions, or `--` when there are none. If the set is due and a snapshot already exists, adds a frozen rating column. Student names are clickable links to the student submission drill-down page. |
| `GET` | `/classes/{class_id}/problem-sets/{set_id}/report/student/{user_id}` | Teacher/admin drill-down page listing all submissions by one student for the problems in the problem set, grouped by problem in a Bootstrap accordion (newest first within each group). Each submission row links to the submission detail page with teacher-view context. |
| `GET` | `/classes/{class_id}/problem-sets/{set_id}/problems/autocomplete` | Teacher/admin JSON endpoint for the add-problem field. Returns up to 10 enabled problems not already in the set, matched case-insensitively by title and, when the query is numeric, by Arena number prefix. Response shape: `{"problems": [{"id": "...", "ref": "123", "label": "123 - Problem title (4.5)"}]}`. |
| `GET` | `/classes/{class_id}/members` | Teacher/admin membership management page. Lists active members and pending registration requests, fixed at 25 rows per page. Includes add-students autocomplete with pending pills. |
| `POST` | `/classes/{class_id}/members` | Teacher/admin action that adds one or more students to the class by repeated `student_ids` form fields selected from the autocomplete. Creates a `CLASS_MEMBERSHIP_ADDED` in-app notification and sends a best-effort email to each added student. |
| `GET` | `/classes/{class_id}/members/autocomplete` | Teacher/admin JSON endpoint for the add-students field. Returns up to 10 active, confirmed `ARENA_USER` accounts that are not already active members and don't have pending registration requests, matched by full name or email. Response shape: `{"students": [{"id": "...", "label": "Full name <email>"}]}`. |
| `POST` | `/classes/registration-requests/{request_id}/approve` | Teacher/admin action that approves a pending request and creates an active membership for today. Creates a `CLASS_REGISTRATION_APPROVED` in-app notification and sends a best-effort email to the requesting user. |
| `POST` | `/classes/registration-requests/{request_id}/deny` | Teacher/admin action that denies a pending request with an optional denial reason. Creates a `CLASS_REGISTRATION_DENIED` in-app notification and sends a best-effort email to the requesting user (including the reason when provided). |
| `POST` | `/classes/{class_id}/members/{user_id}/remove` | Teacher/admin action that marks a user's membership as removed for today. When the actor is not the removed user, creates a `CLASS_MEMBERSHIP_REMOVED` in-app notification and sends a best-effort email. Self-removal produces no notification or email. |

## Student Problem Sets (`arena/routes/student_problem_sets.py`)

Authenticated routes for students. Unauthenticated browser GET requests are redirected to
`/auth/login?next=<current-path-and-query>`. Only active class members (and the assigned teacher
or an admin) may access these pages.

| Method | URL | Description |
|--------|-----|-------------|
| `GET` | `/classes/{class_id}/my-problem-sets` | Student-facing problem-set list for one class. Paginated at 25 rows per page, ordered by deadline descending by default. Sortable by name and deadline. Each row shows problem count, number of problems the student has AC'd, and number of problems with no set-tied submission. Clicking a problem set name opens the detail page. Includes the highlight-row script so the "Destaque de Linha" pattern works when returning from the detail page. |
| `GET` | `/classes/{class_id}/my-problem-sets/{set_id}` | Student-facing problem-set detail page. Shows the set's name, optional description, and scheduling dates above a table of all set problems with links to the public problem detail page and a verdict badge for the student's best set-tied submission. If the set's deadline has passed, appends a Results footer: when the rating snapshot has been computed it shows the student's frozen total rating and a verdict-distribution table (one column per verdict plus a "No submission" column); otherwise shows a "Rating computation is pending" notice. Back button returns to the list with a URL fragment (`#set-{set_id}`) to highlight the row. |

## Submissions (`arena/routes/submissions.py`)

Authenticated routes. Unauthenticated browser GET requests are redirected to
`/auth/login?next=<current-path-and-query>`. POST action routes use the owning
GET page, such as the submission detail page, as the `next` target.

| Method | URL | Description |
|--------|-----|-------------|
| `GET` | `/submissions/{submission_id}` | Submission detail page. Shows three cards: problem limits and language; resources (wall time, peak memory) and verdict with full label; syntax-highlighted source code. Only the submitting user or an `ARENA_ADMIN` may access; others get 404. For non-AC verdicts, the owner sees an AI review section (confirmation modal → pending → batch queued → review depending on state). The modal shows the current and projected AI credit balances, explains when a personal API key leaves the balance unchanged, and disables confirmation when neither a key nor a credit is available. Platform-credit confirmations also show recent average and median batch turnaround, an unavailable state when the Valkey statistics are absent, and the 24-hour maximum wait notice. |
| `POST` | `/submissions/{submission_id}/request-ai-review` | Enqueue an AI code-review job for a submission. **Owner-only** — not accessible by admins for other users' submissions. Idempotent: if `submit_to_ai` is already True or a review row already exists, redirects back without double-enqueueing. **Credit gate:** user must have their own AI API key (`ai_api_key`) **or** at least one `ai_backend_credits`. If neither condition is met, redirects back with a flash error. When using platform credits (no personal key), one credit is atomically consumed before enqueueing. The `use_platform_key` decision is frozen in the job payload. On success sets `submit_to_ai=True`, enqueues `ArenaAIReviewJob`, and redirects to the submission detail page. |
| `POST` | `/submissions/{submission_id}/teacher-feedback` | Create or update teacher feedback on a student's non-AC, set-tied submission. **Manager-only:** the assigned teacher of the submission's problem-set class, or an `ARENA_ADMIN`. Authorization is derived from the submission's persisted `problem_set_id` (never the `back_*` form fields, which are navigation-only). Returns 404 when the submission is missing, not tied to a problem set, AC/unjudged, or the actor is not a manager. Empty/whitespace feedback redirects back with a warning. On success upserts `arena_submission_teacher_feedback` (editing refreshes `feedback_at`) and creates a `TEACHER_FEEDBACK_POSTED` notification for the student with a per-update `source_ref`, so each edit produces a fresh notification while preserving prior ones. Redirects (303) to the detail page, forwarding `back_*` params when present. |

## Users (`arena/routes/users.py`)

| Method | URL | Description |
|--------|-----|-------------|
| `GET` | `/user/profile` | Current Arena user profile with Personal & Security, Solved Problems, Attempted Problems, Favorites, Notifications, and Submissions tabs. Accepts `tab=personal-security\|solved\|attempted\|favorites\|notifications\|submissions` (legacy values `personal` and `security` are silently mapped to `personal-security`), plus `solved_page`, `attempted_page`, `favorite_page`, `notifications_page`, `submissions_page`, `submissions_search`, and `submissions_verdict` for the paginated lists (50 items per page). Redirects unauthenticated requests to login with `next` set to the current path and query. |
| `GET` | `/user/profile/complete` | Login-time profile completion notice. Lists missing affiliation, preferred programming language, country, or AI-feedback language and links to the Personal & Security profile tab. Redirects guests to login and users with complete profiles to the dashboard. |
| `POST` | `/user/profile/photo` | Update the current Arena user's profile photo. Accepts a pre-cropped multipart upload (`foto_cropada`, 2:3 portrait). Redirects to profile on success. |
| `POST` | `/user/profile/personal-data` | Authenticated JSON endpoint that atomically updates name, date of birth, country/subdivision, affiliation, preferred programming language, `prefered_language` locale (`en-US` or `pt-BR`), and optional `ranking_visible` (bool; omit or send `null` to preserve the current value). A changed date applies the age policy: users under 13 are deactivated, users aged 13–17 lose parental consent, and both minor outcomes invalidate all sessions and return a dashboard redirect. Adult changes preserve the existing parental-consent fields. Response includes `ranking_visible` reflecting the persisted value. |
| `POST` | `/user/profile/notifications/{notification_id}/delete` | Delete one notification belonging to the current Arena user. Redirects to the notifications tab. Returns a flash warning if the notification is not found. |
| `POST` | `/user/profile/notifications/delete-all` | Delete all notifications belonging to the current Arena user. Redirects to the notifications tab with a flash message indicating how many were removed. |
| `POST` | `/user/profile/notifications/mark-all-read` | Mark all unread notifications as read for the current Arena user. Redirects to the notifications tab with a flash message. |
| `GET` | `/user/profile/subdivisions` | Authenticated JSON endpoint returning ISO 3166-2 subdivisions for `?country_code=XX`. |
| `POST` | `/user/profile/location` | Authenticated JSON endpoint updating optional profile `country_code` and `subdivision_code`. Subdivision may be empty. (Legacy — superseded by `/user/profile/personal-data`.) |
| `POST` | `/user/profile/location/detect` | Authenticated JSON endpoint reverse-geocoding browser latitude/longitude via the configured Nominatim-compatible provider. Returns detected values; the client saves only after confirmation. |
| `GET` | `/user/profile/affiliations/search` | Authenticated JSON endpoint returning up to 10 affiliation matches for `?q=` using case-insensitive partial name search. |
| `POST` | `/user/profile/affiliation` | Authenticated JSON endpoint setting or clearing the current user's `affiliation_id`. (Legacy — superseded by `/user/profile/personal-data`.) |
| `POST` | `/user/profile/language` | Authenticated JSON endpoint setting or clearing the current user's `preferred_language_id`. (Legacy — superseded by `/user/profile/personal-data`.) |
| `GET` | `/user/profile/rating-history` | Authenticated JSON endpoint returning the current user's rating history for the last 24 months as `{"history": [{"ts": ISO8601, "rating": int}]}`, ordered chronologically. Used by the profile page rating chart. |
| `GET` | `/user/profile/submission-heatmap` | Authenticated JSON endpoint returning the current user's precomputed submission heatmap as `{"heatmap": [["YYYY-MM-DD", count], ...], "range_start": "YYYY-MM-DD", "range_end": "YYYY-MM-DD", "computed_at": ISO8601 or null}`. Covers the last 364 days (52 weeks). Computed by the rating worker; returns an empty heatmap until the first cycle runs. |
| `POST` | `/user/profile/api-key` | Authenticated JSON endpoint to set, replace, or clear the current user's personal AI API key. Accepts `{"api_key": "<value>"}`. An empty or absent value clears the key. The key is encrypted at rest; the plaintext is never returned after this call. Returns `{"ok": true, "cleared": <bool>}`. |
| `GET` | `/user/{user_id}/photo` | Public diagnostic route returning the stored full-size Arena user photo, or generated SVG fallback when no photo is stored. |
| `GET` | `/user/{user_id}/avatar` | Public diagnostic route returning the stored resized Arena user avatar, or generated SVG fallback when no photo is stored. |

## User Submission Status (`arena/routes/user_submission_status.py`)

Realtime backing for the profile submissions tab's in-place verdict updates and AC confetti.

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/user/submissions/status.json` | Authenticated JSON snapshot of the current verdict/status for a validated, owner-scoped set of submission IDs. Query `?ids=` is a comma-separated list of submission UUIDs (max 25); malformed UUIDs or an over-limit count return `400`, an empty/absent value returns `{"submissions": []}`, and guests get `401`. Each row reports `submission_id`, `status`, `is_final` (terminal `DONE`/`FAILED`/`SUPERSEDED`), `verdict`, `verdict_label`, `verdict_badge_class`, and `max_wall_time_ms`. The sole data source the browser renders from. |
| `GET` | `/user/submissions/status/events` | Authenticated, user-scoped SSE channel. Resolves the requested `?ids=` against the current user **once** at connect, then emits a generic `data: refresh` ping only when one of those owned submissions finalizes (plus `data: ping` heartbeats). No verdict data leaves the server; the per-event predicate is in-memory set membership (no per-verdict DB query). Guests get `401`; malformed/over-limit IDs `400`. |

## User Security (`arena/routes/user_security.py`)

| Method | URL | Description |
|--------|-----|-------------|
| `GET` | `/user/profile/2fa/setup` | Render the 2FA setup page with a TOTP QR Code and formatted secret. Initiates the activation flow; stores a short-lived `activating_2fa_token` in the session. Redirects unauthenticated users to `/auth/login`. |
| `POST` | `/user/profile/2fa/confirm` | Confirm and activate 2FA. Validates the setup session token, verifies the TOTP code, stores backup codes in the session, and redirects to the backup codes page. |
| `POST` | `/user/profile/2fa/disable` | Disable 2FA after password verification. Invalidates all backup codes and redirects to the profile page. |
| `POST` | `/user/profile/backup-codes/regenerate` | Regenerate backup codes. Invalidates existing codes, generates a fresh set, stores them in the session, and redirects to the backup codes page. |
| `GET` | `/user/profile/backup-codes` | Show newly generated backup codes (one-time display). Pops codes from the session; redirects to profile if no codes are present. |

## Arena Admin – Dashboard

These routes display and manage the worker-presence records published by the
autojudge, rating, and AI assistant processes. All routes require
`ArenaRole.ARENA_ADMIN`.

| Method | URL | Description |
|--------|-----|-------------|
| `GET` | `/admin/dashboard` | Render the Admin Dashboard landing page with links to Service Status and AI Credits Usage sub-sections. |
| `GET` | `/admin/dashboard/service-status` | Render the Service Status sub-page. Worker cards refresh every 10 seconds through HTMX. |
| `GET` | `/admin/dashboard/workers` | Return the HTMX fragment containing the autojudge, rating, and AI assistant worker cards. |
| `POST` | `/admin/dashboard/workers/remove` | Remove one worker's durable and live Valkey records. Form fields: `worker_class`, `worker_id`. A running worker reappears on its next heartbeat. |
| `POST` | `/admin/dashboard/workers/pause` | Pause one autojudge/aiassistant worker. Commits authoritative pause state + audit row, then publishes a signed Valkey nudge. Form fields: `worker_class`, `worker_id`. Rejects and audits unknown classes and `rating` (400); disabled when no command secret is set. |
| `POST` | `/admin/dashboard/workers/resume` | Resume one paused autojudge/aiassistant worker. Same commit-before-publish flow and validation as pause. Form fields: `worker_class`, `worker_id`. |
| `POST` | `/admin/dashboard/workers/flush-now` | Send a one-shot FLUSH_NOW trigger to an aiassistant worker, waking the batch flusher immediately. Commits an audit row (action=`flush_now`, generation=NULL), publishes a signed Valkey nudge, then updates transport status. Only valid for `aiassistant` class (400 otherwise); disabled when no command secret is set. Form fields: `worker_class`, `worker_id`. |
| `POST` | `/admin/dashboard/workers/poll-now` | Send a one-shot POLL_NOW trigger to an aiassistant worker, waking the batch poller immediately. Same flow as flush-now but with action=`poll_now`. Form fields: `worker_class`, `worker_id`. |
| `GET` | `/admin/dashboard/ai-credits` | Paginated list of all AI credit consumption transactions across all users. Query params: `search` (name/email), `sort_dir` (`desc`/`asc`), `per_page` (10/25/50/100/500), `page`, `date_from` (YYYY-MM-DD, inclusive, converted from admin's local timezone to UTC), `date_to` (YYYY-MM-DD, inclusive end-of-day in admin's timezone). Blank or missing dates are ignored. Displays user link, submission link, AI review cost (or "Pending" while processing), and batch turnaround with compact adaptive units when timing data exists. Above the filters, recent average, median, population standard deviation, and sample count come from `ai:batch:turnaround:stats`; an unavailable notice replaces them when the key cannot be read. |
| `GET` | `/admin/dashboard/login-history` | Paginated list of all Arena login events across all users. Query params: `search` (name/email/location), `sort_dir` (`desc`/`asc`), `per_page` (10/25/50/100/500), `page`, `date_from` (YYYY-MM-DD), `date_to` (YYYY-MM-DD). Each row links to the user profile; a "Details" button opens a modal showing IP, user agent, and auth mode. |
| `GET` | `/admin/dashboard/submissions` | Paginated list of all Arena submissions across all users. Query params: `search` (user name/email), `verdict_filter` (e.g. `AC`), `ai_filter` (`yes`/`no`, filters `submit_to_ai` flag), `language_filter` (language id), `problem_filter` (exact arena_number), `date_from`/`date_to` (YYYY-MM-DD submission-date range, user-timezone), `sort_dir` (`desc` newest first default / `asc`), `per_page` (10/25/50/100/500), `page`. Links each row to the user profile and to the existing submission detail page. |

## Arena Admin – User Management

Split across two route modules and a shared support module:

- `arena/routes/admin_users.py` — GET routes (list, profile)
- `arena/routes/admin_users_actions.py` — POST action routes (all 16 handlers)
- `arena/routes/admin_user_route_support.py` — shared helpers (`NavState`, guards, redirect builder)

All routes require `ArenaRole.ARENA_ADMIN`. Non-admin users receive 403; unauthenticated requests receive 401.

| Method | URL | Source file | Description |
|--------|-----|-------------|-------------|
| `GET` | `/admin/users` | `admin_users.py` | Paginated Arena user list. Query params: `search` (name/email ilike), `role` (ArenaRole value), `can_edit` (`1` → only users with problem-edit permission: ARENA_ADMIN or `can_edit=True`), `per_page` (10/25/50/100/500, default 25), `page`. |
| `GET` | `/admin/users/{user_id}` | `admin_users.py` | Admin-only profile view for a specific user. Shows Personal & Security, AI Credits, and Login History tabs. Accepts `tab`, `credits_page`, login-history params (`login_page`, `login_per_page`, `login_sort_dir`, `login_date_from`, `login_date_to`), and list-filter params (`search`, `page`, `per_page`, `role`) to reconstruct a "← Back" link. Login dates are inclusive and interpreted in the viewing admin's timezone. |
| `GET` | `/admin/users/{user_id}/submission-heatmap` | `admin_users.py` | JSON endpoint returning the precomputed submission heatmap for the given user. Same shape as the self-profile endpoint: `{"heatmap": [["YYYY-MM-DD", count], ...], "range_start": "YYYY-MM-DD", "range_end": "YYYY-MM-DD", "computed_at": ISO8601 or null}`. Used by the admin profile page heatmap chart. |
| `POST` | `/admin/users/{user_id}/role` | `admin_users_actions.py` | Change user role. Form: `new_role` (ArenaRole value). Self-guard and last-admin guard apply. |
| `POST` | `/admin/users/{user_id}/toggle-active` | `admin_users_actions.py` | Toggle account active/inactive. Deactivation invalidates sessions. Self-guard and last-admin guard apply. |
| `POST` | `/admin/users/{user_id}/force-password-change` | `admin_users_actions.py` | Toggle the forced password-change flag on/off. Enabling the requirement sends the user a notification email; delivery failure does not roll back the requirement. |
| `POST` | `/admin/users/{user_id}/remove-photo` | `admin_users_actions.py` | Remove the user's profile photo. |
| `POST` | `/admin/users/{user_id}/disable-2fa` | `admin_users_actions.py` | Disable 2FA, invalidate backup codes and user sessions, and email the user when 2FA was enabled. Delivery failure does not roll back the security action. |
| `POST` | `/admin/users/{user_id}/change-name` | `admin_users_actions.py` | Change the user's display name. Form: `new_name`. Empty names are rejected with a flash error. |
| `POST` | `/admin/users/{user_id}/date-of-birth` | `admin_users_actions.py` | Change date of birth. Form: `date_of_birth` (`YYYY-MM-DD`). Applies the Arena age policy: users under 13 are deactivated, users aged 13–17 lose parental consent, all minor changes invalidate sessions, and adult changes preserve the existing consent fields. |
| `POST` | `/admin/users/{user_id}/remove-location` | `admin_users_actions.py` | Clear country and subdivision codes. |
| `POST` | `/admin/users/{user_id}/remove-affiliation` | `admin_users_actions.py` | Clear the user's affiliation link. |
| `POST` | `/admin/users/{user_id}/reset-api-key` | `admin_users_actions.py` | Clear the user's personal AI API key. |
| `POST` | `/admin/users/{user_id}/topup-credits` | `admin_users_actions.py` | Add AI credits to a user's balance. Form: `quantity` (positive int). Always redirects to the user profile with `tab=credits`. |
| `POST` | `/admin/users/{user_id}/toggle-email-confirmed` | `admin_users_actions.py` | Toggle the email-confirmed flag (`email_confirmado`). Confirming sets the confirmation timestamp; clearing removes it. |
| `POST` | `/admin/users/{user_id}/toggle-parental-consent` | `admin_users_actions.py` | Toggle the parental-consent flag (`consentimento_responsavel`). Granting sets the consent timestamp; revoking removes it. |
| `POST` | `/admin/users/{user_id}/toggle-can-edit` | `admin_users_actions.py` | Toggle the `can_edit` flag granting permission to add/edit problems on the Arena problem base. Admin-only; the profile UI confirms via a modal. |
| `POST` | `/admin/users/{user_id}/toggle-ranking-visible` | `admin_users_actions.py` | Toggle the `ranking_visible` flag. When False, the user is hidden from all public ranking lists and excluded from affiliation rating computation; their rating is still computed and visible on their own profile. Admin-only; password confirmation required. |

All POST routes accept a `NavState` dependency (`search`, `page`, `per_page`, `role_filter`, `source`) and redirect to the user list or profile depending on `source`.

## Arena Admin – Category Management (`arena/routes/admin_categories.py`)

All routes require `ArenaRole.ARENA_ADMIN`. Non-admin users receive 403; unauthenticated requests receive 401.

| Method | URL | Description |
|--------|-----|-------------|
| `GET` | `/admin/categories` | Paginated category list ordered by name. Shows slug, hex color, and linked problem count. Query params: `per_page` (10/25/50/100, default 25), `page`. |
| `GET` | `/admin/categories/new` | Render the create-category form with name, slug, and color fields. |
| `POST` | `/admin/categories/new` | Create a category. Validates required name/slug, unique name/slug, and `#RRGGBB` color. |
| `GET` | `/admin/categories/{category_id}/edit` | Render the edit-category form for a specific category. |
| `POST` | `/admin/categories/{category_id}/edit` | Update category name, slug, and color. |
| `POST` | `/admin/categories/{category_id}/delete` | Delete a category. Problem-category map rows are removed by cascade; problems remain. |

## Arena Admin – Affiliation Management (`arena/routes/admin_affiliations.py`, `arena/routes/affiliations.py`)

All admin routes require `ArenaRole.ARENA_ADMIN`. Non-admin users receive 403; unauthenticated requests receive 401.
The public logo route requires no authentication.

| Method | URL | Description |
|--------|-----|-------------|
| `GET` | `/admin/affiliations` | Paginated affiliation list. Query params: `search` (name ilike), `country_code`, `subdivision_code`, `per_page` (10/25/50/100, default 25), `page`. Invalid country codes are silently ignored. |
| `POST` | `/admin/affiliations/new` | Create an affiliation. Validates required name, optional URL, optional country/subdivision codes. Redirects to list on success or validation error (flash message). Logo is managed separately via the logo route. |
| `POST` | `/admin/affiliations/{affiliation_id}/edit` | Update an affiliation. Same validation as create. Logo is managed separately via the logo route. |
| `POST` | `/admin/affiliations/{affiliation_id}/logo` | Set or clear an affiliation logo via AJAX. Accepts `multipart/form-data` with either `foto_cropada` (pre-cropped image file, max 2 MB) or `remove_logo=1`. Returns JSON `{"ok": true, "logo_url": "<url_or_empty>"}`. Used by the standalone logo modal without any page reload. |
| `POST` | `/admin/affiliations/{affiliation_id}/delete` | Delete an affiliation. Linked users have their `affiliation_id` cleared via bulk UPDATE before removal. |
| `GET` | `/affiliations/{affiliation_id}/logo` | Public logo route. Returns the stored logo image with cache headers. Returns 404 if the affiliation is missing or has no logo. |
| `GET` | `/affiliations/{affiliation_id}/logo/thumbnail` | Public logo thumbnail route. Returns the stored 64×64 logo thumbnail with cache headers. Falls back to a 302 redirect to the full-size logo when no thumbnail is stored yet. Returns 404 if the affiliation is missing or has no logo at all. |
| `GET` | `/affiliations/{affiliation_id}/rating-history` | Public rating history endpoint. Returns `{"history": [{"ts": ISO8601, "rating": int}, ...]}` for the last 24 months. Returns 404 if the affiliation is missing. |

## Arena Admin – Problem Management (`arena/routes/admin_problems.py`)

All routes require `ArenaRole.ARENA_ADMIN` or any user whose `can_edit` flag is set (granted by
an admin). A plain `ARENA_JUDGE` without `can_edit` is rejected. Non-admin editors are restricted
to their own problems; admins may manage any problem.

| Method | URL | Description |
|--------|-----|-------------|
| `GET` | `/admin/problems` | Paginated problem-management list. Supports `search`, `sort_by` (`title_asc`, `title_desc`, `number_asc`, `number_desc`, `rating_asc`, `rating_desc`), `owner_id` (admin only), `category_slugs` (AND semantics, repeatable), `per_page` (10/25/50/100, default 25), `page`, and hash-based row highlighting after CRUD redirects. |
| `GET` | `/admin/problems/new` | Render the create-problem form with owner-backed or free-text authorship, statement editor, limits, optional image upload, category picker, and optional list-return query state. |
| `POST` | `/admin/problems/new` | Create a disabled Arena problem owned by the current user. Form fields `author_is_owner` and `author` select the owner's fullname or a required free-text author of at most 80 characters. The route also validates scalar and Markdown fields, processes an optional image, binds categories, and redirects to the highlighted problem-list row. |
| `GET` | `/admin/problems/{problem_id}/edit` | Render the edit form for an existing problem. Includes test-case list, selected categories, problem rating-history chart data URL, and optional list-return query state (`page`, `per_page`, `search`, `sort_by`, `owner_id`, `category_slugs`). |
| `POST` | `/admin/problems/{problem_id}/edit` | Update mutable problem fields, including `author_is_owner` and `author`, without transferring ownership. It can replace or clear the statement image and category links, applies pending test-case removals (`tc_remove_ids`) and inline add-rows (`tc_in_N` / `tc_out_N` / `tc_explanation_N` / `tc_is_sample_N`) on save, then redirects while preserving list-return state. |
| `POST` | `/admin/problems/{problem_id}/toggle-enabled` | Toggle the problem's `enabled` flag and redirect to the problem list while preserving query state and adding `#problem_id` row highlight. |
| `POST` | `/admin/problems/{problem_id}/delete` | Permanently delete the problem and all dependent data (test cases, submissions, judgments, AI reviews, solve/attempt/favourite records). Requires current-password confirmation via form field. Redirects to the problem list on success or back to the edit page on wrong password. |
| `POST` | `/admin/problems/{problem_id}/rejudge-all` | Create new `QUEUED` judgment rows for every existing submission and enqueue them on the low-priority autojudge queue (`judge:queue:pending`). Requires current-password confirmation. Redirects back to the edit page. |

## Arena Admin – Problem Import/Export (`arena/routes/admin_problem_io.py`)

All routes require `ArenaRole.ARENA_ADMIN` or any user with the `can_edit` grant (a plain `ARENA_JUDGE` without `can_edit` is rejected). Non-admin editors are restricted to
their own problems on export. The ZIP package format is kept compatible with the web module's
problem packages.

| Method | URL | Description |
|--------|-----|-------------|
| `GET` | `/admin/problems/import` | Render the problem import upload page (file picker plus package-format documentation). |
| `POST` | `/admin/problems/import` | Import a problem from an uploaded ZIP (`package` field). The current user becomes owner; a non-empty `problem.json.author` is preserved as free text, while a missing author uses the owner's fullname. The route preserves `source`, imports test cases as secret, validates an optional image, links existing categories, and redirects to edit. |
| `GET` | `/admin/problems/{problem_id}/export` | Download a ZIP package with all problem data. `problem.json.author` contains the free-text author or owner fullname according to `author_is_owner`; the package also includes the statement, test cases, categories, source, and optional image. |

## Arena Admin – Problem Test Cases (`arena/routes/admin_problem_tc.py`)

All routes require `ArenaRole.ARENA_ADMIN` or any user with the `can_edit` grant (a plain `ARENA_JUDGE` without `can_edit` is rejected). Non-admin editors are restricted to
their own problems; admins may manage any problem's test cases.

| Method | URL | Description |
|--------|-----|-------------|
| `POST` | `/admin/problems/{problem_id}/testcases/add` | Append one test case to the problem and redirect back to the problem edit page. |
| `POST` | `/admin/problems/{problem_id}/testcases/add-zip` | Add a single new test case from an uploaded single-case ZIP (`input.txt` / `output.txt`, optional `explanation.txt`). Redirects back to the problem edit page. |
| `GET` | `/admin/problems/{problem_id}/testcases/new` | Render the form for adding one test case. |
| `GET` | `/admin/problems/{problem_id}/testcases/{tc_id}/edit` | Render the edit form for one test case. |
| `POST` | `/admin/problems/{problem_id}/testcases/{tc_id}/edit` | Update one test case's input, output, and sample flag. |
| `POST` | `/admin/problems/{problem_id}/testcases/{tc_id}/toggle-sample` | Flip one test case between sample and secret without opening the edit form, then redirect to the problem edit page anchored to `#tc-{tc_id}` so the row is highlighted. |
| `POST` | `/admin/problems/{problem_id}/testcases/{tc_id}/move` | Move one test case to `?new_ordinal=N`, renumber rows contiguously, and return the refreshed list partial. |
| `POST` | `/admin/problems/{problem_id}/testcases/zip-replace` | Replace all test cases from an uploaded ZIP archive using the standard `in/001.in` + `out/001.out` or flat `001.in` + `001.out` format. |
| `GET` | `/admin/problems/{problem_id}/testcases/{tc_id}/download` | Download one test case as a single-case ZIP (`input.txt` / `output.txt`, optional `explanation.txt`); used for the offline edit round-trip of large cases. |
| `POST` | `/admin/problems/{problem_id}/testcases/{tc_id}/replace` | Replace one test case from an uploaded single-case ZIP (`input.txt` / `output.txt`, optional `explanation.txt`); no size cap. |

## Arena Admin – Problem JSON API (`arena/routes/admin_problem_api.py`)

All routes require `ArenaRole.ARENA_ADMIN` or any user with the `can_edit` grant (a plain `ARENA_JUDGE` without `can_edit` is rejected). Non-admin editors are restricted to
their own problems where applicable.

| Method | URL | Description |
|--------|-----|-------------|
| `GET` | `/admin/problems/categories/search` | JSON endpoint for the admin problem form category picker. Returns matching categories as `{id, name, color, foreground_color}` for `?q=`. |
| `GET` | `/admin/problems/{problem_id}/rating-history` | JSON endpoint returning the problem's rating history for the last 24 months as `{"history": [{"ts": ISO8601, "rating": float}]}` (display-scale, e.g. `7.3`) in chronological order. |

## Ranking (`arena/routes/ranking.py`)

No authentication required. `current_user` dependency is optional so the sidebar renders correctly for logged-in users.

| Method | URL | Description |
|--------|-----|-------------|
| `GET` | `/ranking` | Ranking landing page with two choice cards: User Ranking and Affiliation Ranking. |
| `GET` | `/ranking/users` | Paginated user ranking (50/page). Query params: `search` (name/email ilike), `page`. Global rank computed via SQL `RANK()` window function over all eligible users before search is applied, so rank positions are globally consistent. |
| `GET` | `/ranking/affiliations` | Paginated affiliation ranking (50/page). Query params: `search` (name ilike), `country_code`, `subdivision_code`, `page`. Country/subdivision filter options are sourced from actual affiliation data, not the full pycountry list. |
| `GET` | `/ranking/affiliations/{affiliation_id}/users` | Paginated user ranking scoped to one affiliation's members. Same layout and query params as `/ranking/users`. Returns 404 if the affiliation is not found. |
