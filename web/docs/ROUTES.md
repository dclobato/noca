# NOCA Web Routes

> For `url_for()` endpoint names and path parameters, see [URL_FOR_REFERENCE.md](URL_FOR_REFERENCE.md).

## Assets (`web/routes/assets.py`)

| Method | URL                                              | Description                                                                                                                                                                                                                                                                         |
|--------|--------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `GET` | `/assets/balloon/{color}`                        | Returns an inline SVG balloon. `color` is a 3 or 6 digit hex color (e.g. `00ff00`). Stroke color is auto-picked as black or white based on luminance.                                                                                                                               |
| `GET` | `/assets/star/{color}`                           | Returns an inline SVG star. `color` is a 3 or 6 digit hex color (e.g. `00ff00`). Stroke color is auto-picked as black or white based on luminance.                                                                                                                                  |

---

## Root And Public Pages (`web/routes/root.py`)

| Method | URL | Description |
|--------|-----|-------------|
| `GET` | `/favicon.ico` | Returns app's favicon with public cache headers. |
| `GET` | `/` | Public page listing all active running and upcoming contests, each with a link to its login page. No authentication required. |
| `GET` | `/contests` | Public page listing all active running and upcoming contests, each with a link to its login page. No authentication required. |

---

## Health (`web/routes/health.py`)

The health endpoint reports whether Web's required runtime backends are
available.

| Method | URL | Description |
|--------|-----|-------------|
| `GET` | `/health` | Reports PostgreSQL, Valkey, and Web service health. Returns `200` with `status: "ok"` or `503` with `status: "degraded"`. |

---

## Authentication (`web/routes/auth.py`)

| Method | URL | Description |
|--------|-----|-------------|
| `GET` | `/login` | Renders the login form. Accepts optional `next_url` (redirect after login, default `/uberadmin`) and `msg` query params. |
| `POST` | `/login` | Authenticates an UberAdmin. Validates `identifier` + `password` form fields, sets `noca_access_token` httponly cookie on success and redirects to `next_url`. Returns 401 with error message on failure. |
| `GET` | `/logout` | Clears the `noca_access_token` cookie and redirects with a confirmation message. Contest-scoped users are redirected to `/c/{slug}/login`; other cases fall back to `/login`. |
| `GET` | `/c/{slug}/login` | Renders the contest login form for the given contest slug. Returns 404 if slug is not found or the contest is inactive. |
| `POST` | `/c/{slug}/login` | Authenticates a contest user for an active contest. Validates `identifier` + `password` form fields, sets `noca_access_token` cookie on success and redirects to `/c/{slug}`. Returns 404 for inactive contests and 401 with error message on credential failure. |

---

## UberAdmin Dashboard (`web/routes/uberadmin_dashboard.py`)

All routes in this group require a valid UberAdmin JWT (`noca_access_token` cookie). Unauthenticated requests are redirected to `/login`.

| Method | URL | Description |
|--------|-----|-------------|
| `GET` | `/uberadmin` | Renders the UberAdmin dashboard. Displays three columns — Past, Live, and Upcoming contests — sourced from active contests in the database. Past contest cards include a Make inactive action. Also shows action buttons for creating contests, accessing the problem bank, viewing inactive contests, and managing UberAdmins. |
| `GET` | `/uberadmin/uberadmins` | Lists UberAdmins. Accepts optional `?q=` search over full name and email. |
| `GET` | `/uberadmin/uberadmins/new` | Renders the Add UberAdmin form (fields: full name, email, username). |
| `POST` | `/uberadmin/uberadmins/new` | Creates a new UberAdmin. Validates form fields, checks for duplicate username/email, generates a diceware password, persists the record, and re-renders the page with the generated credentials on success or an error message on failure. |
| `GET` | `/uberadmin/uberadmins/{uberadmin_id}/edit` | Renders the UberAdmin edit form for full name, email, and optional password replacement. |
| `POST` | `/uberadmin/uberadmins/{uberadmin_id}/edit` | Updates an UberAdmin after validating full name, email uniqueness, and optional password policy. |
| `POST` | `/uberadmin/uberadmins/{uberadmin_id}/toggle` | Enables or disables an UberAdmin account. Self-disable is blocked. Disabled accounts cannot log in or continue authenticated requests. |
| `POST` | `/uberadmin/uberadmins/credentials.json` | Returns the given `username`/`password` form fields as a downloadable JSON file (`noca-credentials-<username>.json`). Intended for use immediately after UberAdmin creation. |
| `GET` | `/uberadmin/contests/new` | Renders the Create Contest form (contest metadata + allowed-language checkboxes + initial admin credentials). Loads all active languages from DB to populate the checkbox panel. |
| `POST` | `/uberadmin/contests/new` | Validates form (including `language_ids[]` — at least one required), atomically creates a Contest, its initial admin User (role: admin), and `contest_languages` rows, and re-renders the page with the generated credentials on success or an error message on failure. Owner email and password are optional; a blank password triggers auto-generation. Includes `allow_print_requests` metadata (default enabled). Contest creation copy now makes clear that allowed languages remain editable until contest start. |
| `GET` | `/uberadmin/contests/inactive` | Lists inactive contests for UberAdmins. This is read-only and does not restore or manage inactive contests. |
| `POST` | `/uberadmin/contests/{contest_id}/deactivate` | Marks an active past contest inactive and redirects back to `/uberadmin`. Running, upcoming, missing, and already inactive contests are left unchanged. |
| `POST` | `/uberadmin/contests/credentials.json` | Returns the contest admin credentials (`contest_slug`, `username`, `password`) as a downloadable JSON file (`noca-credentials-<slug>-<username>.json`). Intended for use immediately after contest creation. |
| `POST` | `/uberadmin/contests/credentials/email` | Sends a credentials email for the just-created contest owner when an email is available. Uses the configured web email provider and the standard plain-text NOCA credentials template. |

---

## Contest User Dashboard (`web/routes/generaluser_dashboard.py`)

Routes require a valid contest-scoped JWT or UberAdmin JWT (`noca_access_token` cookie). Invalid or missing auth is currently redirected to `/contests`.

| Method | URL | Description |
|--------|-----|-------------|
| `GET` | `/c/{slug}` | Renders the contest user dashboard with role-appropriate module cards. Returns 404 for inactive contests. |
| `GET` | `/c/{slug}/clock` | Returns contest clock data as SSE (`text/event-stream`, pushes every 30 s) or plain JSON (polling fallback). Payload: `{server_now_ms, start_ms, end_ms, state}`. Accepts both contest-scoped and UberAdmin tokens. |

---

## Contest Modules

All routes require contest-scoped or UberAdmin auth. Invalid or missing auth is currently redirected to `/contests`. Insufficient role returns 403.

### Access Matrix

`always` = accessible regardless of contest state; `after-start` = only when `contest.is_running or contest.is_past`; `—` = no access (not in dashboard).

| Role | Scoreboard | Problems | Clarifications | Runs | Tasks |
|------|-----------|----------|----------------|------|-------|
| **ADMIN / UBERADMIN** | always | always | always | always | always |
| **JUDGE** | always | always | always | after-start | — |
| **STAFF** | after-start | after-start | — | — | after-start |
| **TEAM** | after-start | after-start | after-start | after-start | after-start |
| **USER** | after-start | — | — | — | — |

| Method | URL | Allowed Roles | File |
|--------|-----|---------------|------|
| `GET` | `/c/{slug}/scoreboard/` | ua, a, j, s, t, u (after-start for s/t/u) | `contest_score.py` |
| `GET` | `/c/{slug}/problems/` | ua, a, j, s, t (after-start for s/t) | `contest_problems.py` |
| `GET` | `/c/{slug}/problems/{problem_label}` | ua, a, j, s, t (after-start for s/t) | `contest_problems.py` |
| `GET` | `/c/{slug}/problems/{problem_label}/statement` | ua, a, j, s, t (after-start for s/t) | `contest_problems.py` |
| `GET` | `/c/{slug}/problems/{problem_label}/export` | ua, a, j, s, t (after-start for s/t) | `contest_problems.py` |
| `GET` | `/c/{slug}/clarifications/` | ua, a, j, t | `contest_clarifications.py` |
| `GET` | `/c/{slug}/clarifications/list` | ua, a, j, t | `contest_clarifications.py` |
| `POST` | `/c/{slug}/clarifications/new` | t | `contest_clarifications_submit.py` |
| `GET` | `/c/{slug}/runs/` | ua, a, j, t (after-start for j/t) | `contest_runs.py` |
| `GET` | `/c/{slug}/runs/list` | ua, a, j, t (after-start for j/t) | `contest_runs.py` |
| `GET` | `/c/{slug}/runs/events` | ua, a, j, t | `contest_runs_events.py` |
| `POST` | `/c/{slug}/runs/submit` | t | `contest_runs_review.py` |
| `POST` | `/c/{slug}/runs/{submission_id}/override` | chief judge | `contest_runs_review.py` |
| `GET` | `/c/{slug}/runs/{submission_id}/judging-history` | ua, a, j | `contest_runs_events.py` |
| `GET` | `/c/{slug}/submissions/{submission_id}/review` | ua, a, j | `contest_submissions.py` |
| `POST` | `/c/{slug}/submissions/{submission_id}/confirm` | j | `contest_submissions.py` |
| `POST` | `/c/{slug}/submissions/{submission_id}/rejudge` | chief judge | `contest_submissions.py` |
| `GET` | `/c/{slug}/tasks/` | ua, a, s, t (after-start for s/t) | `contest_tasks.py` |
| `GET` | `/c/{slug}/tasks/list` | ua, a, s, t (after-start for s/t) | `contest_tasks.py` |
| `POST` | `/c/{slug}/tasks/sos` | t | `contest_tasks.py` |
| `POST` | `/c/{slug}/tasks/print` | t | `contest_tasks.py` |
| `POST` | `/c/{slug}/tasks/{task_id}/acquire` | s | `contest_tasks_staff.py` |
| `POST` | `/c/{slug}/tasks/{task_id}/finish` | s | `contest_tasks_staff.py` |
| `POST` | `/c/{slug}/tasks/{task_id}/release` | s (own), a, ua | `contest_tasks_staff.py` |
| `GET` | `/c/{slug}/tasks/{task_id}/source` | s (lock holder), a, ua | `contest_tasks_staff.py` |
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
- Penalty time = `solved_at_minutes + (failed_attempts × contest.wa_penalty)`.
- Rank: teams sorted by `(problems_solved DESC, total_time ASC)`; ties share the same rank.

Caching: Valkey cache with three keys — `:full` for admin/judge (TTL 5 s), `:public` for all others (TTL 180 s), and `:final` for the released final scoreboard (no TTL, permanent). `:full` and `:public` are invalidated whenever a verdict is finalized or overridden.

Post-contest behavior:
- If `contest.release_scoreboard_after_end` is True: everyone sees the final scoreboard (`:final` cache, all results revealed, "Contest Final Scoreboard" badge).
- If `contest.release_scoreboard_after_end` is False: everyone (including admins) sees the frozen scoreboard ("SCOREBOARD FROZEN" badge).

| Method | URL | Allowed | Description |
|--------|-----|---------|-------------|
| `GET` | `/c/{slug}/scoreboard/` | ua, a, j, t, s | Full scoreboard page. Shows ICPC ranking table with per-problem balloon colors and penalty times. During the contest: admin/judge see live verdicts; others see frozen state (pending) after `contest.stop_updating_scoreboard`. After contest ends: if released, shows final standings with "Contest Final Scoreboard" badge; if not released, shows frozen view for all roles. HTMX auto-refresh every 30 s while the contest is running and not frozen. |

---

## Contest Problems (`web/routes/contest_problems.py`)

Contestant-facing problem pages. Access is role- and state-dependent:

- **ua / a (uberadmin, admin):** always accessible regardless of contest state
- **j (judge):** always accessible regardless of contest state
- **s (staff) / t (team):** accessible only when contest has started (`is_running` or `is_past`)
- **u (user):** no access to this module

| Method | URL | Description |
|--------|-----|-------------|
| `GET` | `/c/{slug}/problems/` | Problem list table: label, title (linked to detail), total TC count, public TC count, download icon. |
| `GET` | `/c/{slug}/problems/{problem_label}` | Problem detail: embedded PDF statement, side-by-side public test cases in monospaced preformatted text, back-to-list link. `problem_label` is case-insensitive (e.g. `A`, `B`, `AA`). Returns 404 if label not found. |
| `GET` | `/c/{slug}/problems/{problem_label}/statement` | Serves the problem statement PDF inline. Returns 404 if PDF not uploaded yet. |
| `GET` | `/c/{slug}/problems/{problem_label}/export` | Downloads a ZIP containing `statement.pdf` and public (sample) test cases only. No `problem.json`, no limits, no private test cases. |

---

## Contest Clarifications (`web/routes/contest_clarifications*.py`)

Routes require a valid contest-scoped JWT or UberAdmin JWT. TEAM role is enforced at route level for submission. Visibility is role-scoped:
- `ua`/`a`: all clarifications (including hidden); Team name and Judge name resolved from `user_map`
- `j`: all clarifications (including hidden); no judge/team identification
- `t`: own + public, never hidden; auto-refresh via HTMX every 60 s

Route ownership is split across `contest_clarifications.py`,
`contest_clarifications_submit.py`, `contest_clarifications_judge.py`, and
`contest_clarifications_admin.py`.

| Method | URL | Allowed | Description |
|--------|-----|---------|-------------|
| `GET` | `/c/{slug}/clarifications/` | ua, a, j, t | Full page. Shows clarification list and (team only) a submission form. Flash messages shown via `get_flashed_messages`. Loads `htmx.min.js`, `highlight-row.js`, `refresh-timer.js`, and `clarifications.js`. |
| `GET` | `/c/{slug}/clarifications/list` | ua, a, j, t | HTMX partial. Returns `#clarifications-list-wrapper` div with the current clarification table. Polled every 60 s by team browsers. |
| `POST` | `/c/{slug}/clarifications/new` | t | Submit a new clarification. Form fields: `problem_id`, `question` (max 1024 chars). Collects all validation errors at once. On success redirects to `/c/{slug}/clarifications/#{id}` (303). On error flashes all errors and redirects (303). Implemented in `contest_clarifications_submit.py`. |
| `POST` | `/c/{slug}/clarifications/announcement` | a, j | Create a public announcement. Form fields: `problem_id`, `announcement` (max 1024 chars). Creates a clarification with `question="Announcement"`, `is_contest_public=True`, already answered. Contest must be running. On success flashes and redirects to `/c/{slug}/clarifications/#{id}` (303). On error flashes and redirects (303). Implemented in `contest_clarifications_submit.py`. |
| `POST` | `/c/{slug}/clarifications/acquire` | j | Acquire a Valkey-backed clarification lock so a judge may answer it. Form field: `clarification_id`. On success redirects to `GET /answer?id={id}` (303). If Valkey is unavailable, flashes a degraded-mode warning and redirects to the answer form anyway. Implemented in `contest_clarifications_judge.py`. |
| `GET` | `/c/{slug}/clarifications/answer` | j | Render the answer form for the clarification identified by `?id=`. When Valkey is available, flashes and redirects to `/#{id}` if the judge does not hold the lock. In degraded mode, the form remains available and shows a warning banner. Implemented in `contest_clarifications_judge.py`. |
| `POST` | `/c/{slug}/clarifications/answer` | j | Submit an answer or release a lock. Form fields: `clarification_id`, `answer` (max 1024 chars), `is_contest_public` (checkbox), `action` (`submit`/`release`). On success flashes and redirects to `/#{id}` (303). Validation errors re-render the form (422). Release is available only while the lock service is up. Implemented in `contest_clarifications_judge.py`. |
| `GET` | `/c/{slug}/clarifications/hide` | j | Render the hide confirmation page for the clarification identified by `?id=`. Implemented in `contest_clarifications_admin.py`. |
| `POST` | `/c/{slug}/clarifications/hide` | j | Confirm or cancel a hide. Form fields: `clarification_id`, `action` (`confirm`/`cancel`). On `confirm` calls `toggle_hidden_clarification()`, flashes, and redirects to `/#{id}` (303). On `cancel` redirects to `/#{id}` (303). Implemented in `contest_clarifications_admin.py`. |
| `GET` | `/c/{slug}/clarifications/togglehide` | ua, a | Render the toggle-hide confirmation page for the clarification identified by `?id=`. Shows full question, optional answer, and current hidden status. Implemented in `contest_clarifications_admin.py`. |
| `POST` | `/c/{slug}/clarifications/togglehide` | ua, a | Confirm or cancel a toggle-hide. Form fields: `clarification_id`, `action` (`confirm`/`cancel`). On `confirm` calls `toggle_hidden_clarification()`, flashes, and redirects to `/#{id}` (303). On `cancel` redirects to `/#{id}` (303). Implemented in `contest_clarifications_admin.py`. |
| `POST` | `/c/{slug}/clarifications/releaselock` | ua, a | Force-release a judge's acquisition lock on an unanswered clarification. Form field: `clarification_id`. Uses JS confirm dialog for inline confirmation. Calls `release_clarification()` (admin unconditional path). Flashes and redirects to `/#{id}` (303) on success or error. Implemented in `contest_clarifications_admin.py`. |

---

## Contest Tasks (`web/routes/contest_tasks*.py`)

Routes require a valid contest-scoped JWT or UberAdmin JWT. JUDGE and USER roles have no access. STAFF and TEAM may only access after the contest starts (`is_running` or `is_past`); ADMIN and UBERADMIN always have access. Visibility is role-scoped:
- `t` (team): own tasks only; SOS button shown while contest is running; PRINT creation appears only when `contest.allow_print_requests` is enabled; auto-refresh via HTMX every 60 s
- `s` (staff): all tasks; acquire/finish/release workflow; auto-refresh via HTMX every 60 s
- `a` / `ua` (admin / uberadmin): all tasks with elapsed time column; force-release button for locked tasks; no auto-refresh

Task types: `BALLOON` (auto-created by judgment module), `FIRST_BALLOON` (first accepted solve for a problem, rendered with a golden glow), `PRINT` (team uploads source for printing), `SOS` (help request).

Task status is derived: queued (no staff, not finished), processing (staff assigned, not finished), finished.

Route ownership is split across `contest_tasks.py` and
`contest_tasks_staff.py`.

| Method | URL | Allowed | Description |
|--------|-----|---------|-------------|
| `GET` | `/c/{slug}/tasks/` | ua, a, s, t | Full page. TEAM sees SOS button and, when contest is running and `contest.allow_print_requests` is true, the Print modal/button, plus their own task list. STAFF sees all tasks with acquire buttons and task detail modal. ADMIN/UA sees all tasks with elapsed column and force-release buttons. Flash messages shown via `get_flashed_messages`. Loads `htmx.min.js`, `refresh-timer.js`, and `tasks.js`. |
| `GET` | `/c/{slug}/tasks/list` | ua, a, s, t | HTMX partial. Returns `#tasks-list-wrapper` div with the current task table. Polled every 60 s by TEAM and STAFF browsers. |
| `POST` | `/c/{slug}/tasks/sos` | t | Create an SOS help-request task. No form fields. Requires contest to be running. Flashes success or error. Redirects to `GET /tasks/` (303). |
| `POST` | `/c/{slug}/tasks/print` | t | Create a PRINT task. Form fields: `problem_id`, `source_file` (multipart upload). Validates: contest running, `contest.allow_print_requests=True`, non-empty problem selection, non-empty file, file within `contest.max_problem_file_size_bytes` (0 = unlimited). Blocks duplicate PRINT tasks (same team, problem, source hash while unfinished). Flashes and redirects to `GET /tasks/` (303). |
| `POST` | `/c/{slug}/tasks/{task_id}/acquire` | s | Acquire a Valkey-backed task lock. On success redirects to `/tasks/?open={task_id}` (303) so `tasks.js` auto-opens the detail modal. If Valkey is unavailable, flashes a degraded-mode warning and opens the task directly. Implemented in `contest_tasks_staff.py`. |
| `POST` | `/c/{slug}/tasks/{task_id}/finish` | s | Finish a task. When Valkey is available, STAFF must hold the task lock; in degraded mode the finish action remains available and the UI shows a warning banner. Redirects to `/tasks/` (303). Implemented in `contest_tasks_staff.py`. |
| `POST` | `/c/{slug}/tasks/{task_id}/release` | s (own lock), a, ua | Release a task lock without finishing. STAFF may release only their own lock; ADMIN and UBERADMIN may release any lock. Release is available only while the lock service is up. Redirects to `/tasks/` (303). Implemented in `contest_tasks_staff.py`. |
| `GET` | `/c/{slug}/tasks/{task_id}/source` | s (lock holder), a, ua | Download the source code for a PRINT task as a plain-text file. STAFF must hold the task lock when Valkey is available; in degraded mode the download remains available so staff can continue working. Returns 302 to `/tasks/` on error. Implemented in `contest_tasks_staff.py`. |

---

## Contest Runs (`web/routes/contest_runs*.py`)

Routes require a valid contest-scoped JWT or UberAdmin JWT. TEAM role is enforced at route level for submission. STAFF and USER have no access. JUDGE and TEAM may only access after the contest starts (`is_running` or `is_past`); ADMIN and UBERADMIN always have access. Visibility is role-scoped:
- `t`: own submissions only; submission form shown while contest is running; auto-refresh via HTMX every 60 s
- `ua`/`a`/`j`: all contest submissions; no submission form

Route ownership is split across `contest_runs.py`, `contest_runs_review.py`,
and `contest_runs_events.py`.

| Method | URL | Allowed | Description |
|--------|-----|---------|-------------|
| `GET` | `/c/{slug}/runs/` | ua, a, j, t | Full page. TEAM sees a submission form (problem, language, file upload) while the contest is running, plus their own submission list. Non-TEAM sees all contest submissions without the form. Flash messages shown via `get_flashed_messages`. Loads `htmx.min.js` and `refresh-timer.js`. |
| `GET` | `/c/{slug}/runs/list` | ua, a, j, t | HTMX partial. Returns `#runs-list-wrapper` div with the current submission table. Polled every 60 s by team browsers. |
| `GET` | `/c/{slug}/runs/language-info` | ua, a, j, t | HTMX partial. Returns compile and run command info for `?language_id=`. Used by the submission form language dropdown. Returns empty fragment for unknown/empty language_id. |
| `GET` | `/c/{slug}/runs/events` | ua, a, j, t | SSE stream (`text/event-stream`). Subscribes to verdict events and emits contest-scoped payloads plus heartbeat pings. Clients should trigger an HTMX refresh of the runs list on each message. Implemented in `contest_runs_events.py`. |
| `POST` | `/c/{slug}/runs/submit` | t | Submit a solution. Form fields: `problem_id`, `language_id`, `source_file` (multipart). Validates: contest running, non-empty selections, problem belongs to contest, language is active, non-empty file, file size within `contest.max_problem_file_size_bytes` (0 = unlimited). Computes SHA-256 for duplicate detection. On duplicate flashes "Duplicated submission" (danger). On success creates `Submission` + `SubmissionJudgment` (QUEUED), commits, and asks Valkey runtime to enqueue `JudgeJob`. If Valkey is temporarily unavailable, enqueue is buffered in-memory and replayed after reconnect. Redirects to `GET /runs` (303) with success flash. Implemented in `contest_runs_review.py`. |
| `POST` | `/c/{slug}/runs/{submission_id}/override` | chief judge | Override the effective final verdict of a DONE submission. Form fields: `new_verdict`, `reason` (10-1000 chars). On success creates a `VerdictOverride`, commits, publishes a `VerdictEvent` to `judge:results`, flashes success, and redirects to the submission review page. Implemented in `contest_runs_review.py`. |
| `GET` | `/c/{slug}/runs/{submission_id}/judging-history` | ua, a, j, s | Returns JSON `JudgingHistoryResponse` for one submission. Includes judgment creation and verdict-change audit rows plus explicit override rows; excludes status-only transitions. TEAM users are forbidden. Implemented in `contest_runs_events.py`. |

---

## Contest Live Feed (`web/routes/contest_live_feed.py`)

Public, no-login pages for any active contest (resolved by `login_slug`). The feed shows
the last 20 finalized team submissions, newest first, and refreshes in real time. The SSE
channel only signals "changed"; the blackout-aware JSON snapshot is the sole data source.
While the scoreboard is frozen, post-freeze rows still appear but the team name and verdict
are anonymized (`build_contest_live_feed_snapshot` in `web/services/live_feed_service.py`).

| Method | URL | Allowed | Description |
|--------|-----|---------|-------------|
| `GET` | `/c/{slug}/live` | public | Full page. Renders the live feed table shell and loads `live-feed.js`. Shows a "not started yet" state when the contest is upcoming. |
| `GET` | `/c/{slug}/live/feed.json` | public | JSON snapshot `{"live_feed_limit": int, "has_more": bool, "submissions": [...]}` of the last 20 finalized TEAM submissions, blackout-aware (verdict + team masked server-side for post-freeze rows); `has_more` is `true` when older finalized submissions exist beyond the cap. |
| `GET` | `/c/{slug}/live/events` | public | SSE stream (`text/event-stream`). Subscribes to verdict events via the shared `iter_refresh_events`, emits a generic `refresh` ping for this contest plus heartbeat pings; carries no verdict data. |

---

## Contest Administration (`web/routes/contest_admin*.py`)

Routes require either a valid UberAdmin JWT **or** a contest JWT with role `a` (admin). Uberadmins are authenticated via their global token and bypass contest-scoped auth. Non-admin contest roles return 403. Invalid or missing auth is currently redirected to `/contests`.

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
| `POST` | `/c/{slug}/admin/metadata` | ua, a | Validates and saves contest metadata changes, the submitted site list, and the allowed contest-language set while the contest is still upcoming. Site names are case-insensitively unique, at least one site must remain, and a site cannot be removed while any TEAM or STAFF user is assigned to it. Removing a language also deletes stale per-language problem overrides for that contest; newly added languages rely on fallback limits until explicitly configured. When locked (running/past), timing fields stay editable and `allow_print_requests` also stays editable as an explicit exception, but allowed languages no longer change. Re-renders with success flash or validation errors. Implemented in `contest_admin_metadata.py`. |
| `POST` | `/c/{slug}/admin/start-now` | ua, a | Requires the authenticated admin/uberadmin password confirmation, then sets `contest.start_time` to the current UTC time, starting the contest immediately. No-op if the contest is already running or past. Refuses to start when the contest has no sites. Redirects to `/c/{slug}` (303). Implemented in `contest_admin.py`. |
| `POST` | `/c/{slug}/admin/end-now` | ua, a | Requires the authenticated admin/uberadmin password confirmation, then ends a running contest by shortening `duration_minutes` to the smallest whole-minute value that does not end in the past. Dependent timing fields (`stop_updating_scoreboard`, `stop_answers_after`, and timeout values) are clamped as needed to preserve contest timing invariants. No-op if the contest is not running. Redirects to `/c/{slug}` (303). Implemented in `contest_admin.py`. |
| `POST` | `/c/{slug}/admin/release-scoreboard` | ua, a | Releases the final scoreboard for an ended contest. Sets `release_scoreboard_after_end=True`, computes and permanently caches the final standings (all frozen/pending results revealed, no TTL), then commits. Flashes danger if contest has not ended; flashes warning if already released. Redirects to admin dashboard (303). Implemented in `contest_admin.py`. |
| `GET` | `/c/{slug}/admin/users` | ua, a | Renders the user management page, listing all enrolled members grouped by role (Admin, Judge, Staff, Team, User). Supports remove actions (disabled while contest is running) and shows an `Export Users` action that downloads import-compatible JSON. Implemented in `contest_admin_reports.py`. |
| `GET` | `/c/{slug}/admin/import_export` | ua, a | Renders the Export/Import page with download actions for the Animeitor-compatible ZIP and the markdown contest timeline report. Implemented in `contest_admin_export.py`. |
| `GET` | `/c/{slug}/admin/export-animeitor` | ua, a | Downloads a ZIP file compatible with the `maratona-animeitor` consumer. Contains `contest`, `runs`, `time`, `version`, and `icpc` files in the legacy BOCA webcast format. Returns 303 redirect with flash error if the contest has no teams or no problems. Implemented in `contest_admin_export.py`. |
| `GET` | `/c/{slug}/admin/export-events` | ua, a | Downloads a markdown report containing a wrapped fixed-width text table of persisted contest events. Best-effort only: transient lock-only acquisitions are omitted because they are not historically stored. Implemented in `contest_admin_export.py`. |
| `GET` | `/c/{slug}/admin/users-per-site-report` | ua, a | Downloads a markdown report of contest users grouped by site. Sites are ordered A-Z; users within each site and role are ordered by username. Includes users with no site assigned, chief judge annotation, and contest header with rules summary. Implemented in `contest_admin_export.py`. |

---

## Contest Submissions (`web/routes/contest_submissions*.py`)

Review page routes for a single submission. STAFF is intentionally excluded.

Route ownership is split across `contest_submissions.py`,
`contest_submissions_review.py`, and `contest_submissions_files.py`. Shared UI
helpers live in `contest_submissions_helpers.py`.

| Method | URL | Allowed | Description |
|--------|-----|---------|-------------|
| `GET` | `/c/{slug}/submissions/download-all` | t | **Team only.** Downloads a ZIP archive containing all finalized submissions made by the current team during the contest. Only available after the contest has ended and the scoreboard has been released. Returns 403 if the contest is not past or the scoreboard is not released. Implemented in `contest_submissions.py`. |
| `GET` | `/c/{slug}/submissions/{submission_id}/review` | ua, a, j | Unified submission review page. Left column: source code, compile log, judging history, per-test-case results (ua/a/j only). Right column: verdict confirmation status panel; confirmation form for judges (with chief-judge modal) when the active judgment is `DONE` and the contest is not `autojudge_only`; override form for the chief judge only when a final verdict already exists. Implemented in `contest_submissions.py`. |
| `POST` | `/c/{slug}/submissions/{submission_id}/acquire-review` | j | **Judge only.** Acquires a Valkey-backed review lock for the submission, allowing the judge to submit a verdict confirmation. Validates that the autojudge has finished (`DONE`), the judge hasn't already confirmed, and no other judge holds the lock. If Valkey is unavailable, flashes a degraded-mode warning and leaves confirmation available from the review page without lock controls. Implemented in `contest_submissions_review.py`. |
| `POST` | `/c/{slug}/submissions/{submission_id}/release-review` | j (own), a, ua | Releases a review lock without submitting a confirmation. Judges may release only their own lock; ADMIN and UBERADMIN may release any lock. Release is available only while the lock service is up. Implemented in `contest_submissions_review.py`. |
| `POST` | `/c/{slug}/submissions/{submission_id}/confirm` | j | Submits a human confirmation for the active judgment. Returns 404 for `autojudge_only` contests. When Valkey is available, the judge must hold the review lock; in degraded mode confirmation remains available and the UI warns that coordination locks are off. If the confirmation produces a final verdict, publishes a `VerdictEvent` and invalidates scoreboard cache before redirecting back to the review page. Implemented in `contest_submissions_review.py`. |
| `POST` | `/c/{slug}/submissions/{submission_id}/rejudge` | chief judge | Supersedes the current active judgment and creates a new `QUEUED` judgment for the same submission. Only available when the submission has a final verdict. Enqueues the new judgment with `is_rejudge=True` and invalidates the scoreboard cache before redirecting back to the review page. Implemented in `contest_submissions_review.py`. |
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
| `GET` | `/c/{slug}/admin/problems/new` | Render create-problem form (single-screen: basic info, fallback limits, statement, categories, per-language limits, test cases). Fallback limits always judge with 1 repetition. |
| `POST` | `/c/{slug}/admin/problems/new` | Create a new problem. Requires title, `time_limit_ms`, `memory_limit_kb`, `pids_limit`, a statement, and at least 1 test case. Collects all errors before returning 422. |
| `POST` | `/c/{slug}/admin/problems/{problem_id}/move` | HTMX/drag endpoint. Moves a problem to `?new_ordinal=N`; still accepts legacy adjacent moves via `?direction=up\|down`. Returns `problems_list_table.html` partial. |
| `POST` | `/c/{slug}/admin/problems/{problem_id}/remove` | Remove a problem. Only allowed when `contest.upcoming and contest.active`. Redirects with reason if blocked. |

### Edit (`web/routes/contest_admin_problem_edit.py`)

| Method | URL | Description |
|--------|-----|-------------|
| `GET` | `/c/{slug}/admin/problems/{problem_id}/edit` | Edit problem form (basic info, fallback limits, profiling actions, statement, categories, per-language limits, read-only per-language repetitions, test case list, latest profiling result). |
| `POST` | `/c/{slug}/admin/problems/{problem_id}/edit` | Save problem changes. Before the contest starts, this covers title, limits, statement, categories, and test cases, including pending test-case removals (`tc_remove_ids`) and inline add-rows (`tc_in_N` / `tc_out_N` / `tc_explanation_N` / `tc_is_sample_N`) applied on save. While the contest is running, only the Limits tab is accepted; successful running-contest limit saves may redirect to an affected-submissions review batch. |

### Limits (`web/routes/contest_admin_problem_limits.py`)

| Method | URL | Description |
|--------|-----|-------------|
| `POST` | `/c/{slug}/admin/problems/{problem_id}/profiling` | Queue an Auto-Limit profiling run for one language using an uploaded reference implementation and safety factor. |
| `GET` | `/c/{slug}/admin/problems/{problem_id}/profiling-status` | HTMX partial for the Auto-Limit and per-language limits panel. Polls every 2 seconds while profiling is active and returns the normal static panel again after completion. |
| `POST` | `/c/{slug}/admin/problems/{problem_id}/fallback-limits` | Copy separate `MAX()` values from per-language limits into the problem fallback limits. Repetition is not copied; fallback always uses 1 repetition. When this changes effective limits for running-contest submissions, redirects to a persisted affected-submissions review batch. |
| `GET` | `/c/{slug}/admin/problems/{problem_id}/limit-change-batches/{batch_id}` | Review one persisted affected-submissions batch created by a running-contest limit change. Shows submissions grouped by language with before/after limits and per-row rejudge state. |
| `POST` | `/c/{slug}/admin/problems/{problem_id}/limit-change-batches/{batch_id}/rejudge-all` | Queue rejudges for every still-pending submission in the saved batch. ADMIN and UBERADMIN only. Rows whose captured judgment is no longer active are marked stale instead of being requeued. |
| `POST` | `/c/{slug}/admin/problems/{problem_id}/limit-change-batches/{batch_id}/languages/{language_id}/rejudge` | Queue rejudges only for the pending submissions of one language inside the saved batch. Uses the same stale-row guard as the batch-wide action. |


### Import / Export / Serve (`web/routes/contest_admin_problem_io.py`)

| Method | URL | Description |
|--------|-----|-------------|
| `GET` | `/c/{slug}/admin/problems/import` | Render problem import form with ZIP format documentation. |
| `POST` | `/c/{slug}/admin/problems/import` | Import a problem from a ZIP archive (problem.json + statement.pdf/statement.md + test cases). Raises human-readable error on validation failure. Any `language_limits` entries for languages not currently allowed in the contest are skipped with a warning; allowed languages import normally. |
| `GET` | `/c/{slug}/admin/problems/{problem_id}/statement` | Serve problem statement PDF. `?download=1` for attachment. |
| `GET` | `/c/{slug}/admin/problems/{problem_id}/export` | Export problem as ZIP (Layout A: `in/001.in`, `out/001.out`, `statement.pdf`, `problem.json`). |

### Test Cases (`web/routes/contest_admin_problem_tc.py`)

| Method | URL | Description |
|--------|-----|-------------|
| `POST` | `/c/{slug}/admin/problems/{problem_id}/test-cases/zip` | Upload test case ZIP; **replaces all existing test cases**. Supports Layout A (directory) and Layout B (flat). |
| `GET` | `/c/{slug}/admin/problems/{problem_id}/test-cases/new` | Render the new test case form (dedicated page). |
| `POST` | `/c/{slug}/admin/problems/{problem_id}/test-cases/add` | Add a single test case. On success, redirects back to the problem edit page. |
| `POST` | `/c/{slug}/admin/problems/{problem_id}/test-cases/add-zip` | Add a single new test case from an uploaded single-case ZIP (`input.txt` / `output.txt`, optional `explanation.txt`). Redirects back to the problem edit page. |
| `GET` | `/c/{slug}/admin/problems/{problem_id}/test-cases/{tc_id}/edit` | Render the edit test case form (dedicated page, pre-filled with file content). |
| `POST` | `/c/{slug}/admin/problems/{problem_id}/test-cases/{tc_id}/edit` | Update test case `is_sample` and overwrite file content. On success, redirects back to the problem edit page with `?tab=content#tc-{tc_id}` so the edited row is scrolled into view and highlighted. |
| `POST` | `/c/{slug}/admin/problems/{problem_id}/test-cases/{tc_id}/toggle-sample` | Flip a test case between sample and secret without opening the edit form. On success, redirects back with `?tab=content#tc-{tc_id}` so the affected row is scrolled into view and highlighted. |
| `POST` | `/c/{slug}/admin/problems/{problem_id}/test-cases/{tc_id}/remove` | Remove a test case and resequence ordinals + filesystem files. |
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
| `POST` | `/c/{slug}/admin/users/credentials/email` | ua, a | Sends a credentials email for the just-created user when an email is available. Uses the configured web email provider and a plain-text NOCA credentials template. |
| `GET` | `/c/{slug}/admin/users/batch` | ua, a | Renders the batch user import form. Implemented in `contest_admin_user_batch.py`. |
| `POST` | `/c/{slug}/admin/users/batch` | ua, a | Accepts a `.csv` or `.json` file (max 5 MB) and bulk-creates/updates contest users. Import accepts optional `email` and `site`; missing sites are auto-created case-insensitively within the contest. `TEAM` and `STAFF` rows require `site`, while other roles may omit it and keep `site_id=None`. Re-renders with per-row results including email, site, and generated passwords. Implemented in `contest_admin_user_batch.py`. |
| `POST` | `/c/{slug}/admin/users/batch/results.json` | ua, a | Returns the provided batch results JSON as a downloadable file (`noca-batch-{slug}.json`). Implemented in `contest_admin_user_batch.py`. |
| `POST` | `/c/{slug}/admin/users/batch/credentials/email` | ua, a | Sends credential emails in batch for created/updated rows that include both `password` and `email`, and re-renders the results view with a delivery summary. Implemented in `contest_admin_user_batch.py`. |
| `GET` | `/c/{slug}/admin/users/export.json` | ua, a | Downloads all contest users as import-compatible JSON (`noca-users-{slug}.json`). Passwords are omitted; each row includes `username`, `fullname`, `role`, and optional `email`, `site`, `location`. Implemented in `contest_admin_user_edit.py`. |
| `GET` | `/c/{slug}/admin/users/{user_id}/edit` | ua, a | Renders the Edit User form pre-filled with the user's current data. Returns 404 if the user is not found in this contest. Implemented in `contest_admin_user_edit.py`. |
| `POST` | `/c/{slug}/admin/users/{user_id}/edit` | ua, a | Validates and updates a user's fullname, optional email, site, location, and optionally password. Role is shown as read-only and cannot be changed after creation. `TEAM` and `STAFF` users must keep a site assigned. Redirects back to the edit page on success. Implemented in `contest_admin_user_edit.py`. |
| `POST` | `/c/{slug}/admin/users/{user_id}/remove` | ua, a | Deletes a contest user. Returns 403 if the contest is running or finished. Returns 404 if the user is not found. Redirects to `/c/{slug}/admin/users` on success. Implemented in `contest_admin_user_edit.py`. |

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

## Profile (`web/routes/profile.py`)

Current-user profile routes accept either a valid contest JWT or UberAdmin JWT (`noca_access_token` cookie) and redirect unauthenticated requests to `/login`. The `/user/{user_id}/avatar`, `/user/{user_id}/photo`, and `/user/{user_id}/photo/remove` routes accept either a contest-scoped viewer from the same contest or an UberAdmin viewer. Upload remains self-only.

| Method | URL | Description |
|--------|-----|-------------|
| `GET` | `/profile` | Profile page with display name, email, password, photo forms, and read-only site information for contest users. |
| `POST` | `/profile/fullname` | Update display name; flashes success and redirects to `/profile`. |
| `POST` | `/profile/email` | Update current-user email (`USER` email is optional; `UberAdmin` email cannot be blank); flashes success and redirects to `/profile`. |
| `POST` | `/profile/password` | Change password (requires current password); empty `new_password` = no change; flashes success and redirects to `/profile`. |
| `GET` | `/user/{user_id}/avatar` | User avatar (SVG fallback if no photo); cache: `public` (real photo) or `private` (fallback). Contest-scoped viewers are limited to their own contest; UberAdmins can view any user. |
| `GET` | `/user/{user_id}/photo` | User full photo; same cache policy and contest visibility rules as avatar. |
| `POST` | `/user/{user_id}/photo` | Upload cropped photo for the authenticated contest user. Self-only. Enforces aspect ratio: 16:10 (team), 2:3 (others). |
| `POST` | `/user/{user_id}/photo/remove` | Remove photo. Self-service flashes success and redirects to `/profile`; admin/UberAdmin removals from the edit screen redirect back to that user edit page. |

Site note:
- contest users cannot self-edit their assigned site from `/profile`; the page displays it as administrator-managed read-only information.
