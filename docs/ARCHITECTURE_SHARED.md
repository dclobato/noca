# NOCA Shared Module Architecture

This document describes the `shared/` module: the cross-runtime source of truth
for the database schema, enums, queue payloads, and the services more than one
runtime module reuses. It covers the contracts that both the Contest and Arena
domains speak -- the problem model and its durability rules, custom interactive
validators and sample interactions, the platform announcement board, security
auditing, and the HTTP hardening every server installs. Read
[ARCHITECTURE.md](ARCHITECTURE.md) first for schema ownership, and
[SHARED_SERVICES.md](SHARED_SERVICES.md) for the per-service contracts.

Related references:
- [ARCHITECTURE.md](ARCHITECTURE.md) for the system overview and schema ownership
- [SHARED_SERVICES.md](SHARED_SERVICES.md) for the shared service contracts
- [PROBLEM_PACKAGE_FORMAT.md](PROBLEM_PACKAGE_FORMAT.md) for the import/export ZIP format
- [Interactive validator guide](custom-validator/INTERACTIVE_VALIDATOR.md) for authoring, exit codes, and applicable limits
- [Output checker validator rationale](custom-validator/OUTPUT_CHECKER_VALIDATOR.md) for the planned non-interactive output-checker strategy
- [BOOTSTRAP.md](BOOTSTRAP.md) for what a data root must support
- [CONFIG.md](CONFIG.md) for the security-header and cookie settings

## Responsibilities

The shared module defines cross-runtime contracts: SQLAlchemy Core schema, enums,
queue payloads, language registry helpers, logging, Valkey services, locks,
scoreboard cache support, email delivery, safe outbound network helpers, image
processing, and other services reused by more than one runtime module.

## Platform announcement board

The platform **announcement board** -- the global notices about new problems,
rating changes and new features that #138 asked for, as opposed to the per-contest
clarification announcements described in [ARCHITECTURE_WEB.md](ARCHITECTURE_WEB.md) -- is one shared `announcements` table with a
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

## Problem model

Problems exist in both identity domains (`problems` for Contest,
`arena_problems` for Arena), and every format and durability decision about
them is made once, in `shared/`, so that a problem survives a round trip
between the two. The subsections below follow the columns and files a problem
is made of.

### Validation strategy

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

### Generation counters

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

### Editorials

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

### Test-case storage

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

### Problem packages

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

### Import ordering and edit durability

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

### Output limits and field widths

Both problem tables make `output_limit_in_bytes` **NOT NULL** (server default 65536): a problem
always states an output limit, and `NOCA_JUDGE_OUTPUT_LIMIT_BYTES` is a hard global ceiling applied
as `min(problem_limit, global_limit)` rather than a fallback for a missing value. The *per-language*
`problem_language_limits.output_limit_in_bytes` deliberately stays nullable, where NULL means
"inherit the problem's limit" — which is exactly what the judge's `coalesce(per-language, problem)`
computes. Field widths are unified across the two domains (`title` 256, `author` 256, `notes` 512,
`source` 256, `license` 256, `image_caption` 512) so a value that survives on one side survives a
round trip through the other.

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

## Security auditing

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

## CSRF and trusted request metadata

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

## Error responses

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

## Bounded integer parameters

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

## Browser security headers

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
