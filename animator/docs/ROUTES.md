# NOCA Animator Routes

The animator is a standalone presentation runtime (default port 8003). It reads
PostgreSQL (SQLAlchemy Core over the shared schema) and Valkey directly and never
imports `web`. All contest-scoped routes are gated by `contests.animator_enabled`
through a shared enabled-contest gate — `get_enabled_contest` for ordinary routes
and its session-releasing variant `get_enabled_contest_detached` for the streaming
`/events` route — so a missing slug and an animator-disabled contest are
indistinguishable (identical, non-specific default `404` response — FastAPI's
standard `{"detail": "Not Found"}` body).

Conventions:

- when a route changes, keep this file and [SERVICES.md](SERVICES.md) in sync
- responses expose only presentation-safe fields (see
  `animator/models/responses.py`); operator secrets, digests, credentials,
  emails, and unrelated contest configuration are never serialized

---

## Static mounts (`animator/main.py`)

The standalone Animator serves its CSS, JavaScript, footer images, shared CSS
and JavaScript, vendor assets, and webfonts from its own origin. Footer images
use the named `animator_static_img` mount so every presentation page works in
the module-only container without importing or linking to Web.

| URL | Name | Content |
|-----|------|---------|
| `/static/css/{path}` | `animator_static_css` | Animator stylesheets. |
| `/static/js/{path}` | `animator_static_js` | Animator browser clients. |
| `/static/img/{path}` | `animator_static_img` | Animator footer logos. |
| `/static/shared-css/{path}` | `static_shared_css` | Shared NOCA styles. |
| `/static/shared-js/{path}` | `static_shared_js` | Shared NOCA browser utilities, including the theme toggler. |
| `/static/vendor/{path}` | `static_vendor` | Fetched third-party browser assets. |
| `/static/webfonts/{path}` | `static_webfonts` | Fetched NOCA fonts. |

---

## Health (`animator/routes/health.py`)

| Method | URL | Name | Description |
|--------|-----|------|-------------|
| `GET` | `/health` | `animator_health` | Readiness probe. Runs a live `SELECT 1` and reads Valkey availability; reports booleans and an aggregate status only. Public, rate-limited per client IP with trusted-CIDR bypass. |

---

## Assets (`animator/routes/assets.py`)

Router prefix: `/assets`. Public, database-free balloon, star, and medal SVG
artwork. Balloons and stars support an optional problem letter. Rendering and
medal loading are delegated to the framework-agnostic
`shared.services.balloon_assets` module, so Web and Animator serve identical
artwork from their own origins. Invalid color, letter, or medal-band segments
return `400`; valid responses are `image/svg+xml` with a long
`Cache-Control: public, max-age=3600`.

| Method | URL | Name | Description |
|--------|-----|------|-------------|
| `GET` | `/assets/balloon/{color}` | `animator_balloon` | Balloon SVG in `{color}` (3- or 6-digit hex, optional `#`), no letter. |
| `GET` | `/assets/balloon/{color}/{letter}` | `animator_balloon_letter` | Balloon SVG with the first ASCII letter of `{letter}` centered, in a WCAG-contrasting text color. |
| `GET` | `/assets/star/{color}` | `animator_star` | Star SVG in `{color}`, no letter (marks a first solver). |
| `GET` | `/assets/star/{color}/{letter}` | `animator_star_letter` | Star SVG with the first ASCII letter of `{letter}` centered. |
| `GET` | `/assets/medal/{band}` | `animator_medal` | Shared Gold, Silver, or Bronze medal SVG. Unsupported bands return `400`. |

The `/scoreboard` page passes the two color-only route bases to the client as
`data-balloon-base` / `data-star-base`; the renderer appends `/{color}/{letter}`
per problem.

---

## Animator index (`animator/routes/index.py`)

The public Animator index discovers presentation launchers without exposing
archived or Animator-disabled contests. It groups eligible contests by their
current lifecycle and renders no scoreboard or reveal JavaScript.

| Method | URL | Name | Description |
|--------|-----|------|-------------|
| `GET` | `/` | `animator_index` | Lists contests where `animator_enabled=true` and `active=true`, grouped as live, upcoming, and past. Each card links to the contest presentation launcher. Renders `animator_index.html`. |

---

## Public contest feed (`animator/routes/public.py`)

Router prefix: `/c/{slug}`. Routes resolve the contest through the
`get_enabled_contest` dependency (`404` when missing or animator-disabled); the
streaming `/events` route uses the session-releasing `get_enabled_contest_detached`
variant with identical `404` behavior.

| Method | URL | Name | Description |
|--------|-----|------|-------------|
| `GET` | `/c/{slug}/` | `animator_contest_page` | Presentation launcher. Shows a prominent global section followed by every contest site, with scoped links to the animated scoreboard, reveal projector, and reveal controller. Renders `contest_index.html`; it loads no presentation JavaScript. |
| `GET` | `/c/{slug}/scoreboard?scope=…` | `animator_scoreboard_page` | Public live-scoreboard presentation page for `global` (the default) or one validated contest site. A static HTML shell (no scoreboard state embedded) that fetches `/meta` and the scoped `/snapshot` client-side, renders the standings and a locally ticking contest timer, and then opens the contest live SSE connection. Before the contest starts the feeds carry no problem set, so the page shows a **"The contest has not started yet."** banner in place of the board — sharing the ceremony projector's own banner style — while the countdown and the live connection badge keep running; it re-reads `/meta` on a capped timer (at most a minute) because no SSE event marks the start instant, and swaps in the roster and problem columns as soon as the gate opens. Team names open the shared media modal in photo-only mode: the page embeds the canonical `data-scope` and route-derived `data-photo-base`, but no audio URL or audio element. Other wiring includes the `/meta`, scoped `/snapshot`, and `/events` URLs, the selected site name, `data-poll-fallback` (`NOCA_ANIMATOR_POLL_FALLBACK_SECONDS`), and the balloon/star/medal asset bases. Renders `animator.html`. |
| `GET` | `/c/{slug}/meta` | `animator_contest_meta` | Contest identity, problem labels and balloon colors, start/end/freeze timing, current public `is_frozen` state, and per-site medal-cutoff summary with team counts. An ended contest with `release_scoreboard_after_end=true` reports `is_frozen=false`. **Before the contest starts `problems` is empty and `has_started=false`**: the number of problems and their balloon colors are contest secrets until the start instant, exactly as Web's own scoreboard gate treats them. Sites remain visible in both states — the launcher is built from them and a venue is not part of that secret. Returns `ContestMetaResponse`. |
| `GET` | `/c/{slug}/snapshot?scope=…` | `animator_contest_snapshot` | Public ICPC scoreboard snapshot computed with the shared `compute_icpc` for `global` or one validated site scope, plus a server-generated `version` (equal to `generated_at`) for client refresh and a freeze-safe `pending_submissions` array. Each standing includes `team_fullname`, `site_name`, and `medal` — the band that row's rank falls into under the cutoffs in force for the requested scope (the site's own for a site scope, the contest's `global_*_cutoff` triple for `global`), or `null` when the row wins no medal or the scope has no cutoffs configured; each problem cell includes the accumulated attempt `penalty`, including while unsolved. Post-freeze submissions remain hidden while the contest runs and after an unreleased end; an ended, released contest exposes all final results and reports `is_frozen=false`, matching Web. Site scope filters teams and their submissions before scoring, so ranks and first-solver markers are local to that site. **Before the contest starts the response is empty in every scope** — no `problems`, no `balloon_colors`, no `standings`, no `pending_submissions` — and carries `has_started=false`, which is what lets a client tell that gate apart from a contest with no teams. Returns `ScoreboardSnapshotResponse`. |
| `GET` | `/c/{slug}/events` | `animator_contest_events` | Live Server-Sent Events stream (native `EventSourceResponse` / typed `ServerSentEvent`). Fans out `verdict` (finalized judgment, no logs; redacted to `{"redacted": true}` while the contest is frozen), `submission` (new-submission nudge `{submission_id, team_id, problem_id}`, suppressed entirely while frozen), `scoreboard_refresh` (bare signal to refetch `/snapshot`), and `timer_tick` (`{"server_time": "<ISO8601 UTC>"}`) events for this contest. FastAPI sends a native 15 s idle-only comment heartbeat. Resolves the contest through the **detached** short-lived resolver (`get_enabled_contest_detached`) so the long-lived stream holds no PostgreSQL connection, with the same `404` uniformity. PostgreSQL snapshots remain authoritative. |

### Streaming notes (`/events`)

- **Event contract.** `verdict` payload is
  `{submission_id, judgment_id, problem_id, team_id, verdict, update_kind}` (never
  `compile_log`/`score`/`judge_time_ms`); it is replaced by `{"redacted": true}`
  while the public scoreboard is frozen so post-freeze solves never leak. An
  ended, released contest emits unredacted verdicts. `scoreboard_refresh` is
  `{}`. `timer_tick` carries UTC server time only; the client derives
  elapsed/remaining from the `/meta` timing it already holds. `submission` is a
  low-latency nudge only: the client schedules a `/snapshot` refresh and associates
  the flash with that refresh generation, so an older in-flight snapshot cannot
  consume it; it carries no verdict and is suppressed while the contest is frozen.
- **Ordering & overflow.** A `verdict` is emitted immediately before its
  `scoreboard_refresh`. A `submission` carries no paired refresh — the client's
  handler triggers the refetch. Per-client queues are bounded; on overflow the detail
  is discarded and coalesced into a single pending `scoreboard_refresh`, so a slow or
  disconnected client can lose detail but never the eventual authoritative refresh.
  `judge:submissions` and `judge:results` are **not** mutually ordered; a verdict that
  arrives before its submission signal is reconciled by the authoritative
  `pending_submissions` in the next `/snapshot`.
- **Legacy events.** Verdict events without a `contest_id` are dropped and logged,
  never broadcast.
- **Reconnect.** Each reconnection registers a fresh channel; there is no replay and
  `Last-Event-ID` is not honored (no durable event log yet). Event `id`s are
  monotonic within the process lifetime.

### Client live-update behavior (`/scoreboard` page)

The page script drives the live scoreboard on top of the `/events` stream:

- **One EventSource, refresh on `scoreboard_refresh` only.** Because `/events` emits
  a `scoreboard_refresh` immediately after every `verdict`, the client fetches the
  authoritative `/snapshot` **only** on `scoreboard_refresh`. `verdict` carries
  optional transient metadata (no fetch) and `timer_tick` never fetches — the local
  timer remains authoritative for display. Each accepted snapshot also syncs the
  timer's freeze state, so a contest that freezes while the page is open flips the
  header to **Frozen** live.
- **Coalesced, stale-safe refreshes.** All snapshot fetches go through one
  coordinator: at most one in flight plus one queued (a burst collapses to a single
  follow-up), and a response older than the last applied `version` is rejected, so
  the board never regresses. Rows are always drawn in authoritative server order.
- **Session activity rail.** The page retains the last 30 submission and verdict
  events observed by its current EventSource and moves them right-to-left above
  the scoreboard. Each item uses the contest minute when this page received it.
  Verdicts wait for the paired snapshot: a newly solved cell becomes a balloon
  message, a new `is_first_balloon` cell becomes a first-solver message, and all
  other results keep the verdict code. The rail ignores redacted or unmappable
  events, pauses on hover or focus, and becomes a static horizontal list under
  reduced motion. It is intentionally session-local: reloads clear it, and events
  missed during reconnecting or polling are not reconstructed.
- **Connection status.** A non-blocking, `aria-live="polite"` pill shows **Live**,
  **Reconnecting…**, or **Polling**. On every open (including recovery), the client
  returns to Live, resets its failure count, and reconciles with an immediate
  snapshot fetch (the stream has no replay/`Last-Event-ID`). After two consecutive
  SSE errors, a terminally closed `EventSource`, or a browser without `EventSource`
  support, it falls back to one poll interval of `data-poll-fallback` seconds. Each
  poll refreshes the snapshot; when SSE is supported and the previous source is
  terminally closed, the poll also creates a fresh source. A successful open stops
  polling. A foreground `visibilitychange` triggers one immediate refresh, and a
  back/forward cache restore (`pageshow.persisted`) reopens the stream and
  reconciles.
- **Ended scoreboard state.** After the contest ends, the header keeps the
  scoreboard visibility and contest lifecycle separate. An unreleased scoreboard
  shows **Frozen** and **Ended**; a released scoreboard shows **Final** and
  **Ended**. When the authoritative snapshot reports `is_frozen=false`, the
  connection pill stays hidden, and the client opens neither SSE nor fallback
  polling.
- **Team photo modal.** Each team name is a Bootstrap data-API button. Keyed
  refreshes update the existing button instead of replacing it, so Bootstrap can
  return keyboard focus to the original trigger after the dialog closes. The
  shared modal fetches only the scoped photo route; scoreboard markup and client
  wiring contain no audio element, audio URL, or playback lifecycle.

---

## Reveal spectator feed (`animator/routes/reveal_public.py`)

Router prefix: `/c/{slug}`. The **credential-free** public side of the
ceremony: a projector or a phone in the audience watches through these routes,
and an operator token is never accepted here.

### Scope selection

A spectator picks the ceremony with the `?scope=` query value: `global` (the
default) or a **site id of this contest**. The value grants nothing — it only
selects which already-public ceremony to read, and it is resolved against the
contest by `animator/services/public_scope_service.py`.

- `global` is reserved for the contest-global ceremony and is never treated as a
  site id, matching the store's own reservation
  (`RevealSessionStore.scope_for`), so spectator and operator address one key.
- The parameter is declared as a plain, unconstrained `str` **on purpose**. A
  `Literal`/pattern-validated parameter would make FastAPI answer `422` *before*
  the contest gate ran, so a malformed scope would prove the slug resolved. It is
  parsed permissively and judged only after `EnabledContest`, so an unknown site,
  another contest's site, and garbage are all the same bare `404` an unknown slug
  gets.

| Method | URL | Name | Description |
|--------|-----|------|-------------|
| `GET` | `/c/{slug}/ceremony?scope=…` | `animator_ceremony_page` | Spectator projection. Embeds **no** ceremony state — only the resolved canonical scope and the URLs its scripts read from data attributes: `data-meta-url`, `data-state-url`, `data-events-url`, `data-photo-base`, `data-audio-base`, `data-balloon-base`, `data-star-base`, and `data-medal-base`. Renders `ceremony.html`, which loads the live scoreboard's pure renderer for its shared problem header but not its feed client. |

#### Projector behavior (`/ceremony`)

- **Rendering.** `ceremony-render.js` draws the standings from the authoritative
  projection in server order — it never re-sorts. Problem columns come from the
  **union** of every team's problem keys, sorted naturally (shorter label first,
  then lexicographic), so `Z` precedes `AA` and the order never depends on
  JavaScript object-key iteration or on a team that happens to be missing a cell.
  The public `/meta` feed supplies the configured order, color, and label to the
  live scoreboard's shared balloon renderer; if that optional request fails, the
  naturally sorted text labels remain as a non-blocking fallback.
  Both scoreboards use the same `animator-scoreboard` and `animator-col-*` CSS
  rules for table geometry, typography, padding, sticky headers, truncation, and
  column widths, plus the same Web-style cell stack: balloon or star and solve
  minute for a solved problem, attempt count and accumulated penalty below it,
  or red attempt and penalty lines while unsolved.
  A first-attempt solve has no solitary plus sign. Pending reveal cells keep the
  yellow treatment, replace the attempt line with `? −N`, and update their
  visible attempts and penalty after every step. Rows carry `data-medal` bands
  with a heavier rule on each band's last row and a focused-row class driven by
  `focused_team_id`.
- **Team modal.** Team names render as `<button data-bs-toggle="modal"
  data-bs-target="#team-media-modal" data-team-id="…">`. Using Bootstrap's
  **data API** rather than a programmatic `modal.show()` is deliberate: in
  Bootstrap 5.3 focus restoration lives in the data-API click handler, so a
  programmatic open would trap focus correctly and then return it nowhere. One
  dialog is reused for every team and never recreated, keeping that restoration
  valid.
- **Media.** The modal fetches the scoped photo, uses
  `X-NOCA-Team-Image-Kind` to expand only the checked-in placeholder, and keeps
  real photos and avatars at their intrinsic width up to `75vw`. On
  `shown.bs.modal`, it assigns the audio URL and calls `play()` inside the
  activation the click granted. Native controls stay hidden until playback
  starts, or until loaded metadata confirms the clip exists after the browser
  refuses autoplay (`NotAllowedError`); a missing or unusable clip never flashes
  controls. Every media listener is scoped to a load *generation*, so a previous
  team's teardown error can never hide the current team's working media.
- **Teardown.** `hide.bs.modal` and `hidden.bs.modal` both run one idempotent
  teardown: it aborts the photo fetch, revokes the photo object URL, and resets
  audio with `pause()`, `currentTime = 0`, `removeAttribute("src")`, and
  `load()`.
- **Resilience.** The last good projection stays on screen when a refetch fails;
  `ceremony-transport.js` retries with backoff and reconciles on `reveal_ready`.
  The focused row is scrolled into view, honoring `prefers-reduced-motion`.
| `GET` | `/c/{slug}/reveal/state?scope=…` | `animator_reveal_state` | The authoritative ceremony projection for one scope. Returns `RevealPublicStateResponse`. |
| `GET` | `/c/{slug}/reveal/events?scope=…` | `animator_reveal_events` | One `reveal_ready` event once the Valkey subscription is live, then one `reveal_state_changed` nudge per durable mutation (native `EventSourceResponse` / typed `ServerSentEvent`), with FastAPI's native 15 s idle-only comment heartbeat. |

### `/reveal/state` response shape

`RevealPublicStateResponse` is an envelope: `has_session`, `contest_id`, canonical
`scope`, `site_id`, `site_name`, and `projection` — the very same
`RevealProjectionResponse` the control API returns, or `null`.

- **One projection path.** The route calls `control_service.load_projection()`,
  the function `/control/state` uses. There is no second read implementation, so
  the operator's view and the spectators' view of a ceremony cannot drift.
- **`has_session`, not `started`.** It reports only whether durable state exists
  for the scope. After `reset` a session still exists with `phase="idle"`, which
  a flag called `started` would describe ambiguously. Progress is read from
  `projection.phase`.
- **Unrevealed identities and verdicts never leave the server**: no
  `reveal_log` or `frozen_submission_ids`. The projection exposes only aggregate
  counts, including `pending_frozen_count` for each team/problem cell so the
  projector can draw one `?` per outstanding submission.

### `/reveal/events` streaming notes

- **Fetch before subscribe.** Valkey pub/sub is not replayable and the nudge
  carries no state, so the authoritative order is always *load `/reveal/state`,
  then subscribe*, and every nudge is answered by another state load. The shell's
  `ceremony-transport.js` enforces exactly that on the client.
- **`reveal_ready`, not `onopen`.** The stream emits exactly one `reveal_ready`
  (`{"ready": true}`) **after** the `SUBSCRIBE` completes, and that — not
  `EventSource`'s `open` — is the client's cue to reconcile. An SSE response's
  headers are written when the route returns its generator, so `open` can precede
  the subscription; a mutation published in that window would reach neither the
  client's fetch nor its not-yet-live subscription, and pub/sub has no replay, so
  a projector could stay stale for the rest of a ceremony. Reconciling on
  `reveal_ready` closes the window on the first connection and on every
  reconnect. A stream whose subscription does not become ready within 10 s is
  closed rather than left pretending to be covered.
- **Event contract.** `reveal_ready` carries `{"ready": true}`.
  `reveal_state_changed` carries the `RevealStateChangedEvent` nudge (`command`,
  `phase`, `focused_team_id`, `revealed_count`, `frozen_count`, `published_at`) —
  metadata for logging and cheap filtering, never a substitute for the
  projection.
- **Client retry.** A transiently failed `/reveal/state` request — the initial
  one or a refetch after the last nudge of a ceremony — is retried with backoff
  (500 ms → 5 s, then steady), honoring a server `Retry-After` when it asks for
  longer. Network failures and HTTP `429`, `502`, `503`, and `504` are retryable;
  permanent HTTP failures are surfaced without an automatic retry loop. The
  stream opens on the first success, so a briefly unavailable server at page
  load costs a delay rather than a projector that stays blank until someone
  reloads it.
- **No pinned database connection.** Both the contest and the scope resolve
  through their *detached* dependencies, which close their sessions before
  streaming begins, so an open ceremony stream holds zero pooled PostgreSQL
  connections — the same guarantee the public `/events` stream makes.
- **Cleanup.** The Valkey subscription is wrapped in `aclosing`, so a client
  disconnect tears the pub/sub connection down rather than leaving the generator
  pinned until garbage collection.

### Status codes

| Condition | Status |
|---|---|
| Unknown slug, `animator_enabled=false`, unknown/foreign/garbage/empty scope | `404` (bare, identical bodies) |
| No session stored for the scope | `200` with `has_session=false`, `projection=null` |
| Reveal store briefly unavailable | `503` with `Retry-After: 1` |
| Corrupt, foreign-versioned, or misfiled persisted state | `500`, **no** `Retry-After` (retrying cannot repair it); recovery is the operator's `start-reveal` + `restart=true` |

---

## Team media (`animator/routes/team_media.py`)

Router prefix: `/c/{slug}`. Serves photos to live scoreboards and ceremonies,
plus the optional audio clip used only by the ceremony. Both routes are public
and credential-free, scoped by the same `?scope=` value, and resolved through
one shared scoped lookup so their scope predicates cannot drift.

| Method | URL | Name | Description |
|--------|-----|------|-------------|
| `GET` | `/c/{slug}/teams/{team_id}/photo?scope=…` | `animator_team_photo` | The team's stored full photo, else its stored avatar, else a checked-in placeholder. Returns `X-NOCA-Team-Image-Kind: photo\|avatar\|placeholder`. Conditional: `ETag` + `If-None-Match` → `304`. |
| `GET` | `/c/{slug}/teams/{team_id}/audio?scope=…` | `animator_team_audio` | The team's optional audio clip, served under the MIME type **sniffed from the bytes**. `404` when the team is out of scope or has no usable clip. Conditional: `ETag` + `If-None-Match` → `304`. |

### Audio policy

Audio is genuinely optional, which is the one place it differs from the photo: a
photo request always succeeds (there is a placeholder), while audio resolves to a
clip or to `404`. No stored-data defect may surface as a `500`.

| Case | Result |
|---|---|
| Stored payload decodes and is a recognized MP3, OGG, or WAV | `200`, canonical `audio/mpeg` / `audio/ogg` / `audio/wav` |
| No `users_media` row, or `audio_base64` empty | `404` |
| Undecodable base64, empty decode, unidentifiable content, or a recognized but unsupported type (e.g. an image) | `404` |
| Team out of scope, of another contest, or not `RoleEnum.TEAM` | `404`, identical to a nonexistent team |

- **The stored `audio_mime` claim is never served.** It describes bytes the
  animator did not produce, so the type comes from `puremagic` and is mapped
  through the same canonical set the Web upload path enforces. A mislabeled row is
  served correctly or not at all.
- **Every failure mode is caught** — `binascii.Error`, `ValueError`, and
  `puremagic.PureError` — because an unusable stored blob must be the contract's
  `404`, never a `500`.
- **Absence is not an error to report.** A team with no clip is the ordinary case
  and logs at `debug`; a payload that is present but unusable logs at `warning`,
  because that is a data problem an operator should see.
- Unlike the photo path this route does **not** decode the media: that would need
  a native library for no benefit, and a signature-valid clip the browser cannot
  play surfaces through the `<audio>` `error` event the modal already handles.
- `ETag` is `"audio-<micros>"` from `dta_audio` (`"audio-0"` when null), using the
  same arithmetic as the Web module's `UserMedia.audio_cache_version`. There is no
  fallback chain, so no kind component is needed beyond the constant.
- **`Accept-Ranges` is never sent.** Range requests are not implemented, and
  advertising them would break seeking rather than enable it.
- Audio bytes are never returned in JSON.

### Selection policy

The lookup narrows by contest, `RoleEnum.TEAM`, and — for a site scope — site, all
in one query, so a site spectator addressing another site's team gets the same
`404` a nonexistent team gets, and a judge or admin account is never addressable
as a team. Selection then follows a fixed order:

| Case | Served |
|---|---|
| `com_foto=true` and the photo **decodes** to a supported image | the photo |
| the photo is missing/undecodable/empty/truncated/not an image, and the avatar decodes | the avatar |
| `com_foto=false`, or neither blob is usable, or there is no `users_media` row | the placeholder (`image/svg+xml`) |

- **`com_foto` is authoritative**, exactly as in the Web media properties: when it
  is false both blobs are ignored even if present, so the animator cannot
  resurrect a photo a user removed.
- **Empty decoded bytes count as invalid** and fall through.
- **Stored bytes are re-verified structurally, and the served MIME comes from the
  bytes.** Uploads were validated when written, but a stored blob can be
  truncated or rewritten and `foto_mime` is only a *claim*. Each candidate is
  base64-decoded defensively and then run through the shared `image_validation`,
  which actually **decodes** the image and reports its real format. A signature
  sniff would not be enough — an eight-byte PNG header passes it and still
  renders as broken-image chrome — so validation is what lets the fallback chain
  be trusted: a truncated photo falls through to the avatar instead of being
  served as a corrupt `image/png`. The decode is capped by explicit pixel
  (8000×8000) and dimension limits, so a decompression bomb is refused rather
  than expanded, and the `ETag`/`304` handling keeps a projector from
  re-validating the same photo. SVG is not accepted from a stored blob.
- Every rejected blob is logged at `warning`: the request still succeeds, but a
  corrupt row is a data problem an operator should see.

### Caching

`ETag` combines the media **kind** with `dta_foto`
(`"photo-<micros>"` / `"avatar-<micros>"` / `"placeholder"`), so it moves both
when a new upload changes the bytes and when a corrupt photo makes the response
fall through to the avatar. `Cache-Control: public, max-age=300` — short, because
a photo can be replaced mid-event and revalidation is nearly free.
`If-None-Match` is compared per RFC 9110: comma-separated lists, `*`, and **weak**
comparison (a `W/`-prefixed validator still matches the strong tag issued, which
is the correct rule for `If-None-Match`). A `304` carries the `ETag` and
`Cache-Control` too, so it refreshes freshness rather than stranding an entry that
must be revalidated on every use.

---

## Operator shell (`animator/routes/control_page.py`)

Router prefix: `/c/{slug}`. The HTML page an operator opens to drive a
ceremony. It is a **static shell**, not a control operation.

| Method | URL | Name | Description |
|--------|-----|------|-------------|
| `GET` | `/c/{slug}/control?scope=…` | `animator_control_page` | Renders `control.html`: the secret prompt, the scope selector preselected to the validated global or site scope, single-step and ten-step controls, jump-to-pending, the jump-team selector, and the current-state readout. Carries the initial scope, six command URLs, the `/control/state` URL, and the public `/meta` URL as `data-*` attributes. |

- **It accepts no credential parameter of any kind.** There is no `?secret=`, so a
  token cannot reach an access log, a `Referer`, or browser history through this
  route. A query string appended by hand is simply ignored.
- **The gate is reused, not restated:** the page depends on the same
  `ControlContest` dependency the command API uses, so the contest gate and the
  `NOCA_ANIMATOR_ENABLE_CONTROL` kill switch apply in the same order and answer
  the same bare `404`. A switched-off deployment does not serve a panel whose
  every button would fail.
- **It is not audited.** The `ControlAuditRoute` boundary belongs to the command
  router; fetching a page is not a command attempt, and recording it as one would
  bury the real attempts.

### Client behavior (`control.js`)

- **The secret lives in one closure variable.** It is read from a `type="password"`
  field, the field is **cleared immediately**, and from then on it exists only as
  an `Authorization: Bearer` header. It is never written to a URL, a body,
  `localStorage`, `sessionStorage`, a cookie, or the DOM. A reload requires
  re-entry — the intended cost.
- **The scope selector is server-derived.** `start-reveal` is refused unless the
  body's `site_id` exactly equals the token's scope, so the panel builds its
  options from the public `/meta` feed (`sites[]`) — "Global ceremony" sends an
  explicit `null`. The launcher's validated `?scope=` value preselects the
  matching option. Operators never type a site id. Once a session exists, the
  projection names the token's scope and the selector collapses to static text;
  start commands then send the projection's `site_id`, not the stale option.
- **Ambiguous outcomes never allow a double reveal.** A refusal the server
  *stated* (`400`, `403`, `404`, `409`, `422`) changed nothing, so controls
  re-enable at once and the `detail` is shown verbatim. A network failure or any
  `5xx` — **including the `503`**, which can arrive after a fenced save already
  committed — is ambiguous: the controls stay **disabled** until the operator
  selects **Reload state** and that authoritative read succeeds. A second
  `step` pressed meanwhile is not sent. If reconciliation itself fails, the
  controls remain locked. No message ever claims a `503` was a no-op.
- **Every command attempt carries a fresh `Idempotency-Key`.** The panel
  generates one per attempt (`crypto.randomUUID`, with a time-and-random
  fallback) and sends it on the five mutating commands only. Fresh *per attempt*:
  two deliberate presses of **Step** are two commands and must both apply. The
  key makes a network-level or proxy retry of one attempt safe, and it is what
  would let a future "retry that command" affordance exist at all; the panel
  itself still never re-sends a command after an ambiguous outcome.
- **Ten-step controls are client-side sequences.** **Step 10** and **Back 10**
  send up to ten existing `POST /step` or `POST /back` requests serially. The
  sequence stops on the first non-success. After an ambiguous result it also
  stops after reconciliation, because the client cannot prove whether that
  particular command committed. No new route or bus command is involved.
- `403` discards the secret and re-prompts; `GET /control/state` answering `404`
  is rendered as the legitimate "no ceremony yet" state.
- **The panel is state-driven.** A single mapping (`controlsForState` in
  `control.js`) hides every normal control the current phase makes a no-op: with
  no session only **Start reveal** is visible; while `revealing` the panel shows
  **Step**, **Back**, **Jump**, **Start over**, and **Reset to idle**; when
  `done`, **Step** and **Jump** hide but the destructive actions and **Back**
  stay. **Back** is deliberately *not* gated on `revealed_count` — a step can be
  a pure cursor move, so "0 revealed" does not mean "nothing to undo".
- **Starting over is a dedicated, modal-confirmed action.** **Start over…**
  opens a modal that states how many teams were already revealed and warns that
  the ceremony is discarded and rebuilt from current contest data *and settings*,
  medal cutoffs included; confirming sends `start-reveal` with `restart: true`.
  Plain **Start reveal** always sends `restart: false` and is hidden whenever a
  live session would refuse it with `409`. Start over is offered whenever a
  ceremony is stored — `idle` as well as `revealing`/`done` — because plain Start
  reuses a stored idle session along with the cutoffs it was created with, so
  without it a configuration change could never be adopted through the UI. It is
  hidden only when there is no stored session at all, where there is nothing to
  rebuild.
- **Recovery controls remain visible.** A warning block immediately before the
  action reference keeps **Rebuild state…** and **Reload state** available in
  every phase and tells operators not to use them during normal operation.
  Rebuilding opens a confirmation, replaces stored state with a new frozen
  snapshot from current contest data, and sends `start-reveal` with
  `restart: true`. Reloading only reads authoritative stored state.
- **Recovery messages remain specific.** A `500` carrying the exact
  unusable-state detail hides ordinary commands and tells the operator to use
  **Rebuild state**. Temporary failures and unrelated `500` responses keep the
  load-error message and ordinary commands hidden. An ambiguous command outcome
  keeps its unknown-outcome warning until **Reload state** succeeds.
- **Reset to idle preserves the frozen snapshot.** The modal-confirmed
  **Reset to idle…** action clears reveal progress without rebuilding current
  contest data or starting the ceremony. Its successful `idle` projection
  leaves only **Start reveal** available.
- **Every action is explained on screen.** The reference after the recovery
  warning distinguishes starting, rebuilding, resetting, and read-only
  authoritative reloading.
- Keyboard: `→` step, `←` back — documented on screen and suppressed whenever a
  form control (or a contenteditable element) has focus. Each shortcut fires
  only while its control is visible.

### Second client: the Android remote (`clients/animator-remote`)

The control API has **two** operator clients, so a change to its contract has two
consumers. The Android remote (`clients/animator-remote/`, see its `README.md`)
exists because an operator running a ceremony is on stage, away from the machine
driving the projector.

- It ports `controlsForState` and the stated-refusal/ambiguous split
  **name-for-name** into `core/CommandClient.kt` and `core/Controls.kt`, so the two
  clients can be diffed against each other rather than reasoned about separately.
- It implements the "retry that command" affordance the panel deliberately leaves
  unbuilt: it retains each attempt's `Idempotency-Key` and offers re-sending *the
  same attempt*, which the server replays. **Reload state** remains the other
  recovery, and every command stays locked until one of them succeeds.
- It additionally consumes the credential-free `/reveal/events` nudge feed to stay
  in sync, under the rule that a nudge may refresh the display but may **never**
  release the ambiguous-outcome lock.
- Its Kotlin core imports no Android and no HTTP library — the transport is an
  injected function type, exactly as `control.js` injects `fetchImpl` — so
  `tests/animator/test_remote_core_kotlin.py` exercises it on a plain JVM and skips
  when the toolchain is absent, mirroring `test_ceremony_js.py`.
- Because the animator serves no OpenAPI document, the remote's models are
  hand-written and pinned by `tests/animator/test_remote_client_contract.py`, which
  needs **no Kotlin toolchain and never skips**. **If you change a control
  response model, a request model, `IDEMPOTENCY_KEY_PATTERN`, or the unusable-state
  detail string, that test is what tells you the Android client needs updating.**

---

## Reveal control (`animator/routes/control.py`)

Router prefix: `/c/{slug}/control`. The authenticated operator API that
drives the post-freeze reveal ceremony. Every route returns the same
`RevealProjectionResponse` (`animator/models/responses.py`) — the safe projection
the spectator feed also serves.

### Gates, in order

1. **Contest gate** — `EnabledContest`: a missing slug and an animator-disabled
   contest are the same bare `404`.
2. **Kill switch** — checked inside `ControlContest`, *after* the contest
   resolves. While `NOCA_ANIMATOR_ENABLE_CONTROL=false` every route answers that
   same bare `404`, so deployment configuration is never disclosed. It is
   deliberately **not** a router-level dependency: FastAPI resolves those before
   parameter dependencies, which would invert this order.
3. **Credential** — `Authorization: Bearer <operator-token>`, resolved through
   the shared digest service (`shared.services.animator_access_service`).
4. **Controller ownership** — every **mutating** command additionally requires
   an active controller lease under the caller's resolved scope and a matching
   `X-Animator-Controller-Id` header (see *Controller lease* below). `GET
   /control/state` is read-only and lease-independent.

The order is load-bearing: a token is never resolved for a contest the caller may
not know exists, and a switched-off deployment never answers in an
authentication shape. Tokens travel **only** in the header — never in a path,
query string, or response — so they cannot reach an access log, a `Referer`, or
browser history.

### Scope rules

- A **site** token authorizes exactly its own site; a **global** token (a
  `site_secrets` row with `site_id = NULL`) authorizes exactly the global
  ceremony.
- `start-reveal` accepts a `site_id` only to compare it for **exact** equality
  with the token's own scope, `None` included. Any mismatch is the same generic
  `403` an invalid token gets, so the body cannot enumerate sites.
- Every command after `start-reveal` takes **no** scope input at all: the
  credential selects the stored session. `step`, `back`, and `reset` declare an
  explicit empty `extra="forbid"` body model, so a smuggled `site_id` is a `422`
  rather than JSON FastAPI would otherwise discard in silence — a caller can
  never come away believing it redirected a ceremony. Sending no body remains
  the normal call.

### Retry safety (`Idempotency-Key`)

Every **mutating** command accepts an optional `Idempotency-Key` request header
matching `^[A-Za-z0-9_-]{8,128}$`. It is a header, not a body field, so all five
commands take it uniformly — including the three whose body is empty by design.
`GET /control/state` accepts none: a read has nothing to apply twice.

The key is **not a credential**. It is caller-chosen, grants nothing, and is
never logged (the audit line records `idempotent=yes|no` only). It identifies one
*command attempt*, so a fresh key belongs on each deliberate command; reusing one
is how a caller says "this is the same attempt again".

Given a key, the server matches it against the ceremony's bounded receipt ring
(the last `8` applied commands, stored inside the session state and expiring with
it) **inside the scope lock, before the engine runs**:

| The key names… | Result |
|---|---|
| the **most recent** applied command, same command | `200` with the **original** projection; nothing saved, nothing published |
| the same command, but an **older** ring entry | `409` (`superseded`) — the ceremony moved on and that result cannot be reproduced |
| a **different** command | `409` (`key_reused`) — one key means one command |
| nothing on record | applied normally, and the key is remembered |

Without a key the command is applied exactly as sent. That is why an operator
facing an ambiguous outcome (a network failure, or any `5xx` — including the
store's `503`, which can arrive *after* a fenced save committed) and no key must
reload authoritative state rather than retry; with a key, retrying is safe and is
the intended response. `start-reveal` with `restart=true` discards the ring along
with the rest of the old state, so a pre-restart key identifies nothing and its
command applies normally.

| Method | URL | Name | Description |
|--------|-----|------|-------------|
| `POST` | `/c/{slug}/control/start-reveal` | `animator_control_start` | Open the ceremony this credential authorizes. Body (optional): `{"site_id": <site id or null>, "restart": false}`. Builds the frozen universe from current data on a fresh or restarted session; an `idle` session (fresh, or returned to idle by `reset`) starts over its existing universe. An already `revealing`/`done` session is refused with `409` unless `restart` is `true`. |
| `POST` | `/c/{slug}/control/step` | `animator_control_step` | Reveal exactly one relevant frozen submission — or, on a row with nothing left, move the cursor up one. No body. |
| `POST` | `/c/{slug}/control/back` | `animator_control_back` | Un-reveal the most recent submission (an exact `pop`). No body. |
| `POST` | `/c/{slug}/control/jump-pending` | `animator_control_jump_pending` | Advance cursor-only steps until the next `?` is focused, then stop before revealing it. An already-focused pending cell is an exact no-op. No body. |
| `POST` | `/c/{slug}/control/reset` | `animator_control_reset` | Empty the reveal log and return the ceremony to `idle`. No body. |
| `POST` | `/c/{slug}/control/jump-team` | `animator_control_jump_team` | Replay ordinary steps until `team_id` is focused. Body: `{"team_id": "<id>"}`. |
| `GET` | `/c/{slug}/control/state` | `animator_control_state` | Current projection. Read-only: takes no lock, saves nothing, publishes nothing. |

### Response shape

`RevealProjectionResponse` carries `contest_id`, canonical `scope` (site id or
`global`), `site_id`, `site_name`, `phase`, `focused_team_id`, `revealed_count`,
`frozen_count`, `medal_cutoffs`, derived `teams`, and `next_cell`.

`next_cell` is `{team_id, problem_id, label}` (or `null` while idle or done) and
names the cell the **next `step` will change**, so a projector can draw the
audience's attention to it. It is derived by `reveal_engine.next_reveal_cell()`,
which repeats exactly the selection `step()` performs and stops before applying
it — a projector that guessed instead (say, the focused team's first pending
cell) would eventually highlight a cell the step then leaves alone. It carries
**position only**: no verdict, attempts, or timing, so it discloses nothing about
the result, only where it will land. It is deliberately **not**
`RevealSessionState`: that model holds `frozen_submission_ids` and `reveal_log`,
whose ids would disclose the shape of the *unrevealed* remainder. Only counts
leave the server.

### Status codes

| Condition | Status |
|---|---|
| Unknown slug, `animator_enabled=false`, or `NOCA_ANIMATOR_ENABLE_CONTROL=false` | `404` (bare, identical bodies) |
| Missing, malformed, wrong-scheme, blank, unknown, or other-contest token; scope mismatch on `start-reveal` | `403`, single generic `{"detail": "Invalid operator credential"}` |
| Missing or malformed `X-Animator-Controller-Id` on a mutating command | `422` — rejected before the store is touched |
| Another controller owns this scope, or this controller lost its lease | `409`, **no** `Retry-After` |
| Command on a scope with no stored session (including an expired key) | `404` |
| The credential's site is no longer a site of this contest | `404` |
| `start-reveal` on a `revealing`/`done` session without `restart` | `409` |
| `step`/`jump-team`/`jump-pending` on a session that was never started | `409` |
| `jump-pending` when no pending submission remains | `409`, with no mutation |
| `jump-team` to a team that can never become focused | `409` |
| `jump-team` to a team outside the ceremony's scope | `400` |
| Malformed request body (unknown field, missing `team_id`) | `422` |
| Malformed `Idempotency-Key` (too short, too long, illegal characters) | `422` — rejected before the store is touched |
| Retried key naming a superseded command, or reused for a different command | `409`, **no** `Retry-After` — the operator reloads state instead |
| Lock contention, lost lock ownership, or store unavailable | `503` with `Retry-After: 1` |
| Corrupt, foreign-versioned, or misfiled persisted state | `500`, **no** `Retry-After` — retrying cannot repair it; an operator recovers with `start-reveal` + `restart=true`, which does not read the broken payload |

### Durability and logging

- Each successful mutation is **exactly one** fenced save followed by one
  projection nudge, including no-op mutations (`back` on an empty log, `step` on
  a `done` ceremony) — so "applied, nothing changed" stays distinguishable from
  "never reached the server". Authorization, active-start, and domain refusals
  save and publish nothing. A **replayed** command saves and publishes nothing
  either, and is audited as `outcome=replayed` rather than `success`, so one
  operator action is never recorded as two commands.
- The whole mutation (load → decide → save → publish) runs inside the Phase 11
  per-scope lock, so no decision is made across a window another operator could
  write in. A publish that fails after a durable save still returns `200`,
  matching the Phase 11 contract.
- **Exactly one audit record per request**, emitted by the `ControlAuditRoute`
  boundary (`animator/routes/control_audit_route.py`) that the router installs as
  its `route_class`:
  `event=animator_control_attempt command=… contest_id=… scope=… outcome=… status=… idempotent=… client_ip=…`.
  Nothing else logs: gates, dependencies, and routes only *note* what they know
  onto the request (`animator/services/control_audit.py`), and the boundary emits
  the line when the request finishes. That is what covers the refusals no route
  function ever sees — the contest gate and kill-switch `404`s
  (`outcome=not_found` / `control_disabled`), credential `403`s
  (`invalid_credential`, `scope=unknown`), and FastAPI's own body-validation
  `422`s (`invalid_request`) — while making a duplicate record structurally
  impossible. A request that never matches a control operation (a misspelled
  sub-path, or a wrong method, which Starlette answers with `405` from the
  router) is not audited: no control operation was attempted. It is a
  `key=value` **message**, not `extra=` fields,
  because the production console formatter renders `%(message)s` only. No token,
  digest, or credential label is ever logged; an attempt is identified by the
  scope it resolved to (`unknown` before the credential resolves, and
  `contest_id=unknown` for a gate `404`, which by definition resolved no
  contest). `client_ip` comes from the shared
  `get_ip_from_request`, i.e. the proxy-corrected `request.client.host` —
  behind a reverse proxy, list it in `NOCA_FORWARDED_ALLOW_IPS` or the record
  will name the proxy instead of the operator.
  These are logs, not `security_events` rows: the animator is not in the
  `security_events_reaper` ownership set, so persistent rows would never be
  pruned.
- Log levels match expectations: an accepted attempt is `INFO`, every refusal is
  `WARNING`, and only unusable persisted state adds an `ERROR` with a traceback.
  Contention and a briefly unavailable store are self-healing conditions and
  never produce one.

---

## Controller lease (`animator/routes/controller_lease.py`)

Router prefix: `/c/{slug}/control/controller-lease`. The authenticated
single-controller ownership API for one ceremony scope. It exists so that two
operator panels — the browser control page and the Android remote are both
shipped controllers — cannot drive the same global or site ceremony, while any
number of read-only projectors keep working untouched.

### Gates and identity

The same contest gate, kill switch, and credential gate apply in the same order
as the command API, plus the same `ControlAuditRoute` audit boundary. On top of
the bearer token every lease request carries an opaque `X-Animator-Controller-Id`
header matching `^[A-Za-z0-9_-]{8,128}$` (`animator.models.controller_lease`).
The id is **not a credential**: it identifies one loaded panel, is generated per
panel (a UUID), lives only in that panel's memory, and never appears in a URL,
the DOM, storage, or a log. Lease values in Valkey hold nothing but this id — no
tokens, digests, or addresses.

### Operations

| Method | URL | Name | Description |
|--------|-----|------|-------------|
| `POST` | `/c/{slug}/control/controller-lease/claim` | `animator_controller_lease_claim` | Claim an empty lease, or re-claim idempotently while already the owner. A second controller for the scope gets `409`. |
| `POST` | `/c/{slug}/control/controller-lease/heartbeat` | `animator_controller_lease_heartbeat` | Renew only the caller's active lease; refreshes its TTL. |
| `POST` | `/c/{slug}/control/controller-lease/release` | `animator_controller_lease_release` | Release only the caller's active lease. Best-effort on page teardown; TTL expiry remains authoritative. |
| `POST` | `/c/{slug}/control/controller-lease/takeover` | `animator_controller_lease_takeover` | Replace the current owner — or claim an empty one — after explicit operator confirmation. Succeeds only while the per-scope mutation lock is free, so a takeover never interrupts a mutation in flight; the former controller is fenced from every later heartbeat and command by the atomic replacement itself. |

### Response shape

All four return `ControllerLeaseResponse`
(`animator/models/controller_lease.py`): `status` (`claimed`, `renewed`,
`released`, or `taken_over`), `lease_ttl_seconds`, and
`heartbeat_interval_seconds` — timing only. No response ever names the current
holder: a blocked panel learns *that* it does not own the scope, never *who*
does.

### Status codes

| Condition | Status |
|---|---|
| Unknown slug, disabled contest, or kill switch off | `404` (bare) |
| Invalid credential | `403` |
| Another controller owns the scope, or the caller lost ownership | `409`, **no** `Retry-After` |
| A reveal mutation is in progress (takeover), or the store is unavailable | `503` with `Retry-After: 1` — fail closed; ownership is never granted from stale local state |
| Missing or malformed controller-id header | `422` |

### Client contract

Both shipped controllers follow one lifecycle: generate the id at load, claim
after the credential validates, renew on a bounded interval (promptly again on
`visibilitychange`/`pageshow` restoration), release best-effort on
`pagehide`/background via `fetch(..., {keepalive: true})`, and stop commands and
heartbeats immediately when an operation reports lost ownership. Read-only and
lease-lost panels keep receiving authoritative state through the lease-free
`GET /control/state` and offer **Take over control…** behind a modal warning
that the other panel immediately loses command authority. There is no automatic
takeover.
