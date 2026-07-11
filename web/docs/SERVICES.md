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

## `clarification_service/`

Purpose:
- full lifecycle management for contest clarifications: creation, acquisition, answering, release, moderation (hide/unhide), and role-scoped listing with redacted judge identity for non-admin viewers

Internal structure:
- `errors.py` — service exception types
- `views.py` — `ClarificationView` plus lock-merging helpers
- `queries.py` — contest-scoped reads and role-filtered listing
- `lifecycle.py` — creation, acquisition, answering, release, announcement, and hide/unhide flows

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

Main entrypoints:
- `create_clarification(session, contest, actor, *, problem_id, question) -> Clarification` — TEAM only; contest must be running; question is immutable
- `create_announcement(session, contest, actor, *, problem_id, announcement) -> Clarification` — ADMIN/JUDGE only; contest must be running; creates a clarification pre-answered with `question="Announcement"`, `is_contest_public=True`; actor is recorded as both team and judge
- `get_clarification(session, contest, clarification_id) -> Clarification | None` — contest-scoped lookup; no actor; caller is responsible for authorization
- `list_clarifications(session, contest, actor, lock_client) -> tuple[list[ClarificationView], bool]` — merges PostgreSQL rows with Valkey lock state; bool indicates whether lock coordination is available for the UI
- `acquire_clarification(session, contest, actor, clarification, lock_client) -> Clarification` — JUDGE only; acquires a Valkey TTL lock keyed by contest and clarification id
- `release_clarification(session, contest, actor, clarification, lock_client) -> Clarification` — JUDGE may release own lock; ADMIN/UBERADMIN may force-release any lock through Valkey
- `answer_clarification(session, contest, actor, clarification, lock_client, *, answer, is_contest_public) -> Clarification` — enforces the Valkey lock when available; in degraded mode the DB remains authoritative for answer validity and judge identity
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
- `list_clarifications` joins through `Problem` to scope by `contest_id`; there is no direct contest FK on `Clarification`
- `is_contest_public` is the only public-visibility flag on the model; setting it `True` when answering makes the Q&A visible to all teams
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
- `uberadmin_login(username, password, session, *, ip_address=None, user_agent=None) -> str`
- `user_login(username, password, contest_id, session, *, ip_address=None, user_agent=None) -> str`
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
  `Retry-After`
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
- the global gate allows only `/`, `/contests`, `/login`, `/c/{slug}/login`,
  `/health`, `/favicon.ico`, `/assets/*`, and `/static/*` without a valid
  session cookie
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

## `contest_service/`

Purpose:
- active and inactive contest lookup
- contest dashboard grouping
- contest metadata validation/update
- past contest deactivation
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

Main entrypoints:
- `get_contest_by_slug(slug, session) -> Contest`
- `get_contest_by_id(session, contest_id) -> Contest | None`
- `get_inactive_contests(session) -> list[Contest]`
- `deactivate_past_contest(session, contest_id) -> Contest | None`
- `ensure_contest_admin_or_uberadmin(actor) -> None`
- `contest_metadata_validation_errors(exc) -> list[str]`
- `build_contest_metadata_form_data(contest) -> dict[str, Any]`
- `build_contest_metadata_view(contest, *, site_names=None) -> ContestMetadataView`
- `build_contest_metadata_view_with_sites(session, contest) -> ContestMetadataView`
- `contest_status_label(contest) -> str`
- `build_contest_clock_payload(contest) -> dict[str, int | str]`
- `get_active_contests_grouped(session) -> ContestDashboardGroups`
- `validate_contest_metadata_update(contest, *, metadata, site_names=None) -> ContestMetadataResult`
- `update_contest_metadata(session, contest, actor, *, metadata, site_names, language_ids) -> ContestMetadataResult`
- `build_blank_contest_form(default_start_time) -> dict[str, Any]`
- `create_contest_with_owner(session, *, creator_username, contest_name, login_slug, metadata, owner_username, owner_fullname, language_ids) -> ContestCreationResult`
- `ensure_contest_has_sites(session, contest) -> None`
- `validate_chief_judge_assignment(session, contest, user_id) -> list[str]` — validates that a user is a JUDGE member of the contest; returns errors list (empty = valid)
- `clear_chief_judge_if_no_longer_judge(contest, user) -> None` — clears `chief_judge_id` if the user's role changed away from JUDGE; call after role changes before commit

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
- `update_contest_metadata` now also synchronizes `contest_languages` while the contest is upcoming. Removed languages trigger transactional cleanup of stale `problem_language_limits` rows across that contest's problems; newly added languages rely on fallback problem limits until explicit overrides are set.
- contest metadata includes `allow_print_requests`, which remains editable even while the contest is running/past (explicit exception to the general lock behavior for non-timing fields).
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
- `acquire_submission_review(session, judgment, actor, contest, lock_client) -> SubmissionJudgment` — JUDGE only; acquires a Valkey TTL lock keyed by contest and judgment id; blocks if actor already confirmed this judgment or if `final_verdict` is already set
- `release_submission_review(session, judgment, actor, contest, lock_client, *, force=False) -> None` — releases the Valkey review lock; `force=True` allows privileged force-release
- `set_chief_judge(session, contest, judge_id, requesting_user) -> Contest` — owner or UberAdmin only; accepts `None` for no chief judge, otherwise validates the selected user via `validate_chief_judge_assignment`
- `list_contest_judges(session, contest) -> list[User]` — returns contest judges ordered by full name then username
- `get_chief_judge_admin_panel(session, contest) -> ChiefJudgeAdminPanel` — returns current chief judge, assignable judges, and whether removal is allowed
- `remove_chief_judge(session, contest, requesting_user) -> Contest` — owner or UberAdmin only; blocked once the current chief judge has executed any verdict override in the contest
- `override_verdict(session, submission_id, new_verdict, reason, chief_judge, contest) -> VerdictOverride` — contest-scoped DONE-only override; creates the `VerdictOverride` row and relies on the submission model hook to update `SubmissionJudgment.final_verdict`
- `get_judging_history(session, submission_id, requesting_user, contest) -> JudgingHistoryResponse` — assembles audit-derived auto/rejudge rows plus explicit override rows, excluding status-only transitions
- `rejudge_submission(session, submission_id, chief_judge, contest, lock_client=None) -> SubmissionJudgment` — chief judge only; supersedes the active judgment, force-releases its Valkey review lock when available, and creates a new `QUEUED` judgment
- `queue_limit_change_batch_rejudges(session, batch, contest, actor, lock_client, *, language_id=None) -> list[SubmissionJudgment]` — ADMIN/UBERADMIN only; requeues pending rows from one persisted problem-limit-change batch and marks drifted rows as `STALE`
- `confirm_verdict(session, submission_id, verdict, judge, contest, lock_client) -> HumanSubmissionConfirmation` — creates a human confirmation for the active `DONE` judgment; requires the judge to hold the review lock when Valkey is available; the submission model hook derives `final_verdict` from confirmations
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
- `confirm_verdict` raises `ReviewNotHeldByActorError` when the judge does not hold the review lock
- `remove_chief_judge` raises `ChiefJudgeRemovalBlockedError` when the current chief judge has already executed an override in the contest
- `rejudge_submission` raises `NoFinalVerdictError` when the active judgment has no final verdict yet
- `queue_limit_change_batch_rejudges` is intentionally idempotent at the batch-row level: rows are processed once into `QUEUED` or `STALE`
- override authority lives in the `web.models.submission` hook; this service only inserts `VerdictOverride`
- TEAM callers are forbidden from judging-history and review access; STAFF may fetch history JSON but cannot access the HTML review page

---

## `scoreboard/`

Purpose:
- compute, cache, and serve ICPC-style contest standings
- apply contest freeze/final-release behavior consistently across roles

Internal structure:
- `models.py` — scoreboard DTOs
- `serialization.py` — cache serialization helpers
- `computation.py` — pure ICPC scoring logic
- `service.py` — DB loading and Valkey cache orchestration

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

## `rate_limit_service.py`

Purpose:
- enforce per-team submission rate limits using a PostgreSQL sliding-window count

Main entrypoints:
- `acquire_submission_rate_lock(session, team_id)` — acquires a transaction-scoped PostgreSQL advisory lock keyed on `team_id`; no-op on non-PostgreSQL dialects so SQLite test fixtures work without patching
- `check_submission_rate_limit(session, team_id, window_seconds, max_submissions) -> tuple[bool, datetime | None]` — acquires the lock, counts submissions in the rolling window, returns `(True, None)` if within limit or `(False, next_allowed_at)` when the limit is reached

Reuse this module when:
- adding rate limiting to any web submission endpoint

Do not reimplement:
- the advisory-lock + count pattern (use this service directly)

---

## `submission_service.py`

Purpose:
- list submissions visible to the current actor
- create a submission and its initial queued judgment atomically
- build per-team submission archive ZIPs for finished contests

Main types:
- `DuplicateSubmissionError`
- `SubmissionRateLimitError`

Main entrypoints:
- `list_submissions(session, contest, actor) -> list[Submission]` — TEAM users see only their own submissions; other allowed roles see all contest submissions with eager-loaded team, team site, judgments, judge confirmations, judge sites, overrides, and reviewer site
- `create_submission(session, actor, contest, problem_id, language_id, source_code, source_hash, source_size, *, rate_limit_window_seconds=60, rate_limit_max_submissions=3) -> tuple[Submission, SubmissionJudgment]` — checks the per-team rate limit (raises `SubmissionRateLimitError` if exceeded), creates `Submission` plus initial `SubmissionJudgment(status=QUEUED)`, and inserts the explicit WEB audit row
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
- uses `icpc_minutes_from_seconds` from `shared.timing` for ICPC-rounded run times
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

## `task_service/`

Purpose:
- full lifecycle management for contest tasks: creation, listing, acquisition, release, finish, and duplicate-print protection

Internal structure:
- `errors.py` — service exception types
- `views.py` — `TaskView` plus lock-merging helpers
- `queries.py` — contest-scoped reads and role-filtered listing
- `lifecycle.py` — creation, acquisition, release, and finish flows

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
- `TaskView`

Main entrypoints:
- `create_sos_task(session, contest, actor) -> Task`
- `create_print_task(session, contest, actor, *, problem_id, source_code) -> Task` — requires `contest.allow_print_requests=True`, validates source code size against `contest.max_problem_file_size_bytes`, and deduplicates by source hash
- `create_balloon_task(session, *, problem_id, team_id) -> Task` — system-level call with no actor or contest-running requirement
- `get_task(session, contest, task_id) -> Task | None` — includes SOS tasks (NULL `problem_id`) via LEFT JOIN through team user
- `list_tasks(session, contest, actor, lock_client) -> tuple[list[TaskView], bool]` — merges PostgreSQL rows with Valkey lock state; bool indicates whether lock coordination is available for the UI
- `acquire_task(session, contest, actor, task, lock_client) -> Task` — STAFF only; acquires a Valkey TTL lock keyed by contest and task id
- `release_task(session, contest, actor, task, lock_client) -> Task` — STAFF may release own lock; ADMIN/UBERADMIN may force-release any lock through Valkey
- `finish_task(session, contest, actor, task, lock_client) -> Task` — enforces the Valkey lock when available; PostgreSQL remains authoritative for finished state and finisher identity

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

Main types:
- `ProblemInfo` — lightweight problem descriptor (label, title, color)
- `LanguageInfo` — lightweight language descriptor (id, name, icon)
- `CellValue` — count + percentage cell for cross-tables
- `ProblemSummaryRow` — row for problem summary (runs, AC count/%, AC+PE count/%)
- `DistributionRow` — row for distribution tables (problem, count, %)
- `TeamRow` — row for team x problem table (team display, totals, per-problem cells)
- `TimeWindow` — one bar in time-distribution charts (label, all_count, accepted_count)
- `ContestReport` — all aggregated data: problem summary, distributions, cross-tables (problem×verdict, problem×language, language×verdict), team×problem, time windows

Main entrypoints:
- `compute_contest_report(contest, submissions) -> ContestReport` — filters to DONE judgments with non-null final_verdict; aggregates all data in a single pass; respects `contest.accept_pe` for accepted predicate

Constants:
- `ALL_VERDICTS` — ordered list of all `Verdict` values used as cross-table columns

---

## `contest_user_service/`

Purpose:
- contest user validation, lookup, creation, update, removal, photo-mutation authorization, batch import, and user export shaping

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
- `ensure_user_photo_upload_allowed(actor, target_user) -> None`
- `ensure_user_photo_removal_allowed(actor, target_user) -> None`
- `get_contest_user_groups(session, contest) -> ContestUserGroups`
- `get_user_in_contest(session, contest, user_id) -> User | None`
- `get_user_by_username_in_contest(session, contest, username) -> User | None`
- `create_user(session, contest, actor, *, username, fullname, role, password, email=None, site_id=None) -> tuple[User, str]`
- `update_user(session, contest, user, *, fullname, role, password=None, email=..., site_id=None) -> str | None`
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

Reuse this module when:
- building or validating contest site-management UI
- resolving a site from either a site ID or a human site name
- enforcing contest-scoped site uniqueness and TEAM/STAFF deletion guards

Do not reimplement:
- `casefold()`-based site normalization
- the "cannot remove the only remaining site" rule
- the guard that blocks site removal while TEAM/STAFF users are assigned

Notes:
- `sync_contest_sites` updates display casing for existing sites when the normalized key matches a submitted value.
- removing a site unassigns non-TEAM/non-STAFF users automatically, but refuses removal if any TEAM/STAFF user still points at that site.

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

## `problem_service/`

Purpose:
- ordered problem mutations within a contest
- ordered test-case mutations within a problem
- deterministic append, move, and removal operations for ordinal-based collections
- problem statement I/O (PDF and Markdown)
- problem and test case ZIP import/export
- per-language fallback limits and profiling-run orchestration
- persisted affected-submission batch creation for running-contest limit changes

Internal structure:
- `models.py` — shared limit dataclasses and import-result types
- `ordering.py` — ordered problem and test-case append, move, and removal helpers
- `queries.py` — contest-scoped problem and allowed-language reads
- `files.py` — statement/test-case file I/O and ZIP export/import parsing helpers
- `importing.py` — full ZIP import orchestration for `problem.json`, statements, and test cases
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

Additional entrypoints (query helpers):
- `get_contest_problems(session, contest) -> list[Problem]` — eager-loads categories + test_cases, ordered by ordinal
- `get_problem_in_contest(session, contest, problem_id) -> Problem | None` — eager-loads categories + test_cases + language_limits + profiling_runs
- `get_profiling_runs_for_problem(session, problem) -> list[ProfilingRun]`
- `get_active_profiling_run_for_problem(session, problem) -> ProfilingRun | None`

Additional entrypoints (language helpers):
- `get_active_languages(session) -> list[Language]` — all active languages globally; used for contest creation form and uberadmin screens
- `get_contest_languages(session, contest) -> list[Language]` — languages allowed for the given contest, ordered by name; use this instead of `get_active_languages` for all contest-scoped callers
- `import_problem_from_zip(session, contest, zip_bytes, testcase_dir, statement_dir) -> ProblemImportResult` — full atomic import; supports both PDF and Markdown statements; filters `language_limits` to the contest's currently allowed languages and reports skipped IDs
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
- `validate_md_content(md_text) -> list[str]` — validates Markdown statement content; returns errors for disallowed features or oversized content (>512 KB)
- `get_testcase_path(problem_id, ordinal, ext, testcase_dir) -> Path`
- `save_testcase_files(problem_id, ordinal, in_bytes, out_bytes, testcase_dir) -> tuple[int, int]` — normalizes content to Unix line endings (LF only) before writing and returns the written `(input_size_bytes, output_size_bytes)`; the add/edit/zip handlers persist those onto the `test_cases` row. The contest test-case root resolves to `<NOCA_PROBLEM_TESTCASE_DIR>/contest`. Inline add/edit is gated to ≤ `MAX_INLINE_TESTCASE_BYTES` (10 KB) per side; larger cases use the single-case ZIP download/replace routes (`download_test_case` / `replace_test_case`, no cap)
- `read_testcase_preview(problem_id, ordinal, testcase_dir, max_bytes=32) -> tuple[str, str]`
- `read_testcase_full(problem_id, ordinal, testcase_dir) -> tuple[str, str]`
- `delete_testcase_files(problem_id, ordinal, testcase_dir) -> None`
- `delete_all_testcase_files(problem_id, testcase_dir) -> None`
- `renumber_testcase_files(problem_id, old_ordinal, new_ordinal, testcase_dir) -> None`
- `reorder_testcase_files(problem_id, ordinal_map, testcase_dir) -> None` — collision-free arbitrary testcase file reorder using temporary paths

Additional entrypoints (ZIP):
- `parse_testcases_zip(zip_bytes) -> ParsedTestCases` — supports Layout A (dir: `in/001.in`) and Layout B (flat: `001.in`); returns a `ParsedTestCases` dataclass with `.pairs` (ordinal → input/output bytes) and `.explanations` (ordinal → text from optional `explanation/NNN.txt`, UTF-8, ≤1024 chars; invalid UTF-8 or overlength raise `ValueError`)
- `build_export_zip(problem, testcase_dir, statement_dir, language_limits) -> bytes` — produces Layout A ZIP with all test cases and `problem.json`; writes optional `explanation/NNN.txt` per test case; per-language limits include repetitions, while fallback problem metadata does not
- `build_public_export_zip(problem, testcase_dir, statement_dir) -> bytes` — produces Layout A ZIP with statement and public (sample) test cases only (including any `explanation/NNN.txt`); no `problem.json`, no private test cases; intended for contestant download

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
- `update_photo(session, user, result) -> None`
- `remove_photo(session, user) -> None`

Reuse this module when:
- implementing current-user profile changes
- applying processed `ImageProcessingResult` objects to a user

Do not reimplement:
- current-password verification
- profile photo field mutation

---

## `user_credentials_email_service.py`

Purpose:
- compose and send contest user credential emails after user creation/import flows

Main types:
- `CredentialEmailContent`
- `CredentialEmailSendResult`

Main entrypoints:
- `build_user_credentials_email_content(...) -> CredentialEmailContent`
- `send_user_credentials_email(email_service, *, to_email, fullname, contest_name, contest_login_url, username, password) -> CredentialEmailSendResult`

Notes:
- body template follows the NOCA credentials plain-text structure used by admin routes
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
- `format_site_identity(site_name, base_name) -> str` — prefixes labels as `"[site] Name"` when a site is present
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
