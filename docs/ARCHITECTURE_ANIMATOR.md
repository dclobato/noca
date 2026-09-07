# NOCA Animator Module Architecture

This document describes the `animator/` module: the standalone FastAPI
presentation runtime (default port 8003) that serves a live scoreboard and the
post-freeze reveal ceremony. It covers what the module owns, the contest gate
and medal configuration it reads, its public feeds and event stream, how
ceremony state is persisted and driven, and the operator control surface. Read
[ARCHITECTURE.md](ARCHITECTURE.md) first for the boundary that lets the
animator read the shared schema without importing `web`.

Related references:
- [ARCHITECTURE.md](ARCHITECTURE.md) for the system overview and the boundary between modules
- [animator/docs/ROUTES.md](../animator/docs/ROUTES.md) and [animator/docs/SERVICES.md](../animator/docs/SERVICES.md) for the module's routes and services
- [ANIMEITOR-REVELEITOR.md](ANIMEITOR-REVELEITOR.md) for the presentation concept the module implements
- [noca-animator/README.md](noca-animator/README.md) for the design and implementation plans
- [SHARED_SERVICES.md](SHARED_SERVICES.md) for the scoreboard projection, the single-flight cache, and the SSE connection lease

## Responsibilities

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

## Contest gate, medals, and operator secrets

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

## Public feeds

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

## Live event stream

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
the client's 30-item window had deliberately dropped. Event timestamps on the ticker
are rendered in `hh:mm` format to align directly with the elapsed contest clock.

## Reveal ceremony state

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

## Spectator feed and team media

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

## Absence marker

Both scoreboards -- Web's page and the animator's live board -- mark a team that
shows **no sign of life**, so the staff running a venue can spot an empty seat
without reading the standings for an absence of activity. It lives once in
`shared/services/team_absence_status.py` so the two surfaces cannot classify the
same team differently.

A team is marked when **both** signals are silent: no sign-in since the contest
opened, *and* no activity right now. Neither alone is enough, and the pairing is
what makes the marker honest.

The sign-in window on its own reported present teams as absent (#219). **Start
contest now** moves the start to *now*, so every warm-up login becomes a login
"before the start" and a room full of working teams is marked at the instant the
contest opens; the single-session policy (#216) makes the same shape routine,
since a session opened before the start deliberately survives it. Dropping the
window instead would have traded that false positive for a false negative: a
team that opened the practice page during warm-up and then walked away is
exactly the no-show worth flagging, and only the window can still see it.

Presence comes from `shared/services/user_presence.py` under the `contest`
identity domain, written by Web on ordinary authenticated `GET`s and read here.
It needs no stream and no new endpoint: every contest page re-fetches
`GET /c/{slug}/clock` once a minute through `_base.html`, which is a floor the
default 3-minute TTL clears with room for two missed polls. The session
keepalive would have been the obvious carrier and is the wrong one -- its
cadence is derived from the token lifetime and fires every 15 minutes at the
default, far too coarse to tell an occupied seat from an empty one. A Valkey
outage reports everyone offline, which lands back on the sign-in window alone
rather than on a screen of false alarms.

The flag is deliberately **not** carried on `ScoreboardSnapshot`. That
projection is cached under the `:frozen` and `:final` keys, which are written
once and never invalidated, so a presence flag stored inside one would freeze
along with the standings and stay permanently wrong. Web reads it beside the
snapshot, on the existing short-lived display-cache entry; the animator carries
it as `absent` on its own `TeamStandingResponse` and computes it only
while `is_running_at` holds, which keeps it out of the long-lived
ended-contest snapshot cache for the same reason. It is shown only while the
contest runs: before the start nobody is late, and afterwards an absence is
history rather than something anyone can act on.

The marker itself is a `person_off` glyph plus a muted team cell -- an icon
rather than dimming alone, because opacity carries nothing to a screen reader
and reads as a rendering artifact on a projector at the back of a hall. A
sign-in publishes no event on any channel, so on the animator the board re-reads
`/snapshot` on a timer of its own while any marker is showing and stops when the
last one clears; that matters most in the opening minutes, when no submission
has been made yet and the live stream is therefore silent. The reveal ceremony
draws no marker: by then every team's fate is decided.

## Projector page

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

## Operator control

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
