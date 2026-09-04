# NOCA Web Service Reference

This document lists the service modules under `web/services/` and the main capabilities they already provide.

For shared/cross-module services (email, network utils, image processing, Valkey, lock service, token revocation)
used by both `web` and `arena`, see [docs/SHARED_SERVICES.md](../../docs/SHARED_SERVICES.md).

Purpose:
- help future development discover existing business logic before adding new code
- reduce duplicate implementations across routes and services
- make it clear which module should own a given kind of behavior

Conventions:
- prefer reusing documented public helpers before creating new ones
- treat names starting with `_` as internal implementation details unless there is a strong reason to reuse them
- if route behavior changes, keep this file and `web/docs/ROUTES.md` in sync

---

## `web/access_matrix/` (declaration, not a service)

Purpose:
- the declared access and capability matrices for contest roles, as data: which contest modules each actor reaches and in which contest states, and what each actor may do once there
- replaces the two markdown tables `web/docs/ROUTES.md` used to carry, which drifted because nothing checked them

**It enforces nothing.** No route, service, or ORM hook consults this package. The
enforcing layers are unchanged: the route guards, the per-domain `permissions.py`
predicates, and the `before_flush` hook in `web/models/submission.py`. Editing a
cell here changes documentation and UI and grants nobody anything. Change
behaviour first, then update the declaration.

Internal structure:
- `models.py` — vocabulary: `MatrixActor`, `AccessLevel`, `Grant`, and the frozen `AccessRule` / `CapabilityGrant` / `AccessArea` / `Capability` / `CapabilityGroup` records
- `access.py` — `ACCESS_AREAS`, one area per contest module (scoreboard, problems, clarifications, runs, tasks)
- `capabilities.py` — `CAPABILITY_GROUPS`, the clarification, task, verdict, and administration capabilities
- `__init__.py` — public exports plus the legends, `UBERADMIN_ATTRIBUTION_NOTE`, and `CHIEF_JUDGE_NOTE`

Main entrypoints:
- `MatrixActor.for_role(role) -> MatrixActor` / `MatrixActor.role -> RoleEnum | None` — the mapping between the seven matrix actors and the six roles; `CHIEF_JUDGE` has no role, because it is the single `JUDGE` named by `contests.chief_judge_id`
- `area_by_key(key) -> AccessArea` / `areas_for_actor(actor) -> tuple[AccessArea, ...]`
- `capability_by_key(key) -> Capability` / `capabilities_for_actor(actor) -> tuple[Capability, ...]` / `all_capabilities() -> tuple[Capability, ...]`
- `Capability.enforced_by` — dotted path of the predicate that decides the row at runtime, or `None` when only a route role tuple gates it

How it stays true:
- `tests/web/test_access_matrix.py` builds a real actor per `MatrixActor` in both a running and a not-yet-started contest, calls the predicate each capability names and the gate each area is guarded by, and asserts the answers match the declared cells
- rows with no predicate are checked structurally, and `test_every_capability_without_a_predicate_is_known` pins that set so it shrinks on purpose and never grows by accident

Consumers:
- `web/template_globals.py` exposes the whole thing as the `role_matrix` template global, alongside `all_contest_roles`
- `web/template/admin/users/_role_reference.html` renders both matrices as a reusable card, on every page where an admin chooses a role. Its lead-in is overridable through `role_reference_intro`, because the default one assumes a role picker sits directly above the card
- `admin/users/add.html` mounts it in the `col-lg-5` column that otherwise stays empty until a user is created, and loads `web/static/js/role-reference.js` to highlight the role chosen in its `#role` select
- `admin/users/batch_import.html` mounts it full width (`col-12`) — that page's right column already holds the file-format card — and overrides the lead-in. It has no `#role` control, so it deliberately does not load the highlight script; the script no-ops without one in any case

---

## `clarification_service/`

Purpose:
- full lifecycle management for contest clarifications: creation, acquisition, answering, release, moderation (hide/unhide), and role-scoped listing with redacted judge identity for non-admin viewers

Internal structure:
- `errors.py` — service exception types
- `reads.py` — acknowledgement of everything a team was shown, across both read-state shapes
- `views.py` — `ClarificationView` plus lock-merging helpers
- `queries.py` — contest-scoped reads and role-filtered listing
- `lifecycle.py` — creation, acquisition, answering, release, announcement, and hide/unhide flows
- `permissions.py` — who may answer and who may force-release

Main types:
- `ClarificationView` — role-scoped DTO returned by `list_clarifications`; `judge_id` is `None` for JUDGE and TEAM callers, populated only for ADMIN/UBERADMIN
- `ClarificationError` — base class for all clarification service errors
- `ContestNotRunningError` — state-changing action attempted outside the contest window
- `ForbiddenClarificationActionError` — actor's role is insufficient for the action
- `ClarificationAlreadyAnsweredError` — answer or acquire attempted on an already-answered clarification
- `ClarificationAlreadyAcquiredError` — second judge tries to acquire an already-locked clarification
- `ClarificationLockUnavailableError` — Valkey coordination is unavailable for acquire/release flows
- `ClarificationNotAcquiredByActorError` — judge tries to release or answer a lock they do not hold
- `ClarificationHiddenError` — answer or acquire attempted on a hidden clarification
- `ClarificationRateLimitError` — per-window clarification budget exhausted; carries `next_allowed_at`
- `TooManyUnansweredClarificationsError` — the team already holds the maximum unanswered questions; carries `open_count` and `limit`

Main entrypoints:
- `create_clarification(session, contest, actor, *, problem_id, question, rate_limit_window_seconds, rate_limit_max_requests, max_open_clarifications) -> Clarification` — TEAM only; contest must be running; question is immutable; `problem_id=None` creates a general, contest-wide clarification. After validation (so a request that writes nothing never consumes budget) it enforces the unanswered cap and then the rolling window through `rate_limit_service`; `create_announcement` is deliberately not throttled
- `create_announcement(session, contest, actor, *, problem_id, announcement) -> Clarification` — ADMIN/JUDGE only; a JUDGE may publish only while the contest is running, while a contest ADMIN and the contest's chief judge may publish at any point in the contest lifecycle (see `can_create_announcement`); `problem_id=None` publishes a general, contest-wide announcement; creates a clarification pre-answered with `question="Announcement"`, `is_contest_public=True`; actor is recorded as both team and judge
- `get_clarification(session, contest, clarification_id) -> Clarification | None` — contest-scoped lookup; no actor; caller is responsible for authorization
- `list_clarifications(session, contest, actor, lock_client, sort_by="time_desc") -> tuple[list[ClarificationView], bool]` — orders Time or Problem in SQL (general clarifications group last in both problem directions), merges PostgreSQL rows with Valkey lock state, and returns whether lock coordination is available for the UI
- `count_pending_clarifications(session, contest, team_id=None) -> int` — counts visible unanswered clarifications for the judge/admin dashboard badge; optional `team_id` narrows the query to one requester
- `count_unread_clarification_answers(session, contest, team_id) -> int` — counts visible answers to the team's **own** questions that it has not acknowledged. Announcements are excluded even though they are answered rows: they store `team_id` as their *author*, so a judge later changed to TEAM would otherwise be counted here and by `count_unread_announcements` at once
- `count_unread_announcements(session, contest, team_id) -> int` — counts visible announcements with no `clarification_reads` marker for that team. An announcement matches *every* team, so unlike the answer counter the query cannot scope the caller through the row: it checks the target user's own TEAM membership of the contest explicitly, or a `team_id` from another contest would be handed these announcements
- `count_unread_team_clarifications(session, contest, team_id) -> int` — the merged number the team dashboard badge shows: the two disjoint counts above
- `get_read_announcement_ids(session, team_id, clarification_ids) -> frozenset[str]` — which of the given announcements that team has already read; used by `list_clarifications` to project `unread` without a join or a lazy load
- `mark_clarification_answers_read(session, contest, actor, clarification_ids) -> int` — TEAM only (`reads.py`); acknowledges both kinds in one call, so a caller can never record half of what it rendered: the team's own answered questions get `answer_read_at`, and the contest's visible announcements get a `clarification_reads` row inserted `ON CONFLICT DO NOTHING`, since a page load racing the 60 s HTMX refresh is normal traffic rather than an error. Returns the number newly marked across both. Callers pass IDs actually rendered, to avoid acknowledging a concurrent unseen answer
- `acquire_clarification(session, contest, actor, clarification, lock_client) -> Clarification` — JUDGE or ADMIN; acquires a Valkey TTL lock keyed by contest and clarification id
- `release_clarification(session, contest, actor, clarification, lock_client) -> Clarification` — JUDGE may release own lock; ADMIN/UBERADMIN may force-release any lock through Valkey
- `answer_clarification(session, contest, actor, clarification, lock_client, *, answer, is_contest_public) -> Clarification` — JUDGE or ADMIN; enforces the Valkey lock when available; in degraded mode the DB remains authoritative for answer validity and judge identity
- `can_answer_clarifications(actor) -> bool` / `can_force_release_clarifications(actor) -> bool` — the single source of truth the routes and templates share; uberadmins may force-release but never answer, since `clarifications.judge_id` is a foreign key into `users`
- `can_request_clarification(actor, contest) -> bool` — permits only a TEAM actor in a running contest; the request form and `create_clarification()` share this lifecycle gate, while list visibility remains available in every contest lifecycle state
- `can_create_announcement(actor, contest) -> bool` — whether the actor may publish an announcement in the contest's current lifecycle state; ADMIN/JUDGE while running, plus contest ADMINs and the contest's chief judge at any time (via `has_chief_authority` from `chief_judge_permissions.py`). The route passes the result to the template as `can_create_announcement`, and `create_announcement()` re-checks it, so the form and the guard cannot disagree. Uberadmins are excluded for the same reason they cannot answer
- `toggle_hidden_clarification(session, actor, clarification) -> Clarification` — JUDGE/ADMIN/UBERADMIN; sets `hidden_by_judge_id` XOR `hidden_by_admin_id` on hide; clears both on unhide

Reuse this module when:
- building any clarification UI (team question view, judge queue, admin moderation panel)
- implementing any clarification-related API endpoint

Do not reimplement:
- the XOR logic for `hidden_by_judge_id` vs `hidden_by_admin_id`
- the Valkey lock payload/TTL semantics outside this service
- role-scoped visibility filtering and judge-identity redaction for listing

Notes:
- services flush, never commit; routes are responsible for `await session.commit()`
- contest membership validation is the caller's responsibility (routes enforce this via `get_actor_from_token` in `dependencies.py`); the service trusts the caller has passed a correctly-scoped actor
- `question` and `answer` are immutable after creation/answering respectively; the service enforces this via guard exceptions, not column constraints
- there is no contest FK on `Clarification`, and `problem_id` is nullable for general clarifications, so contest scoping joins through the author (`Clarification.team_id` → `users.contest_id`) — the same total scoping path the SOS-task queries use; every contest-scoped consumer (reaper, dashboards, counters, timeline export, backup export, contest removal) must scope this way or it silently drops general rows
- `is_contest_public` is the only public-visibility flag on the model; setting it `True` when answering makes the Q&A visible to all teams
- `answer_read_at` records when the requesting team acknowledges an answer; new answers leave it null, the team dashboard counts those null markers, and the Clarifications page highlights only rows rendered before acknowledgement
- announcement read state does **not** live in `answer_read_at`. An announcement is one row read by many teams, so a per-row scalar cannot express it; it lives in `clarification_reads(clarification_id, user_id, read_at)`, where absence means unread. That is why a team created after an announcement correctly sees what it missed
- `is_announcement` is stored, not derived from the author's role. `update_user()` can change a role, which would otherwise reclassify historical rows in both directions
- active clarification locks live only in Valkey; PostgreSQL now keeps the durable clarification state and answering judge identity

---

## `clarification_reaper.py`

Purpose:
- auto-answer still-open clarifications for contests that have already ended

Main entrypoints:
- `conclude_finished_contest_clarifications(session, now=None) -> int` — auto-answers unanswered, non-hidden clarifications for past contests using the placeholder response stored in `AUTO_ANSWER_PLACEHOLDER`; records `contest.owner_user_id` as the answering actor when present
- `run_clarification_reaper(session_factory, poll_interval_seconds, stop_event, logger) -> None` — delegates to `reaper_runner.run_reaper_loop`

Reuse this module when:
- wiring post-contest clarification collection into app startup

Do not reimplement:
- the placeholder-answer finalization rule for ended contests
- the periodic loop and shutdown handling for the in-process reaper

Notes:
- active clarification locks live only in Valkey; this module only finalizes unanswered clarifications after the contest ends
- FastAPI startup in `web.main` enables this loop only when `NOCA_WEB_ENABLE_CLARIFICATION_REAPER=true`
- polling interval is controlled by `NOCA_WEB_CLARIFICATION_REAPER_INTERVAL_SECONDS` and must stay within 180 to 300 seconds

---

## `reaper_runner.py`

Purpose:
- generic async loop used by all periodic reaper services

Main entrypoints:
- `run_reaper_loop(session_factory, poll_interval_seconds, stop_event, logger, *, collect_message, failure_message, cycle) -> None` — opens a session, calls the `cycle` callback, commits, sleeps until `stop_event` is set or the interval elapses; exceptions are logged and swallowed per cycle

Reuse this module when:
- adding a new periodic background task that follows the session-per-cycle pattern

Do not reimplement:
- the graceful shutdown and error-swallowing loop

Notes:
- currently consumed by `clarification_reaper` and `task_reaper`
- the `cycle` callback receives the active session; the runner commits after it returns

---

## `category_service.py`

Purpose:
- standalone CRUD for `ProblemCategory` records
- guards deletion when a category is assigned to one or more problems

Main types:
- `CategoryInUseError` — raised by `delete_category` when the category is in use

Main entrypoints:
- `list_categories(session, query=None, limit=None) -> list[ProblemCategory]` — all categories ordered by name; optional substring filter and row cap
- `get_category(session, category_id) -> ProblemCategory | None`
- `get_or_create_categories(session, names) -> list[ProblemCategory]` — normalises names to lowercase, creates missing ones
- `create_category(session, name) -> ProblemCategory` — raises `ValueError` if blank, too long (>48), or already exists
- `rename_category(session, category, new_name) -> None` — raises `ValueError` on conflict; no-op if name unchanged
- `delete_category(session, category) -> None` — raises `CategoryInUseError` if any problem references the category
- `replace_problem_categories(session, problem, categories) -> None` — replaces all categories on a problem atomically
- `count_problems_for_category(session, category) -> int`

Reuse this module when:
- building any category management UI
- validating or applying category names from an import payload
- checking whether a category can be safely removed

Do not reimplement:
- name normalisation (always lowercase, stripped)
- in-use guard for deletion

---

## `actor_service.py`

Purpose:
- resolve the authenticated actor for contest-scoped routes from the JWT cookie
- reuse the request-cached validation result so contest-scoped auth does not
  validate the same JWT repeatedly

Main entrypoints:
- `get_actor_from_token(request, contest, session) -> UberAdmin | User`
  Use when a route accepts either a contest user from the same contest or an UberAdmin.

Typical consumers:
- `web.dependencies.get_contest_context`
- `web.dependencies.get_contest_admin_context`

---

## `authentication_service.py`

Purpose:
- authenticate UberAdmins and contest users
- issue JWTs
- refresh active web-session JWTs while preserving actor claims
- record login history with optional geolocation

Main types:
- `AuthAction`
- `AuthenticationService`

Main entrypoints on `AuthenticationService`:
- `uberadmin_login(username, password, session, *, ip_address=None, source_port=None, user_agent=None) -> str`
- `user_login(username, password, contest_id, session, *, ip_address=None, source_port=None, user_agent=None) -> str`
- `create_access_token(*, sub, audience, extra_data=None, session_started_at=None) -> str`
- `should_refresh_token(result) -> bool`
- `is_absolute_session_cap_exceeded(result) -> bool`
- `build_refreshed_access_token(result) -> str | None`
- `logout(token) -> None` — revokes the raw JWT string via `JWTService.revoke()`; best-effort (no-op when store is unavailable)
- `jwt_service`

Notes:
- successful login writes `Login_History`, resolving the client IP via
  `GeolocationIP.get_details_by_ip` into structured columns (`country_code`,
  `subdivision_code`, `district`, `city`, `is_eu`, `as_number`); the web module has
  no login-history viewer, so these are captured for parity/audit only
- `/login` and `/c/{slug}/login` use shared auth throttling from
  `shared.services.auth_rate_limit`; lockouts return HTTP 429 with
  `Retry-After`. The settings builder lives in
  `password_confirm_throttle.auth_rate_limit_settings()` and is shared with the
  password-reconfirmation routes
- login history uses generated BIGINT identifiers to keep append-only audit
  storage compact
- login-issued tokens include a `session_started_at` marker used to enforce the
  optional absolute sliding-session cap
- logout revocation is backed by `ValkeyRevocationStore`; revoked JTIs are stored with a
  TTL matching the token's remaining lifetime so entries expire automatically
- IP geolocation and external request helpers are shared services under `shared/services/`
  (`geolocation.py` and `network_utils/`) so web and arena login flows use the same behavior

Typical consumers:
- `web.routes.auth`
- `web.middleware.auth_token_refresh`
- dependency helpers that reuse the request-cached token validation result

---

## `dependencies.py`

Purpose:
- enforce Web's default-deny authentication gate
- resolve authenticated actors for contest-scoped and admin-scoped routes

Main entrypoints:
- `enforce_web_default_auth(request) -> None`
- `get_request_user(request, session) -> User`
- `get_uberadmin(request, session) -> UberAdmin`
- contest context and role helpers used by Web route modules

Notes:
- the global gate allows only `/`, `/contests`, `/contests/past`, `/login`,
  `/c/{slug}/login`, `/health`, `/favicon.ico`, `/assets/*`, `/static/*`,
  `/problem-set/*`, and `/announcements/*` (the public announcement board)
  without a valid session cookie
- route-local role checks remain the authoritative authorization layer

---

## `web/middleware/auth_token_refresh.py`

Purpose:
- validate `noca_access_token` at most once per request
- rotate active web-session JWTs when the remaining lifetime reaches half of
  `NOCA_JWT_EXPIRE_SECONDS`
- expire the auth cookie when the optional absolute session cap is exceeded

Canonical location:
- `web/middleware/auth_token_refresh.py` (middleware, not a service)

Main entrypoints:
- `AuthTokenRefreshMiddleware`

Notes:
- stores the validated token result on `request.state.validated_token`
- downstream auth dependencies must explicitly mark the request as refreshable
  only after they resolve a real actor from the database
- skips refresh on login and logout endpoints to avoid overwriting route-owned
  cookie changes

---

## `contest_removal_service.py` and `contest_removal_files.py`

Purpose:

- permanently remove one inactive contest across PostgreSQL, Valkey, and the
  problem-artifact filesystem
- collect the complete contest-owned identifier set before cleanup, including the
  per-team announcement read markers in `clarification_reads`, which are deleted
  explicitly just before their clarifications rather than left to the FK cascade,
  so the deletion graph stays readable and testable
- quarantine PDF, Markdown, and test-case artifacts until the database
  transaction commits
- retain global languages, global problem categories, UberAdmins, unrelated
  contests, and existing security events

Main entrypoints:

- `remove_inactive_contest(session, *, contest_id, actor_uberadmin_id,
  actor_uberadmin_label=None, valkey_runtime, statement_dir, testcase_dir) ->
  ContestRemovalResult` locks
  and rechecks the contest, strictly purges its runtime state, quarantines its
  files, deletes its database graph, writes one sanitized warning audit event,
  and commits
- `quarantine_problem_files(problem_ids, *, statement_dir, testcase_dir) ->
  ContestFileQuarantine` moves guarded artifacts to same-root quarantine
  directories for rollback or final erasure
- `ContestRemovalTargets` and `ContestRemovalResult` expose typed cleanup input
  and result data
- `ContestRemovalNotFoundError`, `ContestRemovalActiveError`, and
  `ContestRemovalError` expose actionable failure classes

Notes:

- the route must reconfirm the acting UberAdmin password before calling the
  service; passwords never enter the service or audit record
- Valkey cleanup runs before files or PostgreSQL are changed and must verify
  that queue entries, job hashes, locks, buffered commands, and scoreboard
  variants are absent
- failures before commit roll back PostgreSQL and restore every quarantined
  artifact; successful commits erase the quarantine
- the retained `contest_deleted` security event stores only the acting
  UberAdmin ID, that UberAdmin's username as the event `actor_label` (so the
  viewers name the actor instead of showing an opaque id), and the deleted
  contest ID

---

## `contest_service/`

Purpose:
- active and inactive contest lookup
- contest dashboard grouping
- contest metadata validation/update
- past contest deactivation
- contest-level (global) animator medal-cutoff updates
- contest creation with initial owner admin
- contest clock payload generation
- chief judge assignment validation

Internal structure:
- `models.py` — shared DTOs and normalized metadata state
- `queries.py` — contest and language lookup helpers
- `authorization.py` — admin access and chief-judge guard helpers
- `forms.py` — `ContestMetadataInput` and form serialization helpers
- `presentation.py` — metadata view-building, dashboard grouping, and clock/status helpers
- `validation.py` — metadata field validation and in-memory update staging
- `updates.py` — persisted metadata/language update flows
- `creation.py` — create-contest flow and blank create form defaults
- `metadata.py` — compatibility re-export module that preserves the previous import surface

Main types:
- `ContestMetadataInput`
- `ContestMetadataView`
- `ContestMetadataResult`
- `ContestDashboardGroups`
- `ContestCreationResult`
- `ContestRulesSummary`

Main entrypoints:
- `get_contest_by_slug(slug, session) -> Contest`
- `get_contest_by_id(session, contest_id) -> Contest | None`
- `get_inactive_contests(session) -> list[Contest]`
- `deactivate_past_contest(session, contest_id) -> Contest | None`
- `update_contest_global_medals(session, contest, gold, silver, bronze) -> Contest` — thin wrapper over `shared.services.animator_access_service.update_contest_global_medals`, mirroring `site_service.update_site_medals`. All-or-nothing: pass three ordered positive ints, or three `None`s to disable the contest's global medals. Does **not** commit, so the caller can batch the global and per-site cutoffs into one transaction.
- `ensure_contest_admin_or_uberadmin(actor) -> None`
- `contest_metadata_validation_errors(exc) -> list[str]`
- `build_contest_metadata_form_data(contest) -> dict[str, Any]`
- `build_contest_metadata_view(contest, *, site_names=None) -> ContestMetadataView`
- `build_contest_metadata_view_with_sites(session, contest) -> ContestMetadataView`
- `contest_status_label(contest) -> str`
- `build_contest_clock_payload(contest) -> dict[str, int | str]` — returns the
  server time and the contest's start, end, scoreboard-freeze, and
  answer-silence boundaries as absolute epoch milliseconds, plus the current
  `upcoming`, `running`, `frozen`, `silence`, or `past` state. The browser uses
  the boundary fields to advance the persistent navbar phase between polls.
- `build_contest_rules_summary(contest) -> ContestRulesSummary` — template-ready
  view of the contest's public rules for the dashboard banner: printing
  availability, duration and local start/end, scoreboard-freeze and
  answer-silence moments, wrong-answer penalty minutes, and the penalizing
  verdict list (mirrors `shared.services.scoreboard_projection`, honoring
  `ce_adds_penalty` and `accept_pe`).
- `get_active_contests_grouped(session) -> ContestDashboardGroups`
- `sort_past_contests_recent_first(contests) -> list[Contest]` — pure helper sorting `ContestDashboardGroups.past_contests` by `(end_time, login_slug)` descending; used by the `/` and `/contests/past` gateway pages to preview and paginate past contests most-recently-ended first
- `validate_contest_metadata_update(contest, *, metadata, site_names=None) -> ContestMetadataResult`
- `update_contest_metadata(session, contest, actor, *, metadata, site_names, language_ids) -> ContestMetadataResult`
- `build_blank_contest_form(default_start_time) -> dict[str, Any]`
- `create_contest_with_owner(session, *, creator_username, contest_name, login_slug, metadata, owner_username, owner_fullname, language_ids) -> ContestCreationResult`
- `ensure_contest_has_sites(session, contest) -> None`
- `validate_chief_judge_assignment(session, contest, user_id) -> list[str]` — validates that a user is a JUDGE member of the contest; returns errors list (empty = valid)
- `list_contest_judge_ids(session, contest, *, exclude_user_id=None) -> list[str]` — ids of the contest JUDGE users, ordered by `fullname, username`
- `ensure_chief_judge_reassignable(session, contest, user) -> None` — raises `ChiefJudgeInvariantError` (a `ValueError`) when `user` is the current chief judge and two or more other judges remain, so there is no unambiguous successor; call **before** demoting or removing a user
- `reconcile_chief_judge(session, contest, *, excluded_user_id=None) -> None` — restores the chief-judge invariant after a role change or removal: keeps a still-valid chief judge, promotes the contest's only judge, clears the assignment when no judge is left; call **after** the change, before commit

**Chief-judge invariant**: a contest with at least one JUDGE must have a chief judge, and a
contest with no judge has none. `contest_user_service.crud` enforces it on every role
mutation (`create_user`, `update_user`, `remove_user`, and therefore the batch importer), and
`judging_service.chief_judge` refuses to clear the assignment while the contest has judges —
the role can only be handed over to another judge. Contests that already violated the
invariant are not backfilled; they are repaired the next time their users are touched.

Reuse this module when:
- you need contest metadata rules
- you need contest-scoped site list synchronization inside the metadata screen
- you need contest creation logic
- you need contest lifecycle labels or clock payloads
- you need active contests grouped for dashboards or public listing
- you need inactive contest listing or past contest deactivation for UberAdmin flows
- validating a chief judge assignment candidate

Do not reimplement:
- timing validation
- timezone/start-time validation
- contest metadata/site synchronization and the "at least one site" rule
- contest creation + owner bootstrap
- chief judge eligibility checks

Notes:
- `ContestMetadataView` now carries `site_names` for the metadata page's editable site list.
- `create_contest_with_owner` bootstraps each new contest with a default `"Main"` site and inserts the `contest_languages` rows in the same transaction. `language_ids` must be non-empty.
- `build_blank_contest_form` defaults a 300-minute contest to stop scoreboard updates at minute 280 and answers at minute 290, matching the create wizard's duration-relative offsets.
- `update_contest_metadata` now also synchronizes `contest_languages` while the contest is upcoming. Removed languages trigger transactional cleanup of stale `problem_language_limits` rows across that contest's problems; newly added languages rely on fallback problem limits until explicit overrides are set.
- contest metadata includes `allow_print_requests`, which remains editable even while the contest is running/past (explicit exception to the general lock behavior for non-timing fields).
- contest metadata accepts a blank `contest_url`; create and edit persist an empty string when no website is provided.
- `ensure_contest_has_sites` is used before `start-now`; contests cannot be started or metadata-edited without at least one site.
- `get_contest_by_slug` only returns active contests. Contest-scoped routes and contest login therefore reject inactive contests with 404; UberAdmin inactive-list views use `get_inactive_contests` instead.
- `deactivate_past_contest` commits the `active=False` change only for active contests whose end time has passed; it returns `None` for live, upcoming, missing, or already inactive contests.
- forced `end-now` contest termination shortens `duration_minutes` and clamps dependent timing fields so `stop_updating_scoreboard <= stop_answers_after <= duration` and timeout fields remain strictly below duration.

---

## `judging_service/`

Purpose:
- chief-judge assignment, removal, and verdict override
- submission review lock lifecycle (acquire, release, timeout)
- human verdict confirmation for non-`autojudge_only` contests
- submission rejudging
- admin bulk rejudging for persisted problem-limit-change batches
- balloon task creation after accepted verdicts
- submission judging-history assembly

Internal structure:
- `types.py` — request/response DTOs and Pydantic models
- `chief_judge.py` — chief-judge assignment, listing, and removal rules
- `review.py` — review lock acquisition/release and human confirmations
- `verdicts.py` — verdict override flow
- `history.py` — audit-log to history-response assembly
- `rejudge.py` — rejudge queuing and balloon side effects

Main types:
- `VerdictOverrideRequest`
- `VerdictOverrideResponse`
- `JudgingHistoryEntry`
- `JudgingHistoryResponse`
- `ContestSetChiefJudgeRequest`
- `ChiefJudgeAdminPanel` — named tuple with `current_chief_judge`, `judges`, and `can_remove`
- `JudgingServiceError` — base class
- `JudgmentNotDoneError`
- `SameVerdictError`
- `ChiefJudgeRemovalBlockedError`
- `AlreadyConfirmedError`
- `JudgmentNotReadyError`
- `NoFinalVerdictError` — rejudge attempted when active judgment has no final verdict
- `ReviewAlreadyLockedError`
- `ReviewLockUnavailableError`
- `ReviewNotHeldByActorError`

Main entrypoints:
- `acquire_submission_review(session, judgment, actor, contest, lock_client) -> SubmissionJudgment` — JUDGE or ADMIN; acquires a Valkey TTL lock keyed by contest and judgment id; blocks if actor already confirmed this judgment or if `final_verdict` is already set
- `release_submission_review(session, judgment, actor, contest, lock_client, *, force=False) -> None` — releases the Valkey review lock; `force=True` allows privileged force-release
- `set_chief_judge(session, contest, judge_id, requesting_user) -> Contest` — owner or UberAdmin only; validates the selected user via `validate_chief_judge_assignment`; a `None` id clears the assignment and is refused with `400` while the contest still has judges (chief-judge invariant)
- `list_contest_judges(session, contest) -> list[User]` — returns contest judges ordered by full name then username
- `get_chief_judge_admin_panel(session, contest) -> ChiefJudgeAdminPanel` — returns current chief judge, assignable judges, and whether removal is allowed (`can_remove` is only true for a judgeless contest with no override history)
- `remove_chief_judge(session, contest, requesting_user) -> Contest` — owner or UberAdmin only; blocked once the current chief judge has executed any verdict override in the contest, and refused with `400` while the contest still has judges (chief-judge invariant)
- `override_verdict(session, submission_id, new_verdict, reason, actor, contest) -> VerdictOverride` — chief judge or ADMIN; contest-scoped DONE-only override; creates the `VerdictOverride` row and relies on the submission model hook to update `SubmissionJudgment.final_verdict`
- `get_judging_history(session, submission_id, requesting_user, contest) -> JudgingHistoryResponse` — assembles audit-derived auto/rejudge rows plus explicit override rows, excluding status-only transitions
- `rejudge_submission(session, submission_id, actor, contest, lock_client=None) -> SubmissionJudgment` — chief judge, ADMIN or UBERADMIN; supersedes the active judgment, force-releases its Valkey review lock when available, and creates a new `QUEUED` judgment
- `queue_limit_change_batch_rejudges(session, batch, contest, actor, lock_client, *, language_id=None) -> list[SubmissionJudgment]` — ADMIN/UBERADMIN only; re-reads the batch rows `FOR UPDATE` (so two overlapping requests serialize and the loser finds them already `QUEUED`), requeues the pending ones and marks drifted rows as `STALE`
- `confirm_verdict(session, submission_id, verdict, judge, contest, lock_client) -> HumanSubmissionConfirmation` — JUDGE or ADMIN; creates a human confirmation for the active `DONE` judgment; requires the caller to hold the review lock when Valkey is available; the submission model hook derives `final_verdict` from confirmations
- `can_confirm_verdict(actor, contest) -> bool` / `confirmation_is_decisive(actor, contest) -> bool` / `can_override_verdict(actor, contest) -> bool` / `is_chief_judge(actor, contest) -> bool` — the single source of truth the routes and templates share. The chief-authority pair (chief judge **and contest admins**, from `chief_judge_permissions.py`) cast decisive confirmations and may override; uberadmins may do neither, since `human_submission_confirmations.judge_id` and `verdict_overrides.overridden_by` are foreign keys into `users`
- `create_balloon_task_if_needed(session, submission_id, contest) -> Task | None` — idempotent balloon creation after accepted final verdict; skipped if the scoreboard is frozen or a balloon-like task already exists for the same (team, problem) pair; creates `FIRST_BALLOON` for the earliest accepted submission on the problem and `BALLOON` otherwise

Reuse this module when:
- assigning or changing `contest.chief_judge_id`
- implementing any manual post-DONE verdict override flow
- implementing manual verdict confirmation for non-`autojudge_only` contests
- rendering first-solve balloon highlights in tasks, runs, or scoreboard views
- implementing the review lock lifecycle (acquire/release/force-release)
- presenting or returning authoritative submission judging history
- triggering rejudges
- queuing bulk rejudges from running-contest problem-limit changes

Do not reimplement:
- chief-judge validation and authorization
- chief-judge removal guard based on existing override activity
- active-judgment selection rules for override
- guard logic that blocks repeated confirmation by the same judge
- review lock acquisition, release, and degraded-mode enforcement via Valkey
- history filtering that drops status-only audit rows and suppresses duplicate override audit rows
- balloon task deduplication logic

Notes:
- services flush, never commit; routes own `await session.commit()`
- `override_verdict` does not publish Valkey events; callers must commit first, then call `publish_verdict(...)`
- `confirm_verdict` raises `JudgmentNotReadyError` when the active judgment is not `DONE`
- `confirm_verdict` raises `AlreadyConfirmedError` when the same judge tries to confirm the same judgment twice
- `confirm_verdict` raises `ReviewNotHeldByActorError` when the caller does not hold the review lock
- `confirm_verdict` raises `DecisiveConfirmationExistsError` when a decisive (chief or admin) confirmation already settled the judgment
- `remove_chief_judge` raises `ChiefJudgeRemovalBlockedError` when the current chief judge has already executed an override in the contest
- `rejudge_submission` raises `NoFinalVerdictError` when the active judgment has no final verdict yet
- `queue_limit_change_batch_rejudges` is intentionally idempotent at the batch-row level: rows are processed once into `QUEUED` or `STALE`, under a row lock so concurrency cannot break that
- the routes that call it add the throttled password reconfirmation, the batch-wide per-problem cooldown (`shared/services/rejudge_cooldown.py`, `NOCA_WEB_REJUDGE_COOLDOWN_SECONDS`) and the warning-severity `admin_action` audit row; none of that lives in the service
- override authority lives in the `web.models.submission` hook; this service only inserts `VerdictOverride`
- TEAM callers are forbidden from judging-history and review access; STAFF may fetch history JSON but cannot access the HTML review page

---

## `scoreboard/`

Purpose:
- compute, cache, and serve ICPC-style contest standings
- apply contest freeze/final-release behavior consistently across roles

Internal structure:
- `service.py` — DB loading and Valkey cache orchestration (the only implementation file left in the package)
- `__init__.py` — re-exports the shared DTOs (`ProblemResult`, `TeamStanding`, `ScoreboardSnapshot`) for backwards compatibility
- the scoreboard DTOs, snapshot serialization, and the pure `compute_icpc` logic live in `shared/services/scoreboard_projection.py` (see `docs/SHARED_SERVICES.md`); this package only adapts web queries, caching, and orchestration

Ranking rules:
- teams rank by most problems solved, then lowest total time, then the earliest
  last accepted submission (`TeamStanding.last_accepted_minutes`); teams equal on
  all three share a rank number and the next distinct team takes its
  position-based rank
- the rules themselves live in `shared/services/scoreboard_projection.py` and are
  documented once under **Ranking rules** in `docs/SHARED_SERVICES.md`; the
  animator applies the identical rules through the same function

Main types:
- `ProblemResult`
- `TeamStanding`
- `ScoreboardSnapshot`
- `ScoreboardService`

Main entrypoints:
- `ScoreboardService.compute_standings(contest, viewer_role, db) -> ScoreboardSnapshot` — computes a fresh snapshot from the database; `viewer_role` is `"admin"`, `"judge"`, or `"public"`
- `ScoreboardService.get_cached_or_compute(contest, viewer_role, db, valkey) -> ScoreboardSnapshot` — role-aware scoreboard with freeze rules and Valkey caching; uses three cache variants: `:full` (admin/judge, 5 s TTL), `:public` (team/staff, 180 s TTL), and `:frozen` (public viewers during freeze, no TTL)
- `ScoreboardService.get_or_compute_final(contest, db, valkey) -> ScoreboardSnapshot` — released final scoreboard with all results visible and permanent cache entry; also deletes the `:frozen` key
- `ScoreboardService.invalidate_cache(contest_id, valkey) -> None` — invalidates the `:full` and `:public` cache keys via `shared.services.scoreboard_cache`

Reuse this module when:
- rendering contest scoreboard pages
- releasing final standings after the contest ends
- invalidating scoreboard cache after verdict events
- needing authoritative scoreboard snapshots instead of route-local ranking code

Do not reimplement:
- scoreboard freeze visibility rules
- cache-key selection for admin/judge vs public/final/frozen views
- ICPC ranking aggregation logic

---

## `scoreboard_display_cache.py`

Purpose:
- cache what the scoreboard page renders *around* the snapshot -- team display
  names, each team's site, and the contest's site list -- so a page that hits
  the snapshot cache does no database work at all

Why it exists:
- the snapshot has been Valkey-cached for a long time, but the two queries
  behind that decoration ran on **every** hit, including cached ones. The
  scoreboard is the most-polled page in a live contest (one HTMX refresh per
  open browser every 30 s), and each hit paid a team scan with an eager site
  load plus a site select
- it is a separate cache entry rather than extra fields on `ScoreboardSnapshot`
  because that snapshot is the shared projection the animator also reads;
  widening it for one page's template would change a contract two modules
  deploy against

Main types:
- `ScoreboardSite` — the `id` and `sitename` the page renders
- `ScoreboardDisplayData` — team names, team-to-site ids, team-to-site names, sites

Main entrypoints:
- `get_scoreboard_display_data(session, contest_id, valkey) -> ScoreboardDisplayData`
- `scoreboard_display_key(contest_id) -> str` — `noca:scoreboard:display:{contest_id}`

Contract:
- 5 s TTL, matching the tightest scoreboard TTL, so a roster edit surfaces as
  fast as the standings it belongs to while the entry still absorbs the poll
  storm
- staleness is harmless by construction: the template falls back to the
  standing's own `team_fullname` for a name it does not find, and a team missing
  from the site map is one the snapshot it decorates has not published either
- reads and writes are best-effort; an unreadable payload is treated as a miss
  and a Valkey failure falls back to the queries, exactly as the snapshot cache
  does

---

## `problem_list_service.py`

Purpose:
- compute the per-problem solving rate and the current team's own status for the participant-facing contest problem list (`contest/problems_list.html`)

Internal structure:
- single module, no submodules

Main types:
- `ProblemCardData` — one card's worth of data: the `Problem` row, its display `label`, `solving_rate` (int percentage, 0 when the contest has no teams), and `viewer_status` (`"solved"` / `"pending"` / `"attempted"` / `"untried"` / `None`)

Main entrypoints:
- `build_problem_cards(problems, snapshot, actor) -> list[ProblemCardData]` — combines contest problems with a `ScoreboardSnapshot` (see `scoreboard/` above); `solving_rate` is `round(teams_solved / total_teams * 100)` over all teams in the snapshot, so it automatically respects the same freeze visibility the caller requested from `ScoreboardService`; `viewer_status` is populated only when `actor` is a `RoleEnum.TEAM` user with a standing in the snapshot — staff, judge, admin, and uberadmin viewers always get `None`

Reuse this module when:
- rendering any page that needs a per-problem solving rate or a team's own per-problem status, instead of querying submissions/judgments directly

Do not reimplement:
- per-problem AC-team counting or percentage rounding
- team-standing lookup by actor id

---

## `public_rate_limits.py`

Purpose:
- per-IP fixed windows for Web's two anonymous read routes, over the shared
  `shared.services.request_rate_limit` primitive

Provides:
- `enforce_problem_set_rate_limit(request)` — bucket `web:problem-set`
  (`NOCA_WEB_PUBLIC_RATE_LIMIT_PROBLEM_SET_MAX_REQUESTS` / `_WINDOW_SECONDS`,
  default 10 per 600 s), route-level on `GET /problem-set/{slug}.zip`
- `enforce_live_feed_rate_limit(request)` — bucket `web:live-feed`
  (`NOCA_WEB_PUBLIC_RATE_LIMIT_LIVE_FEED_MAX_REQUESTS` / `_WINDOW_SECONDS`,
  default 120 per 60 s), route-level on `GET /c/{slug}/live/feed.json`
- `PROBLEM_SET_LIMITER` / `LIVE_FEED_LIMITER` — the module-level in-memory
  fallbacks (tests reset them; `tests/web/conftest.py` does so autouse)

Behavior notes:
- both buckets share `NOCA_WEB_PUBLIC_RATE_LIMIT_ENABLED` and
  `NOCA_WEB_PUBLIC_RATE_LIMIT_TRUSTED_CIDRS`; policies are rebuilt from
  `settings` on every call
- installed as route-level dependencies so they run *before* the contest gate
  query: the answer depends on the client IP alone, so a `429` never confirms
  a slug or a release state
- `/c/{slug}/live/events` is deliberately **not** in either bucket: it is a
  long-lived stream and is bounded by `sse_limits.py` instead

---

## `sse_limits.py`

Purpose:
- hold the `web:sse` concurrent-connection slots for the two Web event streams
  over the shared `shared.services.sse_connection_limit` lease

Provides:
- `sse_slot_policy()` — rebuilt from `settings` on every call (`SSE_LIMIT_ENABLED`,
  `SSE_MAX_PER_IP`, `SSE_MAX_PER_USER`, `SSE_CONNECTION_TTL_SECONDS`,
  `SSE_TRUSTED_CIDRS`) so tests can monkeypatch the knobs
- `enforce_live_events_slots(request)` — yield dependency holding a per-IP slot
  for `GET /c/{slug}/live/events`
- `enforce_runs_events_slots(request, ctx)` — yield dependency holding a per-IP
  and a per-actor slot (`ctx.actor.id`, `User` or `UberAdmin`) for
  `GET /c/{slug}/runs/events`; depends on the same `get_contest_context` the
  route declares, so FastAPI resolves it once

Behavior notes:
- both are *yield* dependencies: teardown runs after the streamed response
  finishes, i.e. on client disconnect, and that is what releases the slots
- refusal is `429` with `Retry-After: 5`; a Valkey outage admits the stream
- lives in `services/` rather than `dependencies.py` to keep that module small

---

## `live_feed_service.py`

Purpose:
- build the public contest live-feed snapshot (last 20 finalized TEAM submissions, newest first)
- own the scoreboard-blackout anonymization for the feed

Main types:
- `LiveFeedRow` — one feed row (timestamp, team, problem label/name, language, verdict, badge class, `frozen` flag)
- `ContestLiveFeedSnapshot` — `rows` plus `limit` and `has_more` overflow metadata
- `CONTEST_LIVE_FEED_LIMIT` — configured row cap (20)

Main entrypoints:
- `build_contest_live_feed_snapshot(session, contest) -> ContestLiveFeedSnapshot` — single SQL join against the latest non-superseded judgment with `final_verdict IS NOT NULL`, fetching `CONTEST_LIVE_FEED_LIMIT + 1` rows to set `has_more`; when the scoreboard is frozen, rows whose `timestamp_seconds` exceed `stop_updating_scoreboard * 60` have their team name (`"—"`) and verdict masked **server-side** before the snapshot is built, so no post-freeze identity or verdict ever reaches the JSON snapshot or the browser

Reuse this module when:
- rendering or refetching the public `/c/{slug}/live` feed (`contest_live_feed.py`)

Do not reimplement:
- the blackout anonymization rule for post-freeze rows
- verdict badge class selection (use `assorted_utils.contest_verdict_badge_class`)

---

## `password_confirm_throttle.py`

Purpose:
- one throttled budget for the seven routes that ask an authenticated actor to
  reconfirm their password (`/profile/password`, contest `start-now` /
  `end-now`, uberadmin contest `remove`, uberadmin contest `export` with
  password hashes, and the two uberadmin lockout unlocks under
  `/uberadmin/lockouts`), so none of them is an unthrottled online password
  oracle

Provides:
- `confirm_password(request, session, *, actor, password, action) -> PasswordConfirmResult`
  — the one call every route makes. Builds the identity (`module="web"`,
  `action="password-confirm"`, identifier `user:{id}` / `uberadmin:{id}`, plus
  the client IP), checks the lockout **before** the hash, then verifies with
  `password_service.password_matches`. A mismatch is counted
  (`record_auth_failure`) and written as an `auth_failure` security event
  (`metadata.action="password_confirm"`, `metadata.route=<action>`); a lockout
  is written as `auth_throttle_lockout` at warning severity; a match resets the
  budget immediately, even if the route's later validation fails
- `PasswordConfirmResult(ok, locked, retry_after_seconds)`
- `render_lockout(request, *, retry_after_seconds, back_url, back_label)` — the
  shared `429` page (`errors/too_many_attempts.html`) with `Retry-After`
- `auth_rate_limit_settings()` — the `AuthRateLimitSettings` builder from
  `NOCA_AUTH_RATE_LIMIT_*` + `JWT_SECRET_KEY`, shared with `/login`
- `PASSWORD_CONFIRM_LIMITER` — the in-memory fallback (`tests/web/conftest.py`
  resets it)

Behavior notes:
- same caps as login (20 per IP, 5 per account, 15-minute window and lockout),
  and **one** bucket across the seven routes, so rotating routes, IPs, or actors
  cannot multiply the allowance
- locked means refused before the password is checked: no protected mutation,
  export, or removal starts, and the correct password does not help until the
  lockout lifts
- an ordinary wrong password keeps each route's existing response; only the
  lockout is rendered by this module
- **`confirm_password` owns the transaction for its own writes**: on a
  mismatch or a lockout it inserts the security-event row on the supplied
  session and **commits** it, so the record survives whatever the route does
  next. Call it before staging any protected change on that session — anything
  pending is committed with the event. The callers all reconfirm first and
  mutate only on `ok=True`; a new caller must keep that order or pass a
  dedicated session. A match writes and commits nothing

---

## `lockout_admin_service.py`

Purpose:
- Web's vocabulary for the administrative lockout reset: which raw identifiers
  Web's throttle buckets hash for one login, which key modules an UberAdmin may
  clear, and which contests an unlock may be scoped to

Provides:
- `WEB_LOCKOUT_MODULES = ("web", "animator")` — an UberAdmin clears the Web
  buckets and the animator's operator-token lockout, which has no admin
  surface of its own; Arena buckets are never touched
- `contest_login_identifier(contest_id, raw) -> str | None` — the raw
  identifier the `web`/`contest-login` bucket hashes: `{contest_id}:{username}`.
  The **single** definition of that shape, called by `web/routes/auth.py` when
  it throttles a contest login and by `resolve_login` when it unlocks one, so
  the producer and the resolver cannot drift. It strips the name *before*
  prefixing — `normalize_identifier` strips and casefolds the whole string, so
  an unstripped `"  Team042 "` would keep its inner spaces and hash to something
  no unlock could rebuild — and returns `None` for a blank name, which would
  otherwise mint an account bucket where today there is none
- `active_contest_choices(session) -> list[ContestChoice]` — the contests an
  operator may pick as an unlock scope, reusing the UberAdmin dashboard's own
  `get_active_contests_grouped` (all three collections flattened, so "active"
  means exactly what the dashboard means, including ended-but-not-deactivated
  contests). Only an active contest can mint a `contest-login` lock, because
  `get_contest_by_slug` refuses an inactive one. Labels carry the login slug
  beside the name, since names are not unique and slugs are
- `resolve_login(session, raw, *, contest_id=None) -> ResolvedLogin` — turns a
  typed login **within one scope** into the hashes to clear. With `contest_id`
  given: only that contest's `{contest_id}:{username}` bucket and that
  contest's matching users' `user:<id>` buckets. The bare-name hash (the
  UberAdmin `login` bucket) and `uberadmin:<id>` are deliberately left alone —
  an UberAdmin is not a contest, and reaching a global bucket from a
  contest-scoped unlock would make "leave the other contests locked" false.
  With `contest_id=None` (*all contests*): the bare name, the UberAdmin's own
  bucket, one scoped identifier per contest carrying the name, and every
  matching `user:<id>`. `ResolvedLogin.summary` states what matched and
  `.scope_label` how the scope reads, both for the audit row
- `ResolvedLogin.contest_labels` — `hash -> contest label` for each
  `contest-login` bucket the resolution built, so the status panel can name the
  contest a lock belongs to. Hashes are one-way, so a lock whose hash is in no
  map stays unlabelled rather than guessed
- `ALL_CONTESTS_SCOPE` — the form value naming the deliberate wide unlock
- `subject_for_ip(ip)`, `subject_for_hashes(hashes)` — Web-scoped
  `LockoutSubject` builders for `shared/services/auth_lockout_admin.py`

Behavior notes:
- the wording and the audited flow live in the shared `auth_lockout_flow.py`,
  so Web and Arena record an unlock identically
- **the scope is chosen, never defaulted.** The route refuses a typed login
  with a blank or unknown scope rather than picking one, because the two
  mistakes are not symmetric: a silent *all contests* default releases every
  contest and flashes exactly like the narrow unlock, while a silent
  single-contest default leaves locks standing the operator believes gone. The
  refusal lives in the route, not in the HTML `required`

---

## `rate_limit_service.py`

Purpose:
- enforce per-team submission rate limits using a PostgreSQL sliding-window count
- enforce the independent per-actor budget for non-scoring solution-test runs
- enforce the per-team write throttles on SOS tasks, print tasks, and clarifications

Two rule shapes, both counting committed rows so a refused, duplicate, or invalid request never
consumes budget:

- **rolling window** — rows created in the last `window_seconds`; refusal carries `next_allowed_at`,
  the moment the oldest in-window row falls out, so the caller can name a time
- **open count** — rows still unfinished (`tasks.finished_at IS NULL`) or unanswered
  (`clarifications.answered_at IS NULL`); refusal carries no time, because only staff action
  releases it

Every maximum treats `0` (or less) as **unlimited** and short-circuits before the lock is taken.

Main entrypoints:
- `acquire_submission_rate_lock(session, team_id)` — acquires a transaction-scoped PostgreSQL advisory lock keyed on `team_id`; no-op on non-PostgreSQL dialects so SQLite test fixtures work without patching
- `check_submission_rate_limit(session, team_id, window_seconds, max_submissions) -> tuple[bool, datetime | None]` — acquires the lock, counts submissions in the rolling window, returns `(True, None)` if within limit or `(False, next_allowed_at)` when the limit is reached
- `check_solution_test_rate_limit(session, actor_key, window_seconds, max_runs) -> tuple[bool, datetime | None]` — the same shape over `solution_test_runs`. `actor_key` is `"user:<id>"` or `"uberadmin:<id>"`, and the advisory lock is namespaced (`hashtext('solution_test:' || actor_key)`) so it neither collides across the two actor id spaces nor serializes against team submissions. A judge's tests never consume a team's allowance.
- `check_sos_task_rate_limit(session, team_id, window_seconds, max_tasks) -> tuple[bool, datetime | None]` — the window over `tasks` scoped to `type = SOS`, so balloon tasks created by the judging path never consume a team's budget. Namespace `sos_task`.
- `check_open_sos_task_limit(session, team_id, max_open) -> tuple[bool, int]` — unfinished SOS tasks the team holds; returns the count so the caller can name it. Shares the `sos_task` namespace with the window check, so both counts are taken under one lock (`pg_advisory_xact_lock` is re-entrant in a transaction).
- `check_print_task_rate_limit(session, team_id, window_seconds, max_tasks) -> tuple[bool, datetime | None]` — the same window scoped to `type = PRINT`. Namespace `print_task`.
- `check_clarification_rate_limit(session, team_id, window_seconds, max_requests) -> tuple[bool, datetime | None]` — the window over `clarifications`, excluding announcements (which are stored with their author as `team_id`). Namespace `clarification`.
- `check_open_clarification_limit(session, team_id, max_open) -> tuple[bool, int]` — unanswered, non-hidden clarifications the team holds. Hidden rows are excluded because hiding is how a judge dismisses a question: such a row is never answered, so counting it would block the team permanently.

The services own the enforcement and translate a refusal into a typed error —
`TaskRateLimitError` / `OpenSosTaskLimitError` (`task_service`) and
`ClarificationRateLimitError` / `TooManyUnansweredClarificationsError`
(`clarification_service`) — which the routes turn into a danger flash and a 303. Two classes per
domain, not one with an optional timestamp: the two refusals produce structurally different copy.

Reuse this module when:
- adding rate limiting to any web submission endpoint
- adding a per-actor cap to any web write endpoint

Do not reimplement:
- the advisory-lock + count pattern (use this service directly)
- the namespaced-lock trick for a second budget over a different table
- the `0 = unlimited` short-circuit

Notes:
- `hashtext` is 32-bit, so a namespaced key can collide with another (or with the legacy
  unnamespaced submission key). The only consequence is two unrelated actors serializing against
  each other, never a wrong decision.
- The lock is held from the check until the caller's transaction ends, which is exactly the
  check-then-INSERT window that must be serialized. Refusal paths must not commit.

---

## `solution_test_service.py`

Purpose:
- create, list, and enqueue non-scoring solution-test runs for JUDGE/ADMIN/UBERADMIN actors

Runs live in `solution_test_runs` / `solution_test_case_results` rather than behind a flag on
`submissions`. A flag would make correctness depend on every present and future consumer
remembering `WHERE is_test = false`, and one missed filter silently corrupts standings.
Separate tables make leakage into standings, balloons, Runs, reports, feeds, and exports
**structurally impossible** rather than test-enforced.

The run status reuses `JudgmentStatus` (`QUEUED/DISPATCHED/JUDGING/DONE/FAILED`) instead of a
third enum: the status machine mirrors submissions so the autojudge state machine is reused
verbatim. This deliberately couples staff tooling to submission status semantics; revisit only
if the reuse becomes painful.

The judgeability gate is the same shared contract contestant submissions use
(`shared.services.problem_judgeability`), fed by
`problem_service.load_contest_problem_judgeability_facts`, so a staff test run
and a real submission agree on whether a problem is ready.

Main types:
- `SolutionTestRateLimitError(next_allowed_at)` — the actor exceeded their independent budget
- `RUNS_PER_PAGE = 50`

Main entrypoints:
- `actor_key(actor) -> str` — `"user:<id>"` or `"uberadmin:<id>"`; the two are separate id spaces
- `create_solution_test_run(session, actor, contest, *, problem_id, language_id, source_code, source_hash, source_size, rate_limit_window_seconds, rate_limit_max_runs) -> SolutionTestRun` — rate-limits, validates judgeability, snapshots `triggered_by_label`, inserts and flushes. Does **not** commit; the caller owns the transaction
- `get_solution_test_run(session, contest, run_id, *, restrict_to_user_id=None) -> SolutionTestRun | None` — `restrict_to_user_id` scopes a JUDGE to their own runs, so the route can answer 404 rather than 403
- `list_solution_test_runs_paginated(session, contest, *, page, per_page, problem_id=None, restrict_to_user_id=None) -> Pagination[SolutionTestRun]`
- `enqueue_solution_test_job(valkey, run, contest, *, priority)` — pushes a `SolutionTestJob` onto the ordinary contestant queues with the same `priority=contest.is_running` rule

Reuse this module when:
- adding any staff-facing "run this code for real, but do not score it" workflow

Do not reimplement:
- the contest scope (there is no `contest_id` column — join `problems`, as profiling does)
- the judgeability gate (validator available, test cases exist, outputs present)

---

## `submission_service.py`

Purpose:
- list submissions visible to the current actor
- create a submission and its initial queued judgment atomically
- build per-team submission archive ZIPs for finished contests

Main types:
- `DuplicateSubmissionError`
- `SubmissionRateLimitError`
- `SubmissionFilters` — optional Problem, Team, Autojudge verdict, and Final verdict predicates for server-side Runs filtering

Main entrypoints:
- `list_submissions(session, contest, actor, sort_by="time_desc", *, filters=None) -> list[Submission]` — applies role visibility, optional `SubmissionFilters`, and Time or Problem SQL ordering, with eager-loaded team, team site, judgments, judge confirmations, judge sites, overrides, and reviewer site
- `list_submission_teams(session, contest) -> list[User]` — returns teams that have contest submissions for an independently populated Team filter
- `create_submission(session, actor, contest, problem_id, language_id, source_code, source_hash, source_size, *, rate_limit_window_seconds=60, rate_limit_max_submissions=3) -> tuple[Submission, SubmissionJudgment]` — checks the per-team rate limit (raises `SubmissionRateLimitError` if exceeded), refuses the submission with a `ValueError` when the problem cannot be judged, via the shared `shared.services.problem_judgeability` contract decided from the problem's **stored strategy** (a standard problem needs cases that all carry an expected output; an interactive one needs an active `VALID` validator and at least one secret case; the reserved output checker is never judgeable), creates `Submission` plus initial `SubmissionJudgment(status=QUEUED)`, and inserts the explicit WEB audit row
- `build_team_submissions_zip(session, contest, team, *, statement_dir) -> tuple[str, bytes]` — builds a ZIP archive of a team's submissions organized by problem with statement PDFs/MDs, AC/PE solutions in an `AC/` folder, and other submissions in `Other/`; ZIP assembly runs via `anyio.to_thread.run_sync` for request safety

Reuse this module when:
- implementing submission list pages or partials
- creating a new judged run from uploaded source code
- exporting team submission archives

Do not reimplement:
- duplicate-submission detection
- initial `SubmissionJudgment` creation
- initial WEB audit-row insertion for new submissions
- team submission ZIP layout and naming conventions
- per-team rate-limit enforcement (use `rate_limit_service` or call `create_submission` with the rate-limit params)

Notes:
- service flushes but does not commit
- duplicate protection uses both a pre-flight query and DB-constraint race handling
- `build_team_submissions_zip` uses `judgment_utils.get_active_judgment` to find the effective verdict per submission

---

## `animeitor_export_service.py`

Purpose:
- generates a ZIP file compatible with the legacy BOCA webcast protocol consumed by `maratona-animeitor`
- isolated export/adaptation layer that does not modify NOCA's internal domain model

Main types:
- `AnimeitorExportError` — raised when preconditions fail (no teams or no problems)
- `AnimeitorTeam` — frozen dataclass for a team in the exported `contest` file
- `AnimeitorRun` — frozen dataclass for a submission in the exported `runs` file

Main entrypoints:
- `map_verdict(verdict, accept_pe) -> str` — maps NOCA `Verdict` to legacy status (`Y`, `N`, `X`, `?`)
- `serialize_contest_file(...) -> str` — pure serialization of the `contest` file with `0x1C` delimiters
- `serialize_runs_file(runs) -> str` — pure serialization of the `runs` file with `0x1C` delimiters
- `build_animeitor_zip(session, contest) -> tuple[str, bytes]` — async orchestrator that loads teams, problems, and submissions, then assembles the five-file ZIP (`contest`, `runs`, `time`, `version`, `icpc`) via `anyio.to_thread.run_sync`

Reuse this module when:
- adding new export formats for external scoreboard consumers
- implementing a polling endpoint for live animeitor integration

Do not reimplement:
- verdict mapping for the legacy protocol — use `map_verdict`
- `0x1C`-delimited serialization — use `serialize_contest_file` and `serialize_runs_file`

Notes:
- uses `get_active_judgment` from `judgment_utils` for verdict selection (same semantics as scoreboard)
- uses `icpc_minutes_from_seconds` from `shared.timing` for ICPC run times, truncated to whole minutes
- penalty is hardcoded to `20` in the export for strict consumer compatibility
- institution field uses `contest.contest_name` since NOCA has no institution attribute on User
- see [ANIMEITOR-REVELEITOR.md](../../docs/ANIMEITOR-REVELEITOR.md) for the full usage guide

---

## `users_per_site_report_service.py`

Purpose:
- generates a markdown-formatted report of contest users grouped by site
- designed as a human-readable document for contest logistics and later PDF conversion

Main entrypoints:
- `build_users_per_site_report(session, contest, login_url) -> tuple[str, str]` — async orchestrator that loads all users and sites, then produces `(filename, markdown_text)`

Reuse this module when:
- adding new text-based export formats for user/site data

Notes:
- sites are ordered A-Z via `list_contest_sites` from `site_service.py`
- users within each role section are ordered by username ascending
- the "no site assigned" section groups all roles together in the order ADMIN, JUDGE, STAFF, TEAM, USER
- chief judge annotation appears under the Judges subsection of each site, sourced from `contest.chief_judge_id`
- location field falls back to "Not assigned" when `user.location` is None or empty
- user tables are rendered through `assorted_utils.render_prettytable()` with ASCII borders, padding `1`, `vrules=1`, `hrules=1`, and default left alignment for every column

---

## `contest_timeline_export_service/`

Purpose:
- generates a markdown-formatted contest timeline from persisted contest history
- normalizes submissions, judgments, confirmations, overrides, clarifications, tasks, and contest timing boundaries into one wrapped text table

Internal structure:
- `common.py` — timeline DTOs plus rendering and label helpers
- `submissions.py` — submission and judging event normalization
- `events.py` — clarification, task, and contest-boundary event builders
- `service.py` — contest-scoped data loading and final report assembly

Main entrypoints:
- `build_contest_timeline_report(session, contest) -> tuple[str, str]` — async orchestrator that loads contest-scoped persisted history and produces `(filename, markdown_text)`

Reuse this module when:
- adding new human-readable contest history exports
- sharing the wrapped PrettyTable configuration with future fixed-width reports

Notes:
- output is best-effort only and intentionally omits transient lock-only acquisitions that are not stored historically
- uses `assorted_utils.render_prettytable()` with per-column `max_width`, top vertical alignment, and right-aligned time column so wrapped cells remain readable within the 90-character width budget
- includes contest boundary rows for start, scoreboard freeze, answer freeze, and end even when no user-generated events exist at those moments
- problem labels reuse `_label()` from `contest_admin_problem_helpers.py` for consistency with admin UI problem lettering

---

## `contest_backup_service/`

This package creates and restores bounded historical-replay archives.

Purpose:

- exports the historical replay dataset (problems, users, submissions, judgment
  history, clarifications, staff tasks, sites, and optional media/password hashes)
  into one portable ZIP and restores it under a new name and slug
- restores verdicts, timings, and timestamps verbatim without re-judging

Internal structure:

- `models.py` — format constants, size ceilings, DTOs, `ContestBackupError`, and
  the shared `remap_optional` id helper
- `serialization.py` — column-driven row-to-JSON and insert conversion
- `export_payload.py` — queries the replay dataset and renders the JSON members
- `export.py` — writes metadata first, then appends and releases one problem
  package at a time; only finished or inactive contests are accepted
- `validation.py` — bounded member reads, strict manifest/row-list parsing, safe
  names, size ceilings, slug validation, and fail-closed language checks
- `row_validation.py` — generic table-driven row/identifier validation primitives
- `integrity.py` — composes the primitives into contest-scope, foreign-key, and
  manifest-to-payload checks across the whole archive graph before restore
- `restore.py` — coordinates the one-transaction Core restore and rollback cleanup
- `restore_problems.py` — restores problem rows and lazily reads their files
- `restore_history.py` — restores submissions, judgments, clarifications, and tasks
- `importing.py` — coordinates validation and restoration, and normalizes a
  pre-NOT-NULL backup's `null` `output_limit_in_bytes` to 65536 **before**
  integrity validation, which would otherwise reject the whole archive

Main entrypoints:

- `build_contest_backup(...)` — writes a temporary archive off the event loop at
  `FORMAT_VERSION` 5, carrying each problem's `validator_type`,
  `artifact_generation`, `public_export_generation`, and optional `editorial`,
  plus each clarification's `is_announcement`, in the payload rows and
  embedding version-2 problem packages. It builds those packages with
  `require_importable=False`, so a
  contest holding an interactive problem whose validator source was removed stays
  backupable; restore never parses the embedded `problem.json`, and the validator
  row is preserved verbatim in `problems.json`
- `import_contest_backup(...) -> ContestImportResult` — validates, then restores
  **version 5 only**, refusing anything else with a message naming the supported
  version. Every column the live table has is mandatory in a v5 row, so both the
  integrity checker and the restorer read stored values rather than deriving
  them: `validator_type` is read straight from the row (and decides whether an
  `out/NNN.out` payload member is required), and so is
  `clarifications.is_announcement`. Versions 1 to 4 were retired with the v5
  bump; each needed its own optional-column set plus an inference rule, and every
  such rule was a place the checker and the restorer could disagree — admitting
  an archive that validates as one kind of row and restores as another

Reuse this module when:

- adding archival/replay export-import flows for whole contests
- changing the format; bump `FORMAT_VERSION` and update the format document

Notes:

- password hashes and user media are optional; hash export additionally requires
  password reconfirmation, and each sensitive opt-in (hashes or media) writes its
  own admin-action audit event after archive creation
- restored custom validators contain data only and aren't compiled
- unknown language identifiers reject the whole import
- the HTTP upload has a compressed-size ceiling; expanded payloads are read one
  member at a time and remain subject to per-member and total ceilings
- profiling runs and problem-limit change batches are operational history and are
  explicitly outside the historical replay format

See the [contest backup format](../../docs/CONTEST_BACKUP_FORMAT.md) for the
archive layout and fidelity notes.

---

## `problem_set_service.py`

Purpose:
- build the public post-contest problem-set archive: one ZIP bundling every
  problem of a contest as its full version-2 package (statement, all test
  cases — including secret ones, validator source, and `editorial.md` when
  set) plus a top-level `index.json` manifest, for anonymous download once the
  contest is over and its scoreboard has been released

Main entrypoints:
- `build_problem_set_archive(session, contest, dest_path, *, testcase_dir, statement_dir)` — writes the archive off the event loop; each embedded package is produced by `problem_service.build_problem_export(profile="full", require_importable=False)` and spliced under `problems/{ordinal:03d}-{label}/` via the shared `shared.services.problem_package.merge.append_package_folder`, so peak disk usage is the outer archive plus one problem package. Embedded packages are a convenience artifact, not a restore source of record: `require_importable=False` means an incomplete one (e.g. an interactive problem whose validator source was removed) exports fine but is **not** re-importable
- `problem_set_filename(contest) -> str` — slug-derived download filename (`problem-set-<slug>.zip`)

Reuse this module when:
- exposing contest problem materials to the public after a contest ends
- adding another multi-problem archive export (the merge helper is shared with `contest_backup_service`)

Do not reimplement:
- the ordinal-to-label bijection beyond the local `_problem_label` (the routes-layer `_label` cannot be imported without a service → routes cycle)
- per-problem package assembly — always go through `build_problem_export`

Notes:
- the release gate (`contest.is_past and contest.release_problem_set_after_end`) lives in the route (`web/routes/problem_set.py`), not here — callers are responsible for checking it first. The flag is independent of `release_scoreboard_after_end`, which still gates the scoreboard and the team submissions download; `is_past` is not optional and keeps the materials private while the contest runs
- a problem whose stored statement file is missing aborts the whole build with `PackageError` (the route answers 409) rather than serving a silently incomplete archive
- `index.json` is a small manifest (`format_version` 1, `kind: "problem_set"`, contest slug/name, and per-problem `label`/`title`/`dir`) so consumers can list contents without opening every embedded package

---

## `problem_set_cache.py`

Purpose:
- keep the anonymous `GET /problem-set/{slug}.zip` endpoint from exhausting the database pool and disk under a burst: when `NOCA_WEB_PUBLIC_PROBLEM_PACK_PATH` is configured, each contest's archive is built once and reused

Main entrypoints:
- `ensure_cached_archive(cache_dir, contest, build) -> Path` — returns a digest-verified cached archive, building it via the async `build` callable when missing or corrupted; concurrent builds for the same contest are serialized by a per-slug `anyio.Lock` with a double-checked cache test inside the lock
- `cached_archive_path(cache_dir, contest) -> Path` — the deterministic per-contest location (`problem-set-<slug>.zip`)
- `discard_cached_archive(cache_dir, contest) -> None` — drops the cached archive and its sidecar so the next download rebuilds; called when an admin withdraws a problem-set release. Removing nothing is a normal outcome (the contest may never have been downloaded), so a missing file is not an error

Do not reimplement:
- the integrity contract: a `<name>.sha256` sidecar must match the file on disk, and publishing is atomic (temp sibling + `os.replace`), so no reader ever observes a half-written archive

Notes:
- there is no invalidation bookkeeping: the route re-checks the release gate per request, so un-releasing a contest stops serving immediately **on every replica**, since the gate is a database read rather than cached state; force a rebuild by deleting the cached file
- what is not cluster-wide is `discard_cached_archive`: it removes the archive the handling replica can see, so a deployment whose replicas do not share the cache directory keeps its other copies until each rebuilds. That is a wasted rebuild, never a disclosure — no replica serves a cached file without passing the gate first
- the cache directory is created at Web startup when configured; a relative or non-directory path is rejected by config validation
- when the setting is unset the route rebuilds on every download (development mode)

---

## `problem_export_cache.py`

Moved to `shared/services/problem_export_cache.py` (#204): Arena caches its public export and
sample-case ZIP with the same module. See [SHARED_SERVICES.md](../../docs/SHARED_SERVICES.md).

---

## `problem_export_rate_limit.py`

Purpose:
- give the per-problem export a much tighter budget than the router-wide `web:user-read` ceiling it sits under. That ceiling is deliberately loose (300 requests a minute) because it guards polled partials whose per-call cost is bounded; a problem package build is not, and 300 of them a minute per team bounds nothing

Main entrypoints:
- `web_problem_export_rate_limit` — the `Depends`-ready per-actor limiter, attached to the `/export` route only
- `problem_export_policy() -> RateLimitPolicy` — rebuilt from settings per request, so a knob change (and a test's monkeypatch) takes effect without rebuilding the dependency
- `PROBLEM_EXPORT_BUCKET` / `PROBLEM_EXPORT_DETAIL` / `PROBLEM_EXPORT_LIMITER`

Do not reimplement:
- build it with `shared.services.request_rate_limit.make_user_rate_limit_dependency`, exactly as `user_read_rate_limit.py` does, and share that module's `web_actor_key` so both budgets count the same identity

Notes:
- keyed per actor, not per client IP: the route is authenticated and a whole venue legitimately shares one address, so counting by address would refuse a room full of contestants for one team's behaviour
- charged before the cache is consulted, so a caller can neither widen the budget by arranging for hits nor be spared by arranging for misses
- no trusted-network bypass: an exemption keyed on an address could only ever lift a per-actor budget for callers with no valid session
- register `PROBLEM_EXPORT_LIMITER` in `tests/web/conftest.py`'s reset fixture — the Web test apps carry no Valkey runtime, so the limiter runs on its process-local fallback and state would leak across tests

---

## `task_service/`

Purpose:
- full lifecycle management for contest tasks: creation, listing, acquisition, release, finish, and duplicate-print protection

Internal structure:
- `errors.py` — service exception types
- `views.py` — `TaskView` plus lock-merging helpers
- `queries.py` — contest-scoped reads and role-filtered listing
- `lifecycle.py` — creation, acquisition, release, and finish flows
- `permissions.py` — who may view, handle, and force-release tasks

Main types:
- `TaskError`
- `ContestNotRunningError`
- `ForbiddenTaskActionError`
- `TaskAlreadyAcquiredError`
- `TaskLockUnavailableError`
- `TaskAlreadyFinishedError`
- `TaskNotAcquiredByActorError`
- `DuplicatePrintTaskError`
- `PrintRequestsDisabledError`
- `TaskRateLimitError` — per-window SOS or PRINT budget exhausted; carries `next_allowed_at`
- `OpenSosTaskLimitError` — the team already holds the maximum unfinished SOS tasks; carries `open_count` and `limit`
- `TaskView`

Main entrypoints:
- `create_sos_task(session, contest, actor, *, rate_limit_window_seconds, rate_limit_max_tasks, max_open_tasks) -> Task` — enforces the open-SOS cap first (waiting does not release it) and then the rolling window, both after the TEAM role gate, so staff, judges, and admins never reach them
- `create_print_task(session, contest, actor, *, problem_id, source_code, rate_limit_window_seconds, rate_limit_max_tasks) -> Task` — requires `contest.allow_print_requests=True`, validates source code size against `contest.max_problem_file_size_bytes`, deduplicates by source hash, and only then applies the rolling window, so the duplicate warning is never masked by the throttle
- `create_balloon_task(session, *, problem_id, team_id) -> Task` — system-level call with no actor or contest-running requirement
- `get_task(session, contest, task_id) -> Task | None` — includes SOS tasks (NULL `problem_id`) via LEFT JOIN through team user
- `get_task_with_details(session, contest, task_id) -> Task | None` — applies the same contest scoping while eagerly loading the team, site, problem, and finished-task staff relationships required by source printouts
- `list_tasks(session, contest, actor, lock_client) -> tuple[list[TaskView], bool]` — merges PostgreSQL rows with Valkey lock state; bool indicates whether lock coordination is available for the UI
- `acquire_task(session, contest, actor, task, lock_client) -> Task` — STAFF, ADMIN, or the contest chief judge; acquires a Valkey TTL lock keyed by contest and task id
- `release_task(session, contest, actor, task, lock_client) -> Task` — STAFF and the chief judge may release own lock; ADMIN/UBERADMIN may force-release any lock through Valkey
- `finish_task(session, contest, actor, task, lock_client) -> Task` — STAFF, ADMIN, or the chief judge; enforces the Valkey lock when available; PostgreSQL remains authoritative for finished state and finisher identity
- `can_view_tasks(actor, contest) -> bool` / `can_handle_tasks(actor, contest) -> bool` / `can_force_release_tasks(actor) -> bool` / `is_chief_judge(actor, contest) -> bool` — the single source of truth the routes and templates share (`is_chief_judge` comes from `chief_judge_permissions.py`); uberadmins may force-release but never handle a task, since `tasks.staff_id` is a foreign key into `users`

Reuse this module when:
- building any staff/team/admin task workflow
- creating SOS, print, or balloon tasks

Do not reimplement:
- task acquisition concurrency guard
- duplicate print-task detection
- Valkey lock merging for unfinished tasks
- role-scoped task listing behavior

Notes:
- services flush, never commit
- active task locks live only in Valkey; PostgreSQL stores queue/finished state plus the staff member who completed the task

---

## `task_reaper.py`

Purpose:
- auto-finish unfinished tasks for contests that have already ended

Main entrypoints:
- `release_expired_tasks(session) -> int` — no-op compatibility helper; active task expiration is handled by Valkey TTL
- `conclude_finished_contest_tasks(session, now=None) -> int` — marks unfinished tasks as finished for past contests, assigning the contest owner as the finishing actor; uses `time_utils.normalize_now_for_reference`
- `run_task_reaper(session_factory, poll_interval_seconds, stop_event, logger) -> None` — delegates to `reaper_runner.run_reaper_loop`; each cycle runs the no-op compatibility hook plus post-contest conclusion

Reuse this module when:
- wiring post-contest task conclusion into app startup

Do not reimplement:
- post-contest task conclusion logic
- periodic loop and shutdown handling

Notes:
- active task locks now live only in Valkey; this module keeps only the post-contest task conclusion behavior
- FastAPI startup in `web.main` enables this loop only when `NOCA_WEB_ENABLE_TASK_REAPER=true`
- contests without an `owner_user_id` are skipped during post-contest conclusion

---

## `contest_report_service/`

Purpose:
- pure aggregation service for contest report analytics; produces a single `ContestReport` dataclass from a list of submissions and a contest; contains no I/O

Internal structure:
- `models.py` — report DTOs used by templates
- `computation.py` — pure aggregation and table-building logic
- `tables.py` — cross-table and per-problem solve-metric builders consumed by `computation.py`

Main types:
- `ProblemInfo` — lightweight problem descriptor (label, title, color)
- `LanguageInfo` — lightweight language descriptor (id, name, icon)
- `CellValue` — count + percentage cell for cross-tables
- `SolveMetrics` — per-problem submission/solve-time metrics: `avg_submissions`/`median_submissions` (mean/median of per-team submission count, over distinct attempting teams); `median_time_solved`, `avg_time_solved`, `first_solved_minutes`, `first_solver_name`, `dirt_ratio` (all `None` until the problem has a solve) computed from each solving team's *first* accepted submission only. `dirt_ratio` is the ICPC resolver "dirt" metric -- pooled wrong-submission count from solving teams (attempts before each one's solve) divided by that count plus the solver count, *not* an average of each team's own ratio
- `ProblemSummaryRow` — row for problem summary (runs, AC count/%, AC+PE count/%, `solve_metrics`)
- `DistributionRow` — row for distribution tables (problem, count, %). `ContestReport.runs_distribution` counts accepted-or-not *submissions*; `ContestReport.accepted_distribution` deliberately counts distinct *solving teams* instead (the same population as `Highlights.most_solved`/`least_solved` and `SolveMetrics.dirt_ratio`), not accepted submissions -- a team that submits an accepted verdict twice to the same problem is one solve, not two
- `TeamRow` — row for team x problem table (team display, totals, per-problem cells)
- `TimeWindow` — one bar in time-distribution charts (label, all_count, accepted_count)
- `ProblemRaceSeries` — one line in the Problem Race chart: `solved_minutes` is every solving team's first-accepted-submission minute for that problem, sorted ascending (one entry per solve, empty when unsolved); the client derives the cumulative step curve by counting entries rather than the server pre-computing one
- `ProblemHighlight` — most/least-solved problem card data; `problems` lists every problem tied for the extreme solved-team count (usually one, all of them on a tie) rather than picking an arbitrary winner; `pct_of_teams` is out of every *active* team (at least one submission, judged or not), not the full enrolled roster -- a team that never showed up must not deflate a problem's acceptance rate
- `LanguageHighlight` — most-used-language card data (language, submission count, % of all judged runs)
- `ActiveTeamsHighlight` — how many enrolled teams actually showed up: `active` (at least one submission, judged or not), `enrolled` (full contest roster), `pct` -- the one Highlights figure that measures participation rather than performance
- `Highlights` — the five top-of-page KPI cards: `most_solved`, `least_solved` (by distinct solving-team count), `most_used_language` (`None` only if the contest has no configured languages), `global_acceptance_pct`, `active_teams`
- `FiveNumberSummary` — Min/Q1/Median/Q3/Max plus Mean for an integer distribution, from `statistics.quantiles(..., n=4)` (stdlib's default "exclusive" method)
- `SolvedCountBucket` — one bar in the Performance section's "Active Teams by Problems Solved" histogram (`solved`, `team_count`)
- `PerformanceSummary` — the Performance section's contest-wide distributions across every *active* team, including teams with 0 solves (0 penalty too): `active_team_count`; `solved_summary`/`penalty_summary` (`FiveNumberSummary | None`, `None` below two active teams since `statistics.quantiles` needs at least two points); `solved_histogram`; `top_10pct_solved` (ceiling of the solved-count distribution's own 90th percentile, clamped to the observed maximum since the stdlib's exclusive-method percentile can otherwise extrapolate past it on a small sample -- not a full ICPC-rank threshold, which would pull in penalty-time tie-breaking from outside this distribution). `penalty_summary` uses the real ICPC penalty formula (solve-minute plus penalizing-verdict attempts × the contest's WA penalty, respecting `accept_pe`/`ce_adds_penalty`), computed separately from `SolveMetrics.dirt_ratio`'s broader "any non-accepted submission is wrong" predicate -- the two metrics answer different questions and must not share one wrongness definition
- `ContestReport` — all aggregated data: highlights, problem summary, distributions, cross-tables (problem×verdict, problem×language, language×verdict), team×problem, time windows, problem race, performance

Main entrypoints:
- `compute_contest_report(contest, submissions, problems, languages, enrolled_teams) -> ContestReport` — `submissions` is `list[ContestReportSubmissionRow]` and `problems` is `list[ContestReportProblemRow]`, both from `contest_report_query_service`, not ORM rows; filters submissions to DONE judgments with non-null final_verdict; sorts them chronologically (`shared.services.scoreboard_projection.submission_sort_key`) since `SolveMetrics` needs each team's attempt order; aggregates all data in a single pass; respects `contest.accept_pe` for accepted predicate. `problem_infos` (and therefore every table's problem set) is built from `problems` directly, *not* derived from which problems appear in `submissions` -- a problem with zero judged submissions in the current scope (e.g. a site whose teams never touched it) still appears, with every count defaulting to zero, instead of silently vanishing; the distinct `team_id`s across the full (unfiltered) `submissions` list -- teams with at least one submission, judged or not -- become `Highlights.most_solved`/`least_solved`'s percentage denominator, `Highlights.active_teams.active`, and `PerformanceSummary`'s population; `enrolled_teams` (every TEAM-role user enrolled, from `contest_user_service.count_contest_teams`) is used only by `Highlights.active_teams`

Constants:
- `ALL_VERDICTS` — ordered list of all `Verdict` values used as cross-table columns

---

## `contest_report_query_service.py`

Purpose:
- the one I/O boundary feeding `contest_report_service`'s pure aggregation; a lean, report-only submission query, deliberately separate from `submission_service.list_submissions` (built for the Runs page, which needs full ORM `Submission` rows: `source_code`, judgment confirmations, verdict overrides). At a few thousand submissions the difference is noise; a busy contest clearing five figures of submissions makes loading every source file into memory just to discard it real, avoidable I/O and RAM. This module selects only the columns the report aggregates.

Main types:
- `ContestReportSubmissionRow` — one submission's report-relevant columns, flattened (problem ordinal/title/color, team username/fullname/site name, language id, timestamps) with its judgment already resolved to `judgment_status`/`final_verdict`. Structurally compatible with `shared.services.scoreboard_projection.SubmissionInput` (`id`, `team_id`, `problem_id`, `timestamp_seconds`, `created_at`), so `submission_sort_key` accepts it directly without a protocol cast.

Main entrypoints:
- `list_contest_report_submissions(session, contest, *, site_id=None) -> list[ContestReportSubmissionRow]` — one SQL round trip: submissions joined to their problem and team (with the team's site), outer-joined to every non-`SUPERSEDED`, non-`FAILED` judgment; the effective one is then picked per submission in Python by latest `created_at`, mirroring `judgment_utils.get_active_judgment`'s exact exclusion set (not the animator's "prefer DONE" ordering, which is a different selection rule for a different consumer). `site_id` scopes the query to one site's teams (a team with no site is excluded); `None` returns the whole contest -- this is the reports page's per-site filter, and since `compute_contest_report` is a pure function over whatever list it is handed, every downstream figure (Highlights, Performance, Dirt Ratio, Problem Race) is automatically scoped too, with no aggregation-side awareness of sites at all
- `list_contest_report_problems(session, contest) -> list[ContestReportProblemRow]` — every problem in the contest (id, ordinal, title, color), regardless of submission activity; this, not "which problems appear in `list_contest_report_submissions`'s result", is `compute_contest_report`'s problem set, so a problem nobody in the current scope has touched still shows up with honest zeros instead of vanishing from the legend and every table

---

## `contest_user_service/`

Purpose:
- contest user validation, lookup, creation, update, removal, media-mutation authorization, batch import, and user export shaping

Internal structure:
- `models.py` — DTOs and shared constants for grouped users and batch-import results
- `queries.py` — contest-scoped reads and grouped enrolled-user presentation
- `credentials.py` — password, email, username, role, and form validation helpers
- `sites.py` — site-assignment rules and import/export helpers
- `imports.py` — CSV and JSON batch payload parsing
- `permissions.py` — contest-state and actor-authorization guards
- `crud.py` — create, update, and remove flows
- `batch.py` — batch import orchestration
- `validation.py` — compatibility re-export module that preserves the previous validation import surface

Main types:
- `UserImportResult`
- `BatchImportResult`
- `ContestUserGroups`
- `RoleUserGroups`
- `SiteUserGroup`

Main entrypoints:
- `normalize_username(username) -> str`
- `parse_single_user_role(raw_role) -> RoleEnum`
- `validate_create_user_form(username, fullname, raw_role, email) -> tuple[...]`
- `validate_edit_user_form(fullname, email) -> tuple[...]`
- `validate_edit_credentials_form(email) -> tuple[str | None, list[str]]` — email-only validation for the post-contest credentials edit path
- `role_requires_site(role) -> bool`
- `validate_role_site_requirement(role, site_id) -> None`
- `resolve_site_for_user(session, contest, *, role, site_id) -> Site | None`
- `resolve_or_create_import_site(session, contest, *, role, raw_site) -> Site | None`
- `build_user_export_row(user) -> dict[str, str]`
- `parse_batch_upload(slug, filename, content) -> list[BatchUserRow]`
- `normalize_batch_users_payload(slug, raw_payload) -> list[BatchUserRow]`
- `ensure_contest_user_add_or_edit_allowed(contest) -> None`
- `ensure_contest_user_remove_allowed(contest) -> None`
- `ensure_user_edit_allowed(actor, target_user) -> None`
- `ensure_user_media_upload_allowed(actor, target_user) -> None`
- `ensure_user_media_removal_allowed(actor, target_user) -> None`
- `get_contest_user_groups(session, contest) -> ContestUserGroups`
- `count_contest_teams(session, contest) -> int` — cheap `COUNT(*)` of TEAM-role users; used by the reports page's Highlights "Active Teams" card as the enrolled-roster figure, distinct from the "active" (submitted at least once) count `contest_report_service` derives itself
- `count_teams_by_site(session, contest) -> dict[str, int]` — TEAM-role user counts per site (one grouped query, a team with no site excluded); labels each tile in the reports page's site picker without a query per site
- `get_user_in_contest(session, contest, user_id) -> User | None`
- `get_user_by_username_in_contest(session, contest, username) -> User | None`
- `create_user(session, contest, actor, *, username, fullname, role, password, email=None, site_id=None) -> tuple[User, str]`
- `update_user(session, contest, user, *, fullname, role, password=None, email=..., site_id=None) -> str | None`
- `update_user_credentials(session, contest, user, *, email=..., password=None) -> str | None` — updates only the email and optional password; permitted even after the contest ends (unlike `update_user`, it skips the `is_past` guard and never touches profile fields). The edit route dispatches here when `contest.is_past`.
- `list_contest_sites_for_form(session, contest) -> list[tuple[str, str]]`
- `list_users_for_export(session, contest) -> list[User]`
- `remove_user(session, contest, user) -> None`
- `batch_import_users(session, contest, actor, users_data) -> BatchImportResult`

Reuse this module when:
- adding any contest-user admin feature
- validating or parsing batch import payloads
- exporting contest users in an import-compatible JSON shape
- applying contest-state restrictions to user management

Do not reimplement:
- username normalization
- role allow/deny rules for contest users
- TEAM/STAFF site-assignment requirements
- import-side case-insensitive site lookup/creation
- batch import parsing and per-row result shaping

Notes:
- `TEAM` and `STAFF` users must always have a site assigned; other roles may keep `site_id=None`.
- cross-service implementations import the defining `contest_user_service`
  submodule directly instead of the package re-export surface, avoiding
  package-initialization cycles
- batch import accepts optional `email` and `site` in JSON and CSV headers (`username,fullname,role,password[,email][,site][,location]`).
- batch import creates missing contest sites on demand using case-insensitive uniqueness (`sitename_normalized`).
- `build_user_export_row` intentionally omits passwords and emits a JSON row compatible with the batch import route, including optional `email`, `site`, and `location`.
- `get_contest_user_groups` returns flat no-site rows plus per-site groups for the enrolled-users admin page; site groups are ordered by `sitename_normalized`.

---

## `site_service.py`

Purpose:
- normalize, list, create, remove, and synchronize contest sites
- enforce contest-scoped case-insensitive site uniqueness
- provide presentation helpers for metadata and user-management screens

Main entrypoints:
- `normalize_site_name(raw_name) -> str`
- `normalize_site_name_key(raw_name) -> str`
- `list_contest_sites(session, contest_id) -> list[Site]`
- `get_site_names_from_sites(sites) -> list[str]`
- `list_contest_site_entries(session, contest_id) -> list[dict[str, int | str]]`
- `parse_site_names_payload(raw_payload) -> list[str]`
- `contest_has_sites(session, contest_id) -> bool`
- `get_site_in_contest(session, contest, site_id) -> Site | None`
- `get_site_by_name_in_contest(session, contest, raw_name) -> Site | None`
- `create_site(session, contest, raw_name) -> Site`
- `remove_site(session, contest, site) -> None`
- `sync_contest_sites(session, contest, raw_site_names) -> list[str]`

Animator medal and operator-secret wrappers (thin Web boundary over
`shared/services/animator_access_service.py`; see
[docs/SHARED_SERVICES.md](../../docs/SHARED_SERVICES.md)):
- `update_site_medals(session, site, gold, silver, bronze) -> Site`
  — validate and persist the ordered cutoffs, returning the refreshed site
- `list_site_secrets(session, contest, site_id) -> list[SiteSecretMetadata]`
  — digest-free credential metadata for one site, scoped to the contest (a site
  from another contest returns nothing)
- `create_site_secret(session, site, label) -> str`
  — generate a site-scoped operator secret, returning the plaintext once
- `create_global_secret(session, contest, label) -> str`
  — generate a contest-global control secret, returning the plaintext once
- `delete_site_secret(session, contest, secret_id) -> bool` — revoke a secret
  owned by the contest; returns whether a row was removed (a foreign contest's
  secret is never touched)
- `get_site_by_secret(session, contest_id, secret) -> Site | None`
  — resolve a site-scoped operator secret to its site (global secrets do not
  match)
- `get_contest_by_global_secret(session, contest_id, secret) -> Contest | None`
  — resolve a global control secret to its contest (site secrets do not match)

Reuse this module when:
- building or validating contest site-management UI
- resolving a site from either a site ID or a human site name
- enforcing contest-scoped site uniqueness and TEAM/STAFF deletion guards
- configuring animator site medals or generating and revoking reveal
  operator secrets

Do not reimplement:
- `casefold()`-based site normalization
- the "cannot remove the only remaining site" rule
- the guard that blocks site removal while TEAM/STAFF users are assigned
- animator token generation, digesting, or scope resolution (these delegate to
  the shared animator access service)

Notes:
- `sync_contest_sites` updates display casing for existing sites when the normalized key matches a submitted value.
- removing a site unassigns non-TEAM/non-STAFF users automatically, but refuses removal if any TEAM/STAFF user still points at that site.
- the animator wrappers persist only the fixed-length secret digest; the plaintext operator token is returned once at creation and never stored.

---

## `password_service.py`

Purpose:
- compatibility wrapper for the shared password service using `web.config.settings`

Canonical implementation:
- `shared/services/password_service.py`
- shared API is documented in [docs/SHARED_SERVICES.md](../../docs/SHARED_SERVICES.md)

Main types:
- `PasswordPolicyError`
- `PasswordPolicy`

Main entrypoints:
- `generate_diceware_password(*, wordlist_path=None, size=None) -> str`
- `password_matches(actor, password) -> bool` — validates non-empty password
  reconfirmation against a Werkzeug-compatible actor hash
- `PasswordPolicy.validate_new_password(password) -> None`
- `PasswordPolicy.policy_hint -> str` — returns the current policy description for UI display

Notes:
- policy is controlled by `web.config.settings` through the shared password service

Reuse this module when:
- maintaining legacy web imports
- new code should import `shared.services.password_service` directly

Do not reimplement:
- password complexity checks
- diceware generation

---

## `problem_edit_save.py`

The Contest half of joining a Save's test-case plan to rows. The plan itself is decided in
`shared.services.testcase_save_plan`, which knows nothing about either module's models;
`arena.services.admin_problem_tc_pending` is the Arena half.

Nothing here touches the filesystem: by the time these rows are written the Save's complete desired
directory already exists in staging, and the swap renames it in as part of the commit. That is the
point — no row is ever committed ahead of a file write that could still fail.

| Function | Purpose |
|----------|---------|
| `current_cases(test_cases)` | The planner's view of the problem's rows. |
| `apply_materialized_cases(session, problem, materialized)` | Make the rows describe the staged directory exactly. Every row is first pushed into a disjoint high range, then the dropped ones are deleted, and only then do survivors and added rows take their final 1..n positions in one flush — because `(problem_id, ordinal)` is unique, a flush emits UPDATEs before DELETEs, and `web.models.problem` maintains dense ordinals on every flush, so a naive delete-then-renumber collides with the row it is deleting. |

---

## `problem_service/`

Purpose:
- ordered problem mutations within a contest
- ordered test-case mutations within a problem
- deterministic append, move, and removal operations for ordinal-based collections
- problem statement I/O (PDF and Markdown)
- database-backed, editor-only problem editorials
- problem illustration image round-trip through the package ZIP (the image itself is stored in
  the database as base64 + MIME + caption; see `shared/services/problem_image.py`)
- problem and test case ZIP import/export
- per-language fallback limits and profiling-run orchestration
- persisted affected-submission batch creation for running-contest limit changes

Internal structure:
- `models.py` — shared limit dataclasses and import-result types
- `ordering.py` — ordered problem and test-case append, move, and removal helpers
- `interactions.py` — sample-interaction persistence (the worked conversations an interactive
  problem shows instead of sample test cases) plus the interactive test-case invariant
- `queries.py` — contest-scoped problem and allowed-language reads
- `files.py` — statement/test-case file I/O plus the projection of a problem onto the shared package contract
- `importing.py` — the Contest adapter over the shared problem-package subsystem: balloon colors, contest-allowed languages, ORM rows, and the validator token
- `language_limits.py` — per-language limits and effective-limit diff helpers
- `profiling.py` — profiling-run creation, lookup, derived limits, and queueing
- `limit_batches.py` — persisted running-contest limit-change batch helpers

Main entrypoints:
- `append_problem(session, contest, problem) -> Problem`
- `append_test_case(session, problem, test_case) -> ProblemTestCase`
- `move_problem(session, contest, problem, new_ordinal) -> None`
- `move_test_case(session, problem, test_case, new_ordinal) -> None`
- `remove_problem_and_resequence(session, contest, problem) -> None`
- `remove_test_case_and_resequence(session, problem, test_case) -> None`

Sample interactions (`interactions.py`) — an interactive problem has no public test cases; its
public examples are up to `MAX_SAMPLE_INTERACTIONS` authored transcripts. Parsing and format rules
live in `shared/services/sample_interactions.py`; this module owns only the SQL:
- `load_sample_interactions(session, problem_id, *, include_hidden=False) -> list[ProblemSampleInteraction]`
- `count_sample_interactions(session, problem_id) -> int` — counts hidden ones too; this is what the cap is judged against
- `append_sample_interaction(session, problem, *, transcript, explanation) -> ProblemSampleInteraction`
- `update_sample_interaction(interaction, *, transcript, explanation) -> None`
- `move_sample_interaction(session, problem, interaction, new_ordinal) -> None`
- `remove_sample_interaction_and_resequence(session, problem, interaction) -> None`
- `hide_sample_interactions(session, problem_id) -> int` / `unhide_sample_interactions(session, problem_id) -> int` — hiding is what "keep" does when a validator is removed; staging a validator again un-hides
- `delete_sample_interactions(session, problem_id) -> int` — permanently drop the set
- `convert_sample_test_cases_to_secret(session, problem_id) -> int` — called whenever a validator is staged
- `interactive_testcase_error(session, problem_id) -> str | None` — why an interactive problem's cases are invalid (a public case, or no secret case at all)

Additional entrypoints (query helpers):
- `get_contest_problems(session, contest) -> list[Problem]` — eager-loads categories + test_cases, ordered by ordinal
- `load_contest_problem_judgeability_facts(session, problem_id) -> ProblemJudgeabilityFacts` — gathers the facts the shared judgeability gate needs (stored strategy, case counts, missing expected-output files, active `VALID` validator) in one query pass; used by both the submission and solution-test gates
- `get_problem_in_contest(session, contest, problem_id) -> Problem | None` — full export/judgment profile with categories, test cases, validator, interactions, language limits, and profiling runs
- `get_problem_definition_in_contest(session, contest, problem_id) -> Problem | None` — definition-editor profile with categories, language limits, and profiling runs; deliberately excludes test cases, validator, and sample interactions
- `get_profiling_runs_for_problem(session, problem) -> list[ProfilingRun]`
- `get_active_profiling_run_for_problem(session, problem) -> ProfilingRun | None`

Additional entrypoints (language helpers):
- `get_active_languages(session) -> list[Language]` — all active languages globally; used for contest creation form and uberadmin screens
- `get_contest_languages(session, contest) -> list[Language]` — languages allowed for the given contest, ordered by name; use this instead of `get_active_languages` for all contest-scoped callers
- `import_problem_package(session, contest, package, testcase_dir, statement_dir, image_service) -> ProblemImportResult` — persists an already-validated `ProblemPackage`, setting the new problem's immutable `validator_type` from the package's normalized strategy — stated explicitly by a version-2 package, derived from `custom_validator` presence by the shared parser for a version-1 one (the shared reader having handled every format decision); supports PDF and Markdown statements; picks an unused balloon color when the package states none; filters `language_limits` to the contest's currently allowed languages, reporting the skipped ones as structured warnings; validates a staged illustration image through `shared.services.problem_image.load_staged_image`. Test-case and statement files are **promoted before** the commit and deleted again if it fails, and stale import journals are reconciled first
- `get_language_limits_map(session, problem) -> dict[str, ProblemLanguageLimit]`
- `problem_fallback_limits(problem) -> EffectiveProblemLimits` — normalized fallback limits snapshot with `repetitions=1`
- `submitted_language_limits(languages, submitted_form, existing_limits) -> dict[str, LanguageLimitInput]` — extracts posted per-language limits and preserves repetitions for unchanged rows
- `changed_effective_limits(problem, languages, *, before_overrides, after_overrides, before_fallback=None, after_fallback=None) -> dict[str, tuple[str, EffectiveProblemLimits, EffectiveProblemLimits]]` — computes which languages had an effective-limit change and whether it was explicit or fallback-driven
- `upsert_language_limits(session, problem, limits) -> None` — re-writes per-language limit rows, preserving stored repetitions when editing an existing row and defaulting new rows from the language registry
- `apply_fallback_limits(session, problem) -> bool` — copies separate `MAX()` values from `problem_language_limits` into `problems`; fallback judging still uses exactly 1 repetition
- `create_problem_limit_change_batch(session, contest, problem, actor, changed_limits) -> ProblemLimitChangeBatch | None` — persists one stable running-contest batch plus captured affected submissions: current active `AC`, `RE`, `TLE`, `MLE`, `OLE`, and `PE` only when `contest.accept_pe` is true
- `get_problem_limit_change_batch(session, contest, problem_id, batch_id) -> ProblemLimitChangeBatch | None` — eager-loads one persisted batch for the admin review page
- `create_profiling_run(session, problem, language_id, source_code, safety_factor, triggered_by_user_id) -> ProfilingRun`
- `enqueue_profiling_job(valkey_runtime, profiling_run) -> None`

Additional entrypoints (file I/O — sync, call via `anyio.to_thread.run_sync`):
- `get_statement_path(problem_id, statement_dir) -> Path` — PDF statement path
- `get_md_statement_path(problem_id, statement_dir) -> Path` — Markdown statement path
- `get_active_statement_path(problem_id, statement_dir) -> Path | None` — returns MD path if it exists, PDF path otherwise, or `None`
- `save_problem_statement(problem_id, pdf_bytes, statement_dir) -> None`
- `save_md_statement(problem_id, md_text, statement_dir) -> None`
- `delete_problem_statement(problem_id, statement_dir) -> None` — deletes both PDF and MD files
- `delete_md_statement(problem_id, statement_dir) -> None`
- `validate_md_content(md_text, *, allow_links=False) -> list[str]` — validates Markdown statement content; returns errors for disallowed features or oversized content (>512 KB). `allow_links=True` (the announcement board) accepts links while still refusing raw HTML and images
- `get_testcase_path(problem_id, ordinal, ext, testcase_dir) -> Path`
- `save_testcase_files(problem_id, ordinal, in_bytes, out_bytes, testcase_dir) -> tuple[int, int]` — normalizes content to Unix line endings (LF only) before writing and returns the written `(input_size_bytes, output_size_bytes)`; the add/edit/zip handlers persist those onto the `test_cases` row. The contest test-case root resolves to `<NOCA_PROBLEM_TESTCASE_DIR>/contest`. Inline add/edit is gated to ≤ `MAX_INLINE_TESTCASE_BYTES` (10 KB) per side; larger cases use the single-case ZIP download/replace routes (`download_test_case` / `replace_test_case`, no cap)
- `read_testcase_preview(problem_id, ordinal, testcase_dir, max_bytes=32) -> tuple[str, str]`
- `read_testcase_full(problem_id, ordinal, testcase_dir) -> tuple[str, str]`
- `delete_testcase_files(problem_id, ordinal, testcase_dir) -> None`
- `delete_all_testcase_files(problem_id, testcase_dir) -> None`
- `renumber_testcase_files(problem_id, old_ordinal, new_ordinal, testcase_dir) -> None`
- `reorder_testcase_files(problem_id, ordinal_map, testcase_dir) -> None` — collision-free arbitrary testcase file reorder using temporary paths

Additional entrypoints (ZIP):
- `parse_testcases_zip(zip_bytes) -> ParsedTestCases` — supports Layout A
  (directory: `in/001.in`) and Layout B (flat: `001.in`); returns `.pairs`
  (ordinal to input/output bytes) and `.explanations` (ordinal to UTF-8 text
  from optional `explanation/NNN.txt`). It rejects mixed layouts, duplicate
  logical members, invalid ordinals, incomplete pairs, and invalid UTF-8
  explanations.
- `problem_to_package(problem, testcase_dir, statement_dir, language_limits) -> ProblemPackage` — projects a contest problem onto the shared package contract, carrying its stored `validator_type` and attaching validator source **only** when that strategy is interactive, so a standard problem holding a stale validator row cannot produce a self-contradictory version-2 package; raises `PackageError` when no statement file is stored
- `build_problem_export(problem, testcase_dir, statement_dir, destination, *, profile, language_limits=None, require_importable=True) -> Path` — writes through the shared version-2 package writer. A full package includes optional `editorial.md` with an independent nested digest, all test cases, the legacy `sha256` manifest, and validator source; a public package omits the editorial, private data, and `problem.json`. Full interactive exports still require validator source unless the Contest backup exporter passes `require_importable=False`.

Reuse this module when:
- adding contest-problem management features
- adding test-case management features
- adding profiling or automatic limit-setting flows
- capturing and reviewing submissions affected by running-contest limit changes
- rendering "profiling in progress" UI that needs the current active run
- implementing explicit reorder UI/actions for problems or test cases
- appending or removing ordered items while preserving dense ordinals
- reading, writing, or deleting problem statement PDFs/MDs or test case files

Do not reimplement:
- manual sibling shifting for `Problem.ordinal`
- manual sibling shifting for `ProblemTestCase.ordinal`
- route-local reorder logic based on direct ordinal edits
- ZIP parsing or export logic
- statement format detection (MD preferred over PDF)

Notes:
- these helpers are the intended API for ordered mutations
- model hooks still enforce dense ordinal invariants as a safety net
- test-case file helpers delegate to `shared.services.testcase_files`, which
  validates UUID/slug-like problem ids and verifies resolved paths stay under
  the contest test-case root
- direct writes like `problem.ordinal = 1` are not a safe substitute for `move_problem(...)`
- file I/O helpers are synchronous; always call them via `anyio.to_thread.run_sync` in async routes
- `move_problem`, `move_test_case`, and removal resequencing helpers use collision-safe ordinal updates to avoid PostgreSQL per-row unique-constraint violations
- ZIP upload (`upload_testcase_zip` route) **replaces** all existing test cases; it does not append
- add/edit test case operations are served on dedicated pages (`testcase_edit.html`), not inline on the problem edit page

---

## `profile_service.py`

Purpose:
- self-service profile validation and persistence for contest users

Main entrypoints:
- `validate_fullname(fullname) -> str`
- `validate_email(email) -> str | None`
- `validate_new_password(new_password) -> str | None`
- `update_fullname(session, user, fullname) -> None`
- `update_email(session, user, email) -> None`
- `update_password(session, user, new_password, *, current_password=None) -> str | None`

Reuse this module when:
- implementing current-user profile changes

Do not reimplement:
- current-password verification (the `/profile/password` route verifies the
  current password through `password_confirm_throttle.confirm_password` and
  then calls `update_password` **without** `current_password`, so the hash is
  checked once and under the shared lockout)

---

## `user_media_service.py`

Purpose:
- validate and persist contest-user photo and audio media

Main types:
- `AudioProcessingResult`

Main entrypoints:
- `process_audio_upload(upload, max_file_size=DEFAULT_AUDIO_MAX_FILE_SIZE) -> AudioProcessingResult`
- `get_user_media(session, user_id) -> UserMedia | None`
- `update_photo(session, user, result) -> UserMedia`
- `remove_photo(session, user) -> UserMedia | None`
- `update_audio(session, user, result) -> UserMedia`
- `remove_audio(session, user) -> UserMedia | None`

Reuse this module when:
- reading or mutating media stored in `users_media`
- validating MP3, OGG, or WAV uploads for contest users

Do not reimplement:
- the configurable audio limit, its 5 MiB hard cap, or file-signature validation
- base64 encoding and media-row creation

---

## `user_credentials_email_service.py`

Purpose:
- compose and send contest user, UberAdmin, and animator operator credential emails

Main types:
- `CredentialEmailContent`
- `CredentialEmailSendResult`

Main entrypoints:
- `build_animator_credential_email_content(...) -> CredentialEmailContent`
- `build_user_credentials_email_content(...) -> CredentialEmailContent`
- `build_uberadmin_credentials_email_content(...) -> CredentialEmailContent`
- `async send_credentials_email(email_service, *, to_email, fullname, content, actor_key) -> CredentialEmailSendResult`
- `async send_user_credentials_email(email_service, *, to_email, fullname, contest_name, contest_login_url, username, password, actor_key) -> CredentialEmailSendResult`
- `email_actor_key(actor) -> str` -- the budget identity of a Web actor (`user:<id>` / `uberadmin:<id>`)

Notes:
- both senders are async and charge the admin's email budget (`tier="admin"`);
  `CredentialEmailSendResult` reports the outcome without raising: `queued`
  when the message went to the mailer rather than out the door, and
  `budget_exceeded` (with `retry_after_seconds`) when the budget refused it --
  the batch route stops at the first such result
- body template follows the NOCA credentials plain-text structure used by admin routes
- animator credential content identifies whether the one-time plaintext token is
  global or site-scoped, including the authorized site name, for delivery to the
  administrator who generated it
- sending is delegated to `EmailService`; transport/provider behavior is inherited from email configuration

---

## `session_service.py`

Purpose:
- session-adjacent helpers tied to request/logout flow
- share request-scoped auth-token helpers used by dependencies and middleware

Main entrypoints:
- `get_validated_auth_token(request) -> TokenVerificationResult | None`
- `mark_auth_refresh_eligible(request) -> None`
- `build_logout_redirect_url(request, session) -> str`

Current use:
- auth dependencies and actor-resolution helpers
- `/logout` route

Notes:
- uses route names via `request.url_for(...)`
- respects the middleware-populated cached JWT validation result when available
- sends contest users back to their contest login page when possible
- `mark_auth_refresh_eligible` is what makes the sliding session slide, and the
  app-wide `enforce_web_default_auth` dependency calls it for every authenticated
  non-public path. That is the whole mechanism behind `POST /session/heartbeat`
  (`web/routes/session.py`), the keepalive an open page pings so a long edit does
  not end at the login page with the form discarded; the interval comes from
  `web/template_globals.py::session_heartbeat_config`

---

## `uberadmin_service.py`

Purpose:
- create and manage UberAdmin accounts with validation, generated credentials,
  searchable listing, profile updates, and enable/disable controls

Main types:
- `UberAdminCreationResult`
- `UberAdminUpdateResult`

Main entrypoints:
- `create_uberadmin_account(session, *, creator_username, fullname, email, username) -> UberAdminCreationResult`
- `list_uberadmins(session, *, query=None) -> list[UberAdmin]`
- `get_uberadmin_by_id(session, uberadmin_id) -> UberAdmin | None`
- `update_uberadmin(session, *, uberadmin_id, fullname, email, new_password) -> UberAdminUpdateResult`
- `toggle_uberadmin_status(session, *, uberadmin_id, actor_id) -> UberAdmin | None`

Reuse this module when:
- building any admin-facing UberAdmin creation flow
- building any admin-facing UberAdmin management flow

Do not reimplement:
- username/email duplicate checks for UberAdmins
- UberAdmin email update validation and uniqueness checks
- UberAdmin password policy validation
- generated password + result payload shaping
- self-disable protection for UberAdmin accounts

---

## `valkey_service.py`

Purpose:
- thin web-layer shim over `shared/services/valkey_service/`
- re-exports all shared Valkey symbols so existing `web.services.valkey_service` imports continue to work
- provides `create_web_valkey_runtime` configured from web application settings

Main entrypoints:
- `create_web_valkey_runtime(*, healthcheck_interval_s) -> ValkeyRuntime` — creates a `ValkeyRuntime` using `web.config.settings.valkey_url`

Re-exported from `shared/services/valkey_service/`:
- `ValkeyRuntime` — owns pool/client lifecycle, periodic ping health checks, reconnect attempts, and local buffering of write commands while Valkey is unavailable
- `create_valkey_pool() -> ConnectionPool`
- `enqueue_job(client_or_runtime, job, *, priority) -> None`
- `dequeue_job_id(client_or_runtime) -> str | None`
- `get_contest_queue_metrics(client_or_runtime, contest_id) -> ContestQueueMetrics | None`
- `remove_from_inflight(client_or_runtime, judgment_id) -> None`
- `publish_verdict(client_or_runtime, event) -> None`
- Queue key constants: `QUEUE_PENDING_KEY`, `QUEUE_PRIORITY_KEY`, `QUEUE_INFLIGHT_KEY`, `QUEUE_INFLIGHT_TIMES_KEY`, `QUEUE_JOB_HASH_PREFIX`, `QUEUE_RESULTS_CHANNEL`

Reuse this module when:
- enqueuing a new submission for judgment (use `enqueue_job`)
- building the autojudge worker dequeue loop (use `dequeue_job_id`)
- cleaning up after a finished or failed judgment (use `remove_from_inflight`)
- delivering verdict events to SSE subscribers (use `publish_verdict`)
- reading per-contest queue backlog directly from Valkey (use `get_contest_queue_metrics`)
- creating the web-layer Valkey runtime at startup (use `create_web_valkey_runtime`)

Do not reimplement:
- connection pool creation or URL construction
- queue key name strings (always read from the constants)
- the profiling/priority/pending fallback logic in `dequeue_job_id`

Notes:
- `app.state.valkey_runtime` is initialized in lifespan startup and is the preferred integration point for routes/services
- startup still fails fast if Valkey is unreachable
- while runtime detects Valkey outage, write operations are queued in process memory and replayed in FIFO order after reconnect
- `enqueue_job` uses a pipeline (non-atomic); `hset` + `lpush` are batched but not transactional
- per-job hash contract for new jobs includes `judgment_id`, `contest_id`, `is_rejudge`, `requeue_count`, and optional `submission_id`
- queue list payload remains only `judgment_id`; per-contest queue metrics are expected to read `judge:queue:*` + `judge:job:*`
- `dequeue_job_id` atomically moves an already queued profiling, priority, or pending job into
  inflight with Lua; it returns `None` immediately when all queues are empty
- `remove_from_inflight` and `publish_verdict` swallow exceptions and log them; callers should not rely on these raising on failure
- buffered commands are in-memory only and are lost on process restart
- internal implementation is split by concern into runtime, queue-ops, queue-metrics, pool, constants, and error-helper modules behind a shared facade

---

## Utility Modules

### `chief_judge_permissions.py`

Purpose:
- contest-scoped chief-judge authority predicates shared by the per-domain `permissions.py` modules, so the rule cannot drift across domains

Main entrypoints:
- `is_chief_judge(actor, contest) -> bool` — `True` only for the JUDGE-role user whose id matches `contests.chief_judge_id`; uberadmins and other roles never match
- `has_chief_authority(actor, contest) -> bool` — `True` for contest admins and the contest's chief judge; the pair carries decisive verdict confirmations, verdict overrides, and lifecycle-independent announcement publishing

Reuse this module when:
- a permission rule needs the chief-judge designation or the chief-authority pair

Do not reimplement:
- a per-service `is_chief_judge` variant; import it from here

### `judgment_utils.py`

Purpose:
- active judgment selection for submissions

Main entrypoints:
- `get_active_judgment(submission) -> SubmissionJudgment | None` — returns the latest non-superseded, non-failed judgment; picks by `created_at` when multiple candidates exist

Reuse this module when:
- determining the current effective judgment for a submission
- any code that needs to find the active judgment without re-querying

Do not reimplement:
- the superseded/failed exclusion logic
- the "latest by created_at" tiebreak

### `time_utils.py`

Purpose:
- timezone normalization and timeout calculation helpers

Main entrypoints:
- `normalize_now_for_reference(now, reference) -> datetime` — strips tzinfo from `now` when `reference` is naive; used for SQLite compatibility
- `elapsed_since(reference, *, now) -> timedelta` — returns elapsed wall time with timezone normalization
- `is_timeout_exceeded(reference, timeout_minutes, *, now) -> bool` — returns `True` when elapsed time exceeds the configured timeout; returns `False` for non-positive timeouts

Reuse this module when:
- checking whether a lock timeout has expired
- computing elapsed time between two timestamps with mixed timezone awareness

### `assorted_utils.py`

Purpose:
- small standalone helpers used by models/services

Current helpers:
- `format_seconds_compact(total_seconds) -> str` — converts seconds to `"Xh Ymin Zs"`, omitting zero units; returns `"0s"` for zero input
- `minutes_from_contest_start(contest_start, timestamp) -> int` — returns whole elapsed minutes between contest start and a timestamp
- `contest_minutes(timestamp_seconds) -> int | None` — returns the display minute value for a contest-relative second offset
- `format_hidden_window(total_seconds) -> str` — renders a withheld-results duration the way a person says it (`"45 min"`, `"1 h"`, `"1 h 20 min"`), for the frozen-scoreboard band. Deliberately not `format_seconds_compact`, which reads as a stopwatch. It is the Python twin of `formatHiddenWindow` in `animator/static/js/animator-render.js`: the Web scoreboard and the animator board describe the same freeze, so changing one means changing the other
- `format_site_identity(site_name, base_name) -> str` — prefixes labels as `"[site] Name"` when a site is present. The scoreboard is the one surface that deliberately does *not* use it: it gives each team two lines, name then site, so the site is rendered on its own line rather than prefixed onto the first
- `render_prettytable(headers, rows, *, header_alignments=None, max_widths=None, vertical_alignments=None) -> str` — shared ASCII table renderer for service-generated markdown/text exports

### `__init__.py`

Purpose:
- package marker only

---

## Gaps to Note

The following capabilities do not currently have dedicated service support:
- editing an existing UberAdmin
- deleting an UberAdmin
- server-side logout/token revocation

When adding those features, prefer extending the relevant existing service module instead of creating route-local business logic.
## Custom validators

Contest problem import and export services persist full-package validator
metadata in `problem.json` and source in `validator/validator<ext>` (named for the
validator's language; the legacy `validator/source.txt` is still accepted on import). Imports always
stage a fresh `PENDING` candidate and mark every packaged test case as a public
sample. Public exports omit validator source.
