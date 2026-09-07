# NOCA Web Module Architecture

This document describes the `web/` module: the server-rendered contest
administration and participant-facing FastAPI application (default port 8000).
It covers what the module owns, how it authenticates and binds team sessions,
the contest-side tables it is responsible for, and the contest-level workflows
-- problem-set release, backups, permanent removal, and non-scoring solution
tests -- whose design decisions live here. Read
[ARCHITECTURE.md](ARCHITECTURE.md) first for the module boundary, and
[ARCHITECTURE_SHARED.md](ARCHITECTURE_SHARED.md) for the problem model and
security contracts Web shares with Arena.

Related references:
- [ARCHITECTURE.md](ARCHITECTURE.md) for the system overview and the boundary between modules
- [ARCHITECTURE_RUNTIME.md](ARCHITECTURE_RUNTIME.md) for judging flow, RBAC, and background processing
- [web/docs/ROUTES.md](../web/docs/ROUTES.md) and [web/docs/SERVICES.md](../web/docs/SERVICES.md) for web-layer responsibilities
- [CONTEST_BACKUP_FORMAT.md](CONTEST_BACKUP_FORMAT.md) for the contest backup/restore ZIP format
- [DATA_FLOW_FROM_SUBMISSION_TO_VERDICT.md](DATA_FLOW_FROM_SUBMISSION_TO_VERDICT.md) for the submission lifecycle
- [FASTAPI_FLASH.md](FASTAPI_FLASH.md) for the flash-message pattern

## Responsibilities

The web module is the server-rendered contest administration and participant-facing
FastAPI app. It owns authentication and authorization, contest management, problem
management, Auto-Limit profiling requests, contest-scoped user management,
clarifications, staff task queues, submission lifecycle actions, scoreboards, and
chief-judge workflows. It applies authentication by default outside a small public
route allowlist -- the contest gateway pages, the login pages, `/health`, static
assets, the released problem-set archives, and the anonymous announcement board
(`/announcements`). Role and capability gates may raise `403` at any layer; the Web
exception boundary converts that result into the role-aware dashboard redirect and
flash described below under [Error responses](#error-responses).

## Authentication bounce and browser drafts

An authentication bounce on a contest path carries the page to return to:
`enforce_web_default_auth` appends `?next=` to `/c/{slug}/login` -- the request's
own path for a `GET`, the same-origin `Referer` for a `POST`, since a save target
cannot be revisited with a `GET` -- and the contest login honours it only after
re-validating it (`safe_contest_next_url`: same-origin, inside `/c/{slug}/`, never
the login page itself). This is what makes the browser-draft safety net reachable
in Web: both problem definition editors (Web and Arena) bind
`shared/static/js/noca-form-draft.js`, which keeps an account-scoped copy of the
form in `localStorage`, probes the session heartbeat before a Save, and offers --
never applies -- the draft on the next load, clearing it only once the save route
confirms the commit through the session (`shared/services/form_draft.py`). See
[SHARED_SERVICES.md](SHARED_SERVICES.md).

## Team session binding

A contest may also require its teams to hold **one session, from one place**,
once it has started. The rule lives in exactly one module
(`web/services/session_policy.py`) and is opt-in per user through
`users.allow_concurrent_login`, whose default is `true`, so nothing changes for a
contest that does not ask for it and nothing can change for staff, who are exempt
by **current role** rather than by that column -- an administrator whose flag the
contest-wide toggle cleared must still be able to run the contest.

Two columns carry it, and the reason there are two is that they answer different
questions. `session_epoch` is a monotonic counter -- the `session_version` /
`artifact_generation` idiom -- stamped into the token at login and compared to
the row on every request, which gives "every other session of this user is
signed out" from PostgreSQL alone, with no dependency on the revocation store or
on Valkey. `locked_ip` (with `locked_at`) is the address the user's sessions are
bound to.

**Every login advances the epoch, including logins before the contest starts,
but the epoch is only enforced from the start instant onward.** That asymmetry is
what makes the cutover pick a session rather than a coin. Several pre-start
logins that shared one epoch would all match the row when enforcement begins, and
the survivor would be whichever session made the first request afterwards -- a
race a forgotten browser tab polling in the background wins against the machine
at the venue. With the unconditional bump, each pre-start session already carries
a distinct epoch and the one that logged in last is the one that survives. The
*address*, by contrast, is bound by the first authenticated request after the
start, so a team that logged in at home and walked to the venue is never
interrupted at the gun; that request now necessarily comes from the surviving
session. A login from an address the user is already bound to is accepted and
still advances the epoch, so a crashed browser is recoverable; a login from any
other address is refused after the password check, which is deliberately not an
authentication failure -- it charges no throttle and says which address the
sessions are bound to.

**Every transition is one conditional statement, never a read-then-write.** Two
requests can observe the same unbound user and two logins can read the same
epoch, so binding, relogin and release are each an `UPDATE ... WHERE` whose guard
decides the outcome and whose `RETURNING` supplies the value the token carries.
The login-time bump is guarded *by the binding itself* rather than performed
first, so a refused attempt cannot advance the epoch and sign out the session
that is actually competing.

**Enforcement is per request, not per login**, or a copied cookie would work from
anywhere and the address rule would be decoration. It runs in the global
authentication dependency (`web/services/session_guard.py`) rather than in the
three actor resolvers, because Web also has `POST /session/heartbeat`, which
resolves no actor at all: a rule written into the resolvers would have left the
one endpoint whose entire purpose is to extend a session as the one endpoint that
never checked whether the session still exists. It runs *before* the request is
marked refresh-eligible, so a session the policy rejects is not handed a rotated
cookie on its way out. The contest row is loaded only for a user the policy could
govern at all, so the ordinary request costs the user query the resolver was
going to run anyway.

Two consequences are accepted rather than mitigated. A forgotten background tab
*can* still bind the address if it is also the session that logged in last, and
the recourse is the administrator's release rather than an endpoint allowlist or
an explicit activation step. And an open Runs SSE stream resolves its actor once,
at connection time, so a superseded session keeps that read-only feed until it
reconnects.

The organiser drives all of this from two controls, and they are deliberately
different kinds of thing. The **contest-wide toggle** on the enrolled-users page
is a preference -- it sets `allow_concurrent_login` for every *team* of the
contest, in one guarded statement that skips rows already holding the requested
value, and it can be set at any time, including before anyone has logged in. It
covers teams alone because staff are exempt by role: setting their flag would
change nothing while making the page imply otherwise. Lifting it **releases the bindings it
made**, so the round trip is a fresh start: re-applying the rule binds each team
wherever it is then. Keeping those addresses was the original decision -- they
are inert the moment the flag is set, and they record where each team sat -- but
it made the round trip a trap, since re-applying the rule enforced addresses
captured before the lift and refused every team that had moved, at exactly the
moment an organiser was trying to let them back in. The release does not bump
`session_epoch`, for the same reason the reaper does not: the policy has stopped
applying, so there is no session left to supersede. The per-user control follows
the same rule, or the guarantee would hold for a contest and break for one team. The same flag is offered
per-user on the create and edit forms, and on the batch-import form, where the
checkbox is only a *default*: a roster row may state `allow_concurrent_login`
itself, and a stated value wins and applies on update as well, because a value
written in the file is the author saying what that user's policy is while the
checkbox only decides for rows that are silent. A silent row updating an
existing user therefore leaves the flag alone, so re-uploading a roster never
reverses a per-team decision, and an unreadable value fails its row rather than
being guessed at -- reading a typo as `false` would silently restrict a roster
and reading it as `true` would silently leave one unrestricted. The user export
emits the field on every row for the same reason, so a roster moved between
contests keeps the policy it left with instead of inheriting the destination's
checkbox.

After the contest ends the policy stops enforcing, so a binding left behind is
already inert -- but `start_time` is editable, and a rescheduled or re-run
contest would begin enforcing again against addresses recorded at the previous
sitting, refusing every team from a seat it never sat in. A reaper
(`web/services/session_lock_reaper.py`, on the same `run_reaper_loop` used by the
task and clarification reapers) therefore releases the bindings of ended
contests. It clears the address only and never bumps the epoch: there is nothing
left to supersede, and bumping would sign out teams still reading their runs and
the final scoreboard. It works per contest rather than per user, so a cycle costs
the same whatever the roster size. Like every other in-process reaper it is
opt-in, since the stale binding it prevents only arises when a contest is re-run
by moving its start time -- a re-run staged as a new contest carries none.

**Clear IP lock** is not a preference but a *release*, and it is the reason the
feature is safe to turn on at all. A team whose DHCP lease changed, that moved
from wifi to cable, or whose machine died is otherwise locked out for the rest of
the contest with no recourse. So the button sits on each bound team's row --
beside the address it is bound to -- and, unlike **Remove**, stays available
*during* the contest, because a running contest is the only time a lock exists.
It clears the address **and** bumps the epoch, since clearing the address alone
would leave every session bound to it still valid and the first of them would
simply re-bind what the operator just released. It is password-confirmed through
the shared `web:password-confirm` budget and audited at warning severity naming
the released address: handing a running contest's team a fresh start from a new
address is a legitimate recovery, and the audit row is what keeps it
distinguishable from a quiet act of help.

## Team presence and the team status map

Web tracks which contest **teams** are active right now, and two surfaces read
it: the scoreboard's absence marker (shared with the animator) and the team
status map at `/c/{slug}/team-status`. The signal is the shared
`user_presence` service under the `contest` identity domain, written by
`web/services/contest_presence.py` from `enforce_web_default_auth` on every
team's authenticated `GET` once the session policy has allowed the request.
No client change was needed: every contest page already re-fetches the contest
clock once a minute, and that request is authenticated, so presence rides it.
The marker expires after `NOCA_WEB_PRESENCE_TTL_SECONDS` (default 180 s, room
for two missed polls); an explicit logout drops it at once, since that is the
one moment the seat is known to be released. It is best-effort throughout -- a Valkey outage is
swallowed on write and reads degrade to "everyone offline", which lands back on
the sign-in window rather than on a screen of false alarms.

The marker's **value** is the client address, so the same key answers *whether*
a team is present (it exists) and *where* (what it holds). Readers never decide
"online" from the value, so Arena's historical `"1"` and Web's addresses coexist
under one reader.

The **team status map** is a contest section of its own, open to uberadmin,
admin, judge and staff -- the people running the venue -- and therefore not an
administration page, which judges and staff never reach. It shows one card per
team, grouped by site in the enrolled page's order, each in one of three states:
*online* (marker present), *never signed in* (no successful sign-in at or after
the contest start) and *offline* (the rest). "Never" is deliberately the shared
`load_teams_without_sign_in` question the scoreboard already asks, so the two
boards cannot disagree on who is late; the address shown is the live one for an
online team and the latest post-start sign-in for an offline one. The board is
built for triage at three hundred teams: inside a site the empty seats come
first and the online teams fold into a count, a site with an empty seat is
listed before one without, and which empty seat is the alarm follows the
contest phase: before the start a team that has not signed in yet is expected
and folds into the count beside the online ones, and only once the contest runs
is it shown as an empty seat. The
reader's scope and unfolded sites ride the URL, and the page polls its own
current URL through htmx every 10 s and swaps the grid, the shape the
scoreboard uses, so there is no JSON twin to keep in step with the template.
Each state is told three ways on its card -- tint, icon and word -- so the wall
display still reads on a projector, in greyscale and through a screen reader.
The Web routes, service and template are described in
[web/docs/ROUTES.md](../web/docs/ROUTES.md) and
[web/docs/SERVICES.md](../web/docs/SERVICES.md); the shared service contract is
in [SHARED_SERVICES.md](SHARED_SERVICES.md).

## Error responses

Web adds one authenticated-browser policy on top of the shared neutral error
contract described in [ARCHITECTURE_SHARED.md](ARCHITECTURE_SHARED.md). Its HTTP exception handler intercepts every `403` raised by a route,
dependency, or service after authentication, resolves the actor's role from the
validated token, and redirects contest users to `/c/{slug}/` or UberAdmins to
`/uberadmin/`. A danger flash names the role that cannot access the feature.
Normal requests use a `303`, which safely turns denied POSTs into dashboard GETs;
HTMX requests use `HX-Redirect`. An unauthenticated `403` remains an ordinary
HTTP error response.

## Contest data model

The web module owns the contest-side tables of the shared schema. Two of them
carry per-user payloads and contest-scoped configuration, and the clarification
tables carry two kinds of team notification that cannot share one storage
shape.

Contest-user photo and audio payloads live in `users_media`, keyed one-to-one by
`user_id` with cascade deletion from `users`. The table keeps the original photo,
derived avatar, MIME metadata, update timestamps, and an optional MP3, OGG, or WAV
clip. Web routes load this row explicitly so normal authentication and user-list
queries do not fetch multi-megabyte base64 payloads. The animator module reads the
same shared table for team presentation media.

Contest-scoped programming language availability is stored in the `contest_languages`
junction table. The web layer uses `get_contest_languages(session, contest)` as the
authoritative query for contest-scoped language lists; the autojudge continues to use
all active languages from the registry.

### Clarification notifications

Clarifications carry **two** kinds of team notification, and they cannot share one
storage shape. An answer belongs to the team that asked, so its read state is the
row's own `clarifications.answer_read_at`. A judge or admin **announcement** is one row
read by *many* teams, so a per-row scalar cannot express it: read state lives in
`clarification_reads(clarification_id, user_id, read_at)`, a composite-PK junction where
absence means unread. No marker is ever seeded on an ongoing basis — not at contest start,
not when a team is created — which is what makes a team created after publication (a late
registration, a mid-contest addition) correctly see the announcements it missed rather
than being silently caught up. The one exception is a single backfill in the migration
that introduced the table, which marked every announcement then existing read by every
team then existing: without it, deploying the feature would have badged the entire
installed base with the whole announcement history at once, exactly as the earlier
`answer_read_at` migration backfilled from `answered_at` for the same reason. The table is
append-only
(written once at first render, never updated, removed only with its contest or by FK
cascade), so it stays on the server-wide autovacuum defaults with no per-table tuning.
It is deliberately **not** archived in a contest backup: read state is per-team UI state,
and "unread" is the safe default for a restored contest.

Which rows are announcements is **stored**, in `clarifications.is_announcement`
(`Boolean`, NOT NULL, server default `false`), set by `create_announcement()` and never
flipped afterwards. Deriving it from the author's role would have been free — the
contest-scoping join already loads that row — but wrong: `update_user()` can change a
role, which would reclassify historical clarifications in both directions, a promoted
team's old questions becoming announcements and a demoted judge's announcements becoming
questions. The stored flag also makes the team dashboard's two counters *disjoint by
construction* rather than by coincidence: an announcement stores `team_id` as its author,
so the own-answers counter must exclude the flag or a demoted author would be counted
twice in the merged number.

## Problem-set release

Finished contests whose problem set has been released (`is_past` and
`release_problem_set_after_end`) expose their complete problem materials to
**anonymous** callers:
`GET /problem-set/{slug}.zip` (listed in Web's public auth allowlist) streams one
ZIP with an `index.json` manifest plus each problem's full version-2 package
spliced under `problems/{ordinal:03d}-{label}/`. Because the packages are built
with `require_importable=False`, a contest holding an interactive problem whose
validator source was removed still exports. The package-merge step lives in
`shared/services/problem_package/merge.py` and is shared with contest backups.

That flag is deliberately **not** the scoreboard's. Publishing standings and
publishing every secret test case, validator source and editorial are separate
decisions with separate audiences — a contest may release its problems and
editorials for study while standings stay embargoed, or release standings while
keeping test data private so the problems can be reused in a mirror contest — so
each has its own column and all four combinations are legal. Only the second half
decoupled: `is_past` remains an unconditional `AND` in the route, because
publishing secret material mid-contest would break the contest itself. Since
`is_past` derives from `start_time + duration_minutes`, extending a running
contest withdraws the download again, which is the safe direction and needs no
bookkeeping. `release_scoreboard_after_end` continues to gate the scoreboard and
the team submissions download, and the migration that introduced the split
backfills from it, because every contest with a released scoreboard was already
serving this archive.

Two admin surfaces write the flag, and both are usable in any contest state:
a contest-configuration radio on the metadata form — setting it before the end
arms the publication for then, which needs no scheduler because the gate is
evaluated per request — and `POST /c/{slug}/admin/release-problem-set`, whose
guard is asymmetric: publishing requires `is_past` (arming is the form's job)
while withdrawing is always allowed, serving as both Revoke after the end and
Cancel on an armed contest. An armed contest shows the pending publication and
its Cancel on the admin dashboard, so the decision cannot be forgotten between
configuration and contest end. Both directions are audited through
`shared.services.admin_audit`, publication at warning severity.

Since that endpoint is anonymous, it is Web's most exposed one, and it is
guarded twice. Every request first counts against a per-IP fixed window
(bucket `web:problem-set`, `NOCA_WEB_PUBLIC_RATE_LIMIT_PROBLEM_SET_*`, through
the shared `request_rate_limit` primitive) checked ahead of the gate query, so
a `429` never confirms a slug. Then, when
`NOCA_WEB_PUBLIC_PROBLEM_PACK_PATH` is set, `web/services/problem_set_cache.py`
builds each contest's archive once, publishes it atomically (temp sibling +
rename) with a `.sha256` sidecar, and serves the cached file while the sidecar
digest matches; concurrent first-hit requests are serialized by a per-slug lock,
and the gate check itself is one indexed query per request. Without the setting,
every download is rebuilt — a development-only posture that production refuses
outright, and now refuses at startup: Web will not boot in production without the
setting, because the same directory also backs the contestant-facing per-problem
export. The route's guard remains, so with `ENVIRONMENT=production` and no cache
path it answers
`503` before any query, since in that state every download is refused
regardless of slug. The other anonymous Web read, `GET /c/{slug}/live/feed.json`,
carries its own per-IP window (`web:live-feed`) and a five-second public
`Cache-Control`. Withdrawing a release
also discards that contest's cached archive, so a later re-release cannot serve
an archive built before the problems were edited; the gate, not the discard, is
what makes the withdrawal effective everywhere.

## Contest backups

Contest **backups** are versioned independently, and the format is now at **version 6** —
which the server restores, and *only* version 6. Strict row validation compares each archived row
against the *live* table, so every column added to `problems`, `clarifications` or `users` forces a
bump: version 2 introduced the stored strategy, version 3 the nullable editorial, version 4 the
stored announcement flag, version 5 the `public_export_generation` counter, and version 6 the four
team session-binding columns.

Version 6 is also where the archive stops treating a row as one kind of thing. `users` now carries
both contest *policy* (`allow_concurrent_login`, an organiser's decision, restored as archived) and
live *session state* (`session_epoch`, `locked_ip`, `locked_at`, which describe sessions of the
contest that was archived and exist nowhere in the restored copy). Restore preserves the first and
resets the other three, so a restored contest cannot bind a team to the address of a machine that
went home with the contest that was backed up.

Support for versions 1 to 4 was **deliberately dropped** with the version 5 bump, and the
simplification is
the point. Each retired version needed its own set of columns-it-predates plus an inference rule for
what those columns would have held — the validation strategy guessed from validator-row presence, the
announcement flag guessed from the archived author's role — and every such rule was a place where the
integrity checker and the restorer could disagree, admitting an archive that validates as one kind of
row and restores as another. Keeping them in step required a shared predicate per rule, written once
and consulted from both sides. Version 5 states every column, so the inference layer is gone
entirely, along with the two modules that held those predicates and the "version 1 covers two archive
shapes" ambiguity that motivated them. An older archive is refused with a message naming the
supported version rather than restored approximately.

## Permanent contest removal

Permanent inactive-contest removal coordinates all three infrastructure
boundaries synchronously. Web locks and rechecks the inactive contest row,
collects its database and runtime identifiers, and requires strict Valkey
cleanup before changing PostgreSQL or files. It then moves each problem's PDF,
Markdown, and test-case directory into guarded same-filesystem quarantine and
deletes the contest graph in one PostgreSQL transaction. The transaction also
adds one warning-level `contest_deleted` security event containing only the
acting UberAdmin ID, that UberAdmin's username as the event actor label, and the
deleted contest ID. A pre-commit failure rolls back the
database and restores the quarantine; a successful commit erases it. Global
languages, global problem categories, UberAdmins, unrelated contests, and
existing security events remain outside the deletion graph.

## Non-scoring solution tests

JUDGE, ADMIN, and UBERADMIN actors can run a candidate solution against any problem
in an active contest through the real compiler, sandbox, limits, test cases, and
custom validator, and see a verdict — with zero effect on the competition.

The runs live in their **own** tables (`solution_test_runs`,
`solution_test_case_results`) rather than behind an `is_test` flag on
`submissions`. A flag would make correctness depend on every present and future
consumer remembering `WHERE is_test = false`, and a single missed filter silently
corrupts standings. Separate tables make leakage into standings, balloons, Runs,
reports, feeds, and exports **structurally impossible** rather than test-enforced.
The worker enforces the same boundary by signature: `process_solution_test_job`
takes no Valkey handle, so it cannot publish a verdict event, invalidate the
scoreboard cache, or create a balloon task.

Execution semantics mirror real submissions rather than profiling: an ordinary
problem runs *every* test case and aggregates the verdict, while an interactive
problem stops at the first case that does not end `AC` — the custom validator
replay's own contract. Interactive attempts are routed to the solution-test tables
by an `attempt_target` axis independent of `domain`, since a solution test always
runs against a *contest* problem.

Each ordinary case-result row snapshots the executed problem input, expected
output, and submission output for later diagnosis. Each value is capped at 10
KB, including an explicit truncation marker, so one run cannot duplicate
unbounded test data in PostgreSQL. Interactive runs retain their transcript
instead because they have no static expected output.

Runs reuse `JudgmentStatus` so the autojudge state machine is reused verbatim.
They share the contestant queues and the `priority=contest.is_running` rule, are
retained for the life of the contest and purged only by permanent contest removal,
and are **excluded from contest backups** exactly as Auto-Limit profiling runs are.
