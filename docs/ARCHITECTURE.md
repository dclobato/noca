# NOCA Architecture Overview

This document summarizes the main system design and application architecture of NOCA.
For detailed runtime behavior, module internals, coordination flows, security, and
operational consequences, see [ARCHITECTURE_RUNTIME.md](ARCHITECTURE_RUNTIME.md).

Related references:
- [ARCHITECTURE_RUNTIME.md](ARCHITECTURE_RUNTIME.md) for detailed runtime architecture and operational constraints
- [autojudge/docs/AUTOJUDGE_INFRA.md](../autojudge/docs/AUTOJUDGE_INFRA.md) for worker isolation, queue protocol, and container execution details
- [DATA_FLOW_FROM_SUBMISSION_TO_VERDICT.md](DATA_FLOW_FROM_SUBMISSION_TO_VERDICT.md) for the submission lifecycle
- [CONTEST_BACKUP_FORMAT.md](CONTEST_BACKUP_FORMAT.md) for the contest
  backup/restore ZIP format (version 5, the only version restored) and fidelity notes
- [Interactive validator guide](custom-validator/INTERACTIVE_VALIDATOR.md) for
  authoring, exit codes, and applicable limits
- [Output checker validator rationale](custom-validator/OUTPUT_CHECKER_VALIDATOR.md)
  for the planned non-interactive output-checker strategy
- [FASTAPI_FLASH.md](FASTAPI_FLASH.md) for the flash-message pattern used in the web and arena modules
- [web/docs/ROUTES.md](../web/docs/ROUTES.md) and [web/docs/SERVICES.md](../web/docs/SERVICES.md) for web-layer responsibilities
- [SHARED_SERVICES.md](SHARED_SERVICES.md) for cross-module shared services (email, network, image, Valkey, locks)

## Table of Contents

- [1. High-level design](#1-high-level-design)
- [2. Main architectural boundary](#2-main-architectural-boundary)
- [3. uv workspace and package layout](#3-uv-workspace-and-package-layout)
- [4. Data model and schema ownership](#4-data-model-and-schema-ownership)
- [5. Module summaries](#5-module-summaries)
- [6. Summary](#6-summary)

## 1. High-level design

NOCA is split into nine main runtime modules:

- `web/`: the FastAPI application that serves HTML pages, handles authentication, enforces authorization, manages contests/problems/users, and creates judging work
- `autojudge/`: the asynchronous judge worker that consumes queued judgments, compiles and runs submissions inside containers, and writes results back
- `arena/`: the FastAPI application that serves the Arena platform, with its own user identity domain, OTP-protected accounts, and login history
- `rating/`: the single-replica Arena rating worker that periodically recomputes problem difficulty, user scores, and affiliation ratings, and publishes the next-cycle timestamp to Valkey for the Arena footer
- `aiassistant/`: the Arena AI review worker that dequeues AI review jobs,
  uses the OpenAI Responses API for user-key reviews, uses the OpenAI Batch API
  for platform-key reviews, and stores feedback in the database
- `mailer/`: the single-replica outbound-email worker that drains the Valkey
  mail queue the Web and Arena processes fill with fully rendered messages and
  delivers them through the configured provider at the deployment's own pace
- `healthmonitor/`: the public health-monitoring FastAPI server that probes the
  other modules through their Valkey worker-presence keys and renders a 30-day
  uptime heatmap dashboard with live per-service statuses
- `animator/`: the standalone FastAPI presentation runtime (default port 8003) that reads
  PostgreSQL and Valkey directly to serve a live scoreboard and post-freeze reveal
  ceremony; it reuses the shared scoreboard projection and never imports `web`
- `landingpage/`: a standalone Caddy-served entry point (default internal port
  8080) that presents the NOCA environment and links to the configured Web,
  Arena, Animator, and Health Monitor deployments without using Python,
  PostgreSQL, or Valkey

Those modules are intentionally separated. The web app owns contest-admin
workflows; the autojudge owns untrusted-code execution and verdict production;
the arena owns public participant registration and authentication; the rating
worker owns periodic rating recomputation cycles so they run exactly once
regardless of how many Arena replicas are deployed; the aiassistant worker owns
external AI provider calls and cost recording; the mailer owns every
conversation with the email provider, so no HTTP process blocks on SMTP and the
deployment's sending rate is set in exactly one place; the health monitor owns
availability observation and uptime history without participating in any
business workflow; the animator owns public scoreboard and reveal presentation,
reading the shared schema directly through SQLAlchemy Core; the landing page
owns only public navigation into an environment and has no data-plane access.

Between them there is one important shared module:

- `shared/`: cross-module source of truth for database schema, enums, queue payloads, language registry helpers, logging, and shared services

## 2. Main architectural boundary

`web`, `autojudge`, and `arena` do not call each other directly through Python imports or HTTP APIs. They collaborate through infrastructure boundaries:

- PostgreSQL
- Valkey
- Shared filesystem directories

This keeps the judge isolated from the web process and makes each side independently scalable and easier to harden.
Durable Arena notifications follow this same boundary: worker-side producers
insert rows in PostgreSQL through shared schema and service helpers, and the
Arena HTTP process owns user-facing display and read state.

Outbound email crosses the same boundary, and the `mailer` worker is the only
process in NOCA that talks to a mail provider. The Web and Arena HTTP processes
hold no SMTP settings at all: every email is rendered in the process that
decided to send it and handed, fully formed, to the shared `EmailService`,
which charges the acting user's per-actor budget and pushes a `MailJob` onto the
Valkey mail queue (`mail:queue:*`, `mail:job:{id}` with a TTL). The worker
delivers it at the deployment's own pace, with retries -- or, with sending
disabled, logs it, which is what a development install runs. That enqueue is
the one Valkey write that is never buffered through an outage: a rendered
message has no database row to recover from, so an unreachable queue is
reported to the caller as "not sent" rather than accepted. Because a deployment
without a mailer would accept mail it never delivers, Web and Arena refuse to
start until a mailer has published its presence (`wait_for_mailer`, bounded by
`NOCA_STARTUP_TIMEOUT_SECONDS`); that is a startup dependency only -- a send
never checks mailer liveness, so a mailer restart refuses no request. See
[SHARED_SERVICES.md](SHARED_SERVICES.md) for the contract and the budget's
fail-open rule.

User online-presence (the green dot on avatars) lives entirely on the Valkey
side of the boundary: the shared `user_presence` service writes a short-TTL live
key per user and reads presence in batch, best-effort with no database writes, so
a Valkey outage simply shows everyone as offline.

Authenticated worker pause/resume follows the boundary too. The Arena admin can
pause or resume the queue-consuming workers (autojudge, aiassistant, mailer;
rating is always-on) from the dashboard. PostgreSQL is the authoritative, monotonic source
of truth (`arena_worker_pause_state`); the Arena route commits the pause-state
bump and an `arena_worker_command_audit` row before publishing a signed
`HMAC-SHA256` command over Valkey. That command is only an authenticated *nudge*:
each worker derives its paused state solely from committed PG rows and treats the
command as a trigger to reconcile now, so a replayed, forged, raced, or
undelivered command can never advance state on its own (workers reconcile from PG
every poll and at startup). See [SHARED_SERVICES.md](SHARED_SERVICES.md) for the
trust and ordering model.

Long-lived Server-Sent-Events connections are bounded across the same boundary.
Every SSE route in Web, Arena, and the animator holds a slot in a Valkey-backed
connection lease (`shared/services/sse_connection_limit.py`): one gauge per
module bucket and client IP, plus one per authenticated user on the Web runs
stream and both Arena streams, taken before the handler runs and released when
the client disconnects, with a TTL that the open connection renews so only a
crashed process can leak a slot. The lease fails open -- a Valkey outage never
refuses a stream -- and the animator adds a per-process ceiling that needs no
Valkey at all. See [SHARED_SERVICES.md](SHARED_SERVICES.md).

Runtime isolation:

- **web**: FastAPI server with async database and Valkey connections, serving HTTP requests (default port 8000)
- **autojudge**: Independent async worker process with fixed-width concurrency, processing judge jobs
- **arena**: FastAPI server with async database and Valkey connections, serving the Arena platform (default port 8001)
- **rating**: Independent single-replica async worker running the Arena rating recomputation loops
- **aiassistant**: Independent async worker dequeuing AI review jobs from
  Valkey, calling the OpenAI Responses API for online user-key reviews, and
  polling OpenAI Batch API jobs for platform-key reviews
- **mailer**: Independent single-replica async worker draining the Valkey mail
  queue and delivering through SMTP off any request path; pausable from the
  Arena dashboard like the other queue consumers
- **healthmonitor**: FastAPI server with a Valkey connection only (no database),
  serving the public uptime dashboard (default port 8002)
- **animator**: FastAPI server with async database (SQLAlchemy Core) and Valkey
  connections, serving the public live scoreboard and reveal presentation (default port 8003).
  Publishes a presence-only `WorkerClass.ANIMATOR` heartbeat from its lifespan, so
  the health monitor shows it as its own service
- **landingpage**: Caddy static-file and template server with no database,
  Valkey, Python, or Node.js runtime (default internal port 8080)
- No Python imports between modules; all communication goes through infrastructure

## 3. uv workspace and package layout

The repository root is a non-package uv workspace defined by `pyproject.toml`.
It provides shared development tooling and resolves these workspace packages:

- `noca-shared` from `shared/`
- `noca-web` from `web/`
- `noca-arena` from `arena/`
- `noca-autojudge` from `autojudge/`
- `noca-rating` from `rating/`
- `noca-aiassistant` from `aiassistant/`
- `noca-healthmonitor` from `healthmonitor/`
- `noca-animator` from `animator/`
- `noca-mailer` from `mailer/`

Each runtime module has its own `pyproject.toml`, build metadata, dependency list, and console script. The runtime entrypoints are:

- `uv run noca-web`
- `uv run noca-arena`
- `uv run noca-autojudge`
- `uv run noca-rating`
- `uv run noca-aiassistant`
- `uv run noca-healthmonitor`
- `uv run noca-animator`
- `uv run noca-mailer`

The module packages use Hatchling `dev-mode-dirs = [".."]` and `packages = ["."]`
so workspace installs are true live editable installs. Console scripts resolve
`web`, `arena`, `shared`, `autojudge`, `rating`, `aiassistant`,
`healthmonitor`, `animator`, and `mailer` from the repository workspace rather than copied
package directories in the virtual environment.

The runtime packages depend on `noca-shared` through the uv workspace source
mapping. This keeps shared schema and service contracts importable without
turning the root project into an installable Python package.

The `landingpage/` module is intentionally not a workspace package. Its
container copies the Caddy configuration, the HTML page and its static assets,
the startup validator, and the two product illustrations owned by `web/` and
`arena/`. It also imports the shared font stylesheet and the Public Sans, Inter,
and IBM Plex Mono files from the internal `assets-base` build stage, so the
landing page renders in the same typefaces as every other NOCA surface without
depending on a CDN. It runs no Python and no Node.js.

## 4. Data model and schema ownership

PostgreSQL is the system of record. The web, arena, and autojudge modules access
it through SQLAlchemy with async drivers.

Schema ownership is centralized in `shared/db_schema/`:

- `shared/db_schema/` (package) defines the physical SQLAlchemy Core tables
- `web/database.py` binds the web ORM base metadata to that shared metadata
- `arena/database.py` binds the arena ORM base metadata to that shared metadata
- `autojudge/db/` package uses the same shared tables directly through SQLAlchemy Core queries

Alembic migrations target `shared.db_schema.metadata`, so the migration environment
is independent of the `web`, `arena`, and `autojudge` runtime modules. Schema
stewardship is limited to the independently-deployable HTTP front doors: the `web`
and `arena` container entrypoints run `scripts/run_migrations.py` (`alembic upgrade
head` under a PostgreSQL advisory lock that serializes concurrent attempts), so a
Web-only or Arena-only install can still bring the schema to head on its own. The
worker and presentation containers (`autojudge`, `rating`, `aiassistant`,
`mailer`, `animator`) are pure schema consumers:
their entrypoints instead run `scripts/wait_for_migrations.py`, which blocks until
`alembic_version` is at or ahead of the head revision the worker image expects (a
revision the image does not recognize counts as "ahead", i.e. newer than the
worker). This keeps a mismatched worker image from driving the schema during a
rolling deploy. The optional `NOCA_WAIT_FOR_MIGRATIONS_TIMEOUT` (seconds, default
300) bounds that wait.

The web module adds application-specific ORM behavior on top of the shared tables:
relationships, computed properties, hybrid properties, model hooks, and invariants.
The autojudge intentionally does not depend on those ORM hooks. It reads and writes
only the shared schema plus its own focused worker-side data access layer.

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

The platform **announcement board** -- the global notices about new problems,
rating changes and new features that #138 asked for, as opposed to the per-contest
clarification announcements above -- is one shared `announcements` table with a
`domain` column (`AnnouncementDomain`: `web` | `arena`) set by the publishing
surface. One table keeps the code path shared, as the issue required; the column
keeps the *data* separate, so a Web-only or Arena-only install never shows the
other product's notices and neither surface can read or delete the other's rows.
Every service function takes the domain and puts it in the `WHERE`, which is what
makes a foreign id indistinguishable from an unknown one. The publisher is stored
the way `security_events` stores its actor: an opaque `published_by_id` (an
`uber_admins` row on Web, an `arena_users` row on Arena, told apart by `domain`)
plus a `published_by_label` snapshotted at publish time. No foreign key is
possible across two identity tables, and none is wanted -- a published
announcement is **immutable** (no update route, no edit form, deletion is the only
retraction, audited at warning severity) and must keep its attribution after the
account that wrote it is gone. The body is statement-grade Markdown, authored
with the same editor as a problem statement and validated by the same sanitizer,
with exactly one difference: external links are allowed (`allow_links`). The
`required` flag lands with the table so the Arena slice can mark an announcement
as one every user must acknowledge; Web never sets it. A handful of inserts a
month makes this a low-churn reference table on the server-wide autovacuum
defaults. See [SHARED_SERVICES.md](SHARED_SERVICES.md) for the service contract.

The `required` flag is given its meaning by `arena_announcement_acknowledgments`:
one row per Arena user per required announcement, composite primary key, absence
meaning pending -- the `clarification_reads` shape -- with both foreign keys
cascading, so deleting an announcement takes its acknowledgments with it (the
#138 decision) and so does deleting the user. Append-only and small, on the
server-wide autovacuum defaults. Existing users are not backfilled: a required
announcement published before they acknowledge it is exactly what they should
see. The pop-up itself is deliberately **not** part of the login redirect chain
(terms, 2FA, forced password change): those redirects fire once, while a
mandatory announcement must come back on every page until it is acknowledged.
It is instead a second app-level dependency beside the authentication gate that
runs only for authenticated HTML `GET` page loads outside `/auth`, reads the
user id from the validated token without loading the user, opens a session only
past that gate, and leaves the oldest pending announcement -- with the pending
count, for the modal's "1 of N" -- on the request for `_base.html` to render as a
static-backdrop modal whose Acknowledge button the checkbox enables. The server
never trusts the modal: an un-acknowledged announcement simply returns on the
next page. The lookup is one query bounded by the number of *required*
announcements (each a primary-key probe into the ledger), not by users or
acknowledgments, which is why no per-user Valkey marker sits in front of it: a
round trip is not cheaper than the query, and invalidating every user's marker
on publication would need a generation scheme for nothing. What *does* sit in
front of it is the user-independent half of the question. On almost every day
in almost every deployment no required announcement exists at all, so
`arena/services/required_announcement_cache.py` remembers that answer per
process for a short TTL (30 seconds, a module constant) over the shared
`SingleFlightCache`, and a page load that finds a live "none" opens no session
and runs no query. The admin publish and delete routes invalidate their own
process **after** commit -- an invalidation before it could be rebuilt from the
pre-commit state and pin the stale answer -- and the TTL bounds how late a
mandatory notice published on another replica reaches this replica's users.
When a required announcement does exist, the per-user query runs exactly as
before. A `POST` that re-renders
a page shows no pop-up for that render; the next `GET` restores it. Because the
`/announcements` prefix is public, the acknowledge `POST` requires the user
itself rather than relying on the global gate.

The animator module (public reveal/scoreboard presentation) is gated per contest by
`contests.animator_enabled` (`Boolean`, default and server default `false`, NOT NULL): only
enabled contests may expose animator snapshot/events/reveal, so a disabled contest is never
reachable by guessing its slug. The existing `sites` table carries the reveal ceremony's
per-site medal configuration — `gold_cutoff`, `silver_cutoff`, `bronze_cutoff` (positive and
ordered by the `ck_sites_medal_cutoffs_ordered` CHECK). Reveal
operators authenticate with a token whose fixed-length digest lives in `site_secrets`
(the plaintext token is never stored); a row with `site_id` set authorizes one site, while
`site_id = NULL` is a contest-global control secret. A composite foreign key
`(contest_id, site_id) → (sites.contest_id, sites.id)` keeps a secret from referencing a site
in another contest, and `(contest_id, secret_digest)` is unique. These three tables are
low-churn configuration data and use the server-wide autovacuum defaults.

Medals also exist at the **contest** level, for the global scope that has no site:
`contests.global_gold_cutoff`, `global_silver_cutoff`, and `global_bronze_cutoff` are nullable
and **all-or-nothing** — either all three are NULL (no global medals, the pre-existing
behavior, so nothing needed backfilling) or all three are set, positive, and ordered.
`ck_contests_global_medal_cutoffs` enforces exactly that, asserting `IS NOT NULL` explicitly on
its configured branch because a CHECK rejects only FALSE and would otherwise let a partial
triple such as `(1, 2, NULL)` through as UNKNOWN. Both scopes' cutoffs are edited on the same
Web admin form (`POST /c/{slug}/admin/animator/medals`) and validated in one transaction.

The cutoffs reach the animator in two places. The `/snapshot` feed bands each standing row with
a `medal` field computed from the cutoffs of the *requested* scope — the site's for a site
scope, the contest's global triple for `global` — so medals render on the live scoreboard in
both scopes. A reveal ceremony instead **snapshots** its cutoffs into
`RevealSessionState.medal_cutoffs` when the session is created, exactly as site cutoffs already
behaved: a settings change cannot reshuffle bands under an operator mid-ceremony, and adopting
one is an explicit `start-reveal` with `restart=true` (**Start over**, which the control panel
therefore also offers on an idle stored session). Both surfaces derive their band from the same
shared `medal_band_for_rank`, so they cannot disagree. The persisted state version is
deliberately **not** bumped: the change is forward-compatible, since a global ceremony recorded
before global medals existed simply carries `medal_cutoffs=None` and shows no medals. Rolling
back with a configured global ceremony in flight is recovered the same way every unreadable
payload is — `start-reveal` with `restart=true`.

Every problem in both domains stores its **validation strategy** explicitly in
`problems.validator_type` / `arena_problems.validator_type` (`ProblemValidatorType`:
`standard`, `interactive`, or the reserved `checker`), NOT NULL with no server default so
every creation path states it and a path that forgets fails loudly. It replaces the previous
derivation from custom-validator row presence, which was wrong in both directions: a problem
whose validator source was removed silently became a standard problem judged by the token
comparator, and a standard problem carrying a stale validator row looked interactive. The
strategy is **immutable** after creation, enforced at the service boundary and again by a
`before_flush` ORM guard (`shared/services/validator_type_guard.py`) in both domains; direct
SQL is outside that boundary and is not detected. Presence of validator *source* remains a
separate axis, governing revision actions -- upload, replace, remove -- so an interactive
problem whose source was removed stays interactive and simply becomes non-judgeable until an
active `VALID` revision exists.

The same tables carry `artifact_generation` (`BigInteger`, NOT NULL, default 0), a monotonic
fence for editor saves that promote filesystem artifacts. It lands with the strategy so the
schema settles in one migration; nothing increments it yet. Its purpose is recovery after a
crash: for an *import*, the problem row's existence answers "did the transaction commit",
but for an *edit* it cannot, since the problem exists either way.

They carry a **second** monotonic counter, `public_export_generation` (`BigInteger`, NOT NULL,
server default 0), and the fact that it is separate is the load-bearing part of the design rather
than an oversight. It is the cache key for the contestant-facing problem package Web serves at
`GET /c/{slug}/problems/{label}/export`: a cached ZIP records the value it was built from in its
sidecar and is served only while that value still equals the row's, so a stale file on any replica
is detected by reading PostgreSQL rather than by cross-replica invalidation.

Reusing `artifact_generation` for that would be **unsafe**, and not merely untidy. Recovery treats
`stored >= expected` as proof that a Save's filesystem promotion committed, an inference that holds
only while every bump corresponds to a real filesystem Save. The bump runs inside the caller's open
transaction, so a Save that crashes before commit rolls back, releases the problem row's lock, and
reverts the value — letting a transaction blocked behind it (a database-only action that also bumped
the shared counter) compute the very integer the crashed Save's journal recorded as its expected
value. Recovery cannot distinguish the two and would keep promoted artifacts whose database changes
never landed. Nothing downstream infers filesystem-commit state from `public_export_generation`, so
the same coincidence costs one stale cache read, corrected by the next bump. `artifact_generation`
is therefore untouched by this and stays recovery-only.

The counter is bumped inside `open_save_swap`, which is the single production caller of
`bump_artifact_generation` and is reached by the definition editor's Save, problem creation, and
every file-changing test-case action — so one site covers all three. The seven Contest actions that
deliberately skip the edit-swap machinery because they touch no file (the sample/secret toggle,
validator upload and removal, and sample-interaction create, delete, update and reorder) bump it
directly, in the same transaction as their own write. A limits-only Save bumps it too and triggers a
rebuild producing identical bytes, since the `public` profile writes no `problem.json`; that is
accepted over-invalidation, not a dependency. The column exists on both problem tables so the bump
can stay inside the domain-agnostic save swap, and both modules now read it: Arena caches its
public export **and** its sample-case ZIP under it (#204), two artifacts keyed separately but
invalidated by one bump, since everything that changes a sample changes the package too.
Arena's definition editor commits without a swap, so its update route bumps the counter
explicitly, as its no-file judgment actions do -- the same shape as Web's seven. The cache
module itself lives in `shared/services/problem_export_cache.py`, and each module has its own
cache-path setting (`NOCA_WEB_…` / `NOCA_ARENA_PUBLIC_PROBLEM_PACK_PATH`), both mandatory
in production, because nothing needs the two caches to share a location.

Because that export is reachable by every contest actor including teams, and because a package build
is far more expensive than the polled reads the router's loose `web:user-read` ceiling is sized for,
the route also carries a tight per-actor budget of its own (`web:problem-export`, default 10 per 10
minutes) charged before the cache is consulted. The cache directory is a `problem-export/`
subdirectory of `NOCA_WEB_PUBLIC_PROBLEM_PACK_PATH`, so one setting backs both package caches — and
Web now **refuses to start** in production without it, since a missing cache would otherwise surface
as a `503` to a contestant mid-contest rather than as a failed deploy.

Both problem tables also carry an optional `editorial` text column. It stores
the editor-only official explanation and solution guide as Markdown. Arena and
Web edit it through the shared problem-definition editor and apply the same
content restrictions and preview pipeline as Markdown statements; participant
pages and public package bundles do not expose it. Arena additionally carries
`arena_problems.editorial_release_policy` (`ArenaEditorialReleasePolicy`:
`never` / `always` / `after_ac`, NOT NULL, default `never`), set from the same
editor's Editorial tab. It records when the editorial should become visible to
participants. The Arena problem detail page is the first (and so far only)
reader: `never` or an empty editorial keeps the page unchanged, `always` shows
an "Editorial" link between the prev/next problem buttons that opens a
standalone Markdown viewer (`GET /problems/{arena_number}/editorial`, mirroring
the existing validator-source viewer's minimal layout) in a new tab, and
`after_ac` shows that same link only once the current user has an Accepted
verdict on the problem — a gate the route re-checks itself rather than trusting
the link's presence. Before that AC, `after_ac` renders a non-actionable
"Editorial available after AC" note in the link's place, so a solver knows an
editorial is waiting; `never` deliberately stays silent, leaving the page
indistinguishable from one whose problem carries no editorial at all. Web has no
equivalent column. The policy travels with the
problem package (as `problem.json.editorial.release_policy`), so moving a problem
between Arena installs preserves it rather than silently resetting it to `never`,
which would leave the editorial present but invisible.

Problem test-case content (both Web and Arena) lives on a single shared filesystem mount
configured by `NOCA_PROBLEM_TESTCASE_DIR`, namespaced by identity domain:
`<root>/contest/<problem_id>/NNN.in|out` for Web and `<root>/arena/<problem_id>/NNN.in|out`
for Arena. The database keeps only metadata and the normalized (LF) on-disk byte sizes
(`test_cases.input_size_bytes` / `output_size_bytes` and the mirrored Arena
`arena_test_cases.input_size_bytes` / `output_size_bytes`); the former Arena
`input_content` / `output_content` text columns were dropped. Inline (textarea) editing is
gated to cases where both sides are ≤ `MAX_INLINE_TESTCASE_BYTES` (10 KB); larger cases are
edited offline via a single-case ZIP download/replace round-trip. The autojudge reads test
files directly from the appropriate domain subdirectory.

Problem **packages** — the import/export ZIP both domains speak — are owned end to end by
`shared/services/problem_package/`, which is the single place every format decision is made:
integer coercion, null semantics, string widths, UTF-8, image resolution, archive safety, and the
export field set. The format is at **version 2**, which carries the stored `validator_type`
explicitly rather than leaving it to be inferred from validator presence; version 1 (and a package
with no version key at all) is still read, with the strategy derived from `custom_validator`
presence, which is the only thing such a package says. A `checker` package is refused centrally in
the shared parser rather than by each importer, and only version 2 is written — so a `full` export
refuses an interactive problem with no validator source, which version 2 cannot express.

Editorials extend version 2 additively. `problem.json.editorial` names
`editorial.md` and carries that member's SHA-256 digest. The editorial stays out
of the legacy top-level manifest so deployed version-2 readers can ignore the
unknown field and safe member without rejecting an over-complete manifest. The
Arena release policy rides inside that same object as `release_policy`, additive
within an already-additive field and therefore still no version bump; it is
nested there because a policy only means something when there is an editorial to
release. Absent means `never`, an unknown value is refused, and Contest — which
has no such column — parses it and exports it back as `null`.

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

Contest **backups** are versioned independently, and the format is now at **version 5** —
which the server restores, and *only* version 5. Strict row validation compares each archived row
against the *live* table, so every column added to `problems` or `clarifications` forces a bump:
version 2 introduced the stored strategy, version 3 the nullable editorial, version 4 the stored
announcement flag, and version 5 the `public_export_generation` counter.

Support for versions 1 to 4 was **deliberately dropped** with that bump, and the simplification is
the point. Each retired version needed its own set of columns-it-predates plus an inference rule for
what those columns would have held — the validation strategy guessed from validator-row presence, the
announcement flag guessed from the archived author's role — and every such rule was a place where the
integrity checker and the restorer could disagree, admitting an archive that validates as one kind of
row and restores as another. Keeping them in step required a shared predicate per rule, written once
and consulted from both sides. Version 5 states every column, so the inference layer is gone
entirely, along with the two modules that held those predicates and the "version 1 covers two archive
shapes" ambiguity that motivated them. An older archive is refused with a message naming the
supported version rather than restored approximately.

Both domain importers consume one frozen `ProblemPackage` and are left with only
category resolution, target-language availability, ORM row construction, and lifecycle queueing.
Neither direction ever holds an archive in RAM: an upload is spooled to disk in bounded chunks and
opened once, and an export is written to a temporary path the route streams and deletes.

That subsystem also owns the ordering between an import's filesystem writes and its transaction.
Artifacts are prepared in hidden siblings of their final locations (so promotion is a
same-filesystem rename), promoted, and only then committed; a failed commit deletes exactly what
was promoted. A guarded, `fsync`'d journal per import makes a crash *between* promotion and commit
recoverable, and is reconciled at Web and Arena startup as well as before each import — with every
journal-supplied path re-validated against its configured root before anything is deleted. See
[SHARED_SERVICES.md](SHARED_SERVICES.md) and
[PROBLEM_PACKAGE_FORMAT.md](PROBLEM_PACKAGE_FORMAT.md).

**Editing** an existing problem gets the same ordering through a *sibling* mechanism rather than the
import path, because both of that path's assumptions are false for an edit. Promotion would delete
the author's existing test-case directory, so a failed commit followed by the import rollback would
leave the problem with no files at all; and recovery's commit signal — does the problem row exist? —
cannot answer anything for a problem that exists either way. The edit swap therefore **quarantines**
what it displaces into a hidden same-filesystem sibling and renames it back on rollback, records that
quarantine — in the journal *before* the first rename, and in memory before the rename that would
strand it, since both windows are otherwise unrecoverable — resolves recovery on what is actually on
disk rather than on a snapshot that may have gone stale, and fences recovery on
`artifact_generation`: the journal states the value the row will hold after the Save commits, and a stored value at or beyond it means the commit landed — keep the new
artifacts and drop the quarantine — while a strictly lower one means it was lost, and the originals
are restored. Both kinds of journal live in the same directory and are resolved by the same startup
and pre-import passes, each on its own predicate. A Save materializes the complete desired test-case
directory in staging, with no exception for small or single-case edits, so no database row is ever
committed ahead of an unprotected filesystem write. A Save can also end with *less* on disk than it
began with — a Contest statement switched from PDF to Markdown drops the PDF — and that deletion goes
through the same swap: the file is parked in the quarantine rather than unlinked, so a failed commit
leaves the problem with the statement it had rather than with neither.

A problem is edited through **two** doors, and the split is deliberate. The *definition* editor
owns what the problem is -- title, statement, illustration, categories, and Contest's resource
limits -- as one form with one Save and client-side panes, because those fields belong to one
transaction and switching between them must not lose typed input. The *judgment-data* editor owns
what judging runs against -- test cases, the custom validator, sample interactions -- as separate
**pages**, because a problem can carry many cases and a case can be large, so neither holding them
all in one page nor deferring them to one Save is reasonable. Creation collects the definition only
and lands on the judgment pages.

On the judgment pages every action on data that already exists posts immediately; the only thing the
server has not seen is a row the author typed and has not saved, which that page's own Save applies
and which an upload warns before discarding. The client-side pending model that the single Save
required -- optimistic markers, undo, rehydration after a reorder swap, conflict rules, and a re-emit
path for rejected saves -- is gone with it, and with it the class of bug where the browser's idea of
the problem and the server's disagree.

Durability did not move. An action that changes **rows and files** stages the complete desired
test-case directory, promotes it, commits, and finishes, with the same edit swap and the same
`artifact_generation` fence; a single shared helper (`shared/services/judgment_case_action.py`) owns
that ordering so it is written once rather than once per endpoint. Each such action takes the problem
row's lock *before* it reads the current cases, because two actions that both snapshot the live
directory would each stage a complete replacement and the loser would reinstate what the winner
replaced. Actions that change **no** file -- the sample toggle, validator upload and removal, every
interaction mutation -- commit directly under that lock, because there is nothing on disk for a
failed commit to leave behind; that is a stated rule, not an oversight.

Staging seeds itself with hardlinks so an immediate action costs a link per case rather than a copy
of the problem's entire test data, which is precisely what made per-action posts affordable on the
large problems that motivated separate pages. The invariant that makes it safe is that nothing is
ever written *through* a link: writes go to a temporary name in the same directory and are renamed
into position. Where the filesystem has no hardlinks the seed copies instead and logs it once; see
[BOOTSTRAP.md](BOOTSTRAP.md) for what a data root must support.

Two properties make the fence trustworthy rather than merely plausible. The writes are **flushed**
before the transaction that depends on them commits -- the bytes before the rename that publishes
them, and the directory entries the promotion creates -- because a committed generation pointing at
content still in the kernel's page cache is precisely the state recovery cannot detect: the fence
tells it the Save landed. And reconciliation asks whether a journal is stale *before* resolving it.
The pre-import pass runs while the application is serving, so a Save may be between its promotion
and its commit right now, which from outside its transaction is indistinguishable from a lost commit
-- the row still holds the previous generation. A Save holds the problem row locked for exactly that
window, so a row another transaction holds means "leave this journal alone", and it is probed with
`SKIP LOCKED` inside a savepoint so the probe neither waits on the Save nor holds a lock of its own.
When more than one interrupted Save exists for the same problem, reconciliation keeps that guard
while it restores the entire newest-first journal chain. Releasing it between journals would expose
an intermediate, never-committed predecessor to a newly starting Save. Recovery also flushes the
directory entries changed by restores, quarantine deletion, and journal deletion before discarding
the recovery record, so another host crash cannot preserve only half of that decision.

Both problem tables make `output_limit_in_bytes` **NOT NULL** (server default 65536): a problem
always states an output limit, and `NOCA_JUDGE_OUTPUT_LIMIT_BYTES` is a hard global ceiling applied
as `min(problem_limit, global_limit)` rather than a fallback for a missing value. The *per-language*
`problem_language_limits.output_limit_in_bytes` deliberately stays nullable, where NULL means
"inherit the problem's limit" — which is exactly what the judge's `coalesce(per-language, problem)`
computes. Field widths are unified across the two domains (`title` 256, `author` 256, `notes` 512,
`source` 256, `license` 256, `image_caption` 512) so a value that survives on one side survives a
round trip through the other.

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

Authenticated worker pause/resume adds two Arena-owned tables to the shared schema:
`arena_worker_pause_state` (authoritative `paused`/`paused_by` plus a monotonic
per-worker, nonnegative `generation`) and `arena_worker_command_audit` (one row
per issued pause/resume attempt, including rejected and malformed worker-class
requests). The Arena route writes them; the autojudge and aiassistant workers
read pause state through
`shared/services/worker_pause_state.py`.

Arena gamification adds the `arena_user_badges` table to the shared schema: an
append-only ledger of which badge each Arena user has earned (`ArenaBadge` enum)
and when (`awarded_at`), with a unique `(user_id, badge)` constraint so a badge is
awarded to a user at most once. The award logic that inserts rows is owned by the
rating worker's badge-assignment loop (`shared.services.arena_badges`); the Arena
ORM exposes the ledger through `ArenaUser.badges`. Streak badges are backed by the
`arena_users.current_streak` / `longest_streak` / `last_ac_date` columns the loop
recomputes, and the loop tracks its incremental watermark plus last full
reconciliation in the singleton `arena_badge_cycle_state` table. Badge families
cover per-submission recovery, solve streaks, distinct solved-problem counts,
distinct-language counts per problem, first-solver and problem-set hand-in
positions, latest on-time problem-set solves after deadlines, non-AC bursts,
unbroken distinct-AC runs, and dynamic low-solve-rate problem solves.

The rating worker's problem-difficulty cycle (`rate_all_problems()`) ends by
snapshotting a 20-bin histogram of the catalogue's current difficulty
distribution into the singleton `arena_rating_cycle_state` table, read by the
Arena `/help/rating` page to render a current-distribution chart without an
aggregate query at request time. A difficulty is stored for every problem, but
it is **displayed** — and counted in that histogram — only once the problem has
at least `MIN_ATTEMPTS_FOR_DISPLAY` (5) unique attempters
(`shared/services/arena_difficulty_display.py`). Below that the Bayesian prior
pins the value to the centre of the scale, so a bare `5.0` would mean both
"medium" and "unknown"; every Arena surface instead renders the shared
`DifficultyDisplay` value, which shows a dash labelled "Not enough data yet",
and the histogram records how many problems it left out. The gate keys on the
attempter count alone: an author's estimate is a prior, not evidence.

That estimate is `arena_problems.expected_difficulty` (nullable integer on the
internal 1–100 scale, entered in the editor as one of five worded anchors —
Introductory 15, Easy 30, Standard 50, Challenging 70, Hard 85 — and carried by
the problem package as an additive optional key). The rating worker uses it as
the **mean** of the Bayesian solve-rate prior, inverted through the display
pipeline so a never-attempted problem rates exactly at its declaration; the
prior's weight is unchanged, so evidence overrides the estimate at the same
rate it overrides the flat 0.5 prior, and above the display threshold only the
measured value is ever shown. Below it, a problem with an estimate renders as
`7.0?` whose accessible label names it an estimate, so a reader can always tell
"we measured this" from "the author thinks this". Low-churn reference data on
the server-wide autovacuum defaults.

The anchors are the editor's *vocabulary*, not the column's constraint: an
imported package may carry any value in `[1, 100]`. Such a value gets an
"Imported value" option of its own in the editor's select, and the save parser
accepts exactly the value already stored alongside the anchors. Without both
halves the browser would fall back to the first option — "No estimate" — and a
Save of an unrelated field would silently discard the author's estimate.

Arena user identity carries a pseudonymous handle of its own. `arena_users.username`
(`String(64)`, `UNIQUE`, NOT NULL) is a globally unique lowercase name assigned at signup
from the shared `animal-adjetivo-NNN` generator; `full_name_public` (adult opt-in to publish
the legal name instead), `dta_troca_username` (change cooldown), and `consent_generation`
(the parental-consent epoch counter, following the `session_version` /
`artifact_generation` monotonic-counter idiom) land with it. All four are low-churn and
stay on the server-wide autovacuum defaults.

**The consent epoch is what makes a guardian's withdrawal link safe to hand out.** LGPD
art. 8 §5 grants the parent or legal guardian withdrawal at any time, so Arena mails them a
signed `PARENTAL_CONSENT_REVOKE` token. A bearer token that never expires after use would be
wrong in two ways at once: after a re-grant the original link would revoke again, and after
the guardian address changed the **former** guardian would keep authority. So the token
carries a `gen` claim bound to `consent_generation` at minting time, and the route requires
an exact match. Every transition -- grant, revoke, guardian-email change, date-of-birth
change -- bumps the counter, which is why replay and former-guardian authority are closed by
one mechanism rather than by a used-token table. The route additionally refuses to act
unless `check_age(dob)` still returns `NEEDS_PARENTAL_CONSENT`, so the link goes inert on
its own the morning the child turns 18, needing no scheduler and no stored expiry -- the
same per-request evaluation the shield itself relies on.

Because the epoch invalidates anything minted before the transition it records, the
revocation link can only live in the mail sent **after** a grant commits, never in the
consent invitation: a link minted before the grant fails the consent check while consent is
pending and carries a stale epoch afterwards, so there is no window in which it works. That
makes the confirmation mail load-bearing, which is stated rather than hidden -- a failed
delivery is audited at warning severity, and an administrator is the interim recovery.

The withdrawal itself is one write path (`user_service.revoke_parental_consent`) that the
guardian link and the admin toggle both call, so an administrative revocation cannot be
weaker than the guardian's own -- the defect that motivated this work, where the admin
toggle flipped a flag and left a suspended minor holding a live JWT. It **suspends rather
than erases**, and deliberately does not touch `ranking_visible`: the public ranking already
drops the row through `ativo`, while the affiliation aggregation filters on
`ranking_visible` alone, so clearing it would move a third party's institutional rating as a
side effect of one family's consent decision. Both routes commit the state change and its
security events **before** queueing any mail, and isolate each recipient's delivery, so a
provider refusal can neither unwind a legally effective revocation nor let a queued message
describe one that failed to commit. The `POST` re-resolves the token under a row lock and
mutates inside it, because the epoch check is otherwise a read-then-write that two
simultaneous submissions could both pass.

**Neither consent direction ever mutates on a `GET`.** Both emailed links land on a
review page that renders the decision and writes nothing -- not even throttle
accounting -- and only an explicit guardian `POST` grants or revokes, re-validating its
token at submission time. Mail scanners, preview generators, and prefetchers follow
links in email, so a `GET` that consented (as the grant link originally did) let a robot
consent on a guardian's behalf. The grant `POST` carries the `token_redeem` throttle the
old `GET` held, and refuses an account that has since dropped into the under-13 blocked
band, so a stale link cannot restore consent that a date-of-birth change cleared.

**The canonical form is the stored form.** `normalize_username()` (NFKC → strip → casefold)
canonicalizes at every write path, backed by a plain `UNIQUE` — no functional `lower()`
index and no second canonical column. A functional index reflects poorly through Alembic
autogenerate and adds SQLite test-path friction, while a `username_canonical` column would
be one more place for an invariant to drift. The accepted cost is that a handle has no
display casing. Uniqueness is guaranteed by the constraint, never by the availability
pre-check, which is a TOCTOU: signup redraws and retries inside a savepoint on a clash and
answers `409` only when the retries are exhausted.

The same migration installs `ck_arena_users_public_profile_requires_ranking`
(`NOT (public_profile AND NOT ranking_visible)`), collapsing an invariant the
`public_profile` column comment had always claimed but which lived only in three
application call sites. No equivalent constraint is possible for the *age* half of the
same policy, because age is time-varying and a CHECK is evaluated at write time — which is
precisely why that half needs both a write-path guard and a read-path mask rather than a
database rule.

That migration backfills every existing row **in one step, in Python, inside the
migration** rather than deferring to a script. `username` becomes a public display name the
moment it lands, so a placeholder cleaned up later would leave everyone's public identity as
`user-3f2b1c9d4e6a` between `alembic upgrade head` and an operator remembering to run the
script — permanently if they never do. The repo's usual migration-plus-script shape is for
backfills that are expensive or fallible; drawing a word pair is neither. It reads the word
lists through an **inlined** reader rather than importing
`shared.services.random_username_service`, because a frozen historical migration must not
couple to mutable application code — which obliges it to repeat that module's NFD folding,
since the shipped lists are Title-Case with diacritics and an unfolded read would write
handles that Arena's own validator rejects. An unreadable word list falls back to
id-derived handles rather than bricking the upgrade.

That handle is what Arena actually **publishes**. Every public read path resolves the name it
renders through `arena/services/user_visibility_service.py`, never from `arena_users.nome`:
the display name is the legal name only for an adult who set `full_name_public`, and the
username for everyone else — a 13-17 year-old, an account whose date of birth is unknown
(the shield **fails closed**), and any adult who never opted in. The stored `public_profile`
flag is masked the same way, so a row that predates the rule cannot publish a profile the
shield would refuse, and a shielded user's masked email is withheld entirely, since a masked
address beside an affiliation and a country re-identifies a minor.

The shield is layered on the age oracle in `shared/age_check.py`, which is evaluated per
request. That is what makes it need no scheduler and no stored expiry: a shielded account
stops being shielded on the morning of its eighteenth birthday, and turning 18 only
*unblocks* the opt-in rather than turning a flag on. It deliberately never touches
`ranking_visible` — a minor stays in the ranking, under their pseudonym — which is why
`_eligible_users_where()` carries no age predicate.

The per-request evaluation is exactly why the shield cannot be a read-path rule **alone**,
and the second half is easy to mistake for redundancy. A shielded row that merely *hides* a
stored `public_profile = true` is a row whose profile publishes itself on its owner's
eighteenth birthday: the mask lifts, the stored `True` is honoured, and nobody chose
anything. So the stored flags are refused on the way in — `400 age_shielded` on the user's
own route, and the same refusal in the admin service, since an admin path weaker than the
user path it mirrors is simply a way around the control — and cleared on every transition
into the shielded band. The invariant is that **no shielded row ever persists
`public_profile = true` or `full_name_public = true`**, which is what leaves the read-side
mask with only the cases it can actually cover: a row whose date of birth is unknown, and
whatever path someone adds later. Neither half is sufficient on its own.

The same reasoning is why `NOCA_ARENA_USERNAME_CHANGE_COOLDOWN_DAYS` exists at all. The
handle is the pseudonym the shield publishes *instead* of a name, so unlimited renaming
would let an observer watching the ranking correlate a shielded user's old and new handles
and reconstruct the identity the shield is protecting — the same confirmation-oracle shape
that moved the avatar seed off the email address. An administrator bypasses the cooldown,
because it protects a user from their own churn rather than from a rename made in response
to a report, and that bypass is password-confirmed and audited with both handles named.

Being one rule with two expressions, it follows the **one-shared-predicate** idiom this
codebase already uses for `is_announcement`: a single Python function (`is_shielded`) and a
single SQL mirror (`shielded_users_clause`), living in one module, with a table-driven test
that executes both over the same boundary rows and asserts they agree — so an archive, a
query, and a template cannot classify the same user three ways. The SQL half takes the table
**or an alias** as a parameter, because its one consumer builds candidate-ID branches over
`arena_users.alias(...)`, and binding the predicate to the base table would add an unjoined
FROM element: a cross join carrying an uncorrelated age test.

Search is shielded by a **separate function** rather than a flag.
`identity_search_service.prepare_public_user_search()` suppresses name matching for shielded
users and adds username matching for everyone; `prepare_user_search()` stays untouched for
the teacher-scoped class autocompletes, where looking a student up by the name on the roll
has a legitimate basis. Without the search half the shield would be self-defeating: a page
that renders a pseudonym while still answering "is this real name in the ranking?" is a
confirmation oracle for the very secret it is hiding. The username branches are backed by
`ix_arena_users_username_trgm` (migration `202608310002`), because the `UNIQUE` B-tree the
column already carries serves neither a leading-wildcard `ILIKE` nor the `%` operator. A
username *full-text* branch is deliberately absent — it would need a second expression index
beside the one on `nome`, whose DDL must stay byte-identical to `202608030001`.

One surface has to override stored data rather than pick a column: the problem-statistics
snapshot written by the rating worker freezes `arena_users.nome` as the first- and
last-solver name. Arena re-resolves it at read time in `problem_stats_service`, rather than
changing the snapshot, precisely because that snapshot is written in `shared/` and the shield
is Arena product policy. A solver whose account no longer exists cannot be aged at all, so
its name is withheld rather than taken from the snapshot — the same fail-closed direction as
an unknown date of birth.

Arena's deterministic fallback avatar is seeded on `username` rather than
`email_normalizado`, matching Web. The email seed was a confirmation oracle: the generator
is deterministic and the image is public, so a guessed address could be rendered and
compared against a user's avatar to test mailbox ownership. The reseed changes every
existing user's generated avatar exactly once, which is why it ships in the same release
as the NOT NULL column it depends on.

`arena_users.avatar_revision` is the cache key for the avatar a visitor can currently see.
It advances when an Arena photo is uploaded or removed, the username changes the fallback,
the Google/Arena source preference changes, or a selected Google picture is refreshed. The
canonical `/user/{id}/avatar` route serves the selected locally stored Google thumbnail when
available, otherwise the preserved Arena photo or deterministic fallback. **Every** template
that renders an avatar appends `?v=<avatar_revision>` -- list pages included, whose row DTOs
(`RankedUser`, `TopRatedUser`, `AdminSubmissionListRow`, `ClassMemberManagementRow`, the two
class-report rows) carry the column for exactly that purpose. This is not cosmetic: a
versioned URL gets the configured public cache lifetime, while an unversioned one is
answered `max-age=0, must-revalidate`. Until #199, the shared image helper emitted no
`ETag`, so "revalidate" meant "download again": a list page that omitted the version turned
the Arena ranking into up to a hundred full avatar fetches per view, each holding a pool
connection. Two fixes, deliberately both: every shipped template now appends the version,
and `tests/arena/test_arena_avatar_url_versioning.py` fails the suite if one stops; and the
helper now stamps a strong content-derived `ETag` on every image response and answers a
matching `If-None-Match` with a bodyless `304`, so the unversioned branch — still the safety
net for a caller that forgets — costs a header exchange rather than the image. The tag is
derived from the bytes rather than supplied by the caller because only the avatar route has
a revision to offer; affiliation logos have nothing to version by, and a content tag is
correct for every caller and cannot go stale when an avatar source switches.

Arena signup reputation adds the `arena_user_reputation` table to the shared schema:
one row per Arena user (unique `user_id` FK) holding the client IP captured at signup
plus the IPQualityScore IP and email reputation reports (fraud scores as columns and the
full signals as JSON). The signup IP is recorded for every account even when the
IPQualityScore integration is disabled, so `scripts/backfill_email_reputation.py` can
later score both the email and any recorded signup IP. The Arena HTTP process owns the
writes (a post-signup background task through `arena.services.signup_reputation_service`,
which also emails every `ARENA_ADMIN` a report); the Arena admin user profile reads the
snapshot on its Reputation tab.

Arena's Google sign-in adds `arena_user_google_identities`, a 1:1 satellite of
`arena_users` modelled on `arena_user_reputation`. Two UNIQUE constraints state the
whole invariant: an Arena account has at most one Google identity (`user_id`) and a
Google identity belongs to at most one Arena account (`google_sub`). `google_sub` --
Google's stable subject claim -- is the join key, never `google_email`, which is
stored for display only because a Google account's address can change while its
subject identifier cannot. The same row stores the latest optional `picture` claim,
a validated local avatar cache, its MIME type and refresh time, and the user's explicit
Arena/Google source preference. The Arena photo remains untouched while Google is selected,
so switching back or unlinking is lossless. Uniqueness is enforced by those constraints alone, never
by a lookup-then-insert, which would be a TOCTOU; the resulting `IntegrityError` is
presented as a stated conflict. There is deliberately **no** separate index on
`user_id`: the UNIQUE constraint already provides one over exactly that column. The
FK cascades, so deleting an account takes its identity and Google-derived cache with it.
Metadata is written on link, unlink, preference changes, and Google login; image bytes change
only after a bounded successful refresh. This remains low-churn account metadata on the
server-wide autovacuum defaults, so the migration deliberately adds no per-table tuning.

The same migration adds `arena_users.password_is_placeholder` (`Boolean`, NOT NULL,
server default `false`) and widens the `arena_login_history.mode` comment to name
`google`, `google_2fa`, and `google_backup_code`. The flag exists because the
unusable-random-password decision leaves nothing to *test*: a random Werkzeug hash is
indistinguishable from a real one, so the last-method guard and the skip-forced-password-change
branch would have no predicate. It is set only by a Google-first signup and cleared by
the `ArenaUser.password` setter -- the single path in the codebase that writes
`password_hash` -- so setting a real password self-corrects it with no caller obliged
to remember. `ArenaUser.has_usable_password` is simply its negation. Backfilling
`false` is correct rather than merely convenient: every account predating the column
was created by a path that wrote a real password.

Cross-module security auditing shares a single `security_events` table (owned by
`shared.services.security_events`) rather than per-domain audit tables. Each row
snapshots both the opaque `actor_user_id` and a human-readable `actor_label` (the
actor's login, e.g. email/username) captured at event time, so the admin viewers
can name who originated an `auth_*` / `parental_*` event without a lookup that could
break on account rename or deletion. Rows also store `client_ip`, `source_port`,
and `request_id` when the ASGI server or a trusted reverse proxy provides those
values. The Caddy deployment example overwrites `X-Request-ID` with Caddy's
per-request UUID so `security_events.request_id` can be correlated with Caddy
access logs. Both the Web and Arena HTTP processes append to it: authentication
failures, throttle lockouts (including Web's throttled password
reconfirmations on the profile-password, contest start/end-now, uberadmin
contest remove/export, and limit-change-batch rejudge routes, which share one
budget keyed by actor and IP, and Arena's throttled `rejudge-all` confirmation),
existing-account signup attempts, and — through
`shared.services.admin_audit` (`event_type="admin_action"`) — destructive and
privilege admin actions, all committed in the same transaction as the mutation
they describe. The mass rejudges are among them at warning severity: Web's
batch rejudges and Arena's `rejudge-all` each record the job count, and both
are additionally guarded by a per-problem cooldown
(`shared/services/rejudge_cooldown.py`) and made idempotent — Web's batch rows
are consumed once under a row lock, and Arena skips any submission whose
judgment is still in flight — so a repeated click cannot stack priority jobs
ahead of contestants' submissions. `POST /c/{slug}/admin/release-scoreboard` is one of them, at
warning severity and with no revoke half to record: revealing every result the
freeze held back is irreversible through the API, so the audit row is the only
record of who made the standings public. Arena's platform-wide Terms of Service
reset (`POST /admin/dashboard/terms/reset`) is another, also at warning severity
and also irreversible: the dates on which users accepted the retired documents
survive nowhere else, so the audit row recording the cleared count, whether
sessions were invalidated, and who ordered it is the only account of the change.
Lifting a **sign-in lockout** early is a third, on both surfaces: an Arena
admin (`/admin/dashboard/lockouts`, and the security tab of the admin user
profile) and an UberAdmin (`/uberadmin/lockouts`) can clear every throttle
bucket of one account or of one client address, password-confirmed, and each
unlock is an `admin_action` row (`unlock_account` / `unlock_ip`) at warning
severity because it weakens the brute-force guard. The *module* scope is
decided by the acting surface, not by the operator: Arena clears
`auth:rate-limit:arena:*` only, Web clears `web:*` and `animator:*` (the
animator's operator-token gate has no admin surface of its own), and buckets
are discovered by `SCAN` rather than a hand-kept action list, so one added
later is covered the day it lands.

Which *accounts* a Web login unlock reaches is the operator's decision, and it
is one the key shape has to make expressible. A contest login is unique only
per contest (`uq_users_contest_username`), so `web`/`contest-login` keys its
account bucket on `{contest_id}:{username}` rather than on the bare name --
otherwise five failures as `admin` against the least important contest on the
deployment would lock `admin` out of every contest, and the administrative
unlock could never be finer than the name either. `/login` keeps the bare name,
because an UberAdmin username genuinely is global. The per-IP bucket, which is
what caps raw volume, is unchanged by the scoping, so nothing defensive is
lost. Both the login route and the resolver build that identifier through one
function (`contest_login_identifier`), which strips the name before prefixing:
`normalize_identifier` strips and casefolds the whole string, so an unstripped
name would keep its inner spaces and hash to something the unlock could never
rebuild. The unlock form then makes the operator **say** which scope they mean
-- a required select with an explicit *All contests* option and no blank
default -- because the two mistakes are not symmetric: a silent wide default
releases every contest with a flash indistinguishable from the narrow one,
while a silent narrow default leaves locks standing that the operator believes
gone. It is the same reasoning that gives the validator-removal endpoints their
exact `keep_interactions=true|false` string. *All contests* also covers the
UberAdmin's own buckets; a single contest never does, since an UberAdmin is not
a contest. The audit row records the scope beside the login, so an entry
releasing one contest cannot be misread as one that released the deployment,
and the status panel labels each `contest-login` row with its contest from the
`hash -> contest` map the resolver built -- a lock whose hash is in no map stays
unlabelled rather than guessed, because hashes are one-way. Changing the key
shape orphans locks in flight at deploy time: they expire on their own within
`NOCA_AUTH_RATE_LIMIT_LOCKOUT_SECONDS` and, being locks rather than grants,
fail **open**, so a user is released early and never held longer.
Unlike the throttle, which fails open, the unlock **fails closed**: the
process-local fallback limiters are cleared first, and a store that cannot
answer is reported as such rather than as done. Both security-event viewers
link each row's address and account hash to the corresponding lockouts page.
Arena admins view the log at `/admin/dashboard/security-events`;
uberadmins view and filter it at `/uberadmin/security-events`. Retention is a
shared `security_events_reaper` loop that each HTTP runtime runs over its own
module ownership set — Web prunes `module=web`, Arena prunes `module in
(arena, aiassistant)` — so an independently deployed Web-only or Arena-only site
still cleans up exactly its own rows older than
`NOCA_SECURITY_EVENTS_RETENTION_DAYS`. Failed
logins deliberately land here (not in `login_history`/`arena_login_history`,
which stay success-only device history) because the log must record attempts
against non-existent accounts that cannot satisfy a login-history user FK.

CSRF protection relies on `SameSite=Lax` session cookies rather than
per-request CSRF tokens; this is a documented accepted risk given the
server-rendered, same-site POST forms. Session and auth cookies are marked
`Secure` whenever `NOCA_COOKIE_SECURE` is set (mandatory in production), and the
trusted client IP for throttling/auditing is taken from the proxy-corrected
`request.client.host` — raw `X-Forwarded-For` is never trusted. The client
source port comes from `request.client.port` unless `NOCA_SOURCE_PORT_HEADER`
names a reverse-proxy-managed header. The request identifier comes from
`X-Request-ID`; deployments must only trust it when the reverse proxy strips
incoming values and sets its own ID.

Error responses are owned by the same shared module across all four HTTP modules
(`shared.error_handlers`). Generic failures the router or validator produced --
`404`, `405`, and every `RequestValidationError` -- answer a neutral
`{"error": <code>}` body rather than FastAPI's `{"detail": ...}` and Pydantic's
error array, which otherwise name the stack, disclose internal parameter names,
and echo the caller's input back. Anything an application authored keeps its
`detail` verbatim, and statuses outside `{404, 405, 422}` are untouched, so the
animator control panel still reads its refusal messages out of `payload.detail`.
Because every handler derives its response from the status code alone and never
from the cause, the animator's requirement that an unknown slug, a disabled
contest, and the control kill switch stay indistinguishable holds for the neutral
body exactly as it did for the framework default.

Web adds one authenticated-browser policy on top of that shared neutral error
contract. Its HTTP exception handler intercepts every `403` raised by a route,
dependency, or service after authentication, resolves the actor's role from the
validated token, and redirects contest users to `/c/{slug}/` or UberAdmins to
`/uberadmin/`. A danger flash names the role that cannot access the feature.
Normal requests use a `303`, which safely turns denied POSTs into dashboard GETs;
HTMX requests use `HX-Redirect`. An unauthenticated `403` remains an ordinary
HTTP error response.

Integer request parameters are bounded through `shared.http_params` (`DbId`,
`PageNumber`). PostgreSQL `integer` is 32-bit while Python integers are unbounded,
so an unbounded parameter reaches a query and raises
`asyncpg.DataError: value out of int32 range`, which surfaces as a misleading
`503` and logs a full SQL statement. Row primary keys are `String(36)` UUIDs, so
this applies to the natural-number columns -- `arena_number`, ordinals, page
numbers, and the problem limit fields -- not to the `str`-typed id parameters.
Route *paths* use the `dbid` convertor (`{arena_number:dbid}`) rather than
Starlette's built-in `int`, whose `int(value)` call raises inside `Route.matches()`
for a path longer than CPython's 4300-digit limit -- before any handler, so it
escaped the error handlers as an unauthenticated `500`. Both rules are enforced by
`tests/shared/test_route_int_bounds.py` against the real registered routes.

Browser security headers are owned by one shared middleware
(`shared.services.security_headers`) that **all four** HTTP modules install — web,
arena, animator, and healthmonitor — so a public page cannot ship without CSP,
HSTS, `nosniff`, framing, referrer, and permissions policy. The two cookieless
modules (animator, healthmonitor) derive `hsts_enabled` from `ENVIRONMENT` alone
rather than from `COOKIE_SECURE`. `NOCA_SECURITY_HEADERS_ENABLED` and
`NOCA_CSP_REPORT_ONLY` are deliberately unprefixed so one setting governs every
module at once. The sample Caddyfile re-applies a subset of the same headers at
the edge with set-only-if-absent semantics as defense in depth, and strips
`Server` and `Via` so the origin stack is not named to clients; see
[CONFIG.md](CONFIG.md) for the two Caddy operator pitfalls involved.

## 5. Module summaries

### `web/`

The web module is the server-rendered contest administration and participant-facing
FastAPI app. It owns authentication and authorization, contest management, problem
management, Auto-Limit profiling requests, contest-scoped user management,
clarifications, staff task queues, submission lifecycle actions, scoreboards, and
chief-judge workflows. It applies authentication by default outside a small public
route allowlist -- the contest gateway pages, the login pages, `/health`, static
assets, the released problem-set archives, and the anonymous announcement board
(`/announcements`). Role and capability gates may raise `403` at any layer; the Web
exception boundary converts that result into the role-aware dashboard redirect and
flash described above.

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

### `autojudge/`

The autojudge module is a separate async worker process. It owns queued submission
and profiling jobs, Docker container pool management, compilation, isolate-based
execution, result persistence, stale in-flight recovery, startup and periodic
reconciliation of non-terminal jobs missing from the queue (recovering jobs lost
between a producer's DB commit and its follow-up enqueue), zombie container
cleanup, and worker heartbeat health monitoring.

### `arena/`

The arena module is the public-facing FastAPI application for Arena users. It owns
Arena signup and login, OTP-protected accounts, login history, LGPD age-gate handling,
Arena submissions, classes (teacher-owned groups with dated membership history and a
self-service registration-request workflow), problem sets (teacher-owned, scheduled
problem collections within a class; students may opt a submission into a set at submit
time to make it visible to the teacher, and post-deadline rating snapshots freeze each
student's AC totals), and Arena-specific user identity separate from contest users and
uberadmins.

Arena also serves the platform announcement board described in section 4: the
anonymous list and detail pages under `/announcements` (a sidebar entry for
everyone), and `ARENA_ADMIN`-only management under `/admin/announcements` that
publishes through the same shared service and editor Web uses, with one Arena-only
control -- the `required` checkbox, chosen once at publication and stored now, whose
acknowledgment pop-up is a separate feature.

When `NOCA_ARENA_GOOGLE_OAUTH_ENABLED` is set, Arena additionally serves Google
sign-in under `/auth/google` (`arena/routes/auth_google.py` for start, callback,
link and unlink; `arena/routes/auth_google_complete.py` for the step that collects
the date of birth and terms Google cannot supply). The trust model, the gate reuse,
and the placeholder-password decision are described below under Google sign-in.

Every Arena problem records the natural language of its statement in
`arena_problems.statement_language` (`pt`, `en`, or `es`; nullable while unknown), which
both problem lists expose as a filter and the problem package carries as the optional
`statement_language` key. The value is normally derived rather than typed: the author may
leave the form on "detect automatically", and `arena.services.statement_language_service`
detects it with `lingua`. An explicit choice that disagrees with detection is refused until
the author confirms it, and an import that stated no language is flagged for the importer to
verify. Rows predating the column are filled by
`scripts/arena/backfill_statement_language.py`, which only ever writes rows whose language is
still NULL.

Arena access is **default-deny**: a single global FastAPI dependency
(`arena.dependencies.access_control.enforce_arena_authentication`, registered on
the app in `arena/main.py`) requires a valid logged-in session for every route
except a small public allowlist — `/dashboard`, the `/problems` list (the problem
*detail* page and sub-resources stay protected), `/legal/*`, `/help/*`,
`/announcements/*` (the public announcement board; its `/admin/announcements`
management stays gated), the entire `/auth/*` namespace (login, signup, the
Google sign-in routes, and the other pre-login flows), `/`, `/health`, and the root
favicon assets. The gate reads only
`request.state.validated_token` (populated by `ArenaAuthMiddleware`, no database
I/O) and raises `HTTPException(401)`, which the Arena exception handler turns into
a login redirect (HTML), an `HX-Redirect` (HTMX), or a plain 401 (API). Per-route
`get_current_arena_user` / `require_arena_*` dependencies still apply full
database gating and role checks on top of the gate. New routes are therefore
protected automatically unless their path is added to the allowlist.

Publishing new Terms of Service or a new Privacy Policy retires every acceptance
already on file, so `ARENA_ADMIN` can clear them all from
`GET /admin/dashboard/terms` (`arena/services/admin_terms_service.py`). The reset
is one bulk `UPDATE` rather than a per-row loop because it must be atomic -- a
partial one would leave two populations bound to two different documents -- and
the acting admin's own acceptance is re-dated to now rather than cleared, so the
operation can neither end the session performing it nor strand that admin at an
acceptance screen Arena gives them no other way to reach. What makes it *effective* is optional and explicit: the acceptance
gate runs at **login**, so clearing the flag alone leaves a live session browsing
under the retired documents until its cookie expires, and a `sign_users_out`
checkbox therefore bumps `session_version` in the same statement to invalidate
every affected session at once. The choice is per reset because a terms change
that merely clarifies wording does not warrant signing an entire user base out
mid-submission, while one that changes what users consent to does.

Arena's reverse-geocoder relay, the profile reverse-geocoder proxy
(`POST /user/profile/location/detect`), is governed by
`arena/services/geocode_service.py` rather than by the route. The provider's usage
policy is stated **per application** — Nominatim allows one request per second for the
whole deployment — so a per-user cap alone cannot honour it. A detection is capped per
user, then answered from a Valkey cache of each 0.001-degree cell (about 100 m), and
only a cache miss consults a deployment-wide gate whose per-second pacing and windowed
budget are decided in one atomic Lua step against *Valkey server time*, so replicas
cannot disagree and a request refused by pacing burns no budget. That gate is the one
limiter in NOCA that **fails closed** -- on any Valkey failure, not merely an
unreachable server -- because `request_rate_limit`'s fallback is process-local, so an
outage across N replicas would multiply the upstream budget by N, and a `503` that calls
nobody is the safe answer. It also has no off switch: `ARENA_REVERSE_GEOCODER_ENABLED`
disables the proxy itself, and headroom comes from raising the ceilings rather than from
skipping the gate. The provider call itself runs in a worker
thread, since the underlying `NetworkService` is synchronous and would otherwise stall
the event loop for a full upstream round trip.

**Google sign-in is Arena's first external identity provider**, and therefore its
first *trust* relationship with a third party rather than merely an outbound call:
an assertion made by Google decides who someone is. It is off by default
(`NOCA_ARENA_GOOGLE_OAUTH_ENABLED`), and while off every `/auth/google` route
answers the same `404` an unknown path does -- except `GET /auth/google/blocked`,
deliberately left reachable so a visitor already refused there for being under 13
can still see why even after the feature is later disabled -- so a deployment
that has not registered an OAuth client is otherwise indistinguishable from one
built before the feature existed. Nothing else in NOCA depends on it: the session
it produces is the same local HS256 JWT the password form produces, logout is
unchanged (Google's session
is not Arena's to end), and `ArenaAuthMiddleware` and the access-control gate see
no difference between the two doors.

The identity trust is deliberately narrow. Google is believed about exactly two things --
the stable `sub` claim and whether it verified the address. The optional name and picture
claims are presentation hints, never authorization evidence.
`arena_user_google_identities.google_sub` is the join key, never the email,
because a Google account's address can change while its subject identifier cannot;
an unverified address is refused outright; and a Google address that matches an
existing Arena account is **not** auto-linked, because possession of an address
would otherwise be enough to take over an account. Linking happens only from an
already-authenticated session, which is what makes "the email need not match" safe.

The matching-address case is the easy one: the callback refuses to create a
second account and tells the visitor to sign in and link from the profile. A
Google address that *differs* from the user's Arena address is indistinguishable,
at the callback, from a genuine new user, so it creates a Google-first account that
holds the subject -- and the real account can then never link it, because the
subject's `UNIQUE` constraint is doing exactly its job. The completion form
therefore offers an "I already have an account" exit
(`arena/routes/auth_google_existing.py`) that changes the rule's *shape* without
weakening it. Leaving the form parks the orphan's id in a time-bounded session
marker (`pending_google_existing_uid`, thirty minutes) and sends the visitor to
the password login, whose page defaults its `next` to a confirmation page while
that marker is live. The claims are not copied anywhere: the orphan's own identity
row holds them. Nothing is hooked into the post-login chain -- the chain is spread
over three routes and the terms gate drops `next`, so the confirmation is simply
where `next` lands once terms, 2FA and a forced password change have all run. It
is an authenticated page that names the Google address, and only its explicit
`POST` re-creates the identity under the signed-in account and deletes the orphan
in one transaction (`google_signup_service.transfer_pending_signup_identity`),
guarded by `describe_pending_google_signup(orphan) == "needs_completion"` so a
completed account is never removed by this path, and deleting with a Core
`DELETE` because the ORM mapping cascades through collections the async session
cannot lazily load. The identity is thus still bound only from an authenticated
session, on an explicit action, so possession of a Google address still takes over
nothing; a marker planted on a shared browser cannot link silently, and starting
Google sign-in again drops it as a statement of a different intent. Declining
leaves the orphan for the Google door to finish as a separate account later. For a
mistaken signup that was already *completed*, the administrator's unlink is the
support escape instead.

That "only from an authenticated session" rule is enforced at **both** ends of the
round trip, and the second half is what makes it hold. `POST /auth/google/link`
records the account to link in a `pending_google_link_uid` session marker, but the
callback never trusts that marker on its own: it re-resolves the request's current
authenticated user and requires an exact match before attaching anything. The
marker lives in the Starlette session, which outlives the JWT cookie that logout
clears, so without that check a session that started a link and then logged out --
or switched accounts -- could attach a Google account to whoever the stale marker
named. Logout deliberately leaves the marker *in place*: it is what makes such a
callback recognizable as a link, and therefore refusable. Clearing it there would
instead make the callback indistinguishable from an ordinary login and send an
unknown Google identity down the signup path, creating a stray second account in
place of a clean refusal. Starting a fresh `GET /auth/google/login` does clear it,
since that is an explicit statement of a different intent.

*When* the callback consumes the marker matters for the same reason. It is read
only after throttle admission and a successful authorization, immediately before
dispatch, because the marker belongs to one OAuth round trip and that round trip
is spent only once Authlib has redeemed its `state`. A callback refused with `429`
never reached Authlib, so its state and code are still valid and the user will
retry them; a marker consumed on entry would have made that retry
indistinguishable from an ordinary login -- the same stray-account path, and this
time for a still-authenticated linker too, since the current-user check only runs
once the callback knows it is a link. A forged callback that fails the state check
likewise spends nothing, so it cannot strip a genuine link attempt of its intent.

What Google cannot assert is what the LGPD age gate and the terms gate need. So an
unknown subject creates an account that is deliberately unusable -- inactive, no
date of birth, terms not accepted -- written in the same transaction as its identity
row, and a completion step collects the rest and applies the same age rules the
ordinary signup applies. An abandoned completion leaves an account that fails
`evaluate_account_access_gates` everywhere, which is the safe direction -- but it is
not a dead end. A visitor who returns through the Google door has just re-proved
control of the same Google account, so the callback resumes the flow where it
stopped rather than answering the generic "deactivated, contact support" message:
back to the completion form when no date of birth was collected, to a waiting page
when a guardian has not yet consented, or to the permanent under-13 refusal.
`google_signup_service.describe_pending_google_signup` decides which, keyed on
`password_is_placeholder` rather than on `ativo` alone, because an ordinary account an
administrator deactivated must still get the generic message and never be swept
into a flow it never went through. Resuming grants nothing the first visit would not
have, and each landing page trusts its own session marker (`pending_google_signup_uid`
to resume the form, `pending_google_consent_uid` for the waiting page) rather than
inferring the stage from account state. The 13-17 outcome and the under-13 outcome
each have a page of their own instead of a flash on a login form the visitor cannot
use: the waiting page renders the same dated consent chronology the guardian pages
render, seen from the student's side, with an IP-throttled resend of its own.

That helper is the reason a second door does not mean a second set of rules.
`arena_auth_service.evaluate_account_access_gates` is the single expression of the
account-state rule (active, date of birth known, LGPD age status) and is consulted
by `efetuar_login`, by the Google callback, and by the per-request dependency;
`auth_common.complete_arena_login` is the single expression of the post-authentication
chain (terms, 2FA, forced password change, token, cookie) and is called by the
password form and the Google callback. Both were extracted from the existing password
path before any Google code was written, so the existing suite proved the extraction
changed nothing. Google login on a 2FA account still requires TOTP -- Google proves
identity, TOTP still proves possession -- and the originating door is carried through
the pending-2FA token so login history records `google_2fa` rather than losing it.

The OIDC flow itself is delegated to **Authlib**, which owns `state`, `nonce`, PKCE,
and ID-token verification against Google's rotating JWKS. PKCE is opt-in there -- a
`code_challenge` is emitted only when the client declares a challenge method -- so the
client registration passes `code_challenge_method=S256` explicitly, and a test builds
the real client and reads the authorization URL rather than assuming the default. It deliberately does not go
through `shared/services/network_utils`: that helper is a synchronous, SSRF-validating
`requests` wrapper for arbitrary operator-supplied URLs, whereas Google's endpoints are
fixed and public and the flow needs async plus JWKS caching. The callback is anonymous
and publicly reachable -- `/auth` is already an allowlisted public prefix, so each
handler gates itself -- and is auth-throttled per client IP exactly as the password form
is, with every refusal answering one generic message so a failure cannot say which half
of the flow it reached.

The optional OIDC `picture` claim is refreshed on successful login only when Google is the
selected avatar source, and immediately when the user selects Google on the profile page.
`google_avatar_service` accepts HTTPS URLs only on Google's profile-image hosts, disables
redirects and environment proxies, enforces the configured upload-size limit while streaming,
then reuses `ImageProcessingService` to decode, validate, and resize the bytes. Arena stores
the processed image locally and the browser continues to request `/user/{id}/avatar`; a
network, status, size, or decode failure is non-fatal and preserves the previous cache and
source. A Google-first signup defaults to Arena until the user explicitly chooses otherwise,
and uploading an Arena photo selects Arena without discarding the Google cache.

A Google-created account holds a **real** Werkzeug hash of a random secret, so
`get_token_id()` and `session_version` are untouched; `arena_users.password_is_placeholder`
is what records that no password can match it. Setting a password later -- through the
ordinary reset flow -- clears the flag in the one setter that owns every password write,
after which both doors work and unlinking Google is permitted. Until then the last-method
guard refuses the unlink, since it would leave the account with no way in at all.

That guard is a *self-service* protection, and the administrator's unlink
(`POST /admin/users/{user_id}/unlink-google`) deliberately does not apply it. The
guard stops a user from locking themselves out by accident; an administrator
detaching a lost, compromised, or mis-attached Google account is doing it on
purpose, and refusing would leave the wrong credential attached with no operator
recourse. What makes the bypass acceptable is that the account keeps the recovery
path the guard was protecting -- the ordinary password reset still works for a
placeholder-password account and sets a real password over it -- so the route says
so in both directions: a warning flash for the administrator and, in the
notification email, the reset link for the user. That justification is also the
bypass's limit. It is false for every *unfinished* Google-first signup
`describe_pending_google_signup` names -- a never-completed row is inactive with no
date of birth, so a freshly set password logs in to the generic "deactivated"
refusal while the Google door can no longer resume it and a fresh signup with the
same address is refused as already registered; a 13-17 account held for consent,
whether an unfinished signup or a completed one whose consent was withdrawn,
reaches the consent flow only through the Google callback; and an under-13
refusal, enforced by the date of birth rather than by the identity row, is
*explained* only through it -- without the row the next Google sign-in ends at
the same dead-end "already registered" message -- so `admin_unlink_refusal`
refuses those
outright, with the reason, and the profile page shows it in place of the control.
The admin path is otherwise no
weaker than the user path: it is password-confirmed, signs the user out (a stripped
credential may be one an attacker holds), and is recorded twice, as an `admin_action`
row and as the same `google_account_unlinked` security event the self-service route
writes, marked `source=admin`. It also ignores `NOCA_ARENA_GOOGLE_OAUTH_ENABLED`,
because the identity row outlives the feature switch and an operator who turned
Google sign-in off must still be able to detach the identities it created.

A failed Arena submission is explained with a **bounded side-by-side diff**, not the
expected output. The submission detail page and the teacher's batch-feedback page
used to render the failing case's whole expected output whether the case was a sample
or a secret, so a Wrong Answer on secret case 7 handed over secret case 7's answer and
repeated wrong submissions walked the secret set one case per submission. Hiding secret
outputs would have been the contest answer; Arena is an educational platform and a bare
"wrong on a secret case" teaches nothing. Instead `arena/services/output_diff.py` shows
the first six differing lines, with one unchanged line of context, between the
student's output and the expected output -- identically for sample and secret cases.
What bounds it is the judge, not the page: the student's side is the
`stdout_excerpt` the judge persisted (at most `NOCA_JUDGE_STDOUT_EXCERPT_BYTES`,
default 8 KB), and the expected side is a fixed 16 KB prefix read through
`read_testcase_output_prefix`, so no request reads an unbounded test file and the
comparison can never reach past the stored excerpt. **Accepted trade-off:** a
determined student can still reveal a secret case's expected output a few lines per
wrong submission, up to the excerpt window and no further. The read happens only for
`WA` and `PE`; every other verdict has no answer to contrast and skips the file
entirely.

### `rating/`

The rating module is a standalone single-replica worker. It owns Arena problem,
user, and affiliation rating recomputation cycles. The affiliation cycle also
stores the sum of ranking-visible members' precomputed solved-problem counts.
The worker publishes scheduler metadata to Valkey so all Arena replicas can show
consistent footer and help-page timing.
It also runs an independent per-problem statistics loop (`run_problem_stats_loop`,
on its own `NOCA_RATING_STATS_INTERVAL` timer) that precomputes the JSON snapshots
read by the Arena problem statistics page
(`shared.services.arena_problem_stats`) — rebuilt in bounded per-problem batches
over a streamed submission cursor, so the worker's peak memory tracks the busiest
batch rather than the whole submission history — and a parallel per-user statistics loop
(`run_user_stats_loop`, sharing the same
`NOCA_RATING_STATS_INTERVAL` timer) that precomputes the verdict and language
distribution snapshots stored in `arena_user_statistics` and read by the Arena
public profile page. A third independent loop (`run_badge_assignment_loop`, on its
own `NOCA_RATING_BADGE_INTERVAL`
timer) awards Arena gamification badges from Accepted submissions
(`shared.services.arena_badges`): each cycle runs a cheap incremental pass bounded by a
watermark, and periodically a full reconciliation pass re-evaluates all Accepted history
so dynamic badges (CLEAN_CODE) and late data stay correct.

### `aiassistant/`

The aiassistant module is a standalone async worker. It owns dequeuing Arena AI
review jobs from the Valkey `ai:queue:pending` list, calling the OpenAI Responses
API when the submitting user has a personal `ai_api_key`, and submitting an
OpenAI Batch API job when the worker falls back to the platform key configured
via `NOCA_AI_OPENAI_API_KEY`. Online user-key jobs store the AI review immediately.
Platform-key jobs first insert a durable `arena_ai_batch_jobs` row with
`local_status='staged'`, without calling OpenAI. The batch flusher periodically
collects all staged rows into one multi-item OpenAI batch, and the batch poller
stores each result after OpenAI completes that batch.

The worker runs the dequeue loop, stale-job reaper, batch flusher, batch poller,
reconciler, and worker-presence loop in one deployment unit. It also runs the
signed command loop when a worker command secret is configured. The reaper uses
`ai:queue:inflight:times` to recover queue jobs that were dispatched but not
cleaned up. The batch flusher wakes every five batch-poll intervals, or when
triggered. It submits all staged jobs as one OpenAI batch. The batch poller
reads non-terminal `arena_ai_batch_jobs` rows, retrieves OpenAI batch status,
stores completed review output in `arena_submission_ai_reviews`, creates Arena
notifications, clears failed retry flags, and deletes uploaded OpenAI files
after terminal states. At the top of each batch poll cycle, a stale-batch
detector locally expires batch jobs whose `submitted_at` is older than
`NOCA_AI_BATCH_STALE_HOURS`: in one transaction per submission it atomically
claims the row, refunds the consumed platform credit, clears `submit_to_ai`,
notifies the user, and finalizes the row as `expired`, then best-effort cancels
the OpenAI batch and deletes its files.

After any poll cycle that completes a batch, the worker derives turnaround
statistics from the 100 most recent successful platform-key reviews and stores
one persistent JSON value at `ai:batch:turnaround:stats`. The value is an
optional cache that Arena reads for the AI credits dashboard and platform-credit
review confirmation modal. Missing or invalid cache data produces an explicit
unavailable state; PostgreSQL remains authoritative, and a Valkey write failure
does not affect completed reviews.

The reconciler is the database-driven safety net for the request route's dual
write: because
`arena_submissions.submit_to_ai=True` is committed to PostgreSQL before the job
is pushed to Valkey, a crash between the two leaves a flagged submission with no
queue job. The reconciler periodically finds such submissions (flagged, no
review row, no active batch job, older than a grace window) that have no live
pending/inflight queue presence and re-enqueues them. It is the *only*
recovery path: the Arena request route answers a flagged submission with the
pending state and never re-enqueues, because `ai:queue:pending` has no dedupe
and a route-side "self-heal" let an owner push unlimited duplicate jobs at no
cost (each one an OpenAI call when dequeued) while re-deriving
`use_platform_key` from the user's *current* key rather than the frozen one.
The route instead serializes overlapping first requests on the submission row
(`SELECT … FOR UPDATE`) and counts every request per user in a Valkey fixed
window (bucket `arena:ai-review`) before any lookup.

### `mailer/`

The mailer module is a standalone single-replica async worker and the only NOCA
process that talks to the email provider. Every outbound email is rendered by
the Web or Arena process that decided to send it and handed, fully formed, to
the shared `EmailService`, which charges the acting user's per-actor budget
and pushes a `MailJob` onto the Valkey mail queue (`mail:queue:pending`,
inflight list, dispatch-time ZSET, and a TTL'd `mail:job:{id}` hash). Whether
that job is really sent, and through what (`NOCA_SEND_EMAIL`,
`NOCA_EMAIL_PROVIDER`, `NOCA_SMTP_*`), is the worker's decision alone. It
dequeues one job at a time -- the move to inflight and its dispatch timestamp
are one atomic script -- delivers it through the configured `EmailProvider` on a
worker thread, and rests `60 / NOCA_MAILER_MAX_PER_MINUTE` seconds after every
attempt — the deployment-wide sending rate, set in exactly one place. A provider
refusal leaves the job inflight on purpose; the reaper requeues it after the
stale threshold in one atomic step that keeps the hash's original TTL, bounded
by a requeue cap. A job older than `NOCA_EMAIL_QUEUE_JOB_TTL_SECONDS` is dropped
unsent, and its hash expires on its own, so a stopped mailer never delivers a
stale credential when it comes back; a job the worker cannot read during a
Valkey outage is left inflight rather than discarded. It has no templates, reads PostgreSQL only to reconcile its pause state,
is pausable from the Arena admin dashboard like autojudge and aiassistant, and
appears on the health monitor as *Mailer*. See
[mailer/docs/SERVICES.md](../mailer/docs/SERVICES.md) and the `email_service.py`,
`email_budget.py`, and `valkey_service/` entries in
[SHARED_SERVICES.md](SHARED_SERVICES.md).

### `healthmonitor/`

The healthmonitor module is a standalone FastAPI server (default port 8002) with no
database access and no authentication — its single page is public. It reads
the Valkey worker-presence keys published by all other runtime modules (the
`web`, `arena`, and `animator` HTTP servers publish presence from their
lifespans exactly like the workers do, under the presence-only
`WorkerClass.WEB` / `ARENA` / `ANIMATOR` classes) and serves:

- `/` — the uptime dashboard: the live Available/Unavailable/Unknown status of
  every monitored service plus an ECharts 30-day heatmap per service (60 slots
  of 12 hours, colored from green at 100% slot uptime to red at 70% or below)
- `/refresh` — the HTMX dashboard fragment, polled every 30 seconds while
  automatic refresh is active; the browser preserves card expansion and focus
  while replacing the live status and chart containers
- `/uptime.json` — the JSON source for all six heatmaps; the browser fetches it
  after initial load and every HTMX refresh, then recreates the ECharts
  instances and their accessible data-table fallbacks

Because every route is anonymous and `/uptime.json` is the platform's cheapest
amplification vector — six services × sixty slots of history read from the
Valkey instance every module shares — that endpoint is guarded three ways. All
360 hashes are read in **one** pipelined round trip
(`ValkeyRuntime.hmget_many`); the built payload is held in a **per-process,
single-flight cache** for one `NOCA_HEALTHMON_PROBE_INTERVAL` and served with a
matching `Cache-Control: max-age`, so concurrent misses build once and a flood
costs Valkey at most one read per interval per replica; and a failed read is a
`503` that caches nothing, since `hmget_many` reports an outage as `None` rather
than as an empty history. The prober invalidates the cache after every recorded
pass, so a new sample is visible on the next request rather than an interval
later. On top of that, `/`, `/refresh`, and `/uptime.json` share one per-IP
fixed window (bucket `healthmon:public`, `NOCA_HEALTHMON_RATE_LIMIT_*`) through
the shared limiter, and `/health` adopts the same `NOCA_HEALTH_RATE_LIMIT_*`
health-endpoint limiter as Web, Arena, and Animator.

A prober loop records one up/down sample per service every
`NOCA_HEALTHMON_PROBE_INTERVAL` seconds into per-slot `up`/`total` hashes
(`noca:healthmon:stats:{service}:{slot_epoch}`), skipping cycles while Valkey
is unreachable so monitor-side outages never count against the services. A
reaper loop deletes slots older than `NOCA_HEALTHMON_RETENTION_DAYS`; slot keys
also carry a TTL as a safety net. That same loop also prunes worker-presence
records of workers unseen for more than 7 days, bounding the durable
`noca:worker-presence:<class>:seen` and `:last-jobs` hashes every module writes
(live markers already expire on their own). Because the health monitor is an
optional deployment, every presence-publishing module runs the same shared pass
once at shutdown, so growth stays bounded in a Web-only or Arena-only install
too. The presence-only classes never appear in the Arena admin dashboard or
pause machinery.

### `animator/`

The animator module is a standalone FastAPI presentation runtime (default port
8003) that owns the public live scoreboard and post-freeze reveal ceremony. It
reads PostgreSQL (through SQLAlchemy Core over the shared schema) and Valkey
directly across the same infrastructure boundary the other runtimes use, and
reuses the shared scoreboard projection so its standings always agree with the
official scoreboard. It defines no ORM mappings and never imports `web`.
Configuration is isolated under the `NOCA_ANIMATOR_*` prefix
(`NOCA_ANIMATOR_HOST`, `NOCA_ANIMATOR_PORT`, `NOCA_ANIMATOR_POLL_FALLBACK_SECONDS`,
`NOCA_ANIMATOR_ENABLE_CONTROL`, `NOCA_ANIMATOR_BRAND_NAME`). The reveal control
endpoints are gated per contest by `contests.animator_enabled` and can be
disabled process-wide with the `NOCA_ANIMATOR_ENABLE_CONTROL` kill-switch.

The animator exposes a presentation launcher at `GET /c/{slug}/`.
It lists a prominent global scope and every contest site, with links to each
scope's animated scoreboard, reveal projector, and reveal controller. The
scoreboard shell lives at `GET /c/{slug}/scoreboard?scope=...`.

The animator exposes a read-only public feed for one enabled contest:
`GET /c/{slug}/meta` (contest identity, problem labels and balloon
colors, start/end/freeze timing, freeze state, and per-site medal-cutoff
summaries only) and `GET /c/{slug}/snapshot?scope=...` (a shared
`ScoreboardSnapshot` plus a server-generated refresh version).

Both feeds are **gated on the contest having started**, and this is a
confidentiality boundary rather than a cosmetic one. Web already withholds the
scoreboard, clarifications, and runs before the start instant so that how many
problems a contest has -- and which balloon colors they carry -- stays secret
until it opens; an anonymous animator feed that answered the same question would
simply be the way around that gate. Before `contests.start_time` the meta feed
therefore returns an empty `problems` list and the snapshot returns empty
`problems`, `balloon_colors`, `standings`, `pending_submissions`, and
`recent_events`, both carrying `has_started=false`. Sites stay visible in both states: the launcher is
built from them, and a venue's name and team count are not part of the secret.
The gate lives inside the feed service's shared projection helper -- which
queries nothing at all before the start -- rather than in each response builder,
so a feed added later cannot forget it. The scoreboard page renders the
ceremony projector's own "not started yet" banner in place of the board, keeps
the countdown and the live connection badge running, and re-reads `/meta` on a
capped timer because no SSE event marks the start instant.

Both feeds, and the spectator `/reveal/state` below, are **cached per process**
(`animator/services/feed_cache.py`, over the shared `SingleFlightCache`): a
snapshot is built at most once per TTL per `(contest, scope, phase, cutoffs)` —
`NOCA_ANIMATOR_SNAPSHOT_CACHE_SECONDS` while the contest runs,
`NOCA_ANIMATOR_SNAPSHOT_CACHE_ENDED_SECONDS` afterwards — and concurrent misses
share one build, so `compute_icpc` leaves the request hot path. The started and
frozen phases are part of the key rather than a reason to invalidate, so the
pre-start empty feed can never be served after the start. The animator's event
stream invalidates every scope of a contest on each verdict or submission event,
before any fan-out and whether or not a spectator is connected, so a cached
snapshot never outlives the verdict that changed it; the TTL only bounds a
missed event or an edit that publishes none. `/meta` is cached for
`NOCA_ANIMATOR_META_CACHE_SECONDS` on TTL alone. There is no permanent final
entry: Web's edit paths cannot reach animator entries. The three anonymous feeds
(`/meta`, `/snapshot`, `/reveal/state`) and the two team-media routes also
share one per-IP fixed window (bucket `animator:public`,
`NOCA_ANIMATOR_PUBLIC_RATE_LIMIT_*`) through the shared limiter, checked *before* the contest gate — its answer depends on the
client IP alone, so a `429` cannot probe the non-enumerating `404`. The two
SSE streams (`/events`, `/reveal/events`) are bounded by open *connections*
instead: a process-wide `NOCA_ANIMATOR_MAX_SSE_CLIENTS` ceiling (`503`, needs
no Valkey) and then the shared per-IP connection lease (bucket `animator:sse`,
`NOCA_ANIMATOR_SSE_*`, `429`), both checked before the contest gate and both
released on disconnect. The authenticated control surface is not rate-limited.

The snapshot defaults to the global scope; a validated site scope filters teams
and submissions before scoring. Animator loads `release_scoreboard_after_end` into
its immutable contest record: post-freeze submissions stay hidden while the
contest runs and after an unreleased end, while an ended, released contest
scores every final result and reports `is_frozen=false`, matching Web. The
final presentation keeps the existing **Ended** timer state, hides the
connection badge, and starts neither SSE nor polling.

An ended contest whose board is **still frozen** is a third state, and it does
not stream either. Releasing a scoreboard publishes no event — the Web route
writes `release_scoreboard_after_end`, audits it, and pre-warms the final cache —
`timer_tick` deliberately triggers no refetch, and once the pre-end judging queue
drains no verdict fires. An SSE connection held open there would deliver nothing
while the release it was ostensibly waiting for never arrived, so a projector
would keep showing frozen standings until someone reloaded the page. The board
therefore closes the stream and re-reads `/snapshot` on the poll-fallback
interval until the release lands, then stops and hides the badge. Because it is
not streaming, the badge reads *Waiting for results* rather than *Live*, and is
not treated as a degraded state, so no outage clock runs.

The **Frozen** timer state names how much of the board is withheld — `Frozen ·
last 45 min hidden` — measured to *now* while the contest runs and to the end
once it is over, since those are different amounts: a board frozen 35 minutes ago
is hiding 35 minutes, not the hour the contest rules set aside. An unknown,
nonsensical, or sub-minute window degrades to a bare `Frozen`.

Both feeds resolve the
contest through a single non-enumerating `get_enabled_contest` dependency, so a
missing slug and an `animator_enabled=false` contest are indistinguishable
(identical `404`). The
feed reads the shared schema through SQLAlchemy Core in
`animator/services/contest_feed_service.py`, loads only `RoleEnum.TEAM` users,
selects the effective judgment deterministically (prefer `DONE`, then latest
`created_at`, then id), and delegates all scoring to the shared `compute_icpc`
so the animator standings agree with the official scoreboard.

The animator also streams live change notifications over Server-Sent Events at
`GET /c/{slug}/events` (native FastAPI `EventSourceResponse` / typed
`ServerSentEvent`). A single `AnimatorEventStream` per process subscribes to the
shared `judge:results` and `judge:submissions` Valkey channels and fans out
`verdict`, `submission`, `scoreboard_refresh`, and `timer_tick` events to bounded
per-client queues; FastAPI adds a native 15 s idle-only comment heartbeat. Verdict
events are filtered by `contest_id` (legacy events without one are dropped) and
redacted while a contest is frozen; `submission` events (a new-submission nudge)
are filtered by `contest_id` and suppressed entirely while frozen. Per-client queues
coalesce to a single pending refresh on overflow, and the stream carries no logs —
PostgreSQL snapshots stay authoritative, so clients refetch `/snapshot` on
`scoreboard_refresh` and on `submission`. The `/snapshot` response carries a
freeze-safe `pending_submissions` array that is the authoritative source for the
pending list; `judge:submissions` and `judge:results` are **not** mutually ordered,
so a verdict arriving before its submission signal is reconciled by the next
snapshot.

The scoreboard's activity ticker was, for the same reason, **session-local**: it
showed only what the page's own `EventSource` had observed, so a projector opened
mid-contest or reloaded after a network blip stared at an empty strip until the
next submission. It is now seeded from the snapshot instead. `recent_events`
carries up to ten past events, built by
`animator/services/recent_events_service.py` from the submissions and judgments
`_project` has **already** loaded -- so the seed costs no query, rides the
existing per-process feed cache, and is invalidated by exactly what invalidates
the standings. Three properties make it safe rather than merely convenient. It
applies the freeze rule `compute_icpc` applies, so the ticker can never narrate a
run the board is withholding. It keys each entry as the live SSE handlers key
theirs (`submission:<submission_id>`, `verdict:<judgment_id>` -- which is why
`JudgmentRecord` carries its `id`, a field the scoring protocol has no use for),
so an event that is both seeded and streamed is rendered once. And it carries the
*parts* of each sentence rather than the sentence, leaving the wording in the one
client function the live path already used, because a backlog and a stream that
described the same solve in two vocabularies would be worse than no backlog at
all. Only the first post-start snapshot seeds; a later one would resurrect entries
the client's 30-item window had deliberately dropped.

The reveal ceremony is a **bottom-up sweep over every row** of the standings, not
only the rows holding frozen runs. One `step` either reveals a single frozen run
on the cursor's row or, when that row has nothing left, moves the highlight up
one row; `back` undoes either kind. The cursor is a *screen position*: when a
reveal lifts a team past others, the cursor holds its row and the team now on it
comes into focus, while the lifted team is met again as the sweep climbs toward
it. A single sweep therefore still resolves every relevant run, because revealing
can only improve a team's score and the teams it overtakes fall at most onto the
cursor's own row. The whole history is one ordered trail of steps
(`RevealSessionState.step_log`), from which both the revealed set and the cursor
are derived — which is what keeps `back()` an exact `pop` now that a step need
not reveal anything. That trail is `state_version=3`; an older payload —
version 1, recorded before the cursor existed, or version 2, recorded before
command receipts did — is refused rather than replayed, and the operator recovers
with `start-reveal` + `restart=true`.

The ceremony's *dataset* — the scoped teams, problems, submissions and
judgments every command and every spectator state read is projected over — is
loaded once per ceremony **generation** and cached per process. The frozen
universe is rebuilt only by a fresh `start-reveal` or an explicit
`restart=true`, each of which mints `RevealSessionState.dataset_generation`
(an opaque id carried unchanged through every later transition), so every
replica keys the same ceremony on the same identity and a restart on one replica
is a cache miss on the others rather than a stale projection. The field is
additive within `state_version=3`, following the `jump_pending` precedent:
replicas must be deployed together, and a ceremony persisted before the field
existed carries `None`, still projects, and simply bypasses the cache until it is
rebuilt. `execute_command` resolves the dataset *after* the stored state, inside
the lock, so a missing session and an active session hit by `start` without
`restart` are refused before a single dataset query runs.

The post-freeze reveal ceremony persists its state in Valkey rather than process
memory, so a ceremony survives a restart and can only be mutated by one writer per
contest+scope at a time. `animator/services/reveal_session_store.py` serializes each
mutation under a token-owned lock at `animator:reveal:lock:{contest_id}:{scope}` and
writes the `RevealSessionState` at `animator:reveal:{contest_id}:{scope}` through a
Lua **fenced** write (state persisted only while the lock still holds the writer's
token, so an expired-then-reacquired lease can never clobber the new owner). State
is saved before a `RevealStateChangedEvent` invalidation nudge is published on
`revelation:events:{contest_id}:{scope}`; the event carries only metadata, so
spectators refetch authoritative state and a missed nudge is harmless. The key TTL
is contest end plus `NOCA_ANIMATOR_REVEAL_TTL_MARGIN_SECONDS`, refreshed on every
mutation.

That durability answers "did the ceremony survive?", but not "did my command
apply?" — a `503` or a dropped connection can arrive *after* the fenced save
committed, and re-sending a `step` in front of an audience is not an acceptable
way to find out. Each mutating control command therefore accepts an optional
`Idempotency-Key` header naming one *attempt*. The ceremony state carries a
bounded ring of the last eight applied keys, written by the same fenced save and
expiring with the same key, and a retry is resolved inside the scope lock before
the engine runs: the most recent key is **replayed** (the stored state is
re-projected and returned, with no save and no publish, because that state
already is the command's result), an older or differently-used key is a stated
`409`, and an unknown key applies normally. `restart=true` discards the ring with
the rest of the old state. Without a key a command is applied exactly as sent,
which is why the operator panel attaches a fresh one to every attempt and why an
ambiguous outcome without one still requires reloading state instead of
retrying.

Spectators watch that ceremony through a **credential-free** public feed under
`GET /c/{slug}/ceremony`, `/reveal/state`, and `/reveal/events`. Both the
ceremony and live scoreboard use the scoped team photo at
`GET /c/{slug}/teams/{team_id}/photo`. A presentation scope is selected by a
validated `?scope=` query value — `global` or a site id
of that contest — which grants nothing and is resolved *after* the enabled-contest
gate, so an unknown site, another contest's site, and garbage all answer the same
bare `404` an unknown slug does (a pattern-validated parameter would answer `422`
first and prove the slug resolved). `/reveal/state` returns the *same*
`RevealProjectionResponse` the control API does, produced by the same
`control_service.load_projection`, wrapped in an envelope whose `has_session`
flag distinguishes "no ceremony yet" from an `idle` one; `/reveal/events` streams
only invalidation nudges, preceded by one `reveal_ready` event emitted **after**
the Valkey subscription is live: reconciling there rather than on
`EventSource.onopen` (which fires when the response headers are written, possibly
before the subscribe) closes the window in which a publication would reach
neither the client's fetch nor its subscription — pub/sub has no replay. The
client's rule is therefore *fetch state, then subscribe*, refetching on every
nudge and every `reveal_ready`, and retrying a failed state request with backoff
so a transient failure cannot strand a projector. Team photos fall back photo → avatar → checked-in placeholder, honor
`users_media.com_foto`, re-verify stored bytes by actually decoding them under
explicit pixel limits (serving the validated format, never the stored MIME
claim, so a truncated blob falls through instead of rendering broken), and are
conditional on an `ETag` that names the tier served and the `dta_foto`
revision. The conditional check is **metadata-first**: one narrow query
(flag, revisions, and per-blob presence booleans — no blob column) decides a
`304` before any payload is read or decoded, so a revalidation costs one row
rather than a multi-megabyte decode; only a miss loads the single column it
needs (photo, then avatar only if the photo fails). The placeholder's tag is
versioned when stored candidates fell through to it, so a re-upload can never
be served from a stale `304`. Both media routes count against the same
`animator:public` per-IP window as the three feeds.

The live scoreboard opens the same Bootstrap modal on a team name in photo-only
mode. Its keyed renderer preserves the exact trigger button across live
refreshes so Bootstrap can restore keyboard focus after close. The scoreboard
contains no audio element, audio URL, playback binding, or audio request.

The projector page itself (`GET /c/{slug}/ceremony`) renders the frozen
standings, the focused team, medal bands, and pending cells, and opens a single
reusable Bootstrap modal on a team name showing that team's photo and playing its
optional audio clip. The clip comes from `GET /c/{slug}/teams/{team_id}/audio`,
which shares the photo route's scoped lookup and serves the MIME type **sniffed
from the bytes** rather than the stored `audio_mime` claim; a missing,
undecodable, or unrecognizable payload is a `404` (never a `500`), so the modal
hides its player instead of rendering a broken one. Playback is started inside the
user activation of the team click, and the three outcomes are distinguished
explicitly: playing, refused by the browser's autoplay policy (native controls
stay, with a status hint), or unusable media (player hidden). Closing the modal
runs an idempotent teardown that stops playback *and* aborts any in-flight media
download.

Operators drive the ceremony from `GET /c/{slug}/control?scope=...`, a
credential-free HTML shell gated by the same `animator_enabled` → kill-switch
order as the command API and deliberately excluded from the control audit stream
(fetching a page is not a command attempt). The **operator token lives in
JavaScript memory only**: it is typed into a password field that is cleared on
capture, sent solely as an `Authorization: Bearer` header, and never written to a
URL, request body, cookie, `localStorage`, or `sessionStorage` — so a reload
requires re-entry. The panel builds its ceremony selector from the public `/meta`
feed, and the launcher's validated scope preselects the intended ceremony,
because `start-reveal` requires a `site_id` exactly equal to the token's own
scope. It also distinguishes *stated* refusals (`400/403/404/409/422`, which
changed nothing and re-enable the controls at once) from *ambiguous* outcomes (a
network failure or any `5xx`, including the store's `503`, which can arrive after
a fenced save committed): an ambiguous outcome keeps the commands disabled until
authoritative state is reloaded, so a failed `step` can never be replayed into a
double reveal.

Operators drive that ceremony through the authenticated control API under
`GET|POST /c/{slug}/control/*` (`start-reveal`, `step`, `back`, `reset`,
`jump-team`, `jump-pending`, `state`). **Jump to next pending** replays only
cursor moves until the next `?` is focused and stops before revealing it; both
the web panel and Android remote expose it only when no `next_cell` is already
selected. Five gates apply in a fixed order: the per-contest
`animator_enabled` gate, then the process-wide `NOCA_ANIMATOR_ENABLE_CONTROL`
kill switch, then a per-IP lockout (`NOCA_ANIMATOR_CONTROL_LOCKOUT_*`, on the
shared `auth_rate_limit` primitive: after repeated credential failures an
address is refused before its header is even read, with the same generic `403`
a bad token gets and no `Retry-After`, so a `403` never reveals whether it was
the credential or the lockout), then an `Authorization: Bearer` operator token
resolved through `shared.services.animator_access_service` (a failure counts
toward the lockout, a success resets it), then — on every mutating command —
active controller ownership: a Valkey-backed per-`(contest_id, scope)` lease
(`NOCA_ANIMATOR_CONTROLLER_LEASE_TTL_SECONDS`/`NOCA_ANIMATOR_CONTROLLER_HEARTBEAT_SECONDS`,
default 45 s/10 s) claimed, renewed, released, and taken over through
`POST /c/{slug}/control/controller-lease/*`. The first two answer the same bare
`404` an unknown slug does — they run before the lockout and the credential
are consulted, so neither can be probed through those — while a credential
failure or a lockout is one generic `403`. Ownership failures are stated:
another controller or a lost lease is `409`; an unavailable store fails closed
with `503`. Every mutating request must carry an opaque `X-Animator-Controller-Id`
header matching the lease owner, verified atomically with the per-scope mutation
lock so a controller that lost ownership can never slip a command past a
takeover; read-only state stays lease-independent, and every attempt, accepted or
rejected, is audited in one token-free structured log line. A site token authorizes exactly
its own site and a global token (`site_secrets.site_id IS NULL`) exactly the
global ceremony; only `start-reveal` reads a `site_id`, and only to require exact
equality with the token's own scope. Every later command derives its scope from
the credential and the stored session, so no request body can redirect a
ceremony. Each successful mutation is exactly one fenced save plus one nudge,
performed inside the per-scope lock; responses carry a projection (counts, phase,
focus, derived team views), never the persisted `reveal_log` or
`frozen_submission_ids`. See
[animator/docs/ROUTES.md](../animator/docs/ROUTES.md) and
[animator/docs/SERVICES.md](../animator/docs/SERVICES.md).

Two commands on that surface are **not** mutations, and the distinction is what
makes them cheap: `show-team-media` and `hide-team-media` put the ceremony's
focused team's photo and clip on every projector in scope, or take them down.
The operator running the ceremony is on stage, away from the machine driving the
projector, and the projector already knew how to show that media — only in
response to a click on that machine. So the cue crosses the same Valkey boundary
every other coordination signal does, as a `RevealMediaCueEvent` published on the
scope's existing `revelation:events:{contest_id}:{scope}` channel, **carrying no
ceremony state at all**. Nothing is written: no fenced save, no receipt ring, no
scope mutation lock, and therefore no `Idempotency-Key` — re-cueing is inherently
a no-op. The route answers `204`, because no state moved and the server cannot
learn whether a projector rendered the overlay; the operator is told *sent*, never
*displayed*, and a cue is never replayed to a client that reconnects.

The cost of that choice is one lost overlay per missed cue, paid by pressing the
button again; what it buys is that no persisted contract moved. `RevealCommand`,
`REVEAL_EVENT_VERSION`, and `RevealSessionState.state_version` are all untouched,
so ceremonies in flight keep working and animator replicas need no lockstep
deploy — an older one cannot parse the frame and drops it, which is the behavior
`iter_revelation_events` already had for any payload it does not recognize. The
two payloads are told apart by **shape** rather than by a discriminator field,
because adding one to `RevealStateChangedEvent` would be the breaking change:
a deployed replica's `extra="forbid"` would reject every new nudge and silently
freeze its projectors.

Ownership is still required in both directions — blanking a projector is as much
a control action as seizing one — and it is **fused with the publication itself**
in one Lua step rather than checked beforehand, because every `await` between an
advisory check and a `PUBLISH` is a window in which the lease expires or a
takeover lands. It still takes no mutation lock, so a cue never queues behind a
`step`. The projector closes the overlay whenever the ceremony moves, and that
one signal — a changed `ceremonySignature`, defined once and ported to each
client — is what lets both operator panels keep a purely local Show/Hide label
rather than reading state they do not own.

The operator on stage also cannot see whether the hall's projectors are still
connected, so every open `/reveal/events` stream registers itself in a
per-scope Valkey presence set (`animator/services/projector_presence.py`,
scored by Valkey's own clock, renewed while the stream lives, expiring on its
own after a crash) and the controller-lease responses carry the unexpired
count back as `projector_count`. It rides the heartbeat both shipped
controllers already send, so it costs no new polling loop and no new
persisted contract. It is a gauge and never a gate: the count is read *after*
the lease decision and can neither refuse nor extend ownership, a Valkey
failure makes it `null` -- shown as *unknown*, never as an empty hall -- and
a failed registration never refuses or ends a projector's stream.

`jump_pending` widens the strict `RevealCommand` literal used by both persisted
`CommandReceipt.command` values (`state_version=3`) and published
`RevealStateChangedEvent.command` values (`event_version=1`) without changing
either version. Animator replicas must therefore be deployed together. An older
replica drops an unknown event nudge; after the first idempotency-keyed
`jump_pending`, it also rejects the whole stored session as unusable because it
cannot validate the receipt. After a rollback, the operator uses **Rebuild
state**, whose `start-reveal` request with `restart=true` deliberately replaces
the payload without reading it.

### `landingpage/`

The landing page is a standalone Caddy static site (default internal port 8080)
that serves one public page: an overview of the deployment and links into the
Contest, Arena, Animator, and Health Monitor instances running in it. It has no
Python package, no application framework, no database or Valkey connection, and
no background loop, which is why it stays outside the `uv` workspace.

Its only runtime input is configuration. Caddy's template middleware renders the
four `NOCA_LANDINGPAGE_*_URL` values and `NOCA_LANDINGPAGE_VERSION` into the page
on each request and HTML-escapes them; `containers/landingpage/entrypoint.sh`
rejects a missing, relative, or whitespace-bearing URL and a missing, oversized,
or whitespace-bearing version tag before Caddy starts, so a misconfigured
deployment fails rather than publishing broken navigation. The version is
configuration rather than something discovered at runtime precisely because there
is no application behind the page to ask.

The module sits outside every infrastructure boundary in section 2. It reaches
neither PostgreSQL, Valkey, nor another module, and its
`Content-Security-Policy` enforces that from the browser side as well: it begins
at `default-src 'none'`, allows only same-origin styles, scripts, fonts, and
images, and sets `connect-src 'none'`. A consequence worth stating explicitly is
that the page cannot display live service status, by construction; it links to
the Health Monitor instead.

Because it publishes no Valkey worker-presence heartbeat — there is no
`WorkerClass` member for it, unlike the `WEB`, `ARENA`, and `ANIMATOR`
presence-only classes — the landing page deliberately does **not** appear as a
service on the health monitor's status and uptime dashboards. Its own liveness is
the dependency-free `/health` route, for container and load-balancer probes only.

The image is the official Caddy image plus static files. A build-only stage
supplies the shared NOCA webfonts, so the page uses the same typefaces as every
other surface without a CDN, and the Contest, Arena, and Animator illustrations
are copied from the modules that own them rather than duplicated into this one.
Development uses `landingpage/serve_dev.py`, a stand-in for Caddy that renders
the same template calls and sends the same headers; it never ships in the image.
See [landingpage/README.md](../landingpage/README.md),
[landingpage/docs/ROUTES.md](../landingpage/docs/ROUTES.md), and
[landingpage/docs/SERVICES.md](../landingpage/docs/SERVICES.md).

### `shared/`

The shared module defines cross-runtime contracts: SQLAlchemy Core schema, enums,
queue payloads, language registry helpers, logging, Valkey services, locks,
scoreboard cache support, email delivery, safe outbound network helpers, image
processing, and other services reused by more than one runtime module.

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

## Custom interactive validators

Contest and Arena problems can stage a UTF-8 custom validator revision. The
application commits the candidate before placing a compile-validation job on
the profiling-priority Autojudge queue. A candidate token makes delayed results
safe: the worker updates or promotes a revision only while its token still
matches.

A successful candidate becomes the active `VALID` revision. A failed
replacement retains bounded compiler diagnostics and leaves an older active
revision available.

The validator is **parametrized by test-case input**: one container pair judges a
whole submission, and the judge replays the conversation once per test case,
writing that case's input to the validator's stdin before the two sides talk. The
first case that does not end `AC` stops the iteration and its verdict is the
submission's. Consequently an interactive problem's test cases carry **input and
explanation only** — no expected output — and its packages, ZIPs and downloads
ship `.in` files alone.

Every test case of an interactive problem is **secret**, because a bare input
reveals a secret without showing what to do with it. The public examples come from
**sample interactions** instead (below). The invariant for a problem with a
configured validator is therefore *zero public test cases and at least one secret
one*: staging a validator demotes any existing public case, the sample toggle is
refused while a validator is configured, and no edit path may remove the last
secret case. The "at least one secret case" half is a gate rather than a write
barrier — Arena creation legitimately stages a validator on a brand-new disabled
draft with no cases yet — so it is enforced at the Arena enable gate and at both
submission paths, where the problem becomes visible or judgeable.

## Sample interactions

A problem with a custom validator cannot show sample test cases meaningfully: what a
contestant needs to see is the *conversation* their program will have. Such a problem
therefore carries up to five **sample interactions** (`problem_sample_interactions` /
`arena_sample_interactions`), each an author-written transcript plus an optional
explanation, ordered by `ordinal`.

Transcripts are stored in the same JSON shape the judge records for a real interactive
attempt, so one shared partial (`_partials/transcript_table.html`) renders both the
authored examples on the problem page and the recorded attempts on the submission page.
Authors write them as plain text with the strict prefixes `> ` (validator → contestant)
and `< ` (contestant → validator); `shared/services/sample_interactions.py` owns the
parser, the package I/O, and the invariant helper, with no ORM dependency.

Removing a validator leaves the interactions with nothing to illustrate, so the removal
endpoints require an exact `keep_interactions=true|false` — a strict string, not a
`bool`, so that FastAPI cannot coerce `1`/`on`/`yes` into a choice between hiding data
and destroying it. `true` sets a nullable `hidden_at` (the same soft-hide pattern
`clarifications` uses), which keeps the rows out of every UI and out of exports; staging
a validator again clears it and the interactions resurface. Hidden rows still count
against the five-interaction cap, so an un-hide can never overflow it.

Packages carry them as `interaction/NNN.interaction` (the raw transcript JSON) and
`interaction/NNN.explain` (plain text). A package with no validator has its
`interaction/` members dropped on import; a validator package with none warns the
importer that the problem shows no examples.

See the
[interactive validator guide](custom-validator/INTERACTIVE_VALIDATOR.md) for
the authoring workflow, the
exit-code-to-verdict mapping, and which problem limits are enforced by the judge
versus by the validator.

Each interactive attempt is recorded in `submission_interactive_attempts` /
`arena_submission_interactive_attempts`, tagged with the `test_case_ordinal` it
belongs to. Its `transcript` JSON column holds the ordered, line-split
conversation between the contestant and the validator (the judge relays every
byte, so it observes protocol order); the case's own input is not part of it. A
judgment retains only the last executed case's attempts, so the surviving rows are
the conversation that decided the submission. Both frontends render them from one
shared partial — and only for a non-`AC` verdict, since an accepted submission has
no failing round to explain.

The per-case watchdog attributes a line-protocol stall only when a complete
message identifies the side that failed to reply. A validator message followed
by contestant silence becomes contestant `TLE`; a contestant message followed
by validator silence remains an internal failure and retries once. A partial
message or no message remains ambiguous and internal. When a complete validator
message establishes contestant silence, that stall can never disable the Arena
problem by being mistaken for a validator failure.

## 6. Summary

NOCA is a nine-process contest platform:

- `web` manages contest and business workflows (default port 8000)
- `autojudge` manages sandboxed compilation and execution
- `arena` manages the public Arena participant platform (default port 8001)
- `rating` manages the single-replica Arena rating recomputation cycles
- `aiassistant` manages the Arena AI code review pipeline (OpenAI Responses API
  and Batch API)
- `mailer` manages outbound email delivery, at the deployment's pace, off every request path
- `healthmonitor` manages the public uptime dashboard (default port 8002)
- `animator` manages the public live scoreboard and reveal presentation (default port 8003)
- `landingpage` serves the environment entry point and public module links
  (default internal port 8080)
- `shared` defines the common contract between them

The architecture is built around separation of concerns, a shared PostgreSQL schema
contract, lightweight Valkey coordination, shared problem/testcase storage,
server-rendered FastAPI pages with flash-based feedback, role-based access control
with strict contest scoping, and background reapers for asynchronous processing.
