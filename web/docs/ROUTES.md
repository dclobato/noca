# NOCA Web Routes

> For `url_for()` endpoint names and path parameters, see [URL_FOR_REFERENCE.md](URL_FOR_REFERENCE.md).

## Static assets

Web exposes module-specific assets and shared assets through separate named
mounts. Shared images are available at `/static/shared-img/{path}` through the
`static_shared_img` mount. The shared `static_vendor` mount serves circular
Brazilian national and UF flags at
`/static/vendor/img/state-flags/{uppercase-code}.svg`.

## Assets (`web/routes/assets.py`)

These public routes serve shared presentation SVG images. Balloon and star
artwork supports color and optional letter customization; medals use one of
three fixed bands.

| Method | URL | Description |
|--------|-----|-------------|
| `GET` | `/assets/balloon/{color}` | Returns an inline SVG balloon. `color` is a 3 or 6 digit hex color, such as `00ff00`. |
| `GET` | `/assets/balloon/{color}/{letter}` | Returns a balloon with the first ASCII letter from `letter` centered inside it and rendered uppercase. The letter is black or white, whichever has more contrast with `color`. A segment containing anything other than ASCII letters returns `400`. |
| `GET` | `/assets/star/{color}` | Returns an inline SVG star. `color` is a 3 or 6 digit hex color, such as `00ff00`. |
| `GET` | `/assets/star/{color}/{letter}` | Returns a star with the first ASCII letter from `letter` centered inside it and rendered uppercase. The letter is black or white, whichever has more contrast with `color`. A segment containing anything other than ASCII letters returns `400`. |
| `GET` | `/assets/medal/{band}` | Returns the shared Gold, Silver, or Bronze medal SVG. Unsupported bands return `400`. |

---

## Root And Public Pages (`web/routes/root.py`)

| Method | URL | Description |
|--------|-----|-------------|
| `GET` | `/favicon.ico` | Returns app's favicon with public cache headers. |
| `GET` | `/` | Public gateway page listing running, upcoming, and (capped to the `PAST_CONTESTS_PREVIEW_LIMIT` most recent) past contests, each with a link to its login page. Past contests whose problem set has been released after the contest ended also link to their public problem-set archive. A "View all" link to `/contests/past` appears whenever more past contests exist than the preview cap. No authentication required. |
| `GET` | `/contests` | Alias for `/`, same `contests_list` handler and template. No authentication required. |
| `GET` | `/contests/past` | Public page listing every past contest, most recently ended first, with no cap. Same card layout and problem-set link as `/`. No authentication required. |

---

## Announcements (`web/routes/announcements.py`)

The platform announcement board (#138), distinct from the per-contest clarification
announcements judges post. Rows live in the shared `announcements` table and are
scoped by `domain`; these routes serve `domain=web` only, so an Arena announcement
answers the same `404` an unknown id does. Both routes are on the public allowlist
(`/announcements` prefix in `web/dependencies.py`). Only `GET` is registered: a
published announcement is immutable.

| Method | URL | Description |
|--------|-----|-------------|
| `GET` | `/announcements` | Paginated list of Web announcements, newest first, fixed at 25 per page (`?page=`, clamped). Each title links to its detail page carrying the current page; each row is `id="announcement-<id>"` and carries `noca-target-row`, so a return with that fragment keeps the row highlighted. The Contests page links here. No authentication required. |
| `GET` | `/announcements/{announcement_id}` | One announcement: title, publisher label, publication time, and the Markdown body rendered in the browser through the single shared pipeline (KaTeX, Mermaid, and external links). `?page=` (default 1) is the list page the reader came from; the Back button returns to `/announcements?page=<N>#announcement-<id>`. `404` when absent or published on Arena. No authentication required. |

---

## Public Problem Set (`web/routes/problem_set.py`)

Anonymous, and therefore guarded twice: every request first counts against the
per-IP `web:problem-set` fixed window of the shared
`shared/services/request_rate_limit.py` primitive (`web/services/public_rate_limits.py`,
knobs `NOCA_WEB_PUBLIC_RATE_LIMIT_*`), ahead of the gate query; then the
archive is served from the on-disk cache when `NOCA_WEB_PUBLIC_PROBLEM_PACK_PATH`
is set. Production refuses the uncached path with `503` -- and refuses to start
without it at all, since that same setting also backs the per-problem export
cache described under the contest problem routes below.

| Method | URL | Description |
|--------|-----|-------------|
| `GET` | `/problem-set/{slug}.zip` | Downloads one ZIP bundling every problem **Per-IP request limit** (`web:problem-set`, `NOCA_WEB_PUBLIC_RATE_LIMIT_PROBLEM_SET_*`, default 10 per 10 min): `429` + `Retry-After`, checked before the gate query so it never confirms a slug. **In production without `NOCA_WEB_PUBLIC_PROBLEM_PACK_PATH` the route answers `503`** (`Problem-set archive cache is not configured.`) before any query — rebuilding the whole problem set per anonymous request is never acceptable there; development keeps the rebuild. of the contest as its full version-2 package (statement, all test cases, validator source, and `editorial.md` when set) plus a top-level `index.json` manifest. Public: no authentication required, but answers `404` unless the contest is active, over, and has `release_problem_set_after_end` set (independent of the scoreboard release; `is_past` is not, and keeps the materials private while the contest runs); `409` when a problem's stored files are missing. When `NOCA_WEB_PUBLIC_PROBLEM_PACK_PATH` is configured, the archive is built once per contest and served from that on-disk cache (integrity-verified via a `.sha256` sidecar); otherwise every download rebuilds it. |

---

## Health (`web/routes/health.py`)

The health endpoint reports whether Web's required runtime backends are
available. Public callers are rate-limited before the backend probes run;
trusted local health-check CIDRs bypass this limit.

| Method | URL | Description |
|--------|-----|-------------|
| `GET` | `/health` | Reports PostgreSQL, Valkey, and Web service health. Returns `200` with `status: "ok"`, `503` with `status: "degraded"`, or `429` when public health probes exceed the configured limit. |

---

## Session keepalive (`web/routes/session.py`)

Web sessions slide, but the cookie only rotates on a request that arrives inside
the token's half-life window, and a page left open makes none. `_base.html` loads
`shared/static/js/noca-presence.js` for every live session so an open page keeps
pinging; without it a long edit ends at the login page and the redirect discards
the form body.

| Method | URL | Description |
|--------|-----|-------------|
| `POST` | `/session/heartbeat` | Rotates the caller's sliding session and returns `{"ok": true}`. Deliberately inert — no database, no Valkey, no request body — because the rotation is a side effect of the request being authenticated: the app-wide `enforce_web_default_auth` dependency marks every authenticated non-public path refresh-eligible, so the path staying off the public allowlist is the whole of this route's authentication. The refreshed cookie, when one is due, travels on the response headers. |

---

## Authentication (`web/routes/auth.py`)

Password **reconfirmation** — the five routes that ask an authenticated actor
to type their password again (`POST /profile/password`,
`/c/{slug}/admin/start-now`, `/c/{slug}/admin/end-now`,
`/uberadmin/contests/{id}/remove`, `/uberadmin/contests/{id}/export` with
password hashes) — shares one throttle budget in
`web/services/password_confirm_throttle.py`, built on the same
`shared.services.auth_rate_limit` primitive and `NOCA_AUTH_RATE_LIMIT_*` caps as
`/login`, keyed by the actor's id and by the client IP. The lockout is checked
before the password, so a locked actor is refused the shared `429` page
(`errors/too_many_attempts.html`, with `Retry-After`) even with the correct
password; every wrong attempt and every lockout is a `security_events` row.

The two login throttles differ in how precisely they name an account, and
deliberately so. `/login` keys its account bucket on the typed name, because an
UberAdmin username is global (`uq_uber_admins_username`). `/c/{slug}/login` keys
it on `{contest_id}:{username}` instead, because a contest login is unique only
*per contest* (`uq_users_contest_username`): on the bare name, five failures as
`admin` against the least important contest on the deployment would lock `admin`
out of every contest, including ones the attacker has no account in, and an
administrative unlock could never be finer than the name either. The contest
*id* and not the slug, since a slug can be renamed while a lock is live. The
per-IP bucket -- which is what caps raw volume -- is untouched by the scoping.
`web/services/lockout_admin_service.contest_login_identifier` builds that
identifier for both the login route and the administrative unlock, so the two
cannot drift; it strips the name before prefixing (`normalize_identifier`
casefolds and strips the *whole* string, so an unstripped name would keep its
inner spaces) and leaves a blank name without an account bucket at all.

All non-public Web routes require a valid `noca_access_token` by default. The
public allowlist is `/`, `/contests`, `/contests/past`, `/login`,
`/c/{slug}/login`, `/health`, `/favicon.ico`, `/assets/*`, `/static/*`, and
`/problem-set/*`. Route-local role checks remain the authoritative
authorization layer after authentication.

| Method | URL | Description |
|--------|-----|-------------|
| `GET` | `/login` | Renders the username-only UberAdmin login form. Accepts an optional same-origin path in `next_url`; missing or unsafe targets use `/uberadmin`. |
| `POST` | `/login` | Authenticates an UberAdmin by username and password. Missing or invalid credentials re-render the form with an accessible inline error and retain the non-secret username. Auth throttling is keyed by ASGI client IP and hashed username. Successful login sets the `noca_access_token` HTTP-only cookie and redirects to the safe same-origin `next_url` or `/uberadmin`. Lockouts return `429` with `Retry-After` and show the retry interval. |
| `POST` | `/logout` | Clears the `noca_access_token` cookie and redirects with a confirmation message. Contest-scoped users are redirected to `/c/{slug}/login`; other cases fall back to `/login`. The navbar exposes this POST form inside the account menu; it is never a link because a `GET` logout is prefetchable by browsers and extensions. |
| `GET` | `/c/{slug}/login` | Renders the contest login form for the given contest slug. Accepts an optional `next` query value -- the page to return to after login, which the default-auth redirect fills with the bounced request's own path (or, for a bounced `POST`, its same-origin `Referer`) -- kept only when it is a same-origin path inside `/c/{slug}/` other than the login page. Returns 404 if slug is not found or the contest is inactive. |
| `POST` | `/c/{slug}/login` | Authenticates a contest user for an active contest. Validates `identifier` + `password` form fields, applies auth throttling keyed by ASGI client IP and hashed identifier, sets `noca_access_token` cookie on success, and redirects to the re-validated `next_url` form field or `/c/{slug}`. A failed attempt redirects back to the form keeping `next`. Lockouts return `429` with `Retry-After`. |

---

## UberAdmin Dashboard (`web/routes/uberadmin_dashboard.py`)

All routes in this group require a valid UberAdmin JWT (`noca_access_token` cookie). Unauthenticated requests are redirected to `/login`.

| Method | URL | Description |
|--------|-----|-------------|
| `GET` | `/uberadmin` | Renders the UberAdmin dashboard. Displays three columns — Past, Live, and Upcoming contests — sourced from active contests in the database. Past contest cards include a Make inactive action. Also shows action buttons for creating contests, accessing the problem bank, viewing inactive contests, and managing UberAdmins. |
| `GET` | `/uberadmin/security-events` | Renders the Web security-event log (auth failures, lockouts, existing-account signups, admin actions). Accepts optional `?event_type=`, `?per_page=` (10/25/50/100/500), and `?page=` filters; shows all retained matching Web rows through pagination. Route lives in `web/routes/uberadmin_security.py`. |
| `GET` | `/uberadmin/security-events.csv` | Downloads the **complete** Web security-event log as a CSV attachment (UTF-8 BOM, newest first, one row per event with the JSON metadata in a single cell). Takes no query parameters: the export deliberately ignores the page's filters and pagination, and is scoped to `module=web` only. Route lives in `web/routes/uberadmin_security.py`. |
| `GET` | `/uberadmin/lockouts` | Sign-in lockouts page: two password-confirmed forms (unlock an IP address, unlock a login) and, when a subject is prefilled through `?ip=`, `?identifier=` (a username) or `?identifier_hash=` (the exact throttle hash a security event recorded; the event viewer's *Unlock* links carry both), the subject's live status -- each active lock as `<module>/<action>`, `address`/`account`, the contest for a `contest-login` row, and the minutes left -- read from Valkey **and** this process's in-memory fallback. A prefilled login is read **wide** (every contest carrying the name) and each `contest-login` row is labelled from the `hash -> contest` map the resolver built; a lock whose hash is in no map (a hash-only prefill, or a name matching no account) stays unlabelled rather than guessed. The scope choice guards the unlock, not this read. The login form carries a **required** `Scope` select with a valueless `Select…` placeholder, an `All contests` option and every active contest (`contest_name (login_slug)`); with `?identifier_hash=` prefilled the form is instead hash-only -- the login is shown read-only and not submitted, and no select is rendered. A store that cannot answer renders *Status unknown*, never *Not locked*. Route lives in `web/routes/uberadmin_lockouts.py`. |
| `POST` | `/uberadmin/lockouts/unlock-ip` | Lifts every `auth:rate-limit:web:*` **and** `auth:rate-limit:animator:*` failure counter and lock of one client address -- the animator has no admin surface of its own -- discovered by `SCAN` so a bucket added later is covered without a registry. It also clears that address's distinct-account set (`…:ip:{ip}:accounts`), the spray evidence gating an IP lock: unlocking an address asserts it is not spraying, and leaving the set would let the next burst re-lock on `max_failures` alone. An *account* unlock never clears one; Arena buckets for the same address are never touched, and the `unknown` no-client sentinel is refused. Form: `ip`, `password` (the shared `password-confirm` reconfirmation budget, checked before the hash; a locked UberAdmin gets the `429` page). Fail-closed: the process-local fallback is cleared first, and if Valkey cannot answer the flash says so and the `admin_action` row records `outcome=valkey_unavailable`. Otherwise audited as `action=unlock_ip`, `target_type=client_ip`, warning severity. Redirects back with `?ip=` so the status re-renders. Route lives in `web/routes/uberadmin_lockouts.py`. |
| `POST` | `/uberadmin/lockouts/unlock-account` | Lifts the Web account lockouts behind a typed login **within one chosen scope**, and/or the exact bucket of an explicit event hash. `web`/`contest-login` is keyed on `{contest_id}:{username}` (a team login is unique only per contest), so `contest_scope` is **required** whenever `identifier` is non-blank and is validated here against the same active-contest list the form offered -- a blank or unknown scope unlocks nothing and is not audited. There is no default on purpose: the two mistakes are not symmetric, since a silent wide unlock and a narrow one look identical afterwards. `all` clears the bare-name `login` bucket, the `uberadmin:<id>` bucket, every contest's `{contest_id}:{username}` bucket and every matching `user:<id>`; a contest id clears only that contest's scoped bucket and its own users' `user:<id>` -- an UberAdmin is not a contest, so a scoped unlock never reaches a global bucket. An `identifier_hash` needs no scope (it names one exact bucket in one contest) and is always cleared; a request carrying both a typed login and a hash with no scope is refused. IP buckets are left alone. Form: `identifier` + `contest_scope` and/or `identifier_hash`, `password`. Audited as `action=unlock_account`, `target_type=login` naming the username, the scope and what it matched (`team042 in Other Contest (other-contest) (1 contest user)` / `team042 in all contests (…)`; logins are not secrets in Web), or `identifier_hash` with a prefix for a hash-only request. Same fail-closed rule as `unlock-ip`. Route lives in `web/routes/uberadmin_lockouts.py`. |
| `GET` | `/uberadmin/announcements` | Announcement management list (`?page=`, 25 per page): title (linking to the public detail page), publication time, publisher, and a delete action confirmed through `data-confirm`. There is no edit action: announcements are immutable. Route lives in `web/routes/uberadmin_announcements.py`. |
| `GET` | `/uberadmin/announcements/new` | Renders the create form. The body is authored with the shared statement editor (`problem-statement-editor-core.js` via `announcement-editor.js`), with the link toolbar action enabled. Route lives in `web/routes/uberadmin_announcements.py`. |
| `POST` | `/uberadmin/announcements` | Publishes an announcement from `title` and `body` (Markdown; LaTeX, Mermaid and external links allowed; raw HTML and images refused; 512 KB cap). `required` is fixed to `false` on Web and never read from the form. A refusal re-renders the form with the messages and the typed values (`200`); success records an `admin_action` (`publish`, info) security event on the same transaction and redirects (`303`) to the management list at `?page=1#announcement-<id>`. Route lives in `web/routes/uberadmin_announcements.py`. |
| `POST` | `/uberadmin/announcements/{announcement_id}/delete` | Removes one Web announcement (form field `page` is carried back to the list). `404` when the id is unknown, belongs to Arena, or was deleted concurrently, in which case no audit row is written; otherwise records an `admin_action` (`delete`, **warning**) security event, since deletion is irreversible, and redirects (`303`) to the list. Route lives in `web/routes/uberadmin_announcements.py`. |
| `GET` | `/uberadmin/uberadmins` | Lists UberAdmins. Accepts optional `?q=` search over full name and email. |
| `GET` | `/uberadmin/uberadmins/new` | Renders the Add UberAdmin form (fields: full name, email, username). |
| `POST` | `/uberadmin/uberadmins/new` | Creates a new UberAdmin. Validates form fields, checks for duplicate username/email, generates a diceware password, persists the record, and re-renders the page with the generated credentials on success or an error message on failure. |
| `GET` | `/uberadmin/uberadmins/{uberadmin_id}/edit` | Renders the UberAdmin edit form for full name, email, and optional password replacement. |
| `POST` | `/uberadmin/uberadmins/{uberadmin_id}/edit` | Updates an UberAdmin after validating full name, email uniqueness, and optional password policy. |
| `POST` | `/uberadmin/uberadmins/{uberadmin_id}/toggle` | Enables or disables an UberAdmin account. Self-disable is blocked. Disabled accounts cannot log in or continue authenticated requests. |
| `POST` | `/uberadmin/uberadmins/credentials.json` | Returns the given `username`/`password` form fields as a downloadable JSON file (`noca-credentials-<username>.json`). Intended for use immediately after UberAdmin creation. |
| `POST` | `/uberadmin/uberadmins/credentials/email` | Sends a credentials email for the just-created UberAdmin when the completion page's email action is used; re-renders the completion page with the delivery outcome, or `422` when the UberAdmin has no email address. Records a `credential_email_sent` / `credential_email_failed` security event. The email goes through the shared `EmailService` (async, off the event loop): it is handed to the `noca-mailer` worker -- the only process that sends mail -- and the result reads *queued* rather than *sent*; it is charged to the acting admin's email budget (`NOCA_EMAIL_BUDGET_ADMIN_MAX`), and a spent budget is reported as a failed delivery naming the wait. |
| `GET` | `/uberadmin/contests/new` | Renders the Create Contest form (contest metadata + allowed-language checkboxes + initial admin credentials). Loads all active languages from DB to populate the checkbox panel. When the user continues to **Timing & Rules**, the wizard sets the scoreboard cutoff to 20 minutes before the entered duration and the answer cutoff to 10 minutes before it. Includes a Publication section whose `release_problem_set_after_end` radios default to Keep private. |
| `POST` | `/uberadmin/contests/new` | Validates form (including `language_ids[]` — at least one required), atomically creates a Contest, its initial admin User (role: admin), and `contest_languages` rows. Success renders a dedicated completion page with the generated credentials, download/email actions, and a return-to-dashboard action; failures return to the populated wizard with an error message. The contest website URL, owner email, and owner password are optional; a blank password triggers auto-generation. Includes `allow_print_requests` metadata (default enabled). Before submission, the review dialog summarizes identification, schedule, timing, rules, publication, languages, and owner data. Accepts the `release_problem_set_after_end` (`yes`/`no`) metadata field from the form's Publication section; an absent field means `no`, so a contest created without an explicit choice withholds its public problem-set archive. |
| `GET` | `/uberadmin/contests/inactive` | Lists inactive contests for UberAdmins. Each card provides export and permanent-removal actions. Removal opens a password-confirmation modal. |
| `POST` | `/uberadmin/contests/{contest_id}/deactivate` | Marks an active past contest inactive and redirects back to `/uberadmin`. Running, upcoming, missing, and already inactive contests are left unchanged. |
| `POST` | `/uberadmin/contests/{contest_id}/remove` | Permanently removes an inactive contest after reconfirming the current UberAdmin password. The synchronous operation requires verified Valkey cleanup, quarantines problem files until the PostgreSQL transaction commits, and redirects to the inactive list with a success or danger flash. Active, missing, and duplicate targets are harmless failures. Route lives in `web/routes/uberadmin_contest_removal.py`. **Password reconfirmation is throttled** through the shared `password-confirm` budget (`web/services/password_confirm_throttle.py`, same `NOCA_AUTH_RATE_LIMIT_*` caps as login, keyed by actor and IP, shared with the other four reconfirming routes): a wrong password is counted and recorded as an `auth_failure` security event; once the cap is spent the actor gets the shared `429` "too many attempts" page with `Retry-After` **before** the password is checked, so even the correct password is refused and nothing is removed. |
| `POST` | `/uberadmin/contests/credentials.json` | Returns the contest admin credentials (`contest_slug`, `username`, `password`) as a downloadable JSON file (`noca-credentials-<slug>-<username>.json`). Intended for use immediately after contest creation. |
| `POST` | `/uberadmin/contests/credentials/email` | Sends a credentials email for the just-created contest owner when an email is available. Uses the configured web email provider and the standard plain-text NOCA credentials template. The email goes through the shared `EmailService` (async, off the event loop): it is handed to the `noca-mailer` worker -- the only process that sends mail -- and the result reads *queued* rather than *sent*; it is charged to the acting admin's email budget (`NOCA_EMAIL_BUDGET_ADMIN_MAX`), and a spent budget is reported as a failed delivery naming the wait. |
| `GET` | `/uberadmin/contests/{contest_id}/export` | Renders the contest backup export form for a finished or inactive contest, with optional user-media and password-hash controls. |
| `POST` | `/uberadmin/contests/{contest_id}/export` | Builds and streams the contest backup ZIP from a temporary file. Password-hash exports require password reconfirmation and record an audit row only after archive creation succeeds. **Password reconfirmation is throttled** (shared `password-confirm` budget, see the remove route): a wrong reconfirmation is an audited `auth_failure`, and past the cap the export answers the shared `429` page before checking the password; hash-free exports never touch the budget. |
| `GET` | `/uberadmin/contests/import` | Renders the contest backup import form (upload + new name + new slug). |
| `POST` | `/uberadmin/contests/import` | Applies compressed and expanded size ceilings, validates the complete archive graph, and restores it under a new name/slug in one transaction. |

---

## Contest User Dashboard (`web/routes/generaluser_dashboard.py`)

Routes require a valid contest-scoped JWT or UberAdmin JWT (`noca_access_token` cookie). Invalid or missing auth is currently redirected to `/contests`.

| Method | URL | Description |
|--------|-----|-------------|
| `GET` | `/c/{slug}` | Renders the contest user dashboard with role-appropriate module cards. The Clarifications counter shows unanswered questions to judges/admins and, to teams, unread clarifications — unread answers to their own questions plus unacknowledged announcements, merged into one number. Returns 404 for inactive contests. |
| `GET` | `/c/{slug}/clock` | Returns a JSON contest clock snapshot for browser polling every 60 seconds. Payload: `{server_now_ms, start_ms, end_ms, freeze_ms, blind_ms, state}`; all times are absolute epoch milliseconds. `state` is `upcoming`, `running`, `frozen`, `silence`, or `past`, and the browser derives phase changes locally between polls from the four boundary fields. Accepts both contest-scoped and UberAdmin tokens. |

---

## Contest Modules

All routes require contest-scoped or UberAdmin auth. Invalid or missing auth is
currently redirected to `/contests`. When a logged-in actor reaches a forbidden
feature, the Web exception handler redirects them to `/c/{slug}/` or, for an
UberAdmin, `/uberadmin/`, and shows a danger alert naming the role that was
denied. Standard requests receive a `303`; HTMX requests receive an
`HX-Redirect` response so the browser performs the same full-page navigation.

### Access Matrix

**The matrix lives in [`web/access_matrix/access.py`](../access_matrix/access.py)** —
`ACCESS_AREAS`, one `AccessArea` per contest module, each declaring an `AccessRule`
for every actor. That module is the source of truth; this document points at it
rather than restating it, because a markdown copy is exactly what used to drift.

It is not documentation that happens to be in Python.
`tests/web/test_access_matrix.py` calls the real route gates —
`contest_score._access_blocked`, `contest_problems._check_access`, the `_ALLOWED`
tuples in the clarification and runs helpers, and `_ensure_task_access` composed
with `contest_tasks_helpers._access_blocked` — for a real actor of every role, in
both a running and a not-yet-started contest, and fails when an answer disagrees
with a declared cell. So the declaration cannot quietly go stale: change the
guard and the test tells you which cell to update.

`always` = accessible regardless of contest state; `after-start` = only when
`contest.is_running or contest.is_past`; `—` = no access (not in dashboard).

The declared matrix gives the **chief judge its own row**, which the table here
used to fold into a parenthetical on the JUDGE/Tasks cell ("chief judge only,
after-start"). That phrasing reads as though a judge reaches Tasks under a
restriction; a plain judge reaches Tasks not at all. The two rows now each say
one thing, and `can_view_tasks` is what proves it.

Contest admins granting a role see this matrix rendered on the Add User form —
`web/template/admin/users/_role_reference.html`, which reads it through the
`role_matrix` template global.

For *what each role may do* inside a page — answer a clarification, confirm a verdict,
handle a task — see [Permission Model](#permission-model) below.

| Method | URL | Allowed Roles | File |
|--------|-----|---------------|------|
| `GET` | `/c/{slug}/scoreboard/` | ua, a, j, s, t, u (after-start for s/t/u) | `contest_score.py` |
| `GET` | `/c/{slug}/problems/` | ua, a, j, s, t (after-start for s/t) | `contest_problems.py` |
| `GET` | `/c/{slug}/problems/{problem_label}` | ua, a, j, s, t (after-start for s/t) | `contest_problems.py` |
| `GET` | `/c/{slug}/problems/{problem_label}/statement` | ua, a, j, s, t (after-start for s/t) | `contest_problems.py` |
| `GET` | `/c/{slug}/problems/{problem_label}/print` | ua, a, j, s, t (after-start for s/t) | `contest_problems.py` |
| `GET` | `/c/{slug}/problems/{problem_label}/export` | ua, a, j, s, t (after-start for s/t) | `contest_problems.py` |
| `GET` | `/c/{slug}/clarifications/` | ua, a, j, t | `contest_clarifications.py` |
| `GET` | `/c/{slug}/clarifications/list` | ua, a, j, t | `contest_clarifications.py` |
| `POST` | `/c/{slug}/clarifications/new` | t (running only) | `contest_clarifications_submit.py` |
| `POST` | `/c/{slug}/clarifications/acquire` | j, a | `contest_clarifications_judge.py` |
| `GET`/`POST` | `/c/{slug}/clarifications/answer` | j, a | `contest_clarifications_judge.py` |
| `GET` | `/c/{slug}/runs/` | ua, a, j, t (after-start for j/t) | `contest_runs.py` |
| `GET` | `/c/{slug}/runs/list` | ua, a, j, t (after-start for j/t) | `contest_runs.py` |
| `GET` | `/c/{slug}/runs/events` | ua, a, j, t | `contest_runs_events.py` |
| `POST` | `/c/{slug}/runs/submit` | t | `contest_runs_review.py` |
| `POST` | `/c/{slug}/runs/{submission_id}/override` | chief judge, admin | `contest_runs_review.py` |
| `GET` | `/c/{slug}/runs/{submission_id}/judging-history` | ua, a, j | `contest_runs_events.py` |
| `GET` | `/c/{slug}/submissions/{submission_id}/review` | ua, a, j | `contest_submissions.py` |
| `POST` | `/c/{slug}/submissions/{submission_id}/acquire-review` | j, a | `contest_submissions.py` |
| `POST` | `/c/{slug}/submissions/{submission_id}/confirm` | j, a | `contest_submissions.py` |
| `POST` | `/c/{slug}/submissions/{submission_id}/rejudge` | chief judge, admin, uberadmin | `contest_submissions.py` |
| `GET` | `/c/{slug}/solution-tests/` | ua, a, j | `contest_solution_tests.py` |
| `POST` | `/c/{slug}/solution-tests/submit` | ua, a, j | `contest_solution_tests.py` |
| `GET` | `/c/{slug}/solution-tests/{run_id}` | ua, a, j | `contest_solution_tests.py` |
| `GET` | `/c/{slug}/solution-tests/{run_id}/status` | ua, a, j | `contest_solution_tests.py` |
| `GET` | `/c/{slug}/tasks/` | ua, a, cj, s, t (after-start for cj/s/t) | `contest_tasks.py` |
| `GET` | `/c/{slug}/tasks/list` | ua, a, cj, s, t (after-start for cj/s/t) | `contest_tasks.py` |
| `POST` | `/c/{slug}/tasks/sos` | t | `contest_tasks.py` |
| `POST` | `/c/{slug}/tasks/print` | t | `contest_tasks.py` |
| `POST` | `/c/{slug}/tasks/{task_id}/acquire` | s, a, cj | `contest_tasks_staff.py` |
| `POST` | `/c/{slug}/tasks/{task_id}/finish` | s, a, cj | `contest_tasks_staff.py` |
| `POST` | `/c/{slug}/tasks/{task_id}/release` | s, cj (own), a, ua | `contest_tasks_staff.py` |
| `GET` | `/c/{slug}/tasks/{task_id}/source` | s, cj (lock holder), a, ua | `contest_tasks_source.py` |
| `GET` | `/c/{slug}/tasks/{task_id}/printout` | s, cj (lock holder), a, ua | `contest_tasks_source.py` |
| `GET` | `/c/{slug}/reports/` | ua, a, j | `contest_reports.py` |
| `GET` | `/c/{slug}/admin` | ua, a | `contest_admin.py` |
| `GET` | `/c/{slug}/admin/counters` | ua, a | `contest_admin.py` |
| `GET` | `/c/{slug}/admin/export-animeitor` | ua, a | `contest_admin.py` |
| `POST` | `/c/{slug}/admin/chief-judge` | ua, a | `contest_admin.py` |

---

## Contest Scoreboard (`web/routes/contest_score.py`)

ICPC-style scoreboard. Access rules depend on role and contest state:

- **ua / a (uberadmin, admin) / j (judge):** always accessible; see live results regardless of freeze
- **s (staff) / t (team) / u (user):** accessible only while `contest.is_running` or `contest.is_past`; submissions after `contest.stop_updating_scoreboard` minutes are shown as pending

Scoring rules:
- A problem is solved by the first AC (or PE if `contest.accept_pe`) final verdict.
- WA, RE, TLE, MLE, OLE count as failed attempts before the first AC/PE.
- CE counts as a failed attempt only if `contest.ce_adds_penalty`.
- Penalty time = `solved_at_minutes + (failed_attempts × contest.wa_penalty)`, where
  `solved_at_minutes` truncates the second offset to whole minutes.
- Rank: teams sorted by `(problems_solved DESC, total_time ASC, last_accepted_minutes ASC)`
  — the earlier last accepted submission breaks a tie on the first two keys, and a team
  that solved nothing sorts last within its group. Teams equal on all three share the
  same rank, and the next distinct team takes its position-based rank.

Caching: Valkey cache with three keys — `:full` for admin/judge (TTL 5 s), `:public` for all others (TTL 180 s), and `:final` for the released final scoreboard (no TTL, permanent). `:full` and `:public` are invalidated whenever a verdict is finalized or overridden.

Post-contest behavior:
- If `contest.release_scoreboard_after_end` is True: everyone sees the final scoreboard (`:final` cache, all results revealed, "Contest Final Scoreboard" badge).
- If `contest.release_scoreboard_after_end` is False: everyone (including admins) sees the frozen scoreboard ("SCOREBOARD FROZEN" badge).

| Method | URL | Allowed | Description |
|--------|-----|---------|-------------|
| `GET` | `/c/{slug}/scoreboard/` | ua, a, j, t, s | Full scoreboard page. Shows ICPC ranking table with per-problem balloon colors and penalty times. In contests with multiple sites, the optional `site_id` query parameter filters rows on the server while preserving global ranks. Users assigned to a site switch between **All sites** and **My site only** buttons; users without a site and uberadmins select all teams or any contest site from a combobox. Single-site contests show no site filter. Missing, stale, foreign-contest, and disallowed site IDs use the all-sites view. During the contest: admin/judge see live verdicts; others see frozen state (pending) after `contest.stop_updating_scoreboard`. After contest ends: if released, shows final standings with "Contest Final Scoreboard" badge; if not released, shows frozen view for all roles. HTMX auto-refresh every 30 s while the contest is running and not frozen and preserves the selected site. |

---

## Contest Problems (`web/routes/contest_problems.py`)

Contestant-facing problem pages. Access is role- and state-dependent:

- **ua / a (uberadmin, admin):** always accessible regardless of contest state
- **j (judge):** always accessible regardless of contest state
- **s (staff) / t (team):** accessible only when contest has started (`is_running` or `is_past`)
- **u (user):** no access to this module

| Method | URL | Description |
|--------|-----|-------------|
| `GET` | `/c/{slug}/problems/` | Problem card grid (one card per problem: balloon, label, title linked to detail, per-problem solving rate, download action for public materials). No admin-only test-case counts. A TEAM viewer additionally sees their own status per problem (Accepted/Judging/Attempted/Not attempted) as a card accent, derived from the same `ScoreboardSnapshot` the scoreboard uses so freeze visibility rules are inherited automatically. For a TEAM viewer of a running contest the grid also loads `htmx.min.js` and `problems-sse.js`, subscribing to the Runs page's `/c/{slug}/runs/events` SSE stream to live-refresh the grid and fire a confetti celebration on a newly-solved problem. |
| `GET` | `/c/{slug}/problems/{problem_label}` | Problem detail: embedded PDF statement, side-by-side public test cases in monospaced preformatted text, back-to-list link. `problem_label` is case-insensitive (e.g. `A`, `B`, `AA`). Returns 404 if label not found. |
| `GET` | `/c/{slug}/problems/{problem_label}/statement` | Serves the stored statement inline (Markdown when present, otherwise the PDF). Returns 404 when no statement file is stored. **Conditional**: served through `StaticFiles.file_response()`, so a request carrying a matching `If-None-Match`/`If-Modified-Since` gets a bodyless `304` costing one `stat()`, and a `200` streams instead of loading the file into memory. `Cache-Control: private, no-cache` — deliberately *not* a positive `max-age`, which would let a browser reuse the response without contacting the server and therefore without re-running the pre-start access check. The `Content-Disposition` filename is built with `safe_package_filename()`, so a title carrying quotes or non-ASCII characters cannot break the header. |
| `GET` | `/c/{slug}/problems/{problem_label}/print` | Standalone print-friendly problem page (simple navbar + footer, no sidebar): statement, samples (test cases or sample interactions), and resource limits. PDF-statement problems link to the PDF instead of embedding it. The user prints via the browser (navbar Print button or Ctrl/Cmd+P). |
| `GET` | `/c/{slug}/problems/{problem_label}/export` | Downloads the contestant-facing (`public` profile) package: statement, image, sample test cases and sample interactions. No `problem.json`, no limits, no secret test cases, no validator source, no editorial. **Per-actor request limit** (`web:problem-export`, `NOCA_WEB_PROBLEM_EXPORT_RATE_LIMIT_*`, default 10 per 10 min): `429` + `Retry-After`, applied whether or not the package is served from cache, and stacked under the router's loose `web:user-read` ceiling. When `NOCA_WEB_PUBLIC_PROBLEM_PACK_PATH` is configured the package is built once per problem into `problem-export/`, published atomically, and reused while its sidecar records both a matching SHA-256 and the problem's current `public_export_generation`; any change to the problem bumps that counter and the next download rebuilds. **In production the route answers `503` when the cache path is unset** — though Web refuses to start in that state, so this is the guard rather than the expected path; development keeps the per-request rebuild. `409` when a stored file the package needs is missing. |

---

## Contest Clarifications (`web/routes/contest_clarifications*.py`)

Routes require a valid contest-scoped JWT or UberAdmin JWT. TEAM role is enforced at route level for submission. Visibility is role-scoped:
- `ua`/`a`: all clarifications (including hidden); Team name and Judge name resolved from `user_map`. ADMIN may also acquire and answer clarifications, exactly like a judge — but the answer form itself stays blind, so answering is never done with the asker's identity on screen
- `j`: all clarifications (including hidden); no judge/team identification; acquire and answer workflow
- `t`: own + public, never hidden; auto-refresh via HTMX every 60 s

Timing is role-scoped too:

| Role | Viewing clarifications | Requesting clarifications | Publishing announcements |
|------|------------------------|---------------------------|--------------------------|
| `ua` | any lifecycle state | never | never (authorship is a `users` FK) |
| `a` | any lifecycle state | never | any lifecycle state |
| `j` (chief judge) | any lifecycle state | never | any lifecycle state |
| `j` (plain) | any lifecycle state | never | while running only |
| `t` | any lifecycle state | while running only | never |
| `s`, `u` | no access (dashboard redirect) | never | never |

Teams can see their own and public clarifications before the contest starts,
including announcements published during contest preparation. The request form
is hidden until the contest is running, and the service rejects direct requests
outside that window. Every route resolves the contest through
`get_contest_by_slug()`, which returns 404 for an inactive (archived) contest,
so no clarification route is reachable for one.

Route ownership is split across `contest_clarifications.py`,
`contest_clarifications_submit.py`, `contest_clarifications_judge.py`, and
`contest_clarifications_admin.py`.

| Method | URL | Allowed | Description |
|--------|-----|---------|-------------|
| `GET` | `/c/{slug}/clarifications/` | ua, a, j, t | Full page. Shows the clarification list in every contest lifecycle state and, for teams while the contest is running, a submission form. `sort_by` orders Time or Problem in SQL and defaults to newest first. Flash messages shown via `get_flashed_messages`. Loads `htmx.min.js`, `highlight-row.js`, `refresh-timer.js`, and `clarifications.js`. |
| `GET` | `/c/{slug}/clarifications/list` | ua, a, j, t | HTMX partial. Returns `#clarifications-list-wrapper` with the clarification table in the requested server-side `sort_by` order. Polled every 60 s by team browsers. |
| `POST` | `/c/{slug}/clarifications/answers/read` | t | Marks only the supplied `clarification_ids` that were rendered to the requesting team as unread — its own answered questions and the contest's visible announcements alike, since a team is notified about both through one list. Announcement acknowledgement is idempotent, so a page load racing the 60 s refresh is harmless. Returns 204. Called by `clarifications.js` after the full page or HTMX partial displays highlighted unread rows. |
| `POST` | `/c/{slug}/clarifications/new` | t (running only) | Submit a new clarification. Form fields: `problem_id`, `question` (max 1024 chars). `can_request_clarification()` gates both the form and the service to the running contest window. On success redirects to `/c/{slug}/clarifications/#{id}` (303). On error flashes and redirects (303). Two per-team throttles apply, both counted in PostgreSQL: at most `NOCA_WEB_CLARIFICATION_RATE_LIMIT_MAX_UNANSWERED` unanswered questions at once (hidden rows excluded, since they will never be answered) and at most `NOCA_WEB_CLARIFICATION_RATE_LIMIT_MAX_REQUESTS` per `…_WINDOW_SECONDS`; either refusal is a danger flash plus a 303 back to `/c/{slug}/clarifications/`, the windowed one naming the next allowed time. Implemented in `contest_clarifications_submit.py`. |
| `POST` | `/c/{slug}/clarifications/announcement` | a, j | Create a public announcement. Not throttled: the team limits apply to `create_clarification` only. Form fields: `problem_id`, `announcement` (max 1024 chars). Creates a clarification with `question="Announcement"`, `is_contest_public=True`, already answered. A JUDGE may publish only while the contest is running; a contest ADMIN and the contest's chief judge may publish at any point in the contest lifecycle (before the start and after the end included). The form is rendered from the `can_create_announcement` context flag and the same predicate is the authoritative guard in the service. On success flashes and redirects to `/c/{slug}/clarifications/#{id}` (303). On error flashes and redirects (303). Implemented in `contest_clarifications_submit.py`. |
| `POST` | `/c/{slug}/clarifications/acquire` | j, a | Acquire a Valkey-backed clarification lock so a judge or admin may answer it. Form field: `clarification_id`. On success redirects to `GET /answer?id={id}` (303). If Valkey is unavailable, flashes a degraded-mode warning and redirects to the answer form anyway. Implemented in `contest_clarifications_judge.py`. |
| `GET` | `/c/{slug}/clarifications/answer` | j, a | Render the answer form for the clarification identified by `?id=`. The form is blind for every role: it shows the problem label, the question, and the answer field, never the asking team. When Valkey is available, flashes and redirects to `/#{id}` if the caller does not hold the lock. In degraded mode, the form remains available and shows a warning banner. Implemented in `contest_clarifications_judge.py`. |
| `POST` | `/c/{slug}/clarifications/answer` | j, a | Submit an answer or release a lock. Form fields: `clarification_id`, `answer` (max 1024 chars), `is_contest_public` (checkbox), `action` (`submit`/`release`). On success flashes and redirects to `/#{id}` (303). Validation errors re-render the form (422). Release is available only while the lock service is up. Implemented in `contest_clarifications_judge.py`. |
| `GET` | `/c/{slug}/clarifications/hide` | j | Render the hide confirmation page for the clarification identified by `?id=`. Implemented in `contest_clarifications_admin.py`. |
| `POST` | `/c/{slug}/clarifications/hide` | j | Confirm or cancel a hide. Form fields: `clarification_id`, `action` (`confirm`/`cancel`). On `confirm` calls `toggle_hidden_clarification()`, flashes, and redirects to `/#{id}` (303). On `cancel` redirects to `/#{id}` (303). Implemented in `contest_clarifications_admin.py`. |
| `GET` | `/c/{slug}/clarifications/togglehide` | ua, a | Render the toggle-hide confirmation page for the clarification identified by `?id=`. Shows full question, optional answer, and current hidden status. Implemented in `contest_clarifications_admin.py`. |
| `POST` | `/c/{slug}/clarifications/togglehide` | ua, a | Confirm or cancel a toggle-hide. Form fields: `clarification_id`, `action` (`confirm`/`cancel`). On `confirm` calls `toggle_hidden_clarification()`, flashes, and redirects to `/#{id}` (303). On `cancel` redirects to `/#{id}` (303). Implemented in `contest_clarifications_admin.py`. |
| `POST` | `/c/{slug}/clarifications/releaselock` | ua, a | Force-release another holder's acquisition lock on an unanswered clarification. Form field: `clarification_id`. Uses JS confirm dialog for inline confirmation. Calls `release_clarification()` (admin unconditional path). Flashes and redirects to `/#{id}` (303) on success or error. Implemented in `contest_clarifications_admin.py`. |

---

## Contest Tasks (`web/routes/contest_tasks*.py`)

Routes require a valid contest-scoped JWT or UberAdmin JWT. The USER role and non-chief JUDGEs have no access. STAFF, TEAM and the chief judge may only access after the contest starts (`is_running` or `is_past`); ADMIN and UBERADMIN always have access. Visibility is role-scoped:
- `t` (team): own tasks only; SOS button shown while contest is running; PRINT creation appears only when `contest.allow_print_requests` is enabled; auto-refresh via HTMX every 60 s
- `s` (staff): all tasks; acquire/finish/release workflow; auto-refresh via HTMX every 60 s
- `cj` (chief judge): same task-handling workflow as staff — the chief judge is the only JUDGE who may open this page
- `a` (admin): all tasks with elapsed time column; may acquire and finish tasks like staff, and force-release a lock held by someone else
- `ua` (uberadmin): all tasks with elapsed time column; force-release only — a finished task is attributed through `tasks.staff_id`, a foreign key into `users`, which has no uberadmin row

Task types: `BALLOON` (auto-created by judgment module), `FIRST_BALLOON` (first accepted solve for a problem, rendered with a golden glow), `PRINT` (team uploads source for printing), `SOS` (help request).

Task status is derived: queued (no staff, not finished), processing (staff assigned, not finished), finished.

Route ownership is split across `contest_tasks.py`, `contest_tasks_staff.py`,
and `contest_tasks_source.py`.

| Method | URL | Allowed | Description |
|--------|-----|---------|-------------|
| `GET` | `/c/{slug}/tasks/` | ua, a, cj, s, t | Full page. TEAM sees SOS button and, when contest is running and `contest.allow_print_requests` is true, the Print modal/button, plus their own task list. STAFF sees all tasks with acquire buttons and a task detail modal; PRINT details offer the formatted printout and the raw source download as separate actions. ADMIN/UA sees all tasks with elapsed column and force-release buttons. Flash messages shown via `get_flashed_messages`. Loads `htmx.min.js`, `refresh-timer.js`, and `tasks.js`. |
| `GET` | `/c/{slug}/tasks/list` | ua, a, cj, s, t | HTMX partial. Returns `#tasks-list-wrapper` div with the current task table. Polled every 60 s by TEAM and STAFF browsers. |
| `POST` | `/c/{slug}/tasks/sos` | t | Create an SOS help-request task. No form fields. Requires contest to be running. Two per-team throttles apply, both counted in PostgreSQL: at most `NOCA_WEB_TEAM_TASK_RATE_LIMIT_MAX_OPEN_SOS` unfinished SOS tasks at once (released when staff finishes one) and at most `NOCA_WEB_TEAM_TASK_RATE_LIMIT_MAX_SOS_REQUESTS` per `…_WINDOW_SECONDS`. Flashes success or error; either refusal is a danger flash, the windowed one naming the next allowed time. Redirects to `GET /tasks/` (303). |
| `POST` | `/c/{slug}/tasks/print` | t | Create a PRINT task. Form fields: `problem_id`, `source_file` (multipart upload). Validates: contest running, `contest.allow_print_requests=True`, non-empty problem selection, non-empty file, file within `contest.max_problem_file_size_bytes` (0 = unlimited). Blocks duplicate PRINT tasks (same team, problem, source hash while unfinished). Throttled to `NOCA_WEB_TEAM_TASK_RATE_LIMIT_MAX_PRINT_REQUESTS` per team per `…_WINDOW_SECONDS`. The throttle is the **last** check: every validation and the duplicate check run first, so their more specific messages are never masked — detecting a duplicate requires the source hash, and the upload is in any case already received and spooled by the multipart parser before the handler runs. Flashes and redirects to `GET /tasks/` (303). |
| `POST` | `/c/{slug}/tasks/{task_id}/acquire` | s, a, cj | Acquire a Valkey-backed task lock. On success redirects to `/tasks/?open={task_id}` (303) so `tasks.js` auto-opens the detail modal. If Valkey is unavailable, flashes a degraded-mode warning and opens the task directly. Implemented in `contest_tasks_staff.py`. |
| `POST` | `/c/{slug}/tasks/{task_id}/finish` | s, a, cj | Finish a task. When Valkey is available, the handler must hold the task lock; in degraded mode the finish action remains available and the UI shows a warning banner. Redirects to `/tasks/` (303). Implemented in `contest_tasks_staff.py`. |
| `POST` | `/c/{slug}/tasks/{task_id}/release` | s, cj (own lock), a, ua | Release a task lock without finishing. STAFF and the chief judge may release only their own lock; ADMIN and UBERADMIN may release any lock. Release is available only while the lock service is up. Redirects to `/tasks/` (303). Implemented in `contest_tasks_staff.py`. |
| `GET` | `/c/{slug}/tasks/{task_id}/source` | s, cj (lock holder), a, ua | Download the source code for a PRINT task as a plain-text file. STAFF and the chief judge must hold the task lock when Valkey is available; in degraded mode the download remains available so staff can continue working. Returns 303 to `/tasks/` on error. Implemented in `contest_tasks_source.py`. |
| `GET` | `/c/{slug}/tasks/{task_id}/printout` | s, cj (lock holder), a, ua | Render a printer-friendly PRINT-task listing. A first-page delivery banner identifies the team, site, and physical location; metadata names the contest, problem, request times, handler, task ID, source size, and short source hash. The source is escaped plain text with line numbers. Handler identity comes from `tasks.staff_id` after finish or the active Valkey lock holder while unfinished; degraded mode states that the handler is unavailable. Uses the same authorization and lock policy as the raw source route. Returns 303 to `/tasks/` on error. Implemented in `contest_tasks_source.py`. |

---

## Contest Runs (`web/routes/contest_runs*.py`)

Routes require a valid contest-scoped JWT or UberAdmin JWT. TEAM role is enforced at route level for submission. STAFF and USER have no access. JUDGE and TEAM may only access after the contest starts (`is_running` or `is_past`); ADMIN and UBERADMIN always have access. Visibility is role-scoped:
- `t`: own submissions only; submission form shown while contest is running; auto-refresh via HTMX every 60 s
- `ua`/`a`/`j`: all contest submissions; no submission form

Route ownership is split across `contest_runs.py`, `contest_runs_review.py`,
and `contest_runs_events.py`.

| Method | URL | Allowed | Description |
|--------|-----|---------|-------------|
| `GET` | `/c/{slug}/runs/` | ua, a, j, t | Full page. TEAM sees a submission form while the contest is running, plus their own submissions. Non-TEAM sees all contest submissions. `filter_problem_id`, `filter_autojudge`, `filter_final_verdict`, and privileged-only `filter_team_id` narrow the SQL query. `sort_by` orders Time or Problem in SQL and defaults to newest first. The optional `queued_submission` query parameter identifies the success flash for a new submission; the page dismisses that flash after its final-verdict event or after five seconds. Loads `htmx.min.js`, `refresh-timer.js`, and `runs-sse.js` while live updates are available. |
| `GET` | `/c/{slug}/runs/list` | ua, a, j, t | HTMX partial. Applies the same server-side filters and ordering as the full page, then returns `#runs-list-wrapper`. Filter changes trigger this endpoint, and live refreshes retain the active query parameters. |
| `GET` | `/c/{slug}/runs/language-info` | ua, a, j, t | HTMX partial. Returns compile and run command info for `?language_id=`. Used by the submission form language dropdown. Returns empty fragment for unknown/empty language_id. |
| `GET` | `/c/{slug}/runs/events` | ua, a, j, t | SSE stream (`text/event-stream`). **Concurrent-connection capped** (`web:sse`, `NOCA_WEB_SSE_*`): the client IP may hold `NOCA_WEB_SSE_MAX_PER_IP` open Web streams and the actor `NOCA_WEB_SSE_MAX_PER_USER` across IPs; the next one gets `429` + `Retry-After`. The request session is closed before streaming. Subscribes to verdict events and emits contest-scoped payloads plus heartbeat pings. Live-visibility payloads include the submission ID so the page can associate a verdict with the queued-submission flash. Frozen-scoreboard payloads remain fully redacted. Clients trigger an HTMX refresh of the runs list on each message. Also consumed by `/c/{slug}/problems/` for a TEAM viewer of a running contest (`problems-sse.js`): every message there triggers an HTMX refresh of the problem card grid, and a card whose `data-viewer-status` newly reads `solved` after that refresh fires a confetti celebration — the payload itself is never trusted for this, only the server-rendered status after the swap, so a scoreboard freeze cannot leak or fake a solve. Implemented in `contest_runs_events.py`. |
| `POST` | `/c/{slug}/runs/submit` | t | Submit a solution. Form fields: `problem_id`, `language_id`, `source_file` (multipart). Validates: contest running, non-empty selections, problem belongs to contest, language is active, non-empty file, file size within `contest.max_problem_file_size_bytes` (0 = unlimited). Computes SHA-256 for duplicate detection. On duplicate flashes "Duplicated submission" (danger). On success creates `Submission` + `SubmissionJudgment` (QUEUED), commits, and asks Valkey runtime to enqueue `JudgeJob`. If Valkey is temporarily unavailable, enqueue is buffered in-memory and replayed after reconnect. Redirects to `GET /runs?queued_submission={submission_id}` (303) with a success flash. Implemented in `contest_runs_review.py`. |
| `POST` | `/c/{slug}/runs/{submission_id}/override` | chief judge, admin | Override the effective final verdict of a DONE submission. Form fields: `new_verdict`, `reason` (10-1000 chars). On success creates a `VerdictOverride`, commits, publishes a `VerdictEvent` to `judge:results`, flashes success, and redirects to the submission review page. Implemented in `contest_runs_review.py`. |
| `GET` | `/c/{slug}/runs/{submission_id}/judging-history` | ua, a, j, s | Returns JSON `JudgingHistoryResponse` for one submission. Includes judgment creation and verdict-change audit rows plus explicit override rows; excludes status-only transitions. TEAM users are forbidden. Implemented in `contest_runs_events.py`. |

---

## Contest Solution Tests (`web/routes/contest_solution_tests.py`)

Non-scoring runs of a candidate solution against a problem's real compiler, sandbox,
limits, test cases, and custom validator. Runs live in their own tables
(`solution_test_runs`, `solution_test_case_results`), never in `submissions`, so they
cannot reach standings, balloons, Runs, reports, the live feed, or exports.

Routes require a valid contest-scoped or UberAdmin JWT plus role `ua`, `a`, or `j`.
`get_contest_by_slug` already 404s on an inactive contest; access is otherwise permitted
before, during, and after the contest. Visibility is role-scoped:
- `j`: only runs they triggered themselves
- `ua`/`a`: every run in the contest

A judge opening another actor's `run_id` gets **404, not a forbidden-feature
redirect**, so a run's existence does not leak. JUDGE, ADMIN, and UBERADMIN are
trusted with this contest's test data, so diagnostics are shown without
redaction.

| Method | URL | Allowed | Description |
|--------|-----|---------|-------------|
| `GET` | `/c/{slug}/solution-tests/` | ua, a, j | Full page: upload form plus paginated run history (50/page). `problem_id` filters the history and preselects the form's problem; `page` paginates. |
| `POST` | `/c/{slug}/solution-tests/submit` | ua, a, j | Queue one run. Form fields: `problem_id`, `language_id`, `source_file` (multipart). Validates in order: problem belongs to contest → language available for contest → non-empty file → size within `contest.max_problem_file_size_bytes` (0 = unlimited) → no NUL bytes → judgeability (the shared contract read from the problem's stored strategy: an interactive problem needs an active `VALID` validator and a secret case, a standard one needs cases that all carry an expected output). Enforces an independent per-actor rate limit (see `rate_limit_service.py`). On success commits and enqueues a `SolutionTestJob` with `priority=contest.is_running`. Publishes **no** `SubmissionEvent` and **no** `VerdictEvent`. Redirects (303) to the run detail page. |
| `GET` | `/c/{slug}/solution-tests/{run_id}` | ua, a, j | Run detail: status panel, per-case results (with interactive transcripts), and the submitted source. |
| `GET` | `/c/{slug}/solution-tests/{run_id}/status` | ua, a, j | HTMX partial `#solution-test-status`. Self-terminating poll: the `hx-*` attributes are emitted only while the run is non-terminal, so the swap rendering the terminal state stops the poll. |

---

## Per-actor read ceiling

Every `GET` route on the polled and contest-read routers -- `contest_runs`,
`contest_tasks`, `contest_clarifications`, `contest_admin`,
`contest_solution_tests`, `contest_admin_problem_validator`, `contest_problems`
and `contest_score`, plus `GET /c/{slug}/runs/{id}/judging-history` -- carries a
**loose per-actor ceiling** (`web/services/user_read_rate_limit.py`, bucket
`web:user-read`, knobs `NOCA_WEB_USER_READ_RATE_LIMIT_*`, default 300 per actor
per minute). It is attached to the *router*, not to individual routes, so a
`GET` partial added to one of them inherits it. The dependency ignores
state-changing methods, so polling cannot consume the capacity needed for a
form submission or administrative action.

Each of these calls costs a bounded amount and legitimate clients poll them
every 5-60 seconds, so the budget sits roughly an order of magnitude above the
fastest honest poller: it stops one actor multiplying that cost during a live
contest and must never refuse a partial to a team watching the scoreboard. The
key is the actor id from the already-validated auth cookie, falling back to the
client IP for a request with no session, so the counter follows an account
across addresses instead of punishing a shared one. Over budget the answer is
`429` with `Retry-After`, and `shared/static/js/htmx-poll-backoff.js` parks the
page's *timer-driven* partials for that long -- user-initiated requests are
never cancelled.

The two SSE streams are deliberately outside it; they are bounded by open
connections instead (below).

## SSE connection caps

Web's two event streams -- `GET /c/{slug}/runs/events` and
`GET /c/{slug}/live/events` -- are long-lived, so they are bounded by open
*connections* rather than by requests: the shared
`shared/services/sse_connection_limit.py` lease (bucket `web:sse`, knobs
`NOCA_WEB_SSE_*`) caps how many a client IP holds at once across both routes,
and how many an authenticated actor holds across IPs on `/runs/events`. A
refused connection is `429` with `Retry-After: 5`; slots are released on
disconnect. The lease fails open on a Valkey outage. See
`docs/SHARED_SERVICES.md`.

## Contest Live Feed (`web/routes/contest_live_feed.py`)

Public, no-login pages for any active contest (resolved by `login_slug`). The feed shows
the last 20 finalized team submissions, newest first, and refreshes in real time. The SSE
channel only signals "changed"; the blackout-aware JSON snapshot is the sole data source.
While the scoreboard is frozen, post-freeze rows still appear but the team name and verdict
are anonymized (`build_contest_live_feed_snapshot` in `web/services/live_feed_service.py`).

| Method | URL | Allowed | Description |
|--------|-----|---------|-------------|
| `GET` | `/c/{slug}/live` | public | Full page. Renders the live feed table shell and loads `live-feed.js`. Shows a "not started yet" state when the contest is upcoming. |
| `GET` | `/c/{slug}/live/feed.json` | public | **Per-IP request limit** (`web:live-feed`, `NOCA_WEB_PUBLIC_RATE_LIMIT_LIVE_FEED_*`, default 120 per minute; `429` + `Retry-After`, checked before the contest lookup). Responses carry `Cache-Control: public, max-age=5` — the snapshot is identical for every viewer, so a shared cache may reuse it during a debounced refetch burst. JSON snapshot `{"live_feed_limit": int, "has_more": bool, "submissions": [...]}` of the last 20 finalized TEAM submissions, blackout-aware (verdict + team masked server-side for post-freeze rows); `has_more` is `true` when older finalized submissions exist beyond the cap. |
| `GET` | `/c/{slug}/live/events` | public | SSE stream (`text/event-stream`). **Concurrent-connection capped** per client IP (`web:sse`, shared with `/runs/events`; `429` + `Retry-After` over `NOCA_WEB_SSE_MAX_PER_IP`). The request session is closed before streaming, so the open connection holds no PostgreSQL connection. Subscribes to verdict events via the shared `iter_refresh_events`, emits a generic `refresh` ping for this contest plus heartbeat pings; carries no verdict data. |

---

## Contest Administration (`web/routes/contest_admin*.py`)

Routes require either a valid UberAdmin JWT **or** a contest JWT with role `a`
(admin). Uberadmins are authenticated via their global token and bypass
contest-scoped auth. Non-admin contest roles are redirected to their contest
dashboard with a danger alert. Invalid or missing auth is currently redirected
to `/contests`.

Metadata edit routes currently allow any authenticated contest admin or UberAdmin. When the contest is running or finished, only timing fields remain editable.

Route ownership is split across `contest_admin.py`,
`contest_admin_metadata.py`, `contest_admin_reports.py`, and
`contest_admin_export.py`. Shared counter and password helpers live in
`contest_admin_helpers.py`.

| Method | URL | Allowed | Description |
|--------|-----|---------|-------------|
| `GET` | `/c/{slug}/admin` | ua, a | Renders the contest administration dashboard with links to metadata, users, problems, and export/import sub-pages. Not accessible by uberadmins via navigation (they reach sub-pages directly from `/uberadmin`). Implemented in `contest_admin.py`. |
| `GET` | `/c/{slug}/admin/counters` | ua, a | Renders contest-scoped operational counters (runs, tasks, clarifications, announcements, and Valkey queue sizes). Implemented in `contest_admin.py`. |
| `POST` | `/c/{slug}/admin/chief-judge` | ua, a | Assign or remove the contest chief judge. Form fields: `action=assign|remove`, optional `judge_id`. Removal is blocked once that chief judge has executed any verdict override in the contest. Implemented in `contest_admin.py`. |
| `GET` | `/c/{slug}/admin/metadata` | ua, a | Renders the metadata edit form pre-filled with current contest values, including the editable contest site list with per-site user counts and the allowed-language selector while the contest is still upcoming. Fields are partially disabled when the contest is running or past; `allow_print_requests` remains editable as an explicit exception. Implemented in `contest_admin_metadata.py`. |
| `POST` | `/c/{slug}/admin/metadata` | ua, a | Validates and saves contest metadata changes, the submitted site list, and the allowed contest-language set while the contest is still upcoming. The contest website URL is optional. Site names are case-insensitively unique, at least one site must remain, and a site cannot be removed while any TEAM or STAFF user is assigned to it. Removing a language also deletes stale per-language problem overrides for that contest; newly added languages rely on fallback limits until explicitly configured. When locked (running/past), timing fields stay editable and `allow_print_requests` also stays editable as an explicit exception, but allowed languages no longer change. Re-renders with success flash or validation errors. Implemented in `contest_admin_metadata.py`. |
| `POST` | `/c/{slug}/admin/start-now` | ua, a | Requires the authenticated admin/uberadmin password confirmation, then sets `contest.start_time` to the current UTC time, starting the contest immediately. No-op if the contest is already running or past. Refuses to start when the contest has no sites. Redirects to `/c/{slug}` (303). Implemented in `contest_admin.py`. **Password confirmation is throttled** (shared `password-confirm` budget; wrong passwords are audited `auth_failure` events, past the cap the shared `429` page is returned before the password is checked). |
| `POST` | `/c/{slug}/admin/end-now` | ua, a | Requires the authenticated admin/uberadmin password confirmation, then ends a running contest by shortening `duration_minutes` to the smallest whole-minute value that does not end in the past. Dependent timing fields (`stop_updating_scoreboard`, `stop_answers_after`, and timeout values) are clamped as needed to preserve contest timing invariants. No-op if the contest is not running. Redirects to `/c/{slug}` (303). Implemented in `contest_admin.py`. **Password confirmation is throttled** (shared `password-confirm` budget; wrong passwords are audited `auth_failure` events, past the cap the shared `429` page is returned before the password is checked). |
| `POST` | `/c/{slug}/admin/release-scoreboard` | ua, a | Releases the final scoreboard for an ended contest. Sets `release_scoreboard_after_end=True`, computes and permanently caches the final standings (all frozen/pending results revealed, no TTL), then commits. Flashes danger if contest has not ended; flashes warning if already released. Redirects to admin dashboard (303). Implemented in `contest_admin.py`. |
| `POST` | `/c/{slug}/admin/release-problem-set` | ua, a | Publishes or withdraws the public problem-set download (`release=yes|no`, a strict string so a resubmitted form is idempotent). Sets `release_problem_set_after_end`, records an admin-audit event (`contest_problem_set_release` at warning severity / `contest_problem_set_revoke`), and commits. Publishing requires the contest to have ended -- arming a future release is done on the metadata form; withdrawing is always allowed, serving as Revoke after the end and Cancel on an armed contest. A withdrawal also discards the cached archive. Redirects to the admin dashboard (303). Implemented in `contest_admin.py`. |
| `GET` | `/c/{slug}/admin/users` | ua, a | Renders the user management page, listing all enrolled members grouped by role (Admin, Judge, Staff, Team, User). Supports remove actions (disabled while contest is running) and shows an `Export Users` action that downloads import-compatible JSON. Implemented in `contest_admin_reports.py`. |
| `GET` | `/c/{slug}/admin/import_export` | ua, a | Renders the Export/Import page with download actions for the Animeitor-compatible ZIP and the markdown contest timeline report. Implemented in `contest_admin_export.py`. |
| `GET` | `/c/{slug}/admin/export-animeitor` | ua, a | Downloads a ZIP file compatible with the `maratona-animeitor` consumer. Contains `contest`, `runs`, `time`, `version`, and `icpc` files in the legacy BOCA webcast format. Returns 303 redirect with flash error if the contest has no teams or no problems. Implemented in `contest_admin_export.py`. |
| `GET` | `/c/{slug}/admin/export-events` | ua, a | Downloads a markdown report containing a wrapped fixed-width text table of persisted contest events. Best-effort only: transient lock-only acquisitions are omitted because they are not historically stored. Implemented in `contest_admin_export.py`. |
| `GET` | `/c/{slug}/admin/users-per-site-report` | ua, a | Downloads a markdown report of contest users grouped by site. Sites are ordered A-Z; users within each site and role are ordered by username. Includes users with no site assigned, chief judge annotation, and contest header with rules summary. Implemented in `contest_admin_export.py`. |

---

## Contest Animator Administration (`web/routes/contest_admin_animator.py`)

Dedicated page (kept off the already large metadata page) for enabling the animator,
editing the contest-wide and all per-site medal bands in one form, and managing
operator credentials. Note that a reveal ceremony snapshots its cutoffs when it is
created, so a change here reaches an already-stored ceremony only after the operator
restarts it with **Start over** (`start-reveal` with `restart=true`). Same
authorization as the rest of contest administration: a valid UberAdmin JWT **or** a
contest JWT with role `a` (admin); other contest roles return to their contest
dashboard with a danger alert. Reuses the shared
`site_service` animator wrappers for the per-site cutoffs and
`contest_service.update_contest_global_medals` for the contest-wide ones; both are thin
wrappers over `shared.services.animator_access_service`. A freshly generated operator token
is shown **exactly once** in an HTMX partial, optionally emailed to the administrator,
and never persisted or flashed; only its digest is stored and no digest is ever
rendered.

| Method | URL | Allowed | Description |
|--------|-----|---------|-------------|
| `GET` | `/c/{slug}/admin/animator/` | ua, a | Renders the `animator_enabled` toggle. The bulk medal-cutoff table and digest-free operator-credential table appear only while Animator support is enabled. Before disabling, the page warns that every site-scoped and contest-global operator credential will be permanently revoked and asks for confirmation. Implemented in `contest_admin_animator.py`. |
| `POST` | `/c/{slug}/admin/animator/settings` | ua, a | Updates `contests.animator_enabled` from the `animator_enabled` switch (`yes`/absent). Disabling also revokes every operator credential owned by the contest in the same transaction. The toggle and revoked credential count are audited via `admin_action`. Redirects to the settings page (303). Implemented in `contest_admin_animator.py`. |
| `POST` | `/c/{slug}/admin/animator/medals` | ua, a | Validates the contest-wide `global_gold_cutoff` / `global_silver_cutoff` / `global_bronze_cutoff` fields **and** the dynamic `gold_cutoff_{site_id}`, `silver_cutoff_{site_id}`, `bronze_cutoff_{site_id}` fields for every site before updating any row. The global triple is all-or-nothing: all three blank (or whitespace) clears it and disables global medals, a partially filled triple is rejected, and a form carrying none of the three fields leaves the stored values alone rather than clearing them. Invalid input flashes the scope-specific error and changes nothing; success commits the global row and all site rows in one transaction. Redirects (303). Implemented in `contest_admin_animator.py`. |
| `POST` | `/c/{slug}/admin/animator/secrets` | ua, a | Creates a site-scoped or global operator credential from the `scope` and `label` fields and records the action in the shared admin audit log in the same transaction. Returns the `admin/animator_operators.html` HTMX partial with the plaintext token shown once and email-delivery feedback. Invalid input returns the same swappable partial with an inline error and HTTP 200. Implemented in `contest_admin_animator.py`. The email goes through the shared `EmailService` (async, off the event loop): it is handed to the `noca-mailer` worker -- the only process that sends mail -- and the result reads *queued* rather than *sent*; it is charged to the acting admin's email budget (`NOCA_EMAIL_BUDGET_ADMIN_MAX`), and a spent budget is reported as a failed delivery naming the wait. |
| `POST` | `/c/{slug}/admin/animator/secrets/{secret_id}/revoke` | ua, a | Revokes a site or global credential owned by the contest, records the action in the shared admin audit log in the same transaction, and returns the updated HTMX operators partial. A missing or foreign `secret_id` changes nothing and returns the partial with an inline error and HTTP 200. Implemented in `contest_admin_animator.py`. |

---

## Contest Submissions (`web/routes/contest_submissions*.py`)

Review page routes for a single submission. STAFF is intentionally excluded.

Route ownership is split across `contest_submissions.py`,
`contest_submissions_review.py`, and `contest_submissions_files.py`. Shared UI
helpers live in `contest_submissions_helpers.py`.

| Method | URL | Allowed | Description |
|--------|-----|---------|-------------|
| `GET` | `/c/{slug}/submissions/download-all` | t | **Team only.** Downloads a ZIP archive containing all finalized submissions made by the current team during the contest. Only available after the contest has ended and the scoreboard has been released. Redirects to the contest dashboard with a danger alert if the contest is not past or the scoreboard is not released. Implemented in `contest_submissions.py`. |
| `GET` | `/c/{slug}/submissions/{submission_id}/review` | ua, a, j | Unified submission review page. Left column: source code, compile log, judging history, per-test-case results (ua/a/j only). Right column: verdict confirmation status panel; confirmation form for judges and admins (with a decisive-confirmation modal for the chief judge and admins) when the active judgment is `DONE` and the contest is not `autojudge_only`; override form for the chief judge and admins once a final verdict exists; rejudge card below the confirmation panel for the chief judge, admins, and uberadmins. See [Permission Model](#permission-model). Implemented in `contest_submissions.py`. |
| `POST` | `/c/{slug}/submissions/{submission_id}/acquire-review` | j, a | **Judges and admins.** Acquires a Valkey-backed review lock for the submission, allowing the holder to submit a verdict confirmation. Validates that the autojudge has finished (`DONE`), the caller hasn't already confirmed, and nobody else holds the lock. If Valkey is unavailable, flashes a degraded-mode warning and leaves confirmation available from the review page without lock controls. Implemented in `contest_submissions_review.py`. |
| `POST` | `/c/{slug}/submissions/{submission_id}/release-review` | j (own), a, ua | Releases a review lock without submitting a confirmation. Judges may release only their own lock; ADMIN and UBERADMIN may release any lock. Release is available only while the lock service is up. Implemented in `contest_submissions_review.py`. |
| `POST` | `/c/{slug}/submissions/{submission_id}/confirm` | j, a | Submits a human confirmation for the active judgment. A confirmation by the chief judge **or a contest admin** is decisive and sets the final verdict on its own; any other judge's confirmation needs a second, agreeing one. A judgment carries at most one decisive confirmation, so a second one is rejected. Returns 404 for `autojudge_only` contests. When Valkey is available, the caller must hold the review lock; in degraded mode confirmation remains available and the UI warns that coordination locks are off. If the confirmation produces a final verdict, publishes a `VerdictEvent` and invalidates scoreboard cache before redirecting back to the review page. Implemented in `contest_submissions_review.py`. |
| `POST` | `/c/{slug}/submissions/{submission_id}/rejudge` | chief judge, admin, uberadmin | Supersedes the current active judgment and creates a new `QUEUED` judgment for the same submission. Only available when the submission has a final verdict. Enqueues the new judgment with `is_rejudge=True` and invalidates the scoreboard cache before redirecting back to the review page. Implemented in `contest_submissions_review.py`. |
| `GET` | `/c/{slug}/submissions/{submission_id}/source` | ua, a, j, t (own only) | Downloads the submitted source code as a plain-text file named after the submission language's source filename. Teams may download only their own submission's source. Implemented in `contest_submissions_files.py`. |
| `GET` | `/c/{slug}/submissions/{submission_id}/test-cases/{test_case_id}/download?file=input|expected_output|team_output` | ua, a, j | Downloads a plain-text file for a single test case result. `input` and `expected_output` are read from the filesystem; `team_output` is the stored stdout excerpt. Implemented in `contest_submissions_files.py`. |
| `GET` | `/c/{slug}/submissions/{submission_id}/test-cases/{test_case_id}/detail` | ua, a, j | Renders a 3-column side-by-side page showing input, expected output, and team output for a single test case result. Back button returns to the submission review page. Implemented in `contest_submissions_files.py`. |

---

## Contest Problem Management

Routes require either a valid UberAdmin JWT **or** a contest JWT with role `a` (admin).
Split across five files; shared helpers in
`web/routes/contest_admin_problem_helpers.py` and
`web/routes/contest_admin_problem_limits_helpers.py`.

### Core (`web/routes/contest_admin_problem.py`)

| Method | URL | Description |
|--------|-----|-------------|
| `GET` | `/c/{slug}/admin/problems` | Browse contest problems. Lists all problems with labels, balloons, test case counts, reorder buttons (HTMX), edit/export/remove actions. |
| `GET` | `/c/{slug}/admin/problems/new` | Render the validation-strategy chooser (`new_problem_choose`, in `contest_admin_problem_new.py`). Offers Standard, Interactive, a disabled **Output checker** card, and the existing import flow. The strategy is stored on the problem and is immutable afterwards, so this is the only place it is chosen. |
| `GET` | `/c/{slug}/admin/problems/new/{validator_type}` | Render the creation editor for one strategy (basic info, fallback limits, statement, categories, per-language limits). It collects the problem **definition** only: test cases, the custom validator and sample interactions are authored on the judgment-data pages afterwards. Fallback limits always judge with 1 repetition. `{validator_type}` is taken as a string and resolved in the handler: `standard`/`interactive` render, `checker` redirects (303) to the chooser with an explanatory flash, and anything else is **404** (not 422, which the framework would render as neutral JSON). |
| `POST` | `/c/{slug}/admin/problems/new/{validator_type}` | Create a new problem under the strategy named by the route parameter, which is the sole authority: a `validator_type` field in the request body is never read, so form tampering cannot select or change one. Requires title, `time_limit_ms`, `memory_limit_kb`, `pids_limit`, and a statement; the new problem starts with no test cases and judgeability is enforced at the execution gates. Validator and test-case fields in the body are never read. Collects all errors before returning the HTML editor with 422, retained values, and the pane/field owning the first error; on success redirects to the new problem's judgment-data pages -- the validator page for an interactive problem, the test-cases page otherwise. |
| `POST` | `/c/{slug}/admin/problems/{problem_id}/move` | HTMX/drag endpoint. Moves a problem to `?new_ordinal=N`; still accepts legacy adjacent moves via `?direction=up\|down`. Returns `problems_list_table.html` partial. |
| `POST` | `/c/{slug}/admin/problems/{problem_id}/remove` | Remove a problem. Only allowed when `contest.upcoming and contest.active`. Redirects with reason if blocked. |

### Edit (`web/routes/contest_admin_problem_edit.py`)

| Method | URL | Description |
|--------|-----|-------------|
| `GET` | `/c/{slug}/admin/problems/{problem_id}/edit` | Tabbed definition form. Panes: **Metadata** (title, balloon colour, author, notes, categories), **Statement** (PDF or Markdown plus the illustration), and **Limits** (failover limits, profiling actions, per-language limits and repetitions). Judgment data uses separate pages. Every pane stays mounted and every control binds to one detached `#edit-form`, so switching tabs cannot lose input. When the contest state forbids editing, the Metadata pane renders the balloon colour as a single read-only balloon of the problem's current colour instead of the full picker. Optional `?tab=` selects the opening pane; an unknown, retired (`content`), or strategy-inappropriate value falls back to Metadata rather than erroring. |
| `POST` | `/c/{slug}/admin/problems/{problem_id}/edit` | Save the problem **definition** in one transaction: the scalar fields, the statement, the illustration and the categories. Test cases, the custom validator and sample interactions are not part of it -- they are authored on the judgment-data pages, and a field naming one is never read. The statement file is staged and swapped in as part of the commit, so a rejected save changes neither rows nor files. Validation failures return the HTML editor with 422, retain submitted values, open the pane that owns the first error, and associate scalar messages with the exact field. While the contest is running, only the Limits pane is accepted; successful running-contest limit saves may redirect to an affected-submissions review batch. |

### Judgment data (`web/routes/contest_admin_problem_judgment_tc.py`, `..._judgment_pages.py`)

Test cases, the custom validator and the sample interactions are edited on their
own **pages**, reached from the problem list's *Judgment data* action, because a
problem can hold many large test cases and carrying them inside the form that
edits its statement is what made that form unwieldy. Every action below applies
immediately through the staged swap; only the rows an author types inline wait for
that page's own Save, since typed text is the only state the browser holds that the
server has not seen. A standard problem is offered the test-cases page alone.

| Method | URL | Description |
|--------|-----|-------------|
| `GET` | `/c/{slug}/admin/problems/{problem_id}/judgment` | Open the page the author most likely wants: test cases, or the validator for an interactive problem that has none (it cannot judge anything until one compiles). |
| `GET` | `/c/{slug}/admin/problems/{problem_id}/judgment/test-cases` | The test-case list, the inline add rows, and the upload controls. |
| `POST` | `/c/{slug}/admin/problems/{problem_id}/judgment/test-cases` | Add the rows typed inline (`tc_in_N` / `tc_out_N` / `tc_explanation_N` / `tc_is_sample_N`). Rows above the 10 KB inline gate return 422 on this page with every submitted row retained, the exact field marked/focused, and a pointer to the uncapped ZIP path. |
| `POST` | `/c/{slug}/admin/problems/{problem_id}/judgment/test-cases/upload` | Append one or more cases from single-case ZIPs (`tc_add_zip`, repeatable). |
| `POST` | `/c/{slug}/admin/problems/{problem_id}/judgment/test-cases/bulk` | Replace every case from one ZIP (`tc_bulk_zip`). Staging is not seeded, since the plan claims nothing from the current files. |
| `POST` | `/c/{slug}/admin/problems/{problem_id}/judgment/test-cases/{tc_id}/toggle-sample` | Flip one case between sample and secret. Commits directly under the row lock: it changes no file, and staging a whole directory for a boolean would copy the problem's entire test data. Refused for an interactive problem, whose cases are always secret. |
| `POST` | `/c/{slug}/admin/problems/{problem_id}/judgment/test-cases/{tc_id}/replace` | Replace one case from a single-case ZIP (no size cap). An archive with no `explanation.txt` leaves the stored text alone. |
| `POST` | `/c/{slug}/admin/problems/{problem_id}/judgment/test-cases/{tc_id}/delete` | Delete one case; the survivors are renumbered inside staging and promoted with the commit. |
| `GET` | `/c/{slug}/admin/problems/{problem_id}/judgment/validator` | The custom-validator page (interactive only; a standard problem is redirected to its test cases). |
| `GET` | `/c/{slug}/admin/problems/{problem_id}/judgment/interactions` | The sample-interactions page (interactive only). |
| `POST` | `/c/{slug}/admin/problems/{problem_id}/judgment/interactions` | Add the transcripts typed inline, subject to the five-interaction cap. A malformed/capped submission returns 422 with every row retained and the exact transcript marked/focused. |
| `POST` | `/c/{slug}/admin/problems/{problem_id}/judgment/interactions/{si_id}/delete` | Delete one sample interaction. |

Every mutating route above refuses a stored `checker` strategy explicitly, and all
of them gate on `_is_edit_allowed` alone: a running contest may still change its
**limits** (see below), but not the data judging runs against.

### Limits (`web/routes/contest_admin_problem_limits.py`)

| Method | URL | Description |
|--------|-----|-------------|
| `POST` | `/c/{slug}/admin/problems/{problem_id}/profiling` | Queue an Auto-Limit profiling run for one language using an uploaded reference implementation and safety factor. |
| `GET` | `/c/{slug}/admin/problems/{problem_id}/profiling-status` | HTMX partial for the Auto-Limit and per-language limits panel. Polls every 2 seconds while profiling is active and returns the normal static panel again after completion. |
| `POST` | `/c/{slug}/admin/problems/{problem_id}/fallback-limits` | Copy separate `MAX()` values from per-language limits into the problem fallback limits. Repetition is not copied; fallback always uses 1 repetition. When this changes effective limits for running-contest submissions, redirects to a persisted affected-submissions review batch. |
| `GET` | `/c/{slug}/admin/problems/{problem_id}/limit-change-batches/{batch_id}` | Review one persisted affected-submissions batch created by a running-contest limit change. Shows submissions grouped by language with before/after limits and per-row rejudge state. |
| `POST` | `/c/{slug}/admin/problems/{problem_id}/limit-change-batches/{batch_id}/rejudge-all` | Queue rejudges for every still-pending submission in the saved batch. ADMIN and UBERADMIN only. Rows whose captured judgment is no longer active are marked stale instead of being requeued. Form: `password`. **Password reconfirmation is throttled** through the shared `password-confirm` budget (`web/services/password_confirm_throttle.py`, same caps as login, keyed by actor and IP, shared with the other reconfirming routes): a wrong password queues nothing and is recorded as an `auth_failure` security event; past the cap the actor gets the shared `429` page with `Retry-After` **before** the password is checked. Then a per-problem **cooldown** (`NOCA_WEB_REJUDGE_COOLDOWN_SECONDS`, `shared/services/rejudge_cooldown.py`) refuses a repeat with a danger flash naming the wait, before the batch is read. The batch rows are re-read `FOR UPDATE`, so two overlapping requests cannot both queue a row. An accepted action writes one warning-severity `admin_action` security event (`limit_batch_rejudge_all`, with the batch id and job count) in the same transaction as the `QUEUED` judgments; queuing nothing releases the cooldown. |
| `POST` | `/c/{slug}/admin/problems/{problem_id}/limit-change-batches/{batch_id}/languages/{language_id}/rejudge` | Queue rejudges only for the pending submissions of one language inside the saved batch. Uses the same stale-row guard, row locking, throttled `password` reconfirmation, and `admin_action` audit (`limit_batch_rejudge_language`) as the batch-wide action, but is **not** subject to the cooldown: the rows are consumed once, and rejudging language A then B in sequence is a normal workflow. |


### Import / Export / Serve (`web/routes/contest_admin_problem_io.py`)

| Method | URL | Description |
|--------|-----|-------------|
| `GET` | `/c/{slug}/admin/problems/import` | Render problem import form with ZIP format documentation. |
| `POST` | `/c/{slug}/admin/problems/import` | Import a problem from a ZIP archive (problem.json + statement.pdf/statement.md + test cases). Raises human-readable error on validation failure. Any `language_limits` entries for languages not currently allowed in the contest are skipped with a warning; allowed languages import normally. |
| `GET` | `/c/{slug}/admin/problems/{problem_id}/statement` | Serve problem statement PDF. `?download=1` for attachment. |
| `GET` | `/c/{slug}/admin/problems/{problem_id}/export` | Export problem as ZIP (Layout A: `in/001.in`, `out/001.out`, `statement.pdf`, `problem.json`). |

### Test Cases (`web/routes/contest_admin_problem_tc.py`; the two per-case pages live in `contest_admin_problem_tc_pages.py`)

What is left here concerns **one** test case, which gets a page of its own because
a case can be far too large to edit in a row. The list-level actions -- add,
upload, replace-all, delete, sample toggle -- live on the judgment-data test-cases
page above; the endpoints that used to duplicate them here are gone, as is the
standalone "new test case" page, since new cases are typed as rows on that page.

| Method | URL | Description |
|--------|-----|-------------|
| `GET` | `/c/{slug}/admin/problems/{problem_id}/test-cases/{tc_id}/edit` | Render the edit test case form (dedicated page, pre-filled with file content). The breadcrumb includes Judgment data, and Cancel returns to the test-case list anchored to `#tc-{tc_id}`. |
| `POST` | `/c/{slug}/admin/problems/{problem_id}/test-cases/{tc_id}/edit` | Update test case `is_sample` and overwrite file content. On success, redirects back to the judgment-data test-cases page anchored to `#tc-{tc_id}` so the edited row is scrolled into view and highlighted. |
| `POST` | `/c/{slug}/admin/problems/{problem_id}/test-cases/{tc_id}/move` | HTMX/drag endpoint. Moves a test case to `?new_ordinal=N`; still accepts legacy adjacent moves via `?direction=up\|down`. Returns `testcases_table.html` partial and preserves matching testcase files. |
| `GET` | `/c/{slug}/admin/problems/{problem_id}/test-cases/{tc_id}/download` | Download one test case as a single-case ZIP (`input.txt` / `output.txt`, optional `explanation.txt`); used for the offline edit round-trip of cases larger than 10 KB. |
| `POST` | `/c/{slug}/admin/problems/{problem_id}/test-cases/{tc_id}/replace` | Replace one test case from an uploaded single-case ZIP (no size cap). Respects the contest-state edit gate. |

---

## Contest User Management (`web/routes/contest_admin_user*.py`)

Routes require either a valid UberAdmin JWT **or** a contest JWT with role `a` (admin), including the credentials and batch-results download endpoints.

Route ownership is split across `contest_admin_user.py`,
`contest_admin_user_batch.py`, and `contest_admin_user_edit.py`.

| Method | URL | Allowed | Description |
|--------|-----|---------|-------------|
| `GET` | `/c/{slug}/admin/users/new` | ua, a | Renders the Add User form with empty fields. Includes a site selector populated from contest sites. Form is locked (read-only) when the contest is past. |
| `POST` | `/c/{slug}/admin/users/new` | ua, a | Validates and creates a new contest user (fields: username, fullname, role, password, optional email, optional site). `TEAM` and `STAFF` users must have a site assigned; other roles may omit it. On success, re-renders with credentials including optional email and site. `UBERADMIN` is forbidden. |
| `POST` | `/c/{slug}/admin/users/credentials.json` | ua, a | Returns the provided user credentials as a downloadable JSON file (`noca-credentials-{slug}-{username}.json`). Payload includes optional `email`, `site`, and `location`. |
| `POST` | `/c/{slug}/admin/users/credentials/email` | ua, a | Sends a credentials email for the just-created user when an email is available. Uses the configured web email provider and a plain-text NOCA credentials template. The email goes through the shared `EmailService` (async, off the event loop): it is handed to the `noca-mailer` worker -- the only process that sends mail -- and the result reads *queued* rather than *sent*; it is charged to the acting admin's email budget (`NOCA_EMAIL_BUDGET_ADMIN_MAX`), and a spent budget is reported as a failed delivery naming the wait. |
| `GET` | `/c/{slug}/admin/users/batch` | ua, a | Renders the batch user import form. Implemented in `contest_admin_user_batch.py`. |
| `POST` | `/c/{slug}/admin/users/batch` | ua, a | Accepts a `.csv` or `.json` file (max 5 MB) and bulk-creates/updates contest users. Import accepts optional `email` and `site`; missing sites are auto-created case-insensitively within the contest. `TEAM` and `STAFF` rows require `site`, while other roles may omit it and keep `site_id=None`. Re-renders with per-row results including email, site, and generated passwords. Implemented in `contest_admin_user_batch.py`. |
| `POST` | `/c/{slug}/admin/users/batch/results.json` | ua, a | Returns the provided batch results JSON as a downloadable file (`noca-batch-{slug}.json`). Implemented in `contest_admin_user_batch.py`. |
| `POST` | `/c/{slug}/admin/users/batch/credentials/email` | ua, a | Sends credential emails in batch for created/updated rows that include both `password` and `email`, and re-renders the results view with a delivery summary. Implemented in `contest_admin_user_batch.py`. Each message is charged to the admin's email budget (`NOCA_EMAIL_BUDGET_ADMIN_MAX`); at the first refusal the loop **stops** and every remaining row is marked `budget_exceeded` (counted as skipped, one failure message naming the wait) so the retry button offers them again later. Accepted rows read `queued` (the `noca-mailer` worker delivers them), and the summary and the `credential_email_batch_completed` security event carry `sent`, `queued`, `failed`, `skipped` and `budget_exceeded`. |
| `GET` | `/c/{slug}/admin/users/export.json` | ua, a | Downloads all contest users as import-compatible JSON (`noca-users-{slug}.json`). Passwords are omitted; each row includes `username`, `fullname`, `role`, and optional `email`, `site`, `location`. Implemented in `contest_admin_user_edit.py`. |
| `GET` | `/c/{slug}/admin/users/{user_id}/edit` | ua, a | Renders the Edit User form with identity fields plus photo and audio preview, upload, replacement, and removal controls. After the contest ends the profile fields (full name, site, location) and media mutations are disabled, but the email and password inputs stay editable. Returns 404 if the user is not found in this contest. Implemented in `contest_admin_user_edit.py`. |
| `POST` | `/c/{slug}/admin/users/{user_id}/edit` | ua, a | Validates and updates a user's fullname, optional email, site, location, and optionally password. Role is shown as read-only and cannot be changed after creation. `TEAM` and `STAFF` users must keep a site assigned. After the contest ends the request takes a credentials-only path that applies just the email and password (via `update_user_credentials`) and ignores any posted profile fields. Redirects back to the edit page on success. Implemented in `contest_admin_user_edit.py`. |
| `POST` | `/c/{slug}/admin/users/{user_id}/remove` | ua, a | Deletes a contest user. Redirects to the actor's dashboard with a danger alert if the contest is running or finished. Returns 404 if the user is not found. Redirects to `/c/{slug}/admin/users` on success. Implemented in `contest_admin_user_edit.py`. |

---

## Problem Categories (`web/routes/contest_admin_problem_categories.py`)

`ProblemCategory` is a global entity, not scoped to any contest. Routes require `ua` or `a` (autocomplete accepts any authenticated user). Delete is further restricted to `ua` only — contest admins see no delete button in the UI, and the POST returns a 403-equivalent redirect if attempted directly.

| Method | URL | Allowed | Description |
|--------|-----|---------|-------------|
| `GET` | `/categories/autocomplete` | any authenticated | Returns JSON `{categories: [{id, name}]}` filtered by `?q=`. Used by the problem edit form chip UI. |
| `GET` | `/categories` | ua, a | List all categories with rename and (ua-only) delete actions. |
| `GET` | `/categories/new` | ua, a | Render the create category form. |
| `POST` | `/categories/new` | ua, a | Create a category. Normalises name to lowercase. Raises error on duplicate or blank name. |
| `GET` | `/categories/{category_id}/edit` | ua, a | Render the rename form pre-filled with current name. Shows problem count. Delete button visible to ua only. |
| `POST` | `/categories/{category_id}/edit` | ua, a | Rename a category. Raises error on conflict. |
| `POST` | `/categories/{category_id}/delete` | **ua only** | Delete a category. Returns error redirect if in use or caller is not uberadmin. |

---

## Profile and user media

Current-user profile routes accept either a valid contest JWT or UberAdmin JWT
(`noca_access_token` cookie) and redirect unauthenticated requests to `/login`.
User media routes accept a contest-scoped viewer from the same contest or an
UberAdmin viewer. Contest users can manage their own media; contest admins and
UberAdmins can manage another user's media while that contest remains editable.
Identity and password routes live in `web/routes/profile.py`; photo and audio
routes live in `web/routes/user_media.py`. Every media route reuses the
request-scoped database session that authorizes the viewer, so a page loading
many avatars uses at most one database connection per in-flight request.

| Method | URL | Description |
|--------|-----|-------------|
| `GET` | `/profile` | Profile page with display name, email, password, photo, audio clip, and read-only site information for contest users. |
| `POST` | `/profile/fullname` | Update display name; flashes success and redirects to `/profile`. |
| `POST` | `/profile/email` | Update current-user email (`USER` email is optional; `UberAdmin` email cannot be blank); flashes success and redirects to `/profile`. |
| `POST` | `/profile/password` | Change password (requires current password); empty `new_password` = no change; flashes success and redirects to `/profile`. The current password is verified through the shared throttled `password-confirm` budget: wrong attempts are audited `auth_failure` events and past the cap the shared `429` page is returned before the password is checked. |
| `GET` | `/user/{user_id}/avatar` | User avatar (SVG fallback if no photo); cache: `public` (real photo) or `private` (fallback), plus a strong content `ETag` — a matching `If-None-Match` is answered with a bodyless `304` that keeps the same directive. Contest-scoped viewers are limited to their own contest; UberAdmins can view any user. |
| `GET` | `/user/{user_id}/photo` | User full photo; same cache policy, `ETag`/`304` behaviour and contest visibility rules as avatar. |
| `POST` | `/user/{user_id}/photo` | Upload a cropped photo. Self-service and authorized admin edits are supported. Enforces aspect ratio: 16:10 (team), 2:3 (others). Multipart streaming stops when aggregate file bytes exceed `NOCA_IMAGE_MAX_FILE_SIZE`; API clients receive 413, while browser forms redirect back with a warning. |
| `POST` | `/user/{user_id}/photo/remove` | Remove photo. Self-service flashes success and redirects to `/profile`; admin/UberAdmin removals from the edit screen redirect back to that user edit page. |
| `GET` | `/user/{user_id}/audio` | Serve a stored MP3, OGG, or WAV audio clip. Returns 404 when no clip exists and applies the same contest visibility rules as photo routes. |
| `POST` | `/user/{user_id}/audio` | Upload or replace an audio clip. Validates the file signature and stops multipart streaming at `NOCA_AUDIO_MAX_FILE_SIZE`. API clients receive HTTP 413; browser forms return to the same page with a warning. |
| `POST` | `/user/{user_id}/audio/remove` | Remove a stored audio clip and return to the self-service profile or admin edit page. |

Site note:
- contest users cannot self-edit their assigned site from `/profile`; the page displays it as administrator-managed read-only information.
## Custom validator routes (`web/routes/contest_admin_problem_validator.py`)

Uploading and removing a validator are actions of the judgment-data **validator
page**, which posts them to the two mutating endpoints below and applies them
immediately. The three read-only endpoints are how that page shows the current
revision.

- `POST /c/{slug}/admin/problems/{problem_id}/validator` stages and
  enqueues a candidate. Two independent conditions apply: the problem's **stored strategy**
  must be interactive (a standard problem has nowhere to put a validator and is
  refused), and no validator source may currently be configured — replacing one
  means removing it first. An interactive problem whose source was removed stays
  interactive and may upload a replacement, which is the documented recovery
  path.
- `GET /c/{slug}/admin/problems/{problem_id}/validator/status` renders the HTMX
  status partial.
- `GET /c/{slug}/admin/problems/{problem_id}/validator/source` downloads the
  current source (active revision, falling back to a staged candidate).
- `GET /c/{slug}/admin/problems/{problem_id}/validator/source/view` renders the
  current source with syntax highlighting and line numbers.
- `POST /c/{slug}/admin/problems/{problem_id}/validator/remove`
  removes active and candidate revisions. It requires a `keep_interactions` form field whose
  value is exactly `"true"` or `"false"` — the validator page posts it from a
  confirmation modal. `"true"` hides the problem's sample interactions (they
  resurface if a validator is added again); `"false"` deletes them permanently.
  The field is a strict string rather than a `bool` on purpose: FastAPI would
  coerce `1`, `on` and `yes` too, and the choice between hiding data and
  destroying it must not hinge on a spelling. Any other value, or none, is a 422.
  Removal never changes the problem's validation strategy: it stays interactive
  and simply stops being judgeable until a replacement validator compiles.

### Sample interactions

An interactive problem has no public test cases. Its public examples are
**sample interactions** — up to five author-written transcripts of the
conversation a correct program has with the validator, rendered on the problem
page with the same transcript UI that shows a submission's recorded attempts.

- `GET|POST /c/{slug}/admin/problems/{problem_id}/interactions/{si_id}/edit`
  view and save one interaction.
- `POST /c/{slug}/admin/problems/{problem_id}/interactions/{si_id}/move`
  reorders one interaction (`new_ordinal` query param) and returns the refreshed
  list partial for the drag-and-drop handler.

Deletion and reordering apply immediately, exactly like test cases. Only the
transcripts an author types inline (`si_transcript_N` / `si_explanation_N`
add-rows) wait, and they are applied by the interactions page's own Save.

A problem with a configured validator must have **zero public test cases and at
least one secret one**. Staging a validator demotes any existing public case to
secret, the sample toggle is refused while a validator is configured, and no edit
path may remove the last secret case. A brand-new *disabled* draft may still have
no test cases at all; that is only forced to be complete at enablement and at
submission time.

On `/c/{slug}/submissions/{submission_id}/review`, an interactive problem replaces
the test-case results table with the per-attempt contestant/validator stdout and
stderr excerpts (the effective-limits table is kept), followed by a link to
`GET /c/{slug}/submissions/{submission_id}/validator-source`
(`submission_validator_source_download`), which serves the active validator source
to uberadmins, admins, and judges — the same audience that may see test results.

---

## Permission Model

Who can do what, inside the contest module — and, just as importantly, where each kind of
answer lives. Three layers, and conflating them is what let the old tables drift:

- **Enforcement** is the only thing that grants or denies anything: the route gates,
  the per-domain `permissions.py` predicates, and the `before_flush` hook in
  `web/models/submission.py`. A permission change starts here.
- **The cells** — which actor may do what, in which contest state — live in
  [`web/access_matrix/`](../access_matrix/), as data. `tests/web/test_access_matrix.py`
  calls the real predicates and route gates and fails when a cell disagrees with them.
- **The narrative** — why the rules are shaped this way — is this section. It explains
  the actors, the uberadmin attribution boundary, verdict authority, and the deliberate
  redactions. It does not define cells.

The per-route `Allowed` columns above are endpoint documentation, hand-written and not
covered by that test. When a column disagrees with the code, fix the column.

### Actors

Two identity domains (`web/models/users.py`):

- **`UberAdmin`** — global, system-level actor. Lives in its own table, not in `users`.
- **`User`** — contest-scoped actor tied to one contest, with a role: `ADMIN`, `JUDGE`,
  `STAFF`, `TEAM`, or `USER`.

The **chief judge** is not a role. It is the single `JUDGE`-role user named by
`contests.chief_judge_id`. An admin can therefore never *be* the chief judge — which is why
admin authority is granted explicitly wherever chief-judge authority exists.

### The uberadmin attribution boundary

An uberadmin outranks an admin everywhere except when the database must **record who did
it**. Four columns attribute work to a person, and all four are foreign keys into `users`:
`tasks.staff_id` (who finished a task), `clarifications.judge_id` (who answered),
`human_submission_confirmations.judge_id` (who confirmed), and
`verdict_overrides.overridden_by` (who overrode).

Uberadmins have no `users` row, so they cannot appear in any of them. An uberadmin may
therefore **supervise** this work — force-release any lock, rejudge — but not **perform**
it. Admins are ordinary `users` rows and attribute cleanly. Every "uberadmin cannot" below
comes from this; changing it means a schema change, not a permission tweak.

### Capability matrix

**The matrix lives in [`web/access_matrix/capabilities.py`](../access_matrix/capabilities.py)** —
`CAPABILITY_GROUPS`, four groups (Clarifications, Tasks, Verdicts, Administration)
of `Capability` rows, each declaring a `CapabilityGrant` per actor. This document
points at it rather than restating it.

Each row names the predicate that decides it in `enforced_by`, and
`tests/web/test_access_matrix.py` calls that predicate for a real actor of every
role, in a running and a not-yet-started contest, asserting the answer matches
the declared cell. A row whose `enforced_by` is `None` is gated only by a route's
role tuple and is checked structurally; `test_every_capability_without_a_predicate_is_known`
pins that set, so the unverified list shrinks deliberately and never grows by
accident. Giving one of those rows a shared predicate is a genuine improvement.

`✓` = allowed. `—` = denied. `own` = only on a resource the actor owns or holds
the lock for. `n/a` = the capability does not apply to that actor. A cell may
carry a qualifier the symbol cannot express — `✓ (running only)` for asking a
clarification, and for a plain judge posting an announcement.

Two deliberate differences from the table this replaced:

- **`USER` has a column.** The old table omitted it. It is a selectable role on
  the Add User form, and an explicitly empty column answers "what can a USER do"
  where a missing one leaves the reader guessing.
- **A plain judge's announcement cell is qualified.** It was a bare `✓`, but
  `can_create_announcement` requires a running contest for a judge who is not the
  chief judge — the prose below already said so, and now the cell does too.

The rows that surprise people:

- **A plain judge sees no tasks at all.** The tasks page admits the `JUDGE` role only for
  the chief judge; every other judge is redirected to the contest dashboard with a
  danger alert, and `list_tasks` refuses them at the service layer too.
- **A plain judge cannot override a verdict.** Override authority is chief-judge-or-admin.
- **Contest state gates access separately.** Admins and uberadmins reach every page at any
  time. Staff and teams reach most participant pages, the chief judge reaches tasks, and
  judges reach runs only once the contest is running or past. Clarifications are the
  exception: authorized roles can view the list in every lifecycle state, but teams can
  request a clarification only while the contest is running. See the
  [Access Matrix](#access-matrix) above.

### Where each rule is enforced

Each domain owns one `permissions.py` that the service, the routes, and the templates all
read from. Add a capability there, not inline in a route.

| Domain | Module | Predicates |
|--------|--------|-----------|
| Shared chief authority | `web/services/chief_judge_permissions.py` | `is_chief_judge`, `has_chief_authority` |
| Tasks | `web/services/task_service/permissions.py` | `can_view_tasks`, `can_handle_tasks`, `can_force_release_tasks`, `is_chief_judge` |
| Clarifications | `web/services/clarification_service/permissions.py` | `can_request_clarification`, `can_answer_clarifications`, `can_force_release_clarifications`, `can_create_announcement` |
| Verdicts | `web/services/judging_service/permissions.py` | `can_confirm_verdict`, `confirmation_is_decisive`, `can_override_verdict`, `is_chief_judge` |

Three layers enforce them, and a capability is only real when all three agree:

1. **Routes** — `ensure_allowed_role(...)` narrows by role, then the predicate applies the
   contest-aware rule (chief judge, lock holder). A role that passes the first check and
   fails the second raises a 403 internally; the Web exception handler converts it to the
   role-aware dashboard redirect described above.
2. **Services** — re-check the same predicate rather than trusting the caller. `list_tasks`
   raises `ForbiddenTaskActionError` on its own, so a future caller cannot leak tasks to a
   plain judge by forgetting the route guard.
3. **ORM invariants** — the `before_flush` hook in `web/models/submission.py` independently
   refuses a confirmation from a non-`JUDGE`/`ADMIN` user, and a `VerdictOverride` from
   anyone but the chief judge or an admin. **Widening a verdict permission means editing
   that hook too**; otherwise authorization passes and the commit raises `ValueError`.

Templates gate the *buttons* from the same predicates, passed in as context flags
(`can_handle_tasks`, `can_answer_clarifications`, `can_acquire_review`, …). A button that
appears without the matching server-side rule is a bug, not a shortcut.

A fourth artifact **describes** those three: `web/access_matrix/`. It enforces
nothing — no route, service, or hook consults it — so widening a permission there
grants nobody anything. Change the three enforcing layers first, then update the
declaration; `tests/web/test_access_matrix.py` fails until the two agree, which is
the whole point of keeping the matrix as code rather than as a table in this file.

### Verdict authority

The final verdict is **derived**, never assigned directly (`_derive_final_verdict`,
`web/models/submission.py`):

- A **decisive** confirmation — from the chief judge *or* an admin, stored as
  `is_chief_confirmation=True` — settles the verdict on its own.
- Otherwise **two** non-chief confirmations that agree with the autojudge settle it.
- An **override** by the chief judge or an admin supersedes whatever the confirmations
  derived.

A judgment carries **at most one** decisive confirmation — the derivation raises if it ever
sees two. `confirm_verdict` therefore refuses a second one with
`DecisiveConfirmationExistsError`, so the chief judge and an admin cannot both settle the
same judgment: first one wins, the second gets a clean error instead of a crash at flush.

`remove_chief_judge` blocks removal while overrides attributed to the chief judge exist.
Admin-created overrides are attributed to the admin, so they do not block chief-judge
removal.

### Deliberate blindness

Two redactions are intentional and must survive any future permission change:

- **The clarification answer page is blind for every role**, admins included. It shows the
  problem, the question, and the answer field — never the asking team. Answering is never
  done with the asker's identity on screen, even though admins see that identity in the
  list view.
- **A plain judge sees no team identity** in the runs list, and no judge/team identity in
  the clarification list (`list_clarifications` nulls `judge_id` for judges).

Both exist so that whoever rules on a submission or a question is not influenced by whose
it is. Treat them as invariants, not UI details.
