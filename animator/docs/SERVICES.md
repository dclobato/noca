# NOCA Animator Service Reference

This document lists the service and read-model modules under `animator/` and the
capabilities they provide. The animator reads the shared schema through
SQLAlchemy Core only; it defines no ORM mappings and never imports `web`.

For shared/cross-module services (Valkey runtime, scoreboard projection), see
[docs/SHARED_SERVICES.md](../../docs/SHARED_SERVICES.md) and
[shared/services/scoreboard_projection.py](../../shared/services/scoreboard_projection.py).

Conventions:

- prefer reusing documented public helpers before creating new ones
- if route behavior changes, keep this file and [ROUTES.md](ROUTES.md) in sync

---

## `main.py` (lifespan services)

Purpose:

- process bootstrap: database pool, feed cache, Valkey runtime, event stream,
  templates, static mounts — and the Valkey worker-presence heartbeat

Provides:

- an `AnimatorFeedCache` on `app.state.feed_cache`, created **before** the
  Valkey branch so the feeds keep working without Valkey (the cache then relies
  on its TTLs alone), and handed to the event stream as its
  `on_contest_changed` hook
- a `worker_presence_loop` task started under `WorkerClass.ANIMATOR`, using
  `NOCA_ANIMATOR_WORKER_ID` (defaulting to `<fqdn>:<pid>`),
  `NOCA_ANIMATOR_WORKER_PRESENCE_INTERVAL_SECONDS`, and
  `NOCA_ANIMATOR_WORKER_PRESENCE_TTL_SECONDS`. `ANIMATOR` is a **presence-only**
  class: the health monitor probes it as its own service, and it never appears in
  the Arena admin worker dashboard or its pause machinery.
- ordered shutdown: presence is retired first (while Valkey is still up, since
  the live marker can only be cleared through that runtime), then the event
  stream, then the Valkey runtime, then the database pool. Each stage sits in its
  own `finally`, so a failure in any one of them cannot skip the rest; the
  heartbeat task is awaited with `return_exceptions=True` so an already-failed
  task still allows `mark_worker_offline()` to run.

---

## `models/query_records.py`

Purpose:

- animator-local immutable query records holding exactly the columns read from
  the shared schema, structurally compatible with the `compute_icpc` protocols

Provides:

- `ContestRecord` — frozen contest projection with UTC timing helpers
  (`start_time_utc`, `end_time_utc`, `freeze_at_utc`, `freeze_at_seconds`) and
  `is_frozen_at(now)`; it carries `release_scoreboard_after_end`, so an ended,
  released contest exposes the same final standings as Web. Satisfies
  `ContestScoringInput`
- `TeamRecord` / `ProblemRecord` / `SubmissionRecord` / `JudgmentRecord` —
  satisfy `TeamInput` / `ProblemInput` / `SubmissionInput` / `JudgmentInput`.
  `JudgmentRecord` additionally carries its `id`, which the scoring protocol does
  not need: it is what lets the recent-activity seed key an entry
  `verdict:<judgment_id>`, the identity the live SSE stream uses, so a seeded and
  a streamed copy of the same result de-duplicate
- `SiteRecord` — site medal cutoffs plus associated team count for meta
- `TeamMediaMetadata` — a team's stored-media **metadata only**: `com_foto`,
  the revisions `dta_foto` / `dta_audio`, and the presence booleans `has_photo`
  / `has_avatar` / `has_audio`. Carries no payload, so the media routes can
  answer a conditional request from it alone; decoding and verification of the
  blobs, loaded one column at a time on a miss, belong to
  `services/team_media_service.py` (photo) and `services/team_audio_service.py`
  (audio). `com_foto` governs the image blobs only — audio has no such flag
- `ensure_utc(value)` — normalize naive/aware datetimes to UTC

---

## `models/responses.py`

Purpose:

- the exact, presentation-safe public surface of the feed (Pydantic models)

Provides:

- `ContestMetaResponse`, `ProblemMeta`, `SiteMeta`
- `ScoreboardSnapshotResponse`, `TeamStandingResponse`, `ProblemCellResponse`,
  `PendingSubmissionResponse`
- `RecentEventResponse` / `RecentEventKind` — one past contest event for seeding
  a freshly loaded activity rail. It carries the *parts* of the sentence
  (`kind`, `team_name`, `team_fullname`, `problem_label`, `verdict`) and never
  the sentence, so the seeded backlog and the live stream cannot drift into two
  vocabularies. The rail names the team with `team_fullname`, falling back to
  the login, exactly as the board does
- live SSE payloads for the `/events` stream: `VerdictPayload`,
  `RedactedVerdictPayload`, `SubmissionPayload`, `ScoreboardRefreshPayload`,
  `TimerTickPayload`
- `RevealProjectionResponse` — the reveal-ceremony projection, with
  `from_projection(state, teams, next_cell)`
- `RevealPublicStateResponse` — the spectator envelope: `has_session`, contest
  and scope identity, and a nullable `projection`
- `RevealReadyPayload` — the `reveal_ready` coverage signal (`{"ready": true}`)

Only the fields declared here are serialized. No operator secrets, secret
digests, credentials, emails, or unrelated contest configuration are exposed.

`RevealPublicStateResponse` wraps that projection rather than replacing it,
because a spectator can legitimately ask about a scope no operator has opened
yet. `has_session` is deliberately **not** called `started`: it reports only
whether durable state exists, and after `reset` a session still exists with
`phase="idle"` — progress is read from `projection.phase`, never from the flag.

`RevealProjectionResponse` is deliberately **not** `RevealSessionState`: the
state model carries `frozen_submission_ids` and `reveal_log`, whose ids would
disclose the identity and shape of the *unrevealed* remainder of a ceremony. The
response exposes counts (`revealed_count` / `frozen_count`), `phase`, canonical
`scope`, `focused_team_id`, `medal_cutoffs`, the already-derived `teams`, and
`next_cell` (position only — see `next_reveal_cell` below).
It lives here rather than beside the control models because the spectator
projection returns the same shape — one model, so the operator view and the
public view of a ceremony cannot drift.

---

## `models/control.py`

Purpose:

- typed request bodies for the authenticated reveal control API

Provides:

- `StartRevealRequest` — optional `site_id` (compared for exact equality with
  the credential's scope, never trusted as scope input) and `restart`
- `JumpTeamRequest` — the required `team_id`
- `EmptyCommandRequest` — the explicit empty body of `step` / `back` / `reset`

Behavior notes:

- **No credential field exists.** The operator token travels only in the
  `Authorization: Bearer` header, so it cannot reach a query string, an access
  log, a validation-error echo, or an OpenAPI example.
- **`extra="forbid"`.** A misspelled field — or a `site_id` smuggled into a
  post-start command — is a `422`, never a silently ignored value.
- **Why the scope-free commands still declare a model.** Without a body
  parameter FastAPI discards unexpected JSON in silence, so a caller passing
  `{"site_id": …}` to `/step` would get a `200` and could reasonably conclude the
  field was honored. `EmptyCommandRequest` makes that a `422` instead. Sending no
  body remains correct: the routes default the parameter to `None`.

---

## `models/reveal_session.py`

Purpose:

- the immutable state contract of one reveal ceremony, plus the derived view
  models the UI payload is built from

Provides:

- `RevealSessionState` — contest id, optional site id/name, `phase`
  (`idle` / `revealing` / `done`), optional `medal_cutoffs`, the ordered
  `frozen_submission_ids` universe, the `step_log`, `focused_team_id`, and the
  bounded `command_receipts` ring. `reveal_log` and `cursor` are **derived
  properties** of the trail, not stored
- `StepEntry` — one operator step: `reveal` (carrying a `submission_id`) or
  `advance` (moving the cursor one row up)
- `MedalCutoffs` — positive `gold` / `silver` / `bronze`, validated ordered
- `TeamRevealView` / `ProblemRevealView` — derived views, never persisted
- `NextRevealCell` — the cell the next `step` will change (`team_id`,
  `problem_id`, `label`); position only, never a verdict
- `with_receipt(receipt)` / `receipt_for(key)` / `is_latest_receipt(key)` — the
  retry-recognition surface `control_service` uses inside the lock
- `REVEAL_STATE_VERSION`, `RevealStateVersionError`, `RevealPhase`, `Medal`

Behavior notes:

- **One trail, two derived values.** The ceremony's whole history is the ordered
  `step_log`; the revealed set and the cursor position are computed from it. A
  cursor stored *beside* the reveals could drift away from them, and `back()`
  would have to undo two things consistently instead of popping one.
- **The cursor walks every row.** A step on a row that still holds a frozen run
  reveals one; a step on a row with nothing left just moves the highlight up. So
  the operator traverses the whole table with one key, and `next_reveal_cell()`
  returns `None` on the rows that only pass through — a glow promises a cell is
  about to change.
- **The cursor is a screen position, not a team.** When a reveal lifts a team
  past others, the cursor holds its row and whoever now occupies it comes into
  focus. Nothing is skipped by that: a reveal can only improve a team's score, so
  the teams pushed down land at most on the cursor's own row, and the lifted team
  is met again as the sweep climbs toward it.
- **No parallel scoreboard.** `phase` and `focused_team_id` also change, but
  neither feeds scoring. Rank, attempts, penalty, solved cells, and medals are
  pure functions of the log, recomputed through `compute_icpc`. Nothing derived
  is ever persisted, so `back()` is an exact `pop` and the ceremony cannot drift
  from the official scoreboard.
- **Deep immutability.** `frozen=True` only blocks field *reassignment*, so every
  collection field is a `tuple`: `state.reveal_log.append(...)` is impossible.
  Tuples still serialize as JSON arrays.
- **`extra="forbid"` on persisted models.** A payload carrying a derived field
  (`rank`, `attempts`, `penalty`, ...) is rejected rather than silently ignored.
- **Transitions rebuild through validation.** `model_copy(update=...)` does *not*
  validate its update values and would bypass the duplicate/subset/scope
  validators. Use `with_reveal_log()` / `with_phase()` / `with_focused_team()` /
  `with_updates()`, which implement `model_dump` → mutate → `model_validate`.
- **Invariants.** The reveals derived from `step_log` form a duplicate-free
  subset of a duplicate-free `frozen_submission_ids`. Scope is all-or-nothing for
  *identity*: `site_name` is set if and only if `site_id` is. `medal_cutoffs` is
  deliberately outside that pairing — a global ceremony carries the contest's own
  global cutoffs when they are configured and `None` when they are not, which is
  also exactly what a ceremony recorded before global medals existed looks like,
  so old payloads keep loading without a version bump.
- **Command receipts are recorded state, not derived.** `command_receipts` is
  the bounded ring of applied `Idempotency-Key`s (see
  `models/command_receipt.py`), kept *inside* the state so one fenced write
  covers both "what is the ceremony" and "which commands produced it". Its
  invariants — unique keys, at most `MAX_COMMAND_RECEIPTS` entries — are enforced
  by the same validator as everything else.
- **Versioning.** `state_version` is `Literal[3]` — bumped from 1 when the trail
  stopped being a plain list of reveals, and from 2 when the state gained command
  receipts. A version-1 payload is refused rather
  than replayed: it recorded no cursor, so reading it would silently restart the
  sweep at the bottom row and re-walk rows the operator had already passed. A
  version-2 payload is refused for the same class of reason: it recorded no
  receipts, so the first retry after an upgrade would apply a second time. An
  in-flight ceremony is recovered with `start-reveal` + `restart=true`. It is
  pinned so a foreign payload cannot be
  constructed at all; `from_payload()` reads the raw version first and raises
  `RevealStateVersionError` with a readable message before validation runs. The
  version is read **with no default**: an unversioned payload is legacy or
  corrupt, not current, and is rejected exactly like a foreign version.
  Defaulting it would relabel such a payload as current and defeat the gate.

---

## `models/command_receipt.py`

Purpose:

- the bounded record of which commands a ceremony has already applied, so a
  retried command can be replayed instead of applied a second time

Provides:

- `CommandReceipt` — one applied command: the caller's `key` and the `command`
  it applied. Frozen, `extra="forbid"`
- `append_receipt(ring, receipt)` — pure append, trimmed to the ring bound
- `MAX_COMMAND_RECEIPTS = 8`, `IDEMPOTENCY_KEY_PATTERN = ^[A-Za-z0-9_-]{8,128}$`

Behavior notes:

- **A receipt carries nothing sensitive.** It is a caller-chosen key plus a
  command name — never an operator token, digest, or label. That is what makes
  it safe to persist alongside the ceremony and to keep out of every log.
- **Bounded twice.** By count (`MAX_COMMAND_RECEIPTS`) and by the state key's
  TTL, since the ring lives inside the state. The count bound is the tighter of
  the two and is deliberate: a retry follows its command by seconds, and an
  unbounded ledger would grow for a whole ceremony to answer a question nobody
  asks anymore. Past the bound a repeated key is reported as *superseded* — a
  stated refusal — never applied a second time.
- **Trimming drops the oldest.** The newest receipt is the only replayable one
  (the current state *is* its result), so it can never be the entry evicted.

---

## `services/reveal_loader.py`

Purpose:

- load and *scope* every projection input for one ceremony, then build the
  deterministic frozen universe and the initial state

Provides:

- `RevealDataset` — frozen dataclass of contest, optional site, teams, problems,
  submissions, judgments, and a `site_names` map for team views
- `load_reveal_dataset(session, contest, site_id=None)` — composes the existing
  `contest_queries` loaders; owns no SQL of its own
- `build_frozen_submission_ids(dataset)` / `initialize_reveal_session(dataset)`
- `submission_sort_key(submission)` / `submissions_by_id(submissions)`
- `UnknownSiteError` — raised when the requested site is not in the contest

Behavior notes:

- **Scope is applied to every projection input.** `compute_icpc` derives first
  solvers by scanning *every* submission it is given, without cross-checking the
  `teams` argument (`shared/services/scoreboard_projection.py`). Filtering only
  the frozen ids would let an out-of-site team's earlier accepted run consume the
  first-solver marker in a site ceremony, so the loader narrows teams,
  submissions, **and** the judgment mapping. Site membership is the explicit
  `users.site_id`; a global ceremony keeps every contest team.
- **Frozen predicate.** A submission is frozen exactly when
  `timestamp_seconds > freeze_at_seconds` — the scoreboard's own criterion, on
  contest-relative seconds, never the wall clock. `submissions.timestamp_seconds`
  is `NOT NULL` with a `0` server default, so legacy pre-migration rows read as
  contest second zero and are always pre-freeze.
- **Ordering.** The universe is ordered by the shared
  `scoreboard_projection.submission_sort_key` — `(timestamp_seconds, created_at,
  id)` — which is re-exported here. It is the *same function object*
  `compute_icpc` sorts with, not a second implementation, so the two paths cannot
  disagree on naive or missing `created_at`. `created_at` is normalized inside
  the key only (naive read as UTC, missing sorts first); the stored value is
  never rewritten. This is *not* the pending list's `(created_at,
  timestamp_seconds, id)` recency order — a different contract.
- **Membership is not relevance.** A post-freeze run on an already-solved problem
  stays in the universe; whether a reveal step must consume it is a later
  decision, not a pruning of this set.

---

## `services/reveal_projection.py`

Purpose:

- pure derivation of ceremony standings and views from `(dataset, state)`

Provides:

- `revealed_submissions(dataset, state)` — pre-freeze submissions ∪ the log
- `compute_reveal_standings(dataset, state)` — shared `compute_icpc` standings
- `pending_frozen_counts(dataset, state)` — count of unrevealed frozen runs for
  each `(team_id, problem_id)` cell
- `pending_frozen_cells(dataset, state)` — compatibility set derived from the
  count-map keys
- `medal_for_rank(cutoffs, rank)` and `build_team_reveal_views(dataset, state)`

Behavior notes:

- **One scoring path.** Standings always come from `compute_icpc` over the
  visible set, called with `viewer_sees_frozen=False` because the freeze
  filtering has already happened when the input set was assembled. There is no
  second ranking implementation, so the ceremony inherits the shared ranking
  rules — solved count, then total time, then the earliest last accepted
  submission, with teams equal on all three sharing a rank (documented under
  **Ranking rules** in `docs/SHARED_SERVICES.md`).
- **Pending state is literal and solve-independent.**
  `pending_frozen_count` counts submissions in `frozen_submission_ids` that are
  not in `reveal_log`, even if the cell already shows as solved.
  `pending_frozen` is a computed boolean derived from that count.
- **View mapping.** `solved` / `attempts` / `solved_at_minutes` come straight
  from the shared cells, `is_first_solver` from `is_first_balloon`, and
  `TeamRevealView.penalty` from `TeamStanding.total_time` (solve minutes plus
  attempt penalties). Only the derived pending count and the medal band are
  added on top.

---

## `services/reveal_engine.py`

Purpose:

- the pure, reversible bottom-up reveal state machine: every command is a
  function of `(dataset, state)` returning a new state plus its derived views

Provides:

- `RevealTransition` — frozen dataclass of `state`, `teams`
  (`tuple[TeamRevealView, ...]`), and `next_cell`, the payload every command
  returns
- `next_reveal_cell(dataset, state)` — the cell the next `step` would change,
  computed *without* changing anything. It repeats exactly the selection
  `_step_state` performs (standings → relevant runs → focus → `next_reveal_id`)
  and stops before applying it, so the projector's glow can never land on a cell
  the step then leaves alone. Returns `None` unless the phase is `revealing`.
- `start(dataset, state)` — establish focus; idle becomes `revealing` or `done`
- `step(dataset, state)` — reveal one relevant frozen run on the cursor's row,
  or climb one row when that row has nothing left
- `back(dataset, state)` — undo the last step of either kind, by `pop`
- `reset(dataset, state)` — empty the trail and return to `idle`
- `jump_pending(dataset, state)` — climb cursor-only rows until the next
  pending cell is focused, without revealing it
- `jump_team(dataset, state, team_id)` — repeat `step` until the target focuses
- `relevant_frozen_submissions` / `focus_at_cursor` / `team_relevant_runs` /
  `next_reveal_id` — the pure selection helpers the commands are built from
- `RevealTransitionError` → `RevealNotStartedError`,
  `NoPendingSubmissionError`, `UnknownTeamError`, `UnreachableTeamError`

Command-state matrix:

| Command | `idle` | `revealing` | `done` |
|---|---|---|---|
| `start` | recompute focus/phase | idempotent recompute | recompute; stays `done` unless a run became relevant |
| `step` | `RevealNotStartedError` | reveal one run, or climb one row | no-op, state unchanged |
| `back` | empty trail: exact no-op | empty trail: exact no-op; otherwise `pop` → `revealing` | empty trail: exact no-op; otherwise `pop` → `revealing` |
| `reset` | no-op | empty trail, `idle`, no focus | empty trail, `idle`, no focus |
| `jump_pending` | `RevealNotStartedError` | stop before the next pending cell; none left → `NoPendingSubmissionError` | `NoPendingSubmissionError` |
| `jump_team` | scope-check, then `RevealNotStartedError` | bounded repeated `step` | `UnreachableTeamError` |

Behavior notes:

- **One derivation of phase and focus.** `start()`, `step()` and `back()` all end
  in the same private `_recompute`, so the three commands cannot disagree about
  what a given reveal log means. `start()` is exactly "recompute from the current
  log", which is why it is idempotent in every phase.
- **Exact reversibility.** `back()` is one `pop` of the step trail plus that
  recomputation — never a stored inverse mutation — so `back(step(s)).state == s`
  field for field, whether the step revealed a run or only climbed a row. That is
  precisely why the cursor lives *in* the trail: with a separate counter, `back`
  would have to guess which of the two the last step had been. An empty trail is
  an exact no-op in every phase, so `back()` never invents an `idle` →
  `revealing` transition; `reset()` is the only route back to `idle`.
- **Relevance.** A frozen run is relevant when it is unrevealed *and* its
  `(team, problem)` cell is not already solved in the revealed view. Once a
  problem is solved, `compute_icpc` ignores its later runs for scoring, so
  revealing them individually would change nothing — they stay in the universe
  (see `reveal_loader`) but are never consumed, and the ceremony ends with them
  outstanding.
- **The cursor sweeps every row, bottom-up.** `focus_at_cursor` indexes
  `standings` from the end rather than comparing rank numbers, because teams tied
  on all of `(solved, total_time, last_accepted_seconds)` share a rank. The order
  is deterministic: teams load in `(username, id)` order and `compute_icpc` sorts
  stably on score alone. A row with nothing to reveal is
  still visited — the operator walks the whole table with the same key — and the
  ceremony ends only once the cursor passes the top row.
- **The cursor is a position, not a team.** When a revealed solve lifts the
  focused team past others, the cursor **holds its row** and whoever now occupies
  it comes into focus. A single sweep still resolves everything: a reveal can
  only improve a team's score, the teams it overtakes fall at most onto the
  cursor's own row, and the lifted team — having moved *up* — is met again as the
  sweep climbs. This replaces the earlier "bottom-most eligible team" rule.
- **Problem traversal is by ordinal, which *is* label order.** Labels come from
  `ordinal_to_label`, so sorting the label strings would place `AA` before `Z`.
  Within the first eligible problem, the oldest run by the shared
  `submission_sort_key` is revealed.
- **`jump_team` is not a shortcut.** It replays the same transition `step` uses,
  so it can only reach states plain stepping could — it iterates a state-only
  helper and projects team views exactly **once**, at the end, so a long jump
  never renders intermediate standings just to discard them. Scope is validated
  first (so an
  out-of-scope id always reports `UnknownTeamError`, never a phase error), and
  the loop is bounded by the frozen-universe length — every step consumes one id,
  so exceeding that bound means no progress is possible and the target raises
  `UnreachableTeamError`.
- **`jump_pending` never reveals.** While the phase is `revealing`, an absent
  `next_reveal_cell` means the next `_step_state` is a pure cursor move. The
  command checks that condition before every step and stops as soon as a cell
  appears. It is an exact no-op when a pending cell is already focused and
  raises `NoPendingSubmissionError` without saving or publishing when none
  remains; it never sweeps the ceremony to `done` as a side effect.
- **No dependency.** `transitions` and `python-statemachine` both model a mutable
  object advanced by callbacks and offer no exact inverse. Here the whole scoring
  history is one ordered log and every other value is recomputed, so a library
  would wrap an immutable value type in a mutable machine for a three-node graph
  and obscure the only property that matters: auditable, exact reversal.
- **Medals are not reimplemented.** Bands come from
  `reveal_projection.medal_for_rank` over the current *scoped* rank, applied by
  `build_team_reveal_views`; the engine only decides which runs are revealed.
  The same shared `medal_band_for_rank` backs the live scoreboard's per-row
  `medal`, so the projector and the board cannot disagree about the podium.
- **Cutoffs are snapshotted, not read live.** `initialize_reveal_session` maps
  the site's cutoffs — or, for a global ceremony, the contest's
  `global_*_cutoff` triple through `MedalCutoffs.from_optional` — into the state
  once, when the session is created. A later settings change therefore cannot
  reshuffle the bands under an operator mid-ceremony; adopting one is an
  explicit `start-reveal` with `restart=true` (**Start over** in the panel,
  which is reachable from an idle stored session for exactly this reason).

---

## `services/controller_lease_service.py`

Purpose:

- server-enforced single-controller ownership per `(contest_id, scope)`, so two
  operator panels cannot drive one global or site ceremony while read-only
  projectors remain untouched

Provides:

- `ControllerLeaseService(client, *, ttl_seconds)` — claim / heartbeat / release
  / takeover over any `ControllerLeaseClient` (`eval`-only Protocol satisfied by
  `ValkeyRuntime` structurally)
- `acquire_controller_mutation_lock(client, *, contest_id, scope, controller_id,
  lock_token, lock_ttl_seconds)` — the atomic "verify ownership **and** take the
  command lock" gate used by `RevealSessionStore.mutate`
- `ControllerLeaseService.verify_ownership(contest_id, scope, controller_id)` —
  the ownership half alone, taking **no lock and extending nothing**, for an
  action that authorizes against the lease but persists nothing (the team-media
  cue). Keeping it lock-free is deliberate: a cue must never contend with a
  command in flight, and never renewing means the heartbeat stays the only thing
  that keeps a lease alive.
- typed errors: `ControllerLeaseError` → `ControllerLeaseUnavailableError`
  (Valkey unreachable → fail closed), `ControllerLeaseConflictError` (another
  owner), `ControllerLeaseLostError` (not the owner / expired),
  `ControllerLeaseContendedError` (a mutation is in flight)
- `ControllerLeaseResult(status, ttl_seconds)` with status `claimed`,
  `renewed`, `released`, or `taken_over`

Behavior notes:

- **Integer-only script returns.** Every Lua script ends in an explicit,
  distinct non-nil integer per outcome, and `None` from `eval` means exactly
  "unavailable → fail closed". This matters because `eval` also returns `None`
  when a script returns nil/false: conflating the two would turn an outage into
  a spurious ownership decision.
- **Claim is a compare-and-set, not `SET NX`.** Absent → `SET EX ttl`;
  present and ours → refresh `EXPIRE` (idempotent re-claim); present and
  foreign → conflict. A plain NX would answer a legitimate owner's retried
  claim with its own conflict.
- **Takeover replaces atomically and fences by replacement.** One script refuses
  while the mutation-lock key exists, else overwrites the lease key with the new
  id. An empty lease is takeover-as-claim — deliberate, so a fresh panel can
  always recover authority after expiry through the same confirmed modal. The
  former owner is fenced from later heartbeats and commands because its id no
  longer compares equal anywhere.
- **Leases carry only the opaque controller id** — no tokens, digests, or
  addresses — and are isolated by exact scope: controllers of different sites or
  global never block each other.

---

## `services/reveal_session_store.py`

Purpose:

- durable, single-writer persistence and projection publication for a reveal
  ceremony: state survives process restarts, writes are serialized per
  contest+scope, and every mutation is saved before its nudge is published

Provides:

- `RevealSessionStore(client, *, ttl_margin_seconds, lock_ttl_seconds=30)` —
  bound to a `ValkeyRuntime` (or a structural `RevealStoreClient` fake)
- `scope_for(site_id)` — `site_id`, or `"global"` for a global ceremony
- `ttl_seconds_for(contest, now=None)` — contest-end + margin, floored at margin
- `load(contest_id, site_id)` — validated load, or `None` on a genuine miss
- `mutate(contest, site_id, *, controller_id, command, now=None)` — async context manager that
  atomically verifies the caller's controller lease and locks the scope, yields a
  `MutationHandle` (`load()` / `set_result(state)`),
  and on exit fenced-saves then publishes only if a result was set
- typed errors: `RevealStoreError` → `RevealStoreUnavailableError`,
  `RevealStoreLockedError`, `RevealStoreLockLostError`, `RevealStorePayloadError`

Behavior notes:

- **Ownership and the lock are one atomic step.** Lock acquisition is no longer
  a bare `SET NX`: `acquire_controller_mutation_lock` (see
  `services/controller_lease_service.py`) runs one Lua script that verifies the
  caller's controller lease *and* acquires the per-command mutation lock in the
  same transaction, so a controller that lost ownership cannot pass a check and
  race a takeover before its command's lock is acquired. A lost lease surfaces
  as `ControllerLeaseLostError` (→ `409`), contention as
  `RevealStoreLockedError` (→ `503`), an unavailable store as
  `RevealStoreUnavailableError`.
- **Fenced write, not just fenced release.** The state write and the lock-token
  check are one Lua transaction (`shared…revelation.fenced_save_state_script`):
  the state is written with `EX` *only while the lock still holds this writer's
  token*. A plain `SET` would be unfenced — if writer A's lease expired and
  writer B acquired the lock and saved, A could still clobber B. Compare-and-
  delete alone only protects lock *release*. A lost fence returns `0` →
  `RevealStoreLockLostError`, and nothing is published.
- **Result is identity-bound before any write.** A result state handed back
  through `set_result` is checked against the *locked* key first: its
  `contest_id` and the **exact** requested `site_id` (not the normalized scope)
  must match, or it is refused with `RevealStorePayloadError` before the fenced
  save or publish runs. Comparing the exact `site_id` matters because
  `scope_for` also **rejects the literal `"global"` as a site id** (it is the
  reserved global scope): a normalized comparison would otherwise let a result
  whose `site_id == "global"` pass a global (`site_id=None`) mutation, save, and
  then be rejected by the next load — corrupting that ceremony. `load` likewise
  refuses a `"global"` literal site id.
- **Save precedes publish, and publish is truly non-fatal.** On a successful
  fenced save the store publishes a `RevealStateChangedEvent`; the state is
  already durable, so a publish that returns `False` **or raises** is logged and
  swallowed — it never rolls back the saved state and never fails a completed
  operator command. Subscribers recover a missed nudge by reloading state.
- **Unavailable ≠ miss.** `load` uses a Lua `{1,value}` / `{0}` return so an
  unreachable Valkey (`None`) is distinguishable from a genuine miss (`{0}`) —
  a bare `GET` returns `None` for both and could silently reinitialize a live
  ceremony. Unavailable raises `RevealStoreUnavailableError`.
- **Key-identity binding.** A loaded payload must both pass
  `RevealSessionState` validation *and* match the requested key: its
  `contest_id` and `site_id` (including `None` for global) must equal what was
  asked for. A valid-but-foreign or misfiled payload raises
  `RevealStorePayloadError`, as do corruption and a foreign/absent
  `state_version`. The channel/key component guard
  (`shared…revelation.validate_component`) independently validates *both* the
  contest id and the scope before any key is built.
- **No `delete()`.** `reset` is a persisted idle state saved and published
  through the normal fenced path, so "reset support" needs no separate delete.
  Omitting `delete()` avoids a best-effort deletion that could not distinguish
  success from unavailability; TTL expiry is the only key removal.
- **Lock lease.** `DEFAULT_LOCK_TTL_SECONDS = 30` bounds a crashed operator's
  lock; because writes are fenced, an over-short lease can only cause a rejected
  (retryable) save, never data loss. It is a constant rather than a setting: it
  bounds one load-decide-save round trip, which no deployment tunes.
- **State TTL.** `contest end + NOCA_ANIMATOR_REVEAL_TTL_MARGIN_SECONDS`, floored
  at the margin and **refreshed on every successful mutation**. A ceremony
  therefore survives its whole contest and then the margin past the end — which
  is the window that matters, since the reveal happens after the contest. A
  *replayed* command refreshes nothing, because it performs no write. Command
  receipts share this TTL: they live inside the state.

### Crash points and what survives each

| Process dies… | State | Lock | Recovery |
|---|---|---|---|
| before the lock | unchanged | free | reissue the command |
| after the lock, before the fenced save | unchanged | freed on unwind, else by lease | reissue the command; it applies exactly once |
| after the save, before the publish | **durable** | freed | spectators reload `/reveal/state`; the nudge is only an invalidation hint |
| during the lock release | **durable and published** | held until the lease expires (≤30 s) | the next command gets a retryable `503`, then succeeds |
| after the commit, before the response reaches the caller | **durable** | freed | retry with the same `Idempotency-Key` → the original result, no second application |

### Operator recovery from unusable state

A corrupt, foreign-versioned, or misfiled payload is **never** silently reset:
the store raises `RevealStorePayloadError`, the control API answers `500`
(`corrupt_state`, no `Retry-After`) and the spectator feed answers `500` too, so
nobody is shown a stale ceremony as if it were live. To recover:

1. Confirm the scope is the one failing — `GET /reveal/state?scope=…` fails the
   same way for the same scope, and other scopes are unaffected.
2. Decide explicitly that the ceremony is to be rebuilt. Everything revealed so
   far is lost; there is no partial repair.
3. Issue `POST /control/start-reveal` with `{"restart": true}`. This is the only
   command that does not read the stored payload first, which is exactly why it
   is the way out — every other command would raise on the load.
4. Re-run the ceremony from the bottom. The rebuilt session re-derives its frozen
   universe from current data, so runs judged in the meantime are included.

Never hand-edit or delete the Valkey key: a `DEL` races the ceremony's own writer
and cannot be sequenced against it, whereas `restart=true` goes through the same
lock and fence as any other command.

---

## `services/control_service.py`

Purpose:

- execute one authenticated reveal command end to end: the seam between the pure
  engine and the durable store, owning every rule about what a command *means*

Provides:

- `execute_command(session, store, contest, *, site_id, command, team_id=None,
  restart=False, idempotency_key=None, cache=None)` — apply one command durably,
  returning a `CommandResult` (the `RevealTransition` plus a `replayed` flag)
- `load_projection(session, store, contest, *, site_id, cache=None)` — read-only
  projection; takes no lock, saves nothing, publishes nothing
- `ControlError` → `MissingSessionError`, `ActiveSessionError`,
  `SupersededCommandError`, `ReusedKeyError`

Both entry points carry the engine's own `RevealTransition` (`state` plus
`teams`) rather than a second, field-identical payload type; a read is simply the
identity transition. `execute_command` wraps it so the route can tell a replay
from a fresh command — the two are indistinguishable by payload, which is the
point.

Behavior notes:

- **The whole mutation happens inside one lock.** The stored state is loaded
  *within* `store.mutate`, never before it, so a session cannot be read, decided
  upon, and written across a window another operator could write in.
- **The dataset is resolved after the state, through the feed cache.** A
  missing session and an active session hit by `start` without `restart` are
  refused before a single dataset query runs. Every other command — and every
  idempotent replay and `load_projection` read — reuses the `RevealDataset`
  cached under the state's `dataset_generation` (`services/feed_cache.py`), so
  a miss costs the four loads once per generation per process rather than on
  every command. Only a fresh `start` or an explicit `restart` loads PostgreSQL
  unconditionally, because that is the moment the frozen universe is rebuilt;
  the new state's generation then seeds the cache. A state persisted before the
  field existed carries `None` and bypasses the cache until it is rebuilt.
- **Exactly one save and one publish per successful command**, including
  no-op commands (`back` on an empty log, `step` on a `done` ceremony).
  Persisting the unchanged state keeps "applied, nothing changed"
  distinguishable from "never reached the server".
- **Scope is never caller-supplied after start.** The function receives the scope
  the credential resolved to, and only the session under that scope is touched.
- **Start semantics.** No stored state, or explicit `restart=true` → rebuild the
  frozen universe from current data via `initialize_reveal_session` (so runs
  judged since the last start are included). An `idle` stored state (fresh, or
  returned to idle by `reset`) is started over its existing universe. A
  `revealing` or `done` state raises `ActiveSessionError` — restarting is an
  explicit choice, never the result of a repeated click.
- **Restart does not read the old payload.** `start` + `restart=true` skips the
  load entirely, because the rebuilt session does not depend on it. That is what
  makes restart the documented recovery from corrupt, foreign-versioned, or
  misfiled state: a load would raise `RevealStorePayloadError` before the restart
  could replace the very payload that is broken.
- **Retries are recognized inside the lock.** When the caller supplies an
  `Idempotency-Key`, it is matched against the loaded state's receipt ring
  *before* the engine runs:
  - it names the **most recent** applied command → **replay**: the stored state
    is re-projected and returned (`replayed=True`) with **no save and no
    publish**, because that state already *is* the command's result;
  - it names the same command but an **older** entry → `SupersededCommandError`;
  - it names a **different** command → `ReusedKeyError`;
  - it is unknown → the command applies, and the key is appended to the ring.
  Without a key the command applies with no retry protection, which is why the
  operator panel always sends one.
- **Restart clears the ring.** `start` + `restart=true` discards the old state
  and therefore its receipts, so a key from before the rebuild identifies nothing
  and its command applies normally. A rebuilt ceremony is a new ceremony.
- **Engine errors pass through** (`RevealNotStartedError`,
  `NoPendingSubmissionError`, `UnknownTeamError`, `UnreachableTeamError`), as do
  store errors; the route owns status mapping.

---

## `services/media_cue_service.py`

Purpose:

- broadcast one transient team-media cue to every projector watching a ceremony
  scope

Provides:

- `cue_team_media(store, lease, contest, *, controller_id, site_id, action)` —
  verifies ownership, resolves the team, publishes; returns nothing
- `MediaCueError` and its three subclasses: `NoMediaSessionError`,
  `NoFocusedTeamError`, `MediaCueUnavailableError`

Behavior notes:

- **Why it is not in `control_service`.** Every function there mutates the
  ceremony: scope lock, load inside it, fenced save, receipt, nudge. A cue does
  none of that. It writes nothing, locks nothing, and its published frame carries
  no state — which is exactly what makes it safe to repeat, safe to lose, and
  safe to run *concurrently* with a real command, and why it needs no
  `state_version` bump, no receipt ring, and no `Idempotency-Key`.
- **The operator never names a team.** `show` reads `focused_team_id` from the
  stored state, so a cue cannot address a team outside the ceremony or in another
  venue's scope. `hide` reads no state at all: a projector showing an overlay is
  reason enough to take it down, and a `hide` that refused after a `reset` would
  strand a photograph on screen with no way to clear it.
- **Ownership is enforced atomically, but nothing is held.** Both directions
  require the controller lease — blanking a projector is as much a control action
  as seizing one — and it is checked **twice, for two different reasons**.
  `ControllerLeaseService.verify_ownership` runs first so the *operator* gets the
  stated lease-lost refusal both clients key on, instead of a confusing "no
  session" from a state read the request should never have reached. Then
  `RevealSessionStore.publish_media_cue` fuses the same check with the `PUBLISH`
  in one Lua step, because that is what the *projectors* need: every `await`
  between an advisory check and the publication is a window in which the lease
  expires or a takeover lands, and without the fence the first check would be
  advisory only. Neither takes the mutation lock, so a `step` in flight and a cue
  can safely happen at the same instant. Neither extends the lease either:
  renewal is the heartbeat's job, and a cue that quietly kept a lease alive would
  let an operator hold a ceremony they are no longer driving.
- **One stated residual.** `show` reads `focused_team_id` before it publishes, so
  a `step` racing that read could leave the previously focused team's photo up
  until the next movement clears it. It needs two commands genuinely in flight on
  one lease, which neither shipped client can produce (both are single-flight)
  and which the fence rules out across two controllers. Bounded by the next
  movement rather than prevented: closing it would mean taking the scope lock
  this command exists without.
- **Zero subscribers is success; an unreachable Valkey is not.** The animator
  publishes and cannot learn whether a projector rendered the overlay, so the
  operator is told *sent*, never *displayed*. But the publish **is** the whole
  action — unlike a state-changed nudge, which follows a state that is already
  durable — so a publish that never reached Valkey is reported
  (`MediaCueUnavailableError` → `503`) rather than swallowed.

---

## `services/control_audit.py`

Purpose:

- the single audit record emitted for every reveal-control attempt, accepted or
  refused

Provides:

- `note_control_outcome(request, *, outcome=None, contest_id=None, scope=None,
  idempotent=None)` — write-once context each layer contributes to; never logs
- `emit_control_audit(request, *, status)` — the single emit point, called only
  by the audit boundary
- `ControlAuditContext` / `audit_context(request)`
- `scope_label(site_id)` and `command_from_path(path)`
- `AUDIT_EVENT` (`animator_control_attempt`) and `SCOPE_UNKNOWN`

Behavior notes:

- **Note-then-emit, because most refusals never reach a route.** The contest
  gate and kill switch answer inside a dependency, an invalid credential answers
  in the authorization dependency, and a malformed body is rejected by FastAPI's
  validation before any of the route's code runs. Logging at each decision point
  would miss those *and* risk two records for one request, so decisions only
  note, and `ControlAuditRoute` emits exactly one line per request. An outcome no
  layer named is derived from the final status (`404` → `not_found`, `422` →
  `invalid_request`, `2xx` → `success`); the ones a layer *does* name are
  `control_disabled`, `invalid_credential`, `throttled`, and the route-level
  refusals.
- **A service, not route-local**, so `animator.dependencies` can note outcomes
  without importing a route and creating a cycle.
- **A `key=value` message, not `extra=` fields.** The production console
  formatter renders `%(message)s` and standard record metadata only
  (`shared/app_logging.py`), so fields attached to a `LogRecord` would be dropped
  before anything was written. The audit line therefore *is* the message.
- **Never records a token, a digest, or a credential label.** An attempt is
  identified by the scope it resolved to, or `unknown` when it resolved to none.
- **Never records the idempotency key either**, only `idempotent=yes|no`. The
  key is caller-chosen and grants nothing, so writing its value down would add
  a liability with no diagnostic upside; the flag is enough to see whether retry
  protection was in play. A replayed command is audited `outcome=replayed`, not
  `success`, so one operator action is never counted as two commands.
- **Path names normalize to the persisted command vocabulary.** In particular,
  `/jump-pending` is recorded as `command=jump_pending`, matching the receipt
  and event literal rather than the hyphenated URL segment.
- **Trusted client IP.** Uses the shared
  `network_utils.validation.get_ip_from_request`, i.e. the proxy-corrected
  `request.client.host`, never a raw `X-Forwarded-For`. `animator/main.py` runs
  uvicorn with `proxy_headers=True` and `forwarded_allow_ips=NOCA_FORWARDED_ALLOW_IPS`
  (the same contract web and arena use), without which the record would name the
  reverse proxy instead of the operator.

---

## `routes/control_audit_route.py`

Purpose:

- the `route_class` boundary that emits exactly one audit record per control
  request

Provides:

- `ControlAuditRoute` — an `APIRoute` whose handler wraps dependency solving,
  request validation, and the endpoint

Behavior notes:

- **Why a route class.** It wraps the whole per-request pipeline, so it sees the
  outcomes that never reach a route function: dependency `HTTPException`s (gate
  `404`s, credential `403`s), `RequestValidationError` (`422`), unexpected
  failures (`500`), and normal responses.
- **Why not middleware.** Middleware runs before routing, so it would wrap
  unrelated traffic and would have to re-derive which requests are control
  operations; `route_class` is applied by the router to exactly its own routes.
- **Not audited:** a request that matches no control operation — a misspelled
  sub-path, or a wrong method (Starlette answers `405` from the router, ahead of
  any route handler).

---

## `services/public_scope_service.py`

Purpose:

- resolve the **credential-free** spectator `?scope=` query value against one
  contest, for the reveal state/SSE routes and the team-photo route

Provides:

- `PublicScope` — frozen `site_id` / `site_name` / `canonical` (the Valkey scope
  component: the site id, or `"global"`)
- `resolve_public_scope(session, contest, scope)` — the resolved scope, or `None`
  when the value names no site of this contest

Behavior notes:

- **The value selects, it does not authorize.** It is not a token and grants
  nothing; it only chooses which already-public ceremony to read. Operator tokens
  are never accepted on a spectator route.
- **Resolved against the contest, never trusted.** A site id is accepted only
  when that site belongs to the resolved contest, so the query value cannot reach
  across contests.
- **`global` stays reserved**, exactly as `RevealSessionStore.scope_for`
  reserves it on the write side, so the spectator and operator views of one
  ceremony always address the same key and channel.
- **Rejection is the caller's uniform bare `404`**, so an unknown site, a foreign
  site, and garbage are indistinguishable from an unknown slug.

---

## `services/team_media_service.py`

Purpose:

- load one team's media **metadata** within the ceremony's scope, and — only on
  a cache miss — the one blob needed to choose an image that is always valid

Provides:

- `scoped_team_query(*columns, contest_id, team_id, site_id)` — the one
  `users` ⟕ `users_media` select every media read is built from (contest,
  `RoleEnum.TEAM`, optional site), so the scope predicates cannot drift between
  the metadata and payload reads or between the two routes
- `load_team_media_metadata(session, *, contest_id, team_id, site_id)` — the
  scoped query returning `TeamMediaMetadata | None`: `com_foto`, `dta_foto`,
  `dta_audio`, and SQL presence booleans `has_photo` / `has_avatar` /
  `has_audio` (`coalesce(length(col) > 0, false)`). **No blob column and no
  `audio_mime`** — this row is the whole cost of a matching conditional request
- `load_photo_payload(session, team_id)` / `load_avatar_payload(session, team_id)`
  — single-column reads by `user_id`, for a team the metadata query already
  proved in scope
- `resolve_team_image(session, media)` — the photo → avatar → placeholder
  decision, as a `TeamImage` (`kind`, `data`, `mime`). Loads the photo only if
  `has_photo`, and the avatar only if the photo failed validation and
  `has_avatar`; a team with nothing enabled queries no blob at all
- `placeholder_image()` — the checked-in `animator/assets/team_placeholder.svg`,
  read once per process
- `TeamImage`, `TeamMediaKind`, `PLACEHOLDER_MIME`

Behavior notes:

- **Scope is enforced in the query, not after it.** Contest, `RoleEnum.TEAM`, and
  (when site-scoped) `users.site_id` narrow the lookup together, so a foreign
  team is simply absent — a site spectator cannot read another site's team, and a
  judge or admin account is never addressable as a team.
- **`com_foto` is authoritative.** When it is false both stored blobs are ignored
  even if present, matching the Web media properties, so the animator cannot
  resurrect a photo a user removed.
- **Stored bytes are re-verified structurally, and the MIME comes from the
  bytes.** Uploads were validated when written, but a stored blob can be
  truncated or rewritten and `foto_mime` is only a *claim*. Each candidate is
  base64-decoded defensively (empty decoded bytes count as invalid) and then run
  through the shared `image_validation`, which **decodes** the image and reports
  its real format; the served `Content-Type` is that format. A signature sniff
  would not be enough: an eight-byte PNG header passes `detect_image_type` and
  still renders as broken-image chrome, which is precisely what this module
  exists to prevent. Validation is what lets the fallback chain be trusted — a
  truncated photo falls through to the avatar rather than being served as a
  corrupt `image/png`. Cost is bounded: payloads are upload-limited, the decode is
  capped at 8000×8000 pixels and dimensions (so a decompression bomb is refused,
  not expanded), and a matching conditional request never reaches the decode:
  the route answers `304` from the metadata row alone. SVG is not accepted from
  a stored blob (it is never a stored user photo and would be an active-content
  vector).
- **Never returns nothing.** The fallback chain ends at the placeholder, so the
  ceremony modal cannot render broken-image chrome.
- Every rejected blob is logged at `warning`: the request still succeeds, but a
  corrupt row is a data problem worth surfacing.

---
## `services/team_audio_service.py`

Purpose:

- resolve a team's **optional** audio clip defensively, or report that there is
  none

Provides:

- `load_audio_payload(session, team_id)` — a single-column read of
  `audio_base64` by `user_id`, for a team the metadata query already scoped
- `resolve_team_audio(session, media)` — `TeamAudio | None` (`data`, canonical
  `mime`). Returns `None` **without a query** when the metadata says no clip is
  stored (`has_audio=false`); otherwise loads only the clip column and validates
  its signature
- `TeamAudio`

Behavior notes:

- **Optional, and that changes the contract.** The photo path must always produce
  an image (it ends at a placeholder); audio either resolves to a playable clip or
  to `None`, which the route turns into `404`. Every failure path returns `None` —
  **no stored-data defect may surface as a `500`**.
- **The stored `audio_mime` claim is never served.** The type comes from
  `shared.services.audio_signature.detect_audio_mime()`, the single owner of the
  accepted-format policy, which the Web upload path also uses. One owner means a
  clip that could be uploaded can always be played back, and the two runtimes can
  never drift into accepting different sets.
- **All failure modes are caught:** `binascii.Error` / `ValueError` from base64
  decoding, and `AudioSignatureError` from the shared detector (its
  `UnrecognizedAudioError` / `UnsupportedAudioError` subclasses cover
  unidentifiable and identified-but-unwanted content). Letting any of them escape
  would convert an unusable stored blob into a `500`.
- **Log level separates absent from broken.** No clip is the ordinary case and
  logs at `debug`; a payload that is present but undecodable, empty, or
  unrecognized logs at `warning`.
- **It does not decode the media.** Decoding audio would require a native library
  for no benefit; a signature-valid clip a browser cannot play surfaces through
  the `<audio>` element's `error` event, which the ceremony modal already treats
  as "no usable clip".
- `puremagic==2.2.0` is declared directly in `animator/pyproject.toml` because this
  module imports it directly — a per-module install must not rely on another
  member's dependency graph.

---

## `services/sse_capacity.py`

Purpose:

- a process-wide, Valkey-independent ceiling on open SSE clients, shared by
  `/events` and `/reveal/events`

Provides:

- `SseCapacity(max_clients)` — `slot(detail=…)` async context manager that
  raises `503` + `Retry-After: 5` when `active >= max_clients` and releases
  idempotently in `finally`; `active` and `max_clients` properties for tests and
  diagnostics

Behavior notes:

- lives here rather than in `AnimatorEventStream.register()` because the
  reveal stream has no registry of its own -- one gauge in the dependency guards
  both streams with one implementation
- created in the lifespan (`app.state.sse_capacity`) from
  `NOCA_ANIMATOR_MAX_SSE_CLIENTS` (default 2000)

---

## `services/projector_presence.py`

`ProjectorPresence` is the best-effort gauge behind `projector_count` in every
controller-lease response: how many `/reveal/events` streams are open on one
ceremony scope right now. The operator on stage cannot see the hall's
projectors, and the only evidence the server has that a projector exists is
its open stream, so the stream route wraps its whole lifetime in
`presence.attend(contest_id, scope)`.

- **Storage.** One sorted set per scope, `animator:reveal:projectors:{contest_id}:{scope}`
  (`reveal_projectors_key`, built from validated components like every other
  reveal key), keyed by a random per-connection id and scored by the entry's
  expiry instant. The score is computed from Valkey's own `TIME` inside the Lua
  script, so replicas with drifting clocks still agree, and `count()` sweeps
  expired scores before `ZCARD`. The set also carries an `EXPIRE` equal to the
  TTL, so a scope nobody renews disappears entirely.
- **Lifecycle.** `attend()` registers before the body runs, renews at a third
  of `NOCA_ANIMATOR_PROJECTOR_PRESENCE_TTL_SECONDS` from a task, and on exit
  cancels the renewer and **detaches** the `ZREM` into a task of its own. The
  block ends because the client disconnected, and that teardown runs inside
  the response's cancel scope where every `await` is cancelled again -- an
  awaited removal would never reach Valkey, and every clean disconnect would
  linger for one TTL.
- **Gauge, not gate.** Nothing here can refuse or end a stream: a failed
  registration or renewal is a warning, and `count()` answers `None` when
  Valkey cannot be read -- deliberately not `0`, because an empty hall and an
  outage are different facts and the operator must be able to tell them apart.
- **Where it is read.** Only the controller-lease routes, on `claim`,
  `heartbeat`, and `takeover`, after the ownership decision; `release` reports
  `None`. The count therefore reaches both shipped controllers at their
  existing heartbeat cadence and costs one extra `EVAL` per heartbeat.

## `services/reveal_stream_service.py`

Purpose:

- turn one Valkey ceremony subscription into the spectator SSE sequence: a
  coverage signal, then nudges

Provides:

- `iter_ready_then_events(runtime, contest_id, scope)` — yields `None` once the
  subscription is live (the route renders it as `reveal_ready`), then one frame
  per publication: a `RevealStateChangedEvent` nudge or a `RevealMediaCueEvent`
- `EVENT_REVEAL_READY` / `EVENT_REVEAL_STATE_CHANGED` / `EVENT_REVEAL_MEDIA_CUE`
  — the SSE `event:` names
- `RevealEventSource` — the Valkey surface it needs, as a Protocol

Behavior notes:

- **Why it exists: `open` is not coverage.** An SSE response's headers are
  written when the route returns its generator, so `EventSource.onopen` can fire
  *before* the Valkey `SUBSCRIBE` behind it completes. A client reconciling on
  `open` therefore has a real gap: a mutation published inside it reaches neither
  the client's already-returned fetch nor its not-yet-existing subscription, and
  pub/sub has no replay — with no further operator step, the projector stays
  stale for the rest of the ceremony. Emitting `reveal_ready` *after* the
  subscribe makes the client's reconciliation strictly follow coverage.
- **The subscription signal comes from `shared`.** `ValkeyRuntime.iter_revelation_events`
  takes an `on_subscribed` callback invoked immediately after `SUBSCRIBE`. The
  moment cannot be derived from the first yielded event, because a quiet channel
  may never produce one.
- **A pump task is required, not incidental.** An async generator does not run
  its body — and so never subscribes — until something awaits its first item, so
  the subscription is advanced concurrently into a bounded queue. That is also
  what lets `reveal_ready` be emitted while the channel is silent.
- **Bounded, oldest-dropping queue** (64). Nothing on this channel carries
  state, so under overflow the newest frames are kept and the oldest discarded —
  never blocking the subscriber, never growing. A dropped nudge is recovered by
  the next one or by the client's own reconciliation; a dropped cue costs one
  press of the operator's button.
- **Two frame shapes, one subscription.** The media cue rides the same channel
  because it addresses exactly the same audience — every projector on this
  contest and scope. The module stays free of SSE vocabulary: it yields the model
  and the route decides which `event:` name each becomes. That separation is what
  keeps the client-side distinction honest, since a nudge means "refetch" and a
  cue must trigger no fetch at all.
- **Bounded readiness.** A subscription that does not become ready within 10 s
  closes the stream instead of leaving a client that believes it is covered; the
  browser then reconnects. A generator that ends or fails before `SUBSCRIBE`
  completes also closes without emitting `reveal_ready`; termination and
  successful subscription are separate signals.

---

## `services/contest_queries.py`

Purpose:

- SQLAlchemy Core loaders and deterministic judgment resolution for the feed

Provides:

- `load_enabled_contest(session, slug)` — resolve a contest gated on
  `login_slug` + `animator_enabled` only (no `active` filter); returns `None`
  for both missing and disabled contests
- `load_teams` / `load_problems` / `load_sites` — deterministic Core loaders
- `load_submission_rows(session, contest_id)` — one joined query returning team
  submissions plus their effective judgments

Behavior notes:

- **Team-scoped submissions.** The submission query joins `users` and requires
  `users.contest_id == contest_id` and `users.role == RoleEnum.TEAM`. Without
  this, an earlier accepted submission by a judge, admin, or cross-contest user
  could consume the first-balloon marker in `compute_icpc` before the legitimate
  first team solve is seen.
- **Effective judgment selection.** Among a submission's non-`SUPERSEDED`
  judgments, the effective one is chosen deterministically: prefer `DONE`, then
  the latest `created_at`, then the highest judgment id — so repeated snapshots
  never select a different judgment.

---

## `services/contest_index_service.py`

Purpose:

- discover presentation-safe contests for the public Animator index

Provides:

- `AnimatorContestSummary` — immutable contest name, slug, and UTC start/end
  values with ISO and display-label helpers
- `AnimatorContestGroups` — immutable live, upcoming, and past collections plus
  their total count
- `list_animator_contests(session, now=None)` — load contests where
  `animator_enabled=true` and `active=true`, then group and sort them for display

Ordering and lifecycle behavior:

- live contests include both the exact start and end instants and sort by
  earliest end
- upcoming contests sort by earliest start
- past contests sort by most recent end and remain visible until archived

---

## `services/contest_meta_service.py`

Purpose:

- the public contest metadata builder behind `/meta` and the launcher page,
  split from `contest_feed_service.py` so that module keeps to the scoreboard
  projection

Provides:

- `build_meta_response(session, contest, now=None, cache=None)` —
  `ContestMetaResponse`
- `build_meta_response_cached(...)` — the same, returning
  `(response, seconds_left)` for the route's `Cache-Control: max-age`;
  `seconds_left` is `0` without a cache

Behavior notes:

- **Pre-start gate.** Before `contest.start_time` the response carries an empty
  `problems` list and `has_started=false`; sites stay visible in both states.
- **Cached per process** under `(contest_id, has_started, is_frozen)` for
  `NOCA_ANIMATOR_META_CACHE_SECONDS` (`services/feed_cache.py`); the launcher
  and `/meta` share the entry. Two queries per miss (problems when started,
  sites with team counts), one (the contest gate) per hit.

---

## `services/contest_feed_service.py`

Purpose:

- read-only orchestration that composes the Core loaders and projects the shared
  ICPC scoreboard for public presentation, delegating scoring to
  `shared.services.scoreboard_projection.compute_icpc` (never re-implemented)

Provides:

- `load_enabled_contest` — re-exported from `contest_queries` as the single
  public contest-resolution entry point
- `build_snapshot_response(session, contest, now=None, site_id=None,
  cutoffs=None, cache=None)` — `ScoreboardSnapshotResponse`
- `build_snapshot_response_cached(...)` — the same, returning
  `(response, seconds_left)` so the route can emit a matching
  `Cache-Control: max-age`; `seconds_left` is `0` without a cache
- `build_snapshot(session, contest, now=None, site_id=None)` — the underlying
  shared `ScoreboardSnapshot`
- `snapshot_to_response(snapshot, pending_submissions=None, *, teams,
  wa_penalty, cutoffs=None, has_started, recent_events=None,
  absent=frozenset())` — the Animator response mapper,
  including team site names and accumulated per-cell attempt penalties
- `build_pending_submissions(standings, submission_records, judgments, teams,
  problem_records, freeze_at_seconds)` — the authoritative, freeze-safe pending list

Behavior notes:

- **Pre-start gate.** Before `contest.start_time` neither feed publishes a
  problem set: `/meta` returns an empty `problems` list and `/snapshot` returns
  empty `problems`, `balloon_colors`, `standings`, `pending_submissions`, and
  `recent_events`.
  Both carry `has_started=false` so a client can render a "not started yet"
  banner rather than mistaking the gate for a contest with no teams. Web already
  withholds its scoreboard, clarifications, and runs before the start precisely
  so the *number of problems* and their balloon colors stay secret; this
  anonymous feed must not be the way around that, which is why the gate lives in
  the private `_project` helper (nothing is queried, so nothing can leak through
  a future builder that forgets it) and in `build_meta_response`. Sites stay
  visible in both states: the launcher is built from them, and a venue's name
  and team count are not part of the secret.
- **Freeze visibility.** The snapshot uses
  `viewer_sees_frozen=contest.is_frozen_at(now)` and
  `freeze_at_seconds = stop_updating_scoreboard * 60`. Reaching the cutoff hides
  later submissions while the contest runs and after an unreleased end. Once
  the contest has ended and `release_scoreboard_after_end` is true,
  `is_frozen_at(now)` becomes false, every final result is scored, and the
  response reports `is_frozen=false`, matching Web's released scoreboard.
- **Single projection.** `build_snapshot_response` loads the contest rows once
  via a private `_project` helper and derives the snapshot, the
  `pending_submissions` list, and the `recent_events` backlog from the same
  visible data — so neither derived list costs a query of its own.
- **Activity seed.** `recent_events` comes from
  `animator.services.recent_events_service.build_recent_events`, called with the
  same rows and the snapshot's own `is_frozen`, so the ticker's history can never
  narrate a run the board is withholding.
- **Presentation fields.** The team query joins the site's display name without
  adding a query. `TeamStandingResponse` therefore carries `team_fullname` and
  `site_name`. `ProblemCellResponse.penalty` is `attempts × wa_penalty` for both
  solved and unsolved cells; ranking still adds that penalty only after a solve.
- **Absent teams.** While the contest is running (`ContestRecord.is_running_at`)
  the projection also reads `shared.services.team_absence_status`, and every
  standing carries `absent`: no successful sign-in since
  `contest.start_time`. Outside a running contest the lookup is skipped and the
  flag is `False` for everyone — before the start nobody is late, and the
  ended-contest snapshot cache lives far too long for a presence marker to stay
  honest inside it. The flag is on the animator's own `TeamStandingResponse`,
  never on the shared `TeamStanding`, for the reason given in
  `docs/SHARED_SERVICES.md`: the frozen and final scoreboard snapshots are
  written once and never invalidated. A sign-in publishes no event, so the
  board's own absence watch (`animator.js`) re-reads `/snapshot` on a timer
  while any marker is showing.
- **Pending list authority.** `build_pending_submissions` emits one entry per
  scoreboard cell whose `is_pending` is True (so it agrees with the `?` cells).
  It reuses shared `bucket_visible_pending_submissions` so the list and
  `compute_icpc` cannot drift on unresolved-judgment or freeze filtering. The newest
  candidate supplies `submission_id`/`created_at`, and the list is sorted
  newest-first. Once frozen, pre-freeze unresolved submissions remain visible while
  post-freeze submissions stay hidden.
- **Bounded query count** (independent of team/problem/submission/site counts):
  - `/meta`: contest (dependency) + problems + site team-counts + sites = 4
  - `/snapshot`: contest (dependency) + teams + problems + submissions/judgments = 4
- **Cached per process.** With an `AnimatorFeedCache` (the routes always pass
  one), only the contest gate's query runs per request; the loads and
  `compute_icpc` run once per TTL per `(contest, scope, phase, cutoffs)` and are
  shared by concurrent misses. Without a cache (unit tests, ad-hoc callers) the
  builders behave exactly as before. The reference instant is fixed *before* the
  cache lookup, so a cached snapshot's `generated_at`/`version` is the instant
  it was built and stays constant until it is rebuilt.

---

## `services/recent_events_service.py`

Purpose:

- Build the freeze-safe recent-activity backlog that seeds a freshly loaded
  scoreboard's ticker, from rows the snapshot has already loaded

Provides:

- `RECENT_EVENT_LIMIT` — how many past events a page is seeded with (10)
- `build_recent_events(submission_records, judgments, teams, problem_records, *,
  freeze_at_seconds, viewer_sees_frozen, accept_pe, limit=RECENT_EVENT_LIMIT)` —
  up to `limit` `RecentEventResponse` entries, oldest first

Behavior notes:

- **No extra query.** It consumes the projection's submissions, judgments, teams
  and problems, so it runs inside the snapshot build and inside the per-process
  feed cache.
- **One entry per submission, never two.** An unresolved submission reports that
  it was submitted; a resolved one reports its result only. Emitting both halves
  would spend a ten-item backlog on five events.
- **Same freeze boundary as the board.** A submission past `freeze_at_seconds` is
  dropped while `viewer_sees_frozen`, exactly as `compute_icpc` drops it.
- **Same event keys as the live stream.** `submission:<submission_id>` and
  `verdict:<judgment_id>` — which is why `JudgmentRecord` carries its `id`. A page
  that seeds and then receives the same event over SSE renders it once.
- **Solve wording mirrors `compute_icpc`.** The cell's *solving* submission is the
  first accepted one for that team and problem (the same "stop at the first
  accepted" rule), and it is the *first solver* when it is also the earliest
  accepted submission for that problem across the scope's teams — the definition
  behind `is_first_balloon`. A later accepted submission on an already-solved cell
  is neither and reports its bare verdict.
- **Wording is not built here.** The payload carries a `kind` and the sentence
  parts; the client composes the text, so the seeded backlog and the live stream
  cannot drift into two vocabularies.
- **Unnameable rows are dropped.** A submission whose team or problem is outside
  the requested scope is skipped rather than rendered with a raw identifier.

## `services/feed_cache.py`

Purpose:

- the process-local caches behind the three read paths that reload and re-score
  a whole contest per request: the anonymous `/snapshot` and `/meta` feeds, and
  the reveal ceremony's dataset behind `/reveal/state` and every control command.
  Owns every key and TTL decision, so the services and routes only say *what*
  they are building.

Provides:

- `AnimatorFeedCache` — three `shared.services.single_flight_cache.SingleFlightCache`
  instances behind one object stored on `app.state.feed_cache`:
  - `snapshot(contest, *, site_id, cutoffs, now, build)` → `(response, seconds_left)`;
    key `(contest_id, scope, has_started, is_frozen, cutoffs)`; TTL
    `NOCA_ANIMATOR_SNAPSHOT_CACHE_SECONDS` while the contest runs,
    `NOCA_ANIMATOR_SNAPSHOT_CACHE_ENDED_SECONDS` once it has ended
  - `meta(contest, *, now, build)` → `(response, seconds_left)`; key
    `(contest_id, has_started, is_frozen)`; TTL `NOCA_ANIMATOR_META_CACHE_SECONDS`
  - `reveal_dataset(contest, *, site_id, generation, build)` → `RevealDataset`;
    key `(contest_id, scope, dataset_generation)`; TTL
    `NOCA_ANIMATOR_REVEAL_DATASET_CACHE_SECONDS`
  - `invalidate_contest(contest_id)` — drops every snapshot and reveal entry of
    one contest in every scope (meta is left alone: nothing in a judging event
    changes it); `clear()` drops everything
  - `snapshot_ttl(contest, now)` — the running/ended TTL rule, exposed for tests

Behavior notes:

- **Phase is part of the key, not a reason to invalidate.** `has_started` and
  `is_frozen` are derived from the contest record and the clock without a
  query, so the pre-start empty feed can never be served after the start and
  the running → frozen switch is visible on the very next request rather than
  one TTL later.
- **Events invalidate, TTL bounds.** `main.py` wires `invalidate_contest` to the
  event stream's `on_contest_changed`, so a verdict or submission drops the
  contest's snapshots and reveal datasets at once, on every replica, whether or
  not a spectator is connected. The TTL remains as protection against a missed
  event and against edits that publish none (site and problem changes, which is
  why meta relies on TTL alone).
- **No permanent final entry.** Web's contest, team and problem edit paths
  cannot reach animator entries, so an ended contest is bounded by
  `SNAPSHOT_CACHE_ENDED_SECONDS` rather than cached forever.
- **The reveal dataset is keyed on identity, not on scope alone.** A
  `(contest, scope)` key would be unsafe with several replicas: a `restart` on
  replica A rebuilds the frozen universe from newer judgments while replica B
  would keep projecting the new state over its old rows. `dataset_generation`
  is minted by `initialize_reveal_session` — that is, by a fresh `start-reveal`
  or an explicit `restart` — carried unchanged through every transition, and is
  what every replica keys on; a restart elsewhere is therefore a miss here, never
  a stale projection. Keying on `frozen_submission_ids` would not do: the same
  ids can carry newer judgments.
- **Process-local by design.** A multi-replica deployment builds once per
  replica, which is bounded and needs no shared state; a `RevealDataset` holds
  dataclasses rather than JSON, and the hot path gains no extra Valkey hop.

---

## `services/event_stream_service.py`

Purpose:

- one in-process live-event fan-out per animator process: two Valkey subscribers
  (over `judge:results` and `judge:submissions`) plus a bounded-cadence timer,
  fanned out to per-client bounded queues that back the `/events` SSE route

Provides:

- `AnimatorEventStream` — process-wide service with `start()` / `stop()`
  lifecycle, `register(contest)` / `unregister(channel)` for SSE clients,
  `client_count`, and `next_event_id()` (monotonic process-scoped ids). Its
  optional `on_contest_changed(contest_id)` hook is called for every verdict
  and submission event **before** any fan-out and whether or not a client is
  connected; `main.py` wires it to `AnimatorFeedCache.invalidate_contest`, which
  is what keeps a cached snapshot from outliving the verdict that changed it. A
  raising hook is logged and never stalls fan-out.
- `EVENT_VERDICT` / `EVENT_SUBMISSION` / `EVENT_SCOREBOARD_REFRESH` /
  `EVENT_TIMER_TICK` — SSE `event:` names
- the typed SSE `data:` payloads (`VerdictPayload`, `RedactedVerdictPayload`,
  `SubmissionPayload`, `ScoreboardRefreshPayload`, `TimerTickPayload`) are defined in
  `models/responses.py` (the centralized public feed surface) and re-exported here
  for convenience

Behavior notes:

- **One subscriber, visibility-aware fan-out.** A single background task consumes
  `runtime.iter_verdict_events()` and reconnects with bounded exponential backoff
  (`_BACKOFF_BASE_SECONDS` → `_BACKOFF_MAX_SECONDS`), resetting on healthy
  delivery. Each verdict is filtered by `contest_id` and, per matching channel,
  redacted when `ContestRecord.is_frozen_at(now)` — so the public stream never
  leaks post-freeze solves, but an ended, released contest emits the same
  unredacted public updates as its final snapshot.
- **Second subscriber for submissions.** A parallel background task consumes
  `runtime.iter_submission_events()` (over `judge:submissions`) with the same
  bounded backoff and dispatches through `_dispatch_submission`: for each channel
  matching `event.contest_id`, a `submission` (`SubmissionPayload`) is enqueued —
  **unless** the channel's contest `is_frozen_at(now)`, in which case it is
  suppressed entirely (no redacted variant), so post-freeze activity never
  leaks. An ended, released contest is not frozen and receives the event.
  No paired `scoreboard_refresh` is enqueued; the client's `submission` handler
  triggers the authoritative refetch, and queue overflow already coalesces a
  dropped `submission` into the single pending-refresh flag. `start()` launches all
  three tasks (verdict subscriber, submission subscriber, timer); `stop()` cancels
  and awaits all three.
- **Legacy events** (`contest_id is None`) are dropped and logged, never
  broadcast — current publishers always populate `contest_id`.
- **Bounded, coalescing overflow.** Each `_ClientChannel` has a bounded queue
  (`_CLIENT_QUEUE_MAXSIZE = 64`) plus a single pending-refresh flag. A full queue
  never evicts queued items: `verdict`/`scoreboard_refresh` coalesce to one
  pending refresh and `timer_tick` is dropped, so memory is bounded and exactly
  one authoritative `scoreboard_refresh` always survives. A `verdict` is enqueued
  immediately before its `scoreboard_refresh`.
- **Heartbeat is native.** The 15 s idle-only comment heartbeat and structured
  disconnect teardown are owned by FastAPI's native SSE layer, not this service.
- **Shutdown** sets a stop event that interrupts both the backoff and timer waits
  promptly; task cancellation is the backstop. `main.py` calls
  `event_stream.stop()` before `valkey_runtime.stop()` and the engine dispose.

---

## `dependencies.py`

Purpose:

- reusable FastAPI dependencies for the animator runtime

Provides:

- `DbSession`, `Valkey` — session and Valkey runtime aliases
- `get_feed_cache(request)` / `FeedCache` — the process-wide `AnimatorFeedCache`
  from `app.state.feed_cache`
- `enforce_public_rate_limit(request)` — the `animator:public` per-IP window
  (`NOCA_ANIMATOR_PUBLIC_RATE_LIMIT_*`, detail `PUBLIC_RATE_LIMIT_DETAIL`) over
  the shared `shared.services.request_rate_limit`; installed as a route-level
  dependency on `/meta`, `/snapshot`, `/reveal/state`, and the two team-media
  routes (`/teams/{team_id}/photo`, `/audio`), so it runs *before*
  the contest gate. That is deliberate: the answer depends on the client IP
  alone, so a `429` cannot probe the non-enumerating `404`, and a flood must be
  stopped ahead of the gate's own query. The policy is rebuilt from `settings`
  on every call and the fallback limiter (`PUBLIC_RATE_LIMITER`) is
  module-level, so tests can monkeypatch the knobs and reset the state.
- `enforce_sse_connection_caps(request)` — yield dependency installed
  route-level on `/events` and `/reveal/events`, so it too runs *before* the
  contest gate. It first takes a slot on the process-wide `SseCapacity` gauge
  (`get_sse_capacity`, `NOCA_ANIMATOR_MAX_SSE_CLIENTS`, `503` + `Retry-After`
  when this replica is full, no Valkey involved), then a per-IP slot in the
  shared `shared.services.sse_connection_limit` lease (bucket `animator:sse`,
  `NOCA_ANIMATOR_SSE_*`, `429` + `Retry-After`). Both are released when the
  streamed response ends, i.e. on disconnect. The lease fails open on a Valkey
  outage, which is exactly why the process ceiling exists.
- `get_sse_capacity(request)` — the `SseCapacity` from `app.state.sse_capacity`,
  created lazily from `settings.MAX_SSE_CLIENTS` when an app was assembled
  without the lifespan (tests, tooling)
- `get_enabled_contest(slug, db)` / `EnabledContest` — non-enumerating enabled
  contest gate raising the non-specific default `404`; shared by all current and
  later contest-scoped routes so the `404` behavior stays uniform
- `get_enabled_contest_detached(slug, request)` / `DetachedEnabledContest` — the
  same `404`-uniform gate for **streaming** routes: it opens its own session,
  resolves the detached `ContestRecord`, and closes the session before the
  response begins, so a long-lived SSE connection pins no pooled PostgreSQL
  connection (unlike the `yield`-style `DbSession`, which stays open for the whole
  response)
- `get_public_scope(contest, db, scope)` / `PublicScopeDep` and
  `get_public_scope_detached(contest, request, scope)` / `DetachedPublicScope` —
  the credential-free spectator scope, resolved *after* the contest gate; the
  detached variant closes its session before an SSE stream begins
- `get_reveal_store(valkey)` / `RevealStore` — a `RevealSessionStore` built on
  the existing `Valkey` dependency and `NOCA_ANIMATOR_REVEAL_TTL_MARGIN_SECONDS`
- `get_control_contest(request, contest)` / `ControlContest` — the enabled-contest gate
  **followed by** the reveal-control kill switch; both refuse with the same bare
  `404`, so a switched-off deployment never advertises that the routes exist
- `resolve_operator_scope(request, contest, db, credentials)` / `OperatorScope` —
  consults the per-IP lockout, then resolves the `Authorization: Bearer`
  operator token to its `ResolvedScope`; a failure is counted, a success resets
- `CONTROL_LOCKOUT_LIMITER` — the in-memory fallback of the lockout
  (`NOCA_ANIMATOR_CONTROL_LOCKOUT_*`; `tests/animator/conftest.py` resets it)
- `FORBIDDEN_DETAIL` — the one detail string every authorization refusal uses,
  the lockout included

Behavior notes on the spectator scope dependencies:

- **`scope` is an unconstrained `str` on purpose.** A `Literal`- or
  pattern-validated query parameter would make FastAPI answer `422` *before* the
  contest gate ran, so a malformed scope would prove the slug resolved and turn
  the parameter into an enumeration oracle. It is parsed permissively and judged
  only after `EnabledContest`, so every bad value is the same bare `404`.

Behavior notes on the control dependencies:

- **`HTTPBearer(auto_error=False)` is deliberate.** With `auto_error=True`, a
  missing or malformed header would answer *before* the contest gate, turning an
  animator-disabled contest into an authentication-shaped `403` and revealing
  that it exists. With it off, the header is merely parsed and the resolver
  decides after the gate.
- **Gate order.** `EnabledContest` → kill switch (both inside `ControlContest`)
  → per-IP lockout → token lookup. The switch is checked *inside* a parameter
  dependency rather than as a router-level one precisely because FastAPI
  resolves router `dependencies=[...]` before parameter dependencies, which
  would put the switch ahead of contest resolution. A token is never resolved
  for a contest the caller may not know exists, and the lockout — checked in
  the resolver body, after `ControlContest` — can never be used to probe the
  two `404` gates.
- **Lockout, same `403`.** After `NOCA_ANIMATOR_CONTROL_LOCKOUT_FAILURES`
  credential failures from one address inside `NOCA_ANIMATOR_CONTROL_LOCKOUT_SECONDS`
  (also the lockout duration) the address is refused before the header is read,
  with the identical generic `403` and **no** `Retry-After`, and audited as
  `outcome=throttled`. Built on `shared.services.auth_rate_limit` with an
  IP-only identity (no account component, so no HMAC secret is needed). A valid
  token resets the counter; scope and ownership refusals are not counted.
- **One generic `403`.** Absent header, wrong scheme, blank token, unknown token,
  and another contest's token all produce the identical detail, so a caller
  cannot probe for the existence of contests, sites, or secrets.
- **Refusals are audited.** Each one logs a `control_audit` record with
  `scope=unknown` and `outcome=invalid_credential` (or `throttled`) before
  raising, so rejected attempts appear in the same audit stream as accepted
  ones.

---

## Client modules (`static/js/`)

Animator presentation clients are split into pure, headlessly testable UMD
modules plus thin browser glue. Contract tests live in `tests/animator/js/` and
run under Node through the Animator test wrappers.

### `cell-format.js` (`window.AnimatorCellFormat`)

The single owner of what one scoreboard cell *means*, shared by the live board and
the reveal projector so an audience and a live viewer can never see different
attempt counts.

- `formatCellText(cell)` / `cellState(cell)` / `describeCell(cell)` — the attempt
  glyph (`+N` / `? −N` / `−N` / empty), the state, and the screen-reader wording.
- `formatAttemptLine(cell, {stacked})` — the attempt count **and the penalty it
  caused, as one complete line**, which is what both renderers draw. Attempts and
  their penalty describe the same failures, so they are never split across two
  lines: a solved cell showed `+2 (40')` on one line while a pending cell put
  `−5` and `(100')` on two, so the same information changed shape the moment it
  was revealed, and the projector's pending cell became the tallest on any
  surface. `stacked` is the projector's pending cell, where the `?` marks already
  occupy the line above and must not be repeated. Returns `""` — never a stray
  `()` — when the cell has no such line. It is returned finished rather than as
  punctuation pieces a renderer recombines, because the pieces are exactly what
  drifted: both renderers used to build `"+" + attempts + " (" + penalty + "')"`
  inline, and Web's Jinja builds the same sentence a third time in another
  language. `tests/fixtures/scoreboard_cell_cases.json` pins all three to the
  same cases.
- `penaltyOf(cell)` normalizes the accumulated attempt penalty.
- `isPending(cell)` / `isFirst(cell)` normalize the two feeds' field names
  (`is_pending`/`pending_frozen`, `is_first_balloon`/`is_first_solver`), so a
  naming difference cannot become a behavior difference.
- `pendingCountOf(cell)` / `formatPendingMarks(cell)` normalize the per-cell
  outstanding count and produce one `?` for every unrevealed submission.
- **`attempts` is used verbatim.** It already means "penalizing attempts made
  before the accepted submission" (`ProblemResult.attempts` in
  `shared/services/scoreboard_projection.py`), so a solved cell renders
  `+attempts` when it is nonzero — adjusting it would understate every solve.
  A first-attempt solve renders no solitary plus.
- State precedence is solved → pending → attempted → none: a solve is settled, so
  a cell solved before the freeze keeps its glyph and merely gains a pending
  marker when it also holds an unrevealed frozen run.

### `animator-keyed-rows.js` (`window.AnimatorKeyedRows`)

The live scoreboard and reveal projector share one keyed table-body reconciler.
It preserves each row's DOM identity by `team_id`, moves surviving rows into
authoritative server order, creates new rows, and removes departed rows. Each
renderer supplies only its surface-specific row builder and updater. Stable row
identity lets `animator-animate.js` measure and animate the same painted element,
so the two presentations cannot drift into different FLIP behavior.

### `animator-team-cell.js` (`window.AnimatorTeamCell`)

Shared team-cell presentation for the live scoreboard and reveal ceremony.

- `createTeamCell(doc, team, options)` creates the row header with the team-media
  Bootstrap trigger, optional site line, and medal watermark.
- `syncTeamCell(doc, th, team, options)` updates the existing trigger's text,
  title, team id, and modal attributes while preserving the exact button object.
  The live keyed renderer can therefore refresh standings while a modal is open
  without invalidating Bootstrap's `relatedTarget` or losing focus restoration.
- All team names use `textContent`; hostile names never become markup.

### `animator-animate.js` (`window.AnimatorAnimate`)

The shared FLIP applier measures keyed row positions before rendering and moves
surviving rows from their previous screen position to their new authoritative
rank.

- `createApplier(tbody, options)` uses the CSS-transition path for the live
  Animator scoreboard. The reveal projector supplies a Web Animations timing
  object because browser style-write coalescing can suppress a table-row CSS
  transition after reconciliation.
- `rowAnimationFromCss(element)` reads `--animator-row-motion-duration` and
  `--animator-row-motion-easing` from computed styles and converts CSS seconds or
  milliseconds into a Web Animations timing object. It returns `null` when
  computed timing is unavailable, preserving the CSS fallback.
- `animator.css` owns the live scoreboard defaults (`1s`, `ease`), and
  `ceremony.css` overrides the same tokens for ceremony motion (`3s`,
  `cubic-bezier(0.22, 1, 0.36, 1)`). CSS transitions and Web Animations
  therefore consume one timing definition per surface.
- Reduced-motion viewers skip FLIP transforms entirely; static row and cell
  highlights remain available.

### `ceremony-render.js` (`window.CeremonyRender`)

Pure DOM rendering for the projection; every function takes an explicit `doc`.

- The projector table carries the live board's `animator-scoreboard` class, and
  its four leading header/body cells carry the same `animator-col-*` classes.
  Table geometry, typography, padding, sticky headers, truncation, and problem
  widths therefore have one CSS owner in `animator.css`; `ceremony.css` adds
  only ceremony-specific states and motion.
- `renderProblemCell(doc, view, isNext, problem)` — delegates its complete inner
  cell stack to `animator-render.js` with the reveal-only stacked-pending option,
  then adds the optional next-cell marker (`data-next="true"` plus the glow
  class). Pending cells render question marks, revealed failures, and penalty on
  separate lines, for example `???`, `−5`, and `(100')`.
- `problemLabels(projection)` / `compareLabels(a, b)` — the authoritative column
  order: the **union** of every team's problem keys sorted naturally (shorter
  first, then lexicographic), so `Z` precedes `AA` and the board never depends on
  object-key iteration order or on a team missing a cell.
- `renderStandings(doc, tbody, headerRow, projection, options)` — draws rows in
  **server order** (it never re-sorts), applying `data-medal` bands, the
  band-end rule, the focused-row class, pending cells (`? −N`, never a result),
  first-solver stars, and the `ceremony-cell--next` glow on the cell the next
  step will change. Attempt counts and penalties come from each newly derived
  projection, so they advance after every reveal step. That cell comes from the
  projection's `next_cell` and is
  matched by **problem id**, not label, so a relabelled column cannot move the
  highlight; the renderer never guesses which cell is next. The shared keyed
  reconciler reuses and reorders surviving rows, so a reconnect cannot duplicate
  the board or discard the element that FLIP must animate.
- Team names render through `animator-team-cell.js` as Bootstrap **data-API**
  modal triggers
  (`data-bs-toggle`/`data-bs-target`/`data-team-id`). This is required for
  accessibility, not cosmetic: Bootstrap 5.3 restores focus to the trigger only
  through that handler, so a programmatic `modal.show()` would leave a keyboard
  operator at the top of the document after every team.
- All text is written with `textContent` and attributes with `setAttribute`, so a
  hostile team name stays inert.

### `team-media-modal.js` (`window.AnimatorTeamModal`)

The reusable team modal, with injected elements and an explicit `audioEnabled`
mode so it is testable headlessly and shared by both presentations.

- `onShow(event)` reads the team from `event.relatedTarget`, opens a new load
  *generation*, and fetches the scoped photo. It reads
  `X-NOCA-Team-Image-Kind` before creating the image object URL, so real photos
  and avatars retain their intrinsic width -- the `fit-content` dialog wraps
  them, capped at `80vw` -- while the placeholder, which has no useful intrinsic
  width, expands to that whole cap. A request or image-decode failure hides the image in
  favor of a plain caption rather than leaving broken-image chrome.
- `onShown()` starts playback inside the click's user activation and resolves one
  of `played` / `blocked` / `unavailable` / `skipped`. A rejected `play()` is
  always caught: `NotAllowedError` keeps the native controls and shows a status
  hint; unusable media (the `error` event, registered **before** `src`, or a
  `NotSupportedError`) hides the player entirely with no error chrome. Controls
  remain hidden until playback succeeds, or until `loadedmetadata` confirms the
  clip exists after autoplay is refused. An absent clip therefore cannot flash
  a player when a policy rejection precedes its `404`. A promise rejection alone
  cannot distinguish those two cases, which is why the `error` listener is the
  discriminator.
- In scoreboard photo-only mode, `audioEnabled=false` makes `onShown()` return
  `skipped` before any audio access. The controller requires no audio element,
  status element, or audio base, and the scoreboard does not bind the `shown`
  event, so it cannot request or play team audio.
- Every media listener — photo and audio alike — is scoped to a **load
  generation** opened by `onShow` and closed by `teardown`, so a previous team's
  late failure cannot hide the current team's working photo or player.
- `teardown()` is idempotent and runs on both `hide` and `hidden`: it aborts the
  photo fetch, revokes the photo object URL, and resets the audio with `pause()`,
  `currentTime = 0`, `removeAttribute("src")`, and `load()`.

### `ceremony.js`

Browser glue: reads the data attributes, drives `RevealTransport` (fetch state,
then subscribe, refetch on every nudge), renders each projection, wires the modal
events, and scrolls the focused row into view honoring
`prefers-reduced-motion`. It owns no ceremony logic and keeps the last good
projection on screen when a refetch fails. A stream error closes the failed
`EventSource` and creates a fresh one with bounded backoff, including when the
browser marks the old connection terminal during an Animator restart. The
replacement stream reloads authoritative state only after `reveal_ready`
confirms subscription coverage. The glue passes both the balloon and star asset
bases to the live scoreboard's shared problem-metadata extractor, so
first-solver cells render the star in both presentations. It replaced Phase 13's
`ceremony-boot.js`, which was a placeholder.

It supplies `CeremonyMediaCue` with its DOM and owns one piece of copy of its
own: the overlay's blocked-audio message is a *function*, returning the
projector-appropriate line only while the current open came from a cue. A local
click keeps the module default ("press play"), since the person who clicked is
the one who can act on it. A blocked remote open reveals a quiet operator note in
the header asking for the one click that grants this page audio, and any
`pointerdown` retires it.

### `ceremony-media-cue.js` (`window.CeremonyMediaCue`)

The projector's half of the operator's team-media cue. Pure: no network, no
ceremony state, and no decision about *which* team — the server already resolved
that from the ceremony's own cursor.

- `createMediaCueController({findTrigger, isOpen, close, currentTeamId})` →
  `{apply, applyState, onHidden, openedRemotely}`
- `ceremonySignature(projection)` — `phase | revealed_count | focused_team_id`.
  **Also imported by `control.js`, and ported into the Android
  `core/Controls.kt`**: the projector closes its overlay when this value changes
  and both operator panels reset their Show/Hide label on it, so a second
  definition of "the ceremony moved" would drift into a button describing the
  opposite of what is on the projector. That is why the control page loads this
  module for the function alone.

Behavior notes:

- **A show clicks the team's real row button, never `modal.show()`.** In
  Bootstrap 5.3 focus *restoration* lives in the data-API click handler, so a
  programmatic open traps focus correctly and then returns it nowhere. Clicking
  the trigger is also what supplies `relatedTarget`, which is how
  `team-media-modal.js` learns the team.
- **Switching teams closes first and reopens on `hidden`.** Bootstrap ignores a
  show on an open dialog, and the teardown that frees the previous team's media
  runs on hide, so the incoming team waits for that close rather than racing it.
  A switch still pending when the ceremony moves is abandoned — reopening a team
  the board has moved past would put a stale face on the projector.
- **A ceremony that moves takes the overlay down** (`applyState` returns whether
  it did). An operator who cues a photo and then presses Step is not asking to
  reveal a result from behind a photograph, and nothing on the server can enforce
  it: the cue persists no state for a later command to clear. Keying on the
  *signature* rather than on the state request is what keeps a reconnect
  reconciliation — which refetches identical state — from closing an overlay the
  operator just raised. It is also what lets both operator panels keep a purely
  local Show/Hide label, since projector and panels reset on one signal.
- **Absent or malformed input is ignored, never thrown over.** A cue for a team
  that is not on the board (a cue that raced a re-render, or a row this scope
  filtered out) does nothing: there is no photo this projector could correctly
  show.

### `control-lease.js` (`window.AnimatorControlLease`)

The ephemeral controller-identity transport; `createLeaseClient(deps)` with
injectable `fetchImpl`, timer functions, visibility/page targets, and a state
handler, so the whole lifecycle runs under Node with fake timers.

- Generates **one controller id per loaded panel** (`crypto.randomUUID`, with a
  time-and-random fallback shaped like the idempotency-key fallback). The id is
  read once into a closure variable and travels only in the
  `X-Animator-Controller-Id` header — never a URL, the DOM, storage, or a log;
  a source scan in `control-lease.test.cjs` pins this.
- `claim(secret)` / `heartbeat()` / `takeover()` / `release(keepalive)` map the
  four lease routes onto panel states (`active`, `read-only`, `lease-lost`,
  `unavailable`, `released`) emitted through the state handler. A `409` on claim
  means another controller owns the scope (read-only); a `409` anywhere else
  means this panel lost ownership and stops commanding immediately. Anything
  non-stated is unavailable → fail closed.
- Heartbeats reschedule from the server's returned cadence
  (`heartbeat_interval_seconds`), not a client constant.
- The `active` state is emitted **with the lease payload** as its detail, which
  is how `projector_count` reaches the ownership controller on every claim,
  heartbeat, and takeover without a request of its own.
- **A blip does not cost command authority.** A heartbeat that fails without a
  stated ownership answer (network error, `503`) is retried ~`ttl/3` later,
  twice, inside the remaining TTL — the panel stays fail-closed while retrying
  (`pending`) and only reports unavailable once the budget is exhausted. A
  stated `409` is terminal immediately.
- `visibilitychange`/`pageshow` renewal fires only when actually visible, so a
  backgrounded tab does not burn attempts while throttled; `pagehide` sends one
  best-effort `release` with `keepalive: true`. A page restored from the
  back-forward cache still holds its credential, so it **re-claims on
  `pageshow`** instead of sitting on "released"; claim's idempotent
  compare-and-set means it lands read-only rather than stealing the scope if
  another controller took over meanwhile. TTL expiry remains authoritative for
  every path.

### `control-ownership.js` (`window.AnimatorControlOwnership`)

The ownership state machine and operator-panel glue;
`createOwnershipController(deps)` wraps a lease client and renders the four
panel states plus pending/released into the status badge and detail line.

- Commands are enabled **only** in the active state: `setCommandsEnabled` gates
  every mutating button and keyboard shortcut through `control.js`.
- **Take over control…** renders only in read-only or lease-lost states and
  always goes through `confirmTakeover` — `control.js` shows its modal warning
  that the other panel immediately loses command authority, and takeover fires
  only from the modal's confirm button. There is no automatic takeover.
- Retry exists only in the unavailable state and re-runs a claim — which can
  never steal the scope from another owner.
- **Projector readout.** `projectorCopy(count)` words the `projector_count`
  the lease payload carries — `N projectors connected`, `1 projector
  connected`, `No projectors connected` (warning tone), and `Projector count
  unavailable` for `null`, which is deliberately never rendered as zero — into
  `#control-projectors` beside the ownership badge, so the glance that answers
  "am I in control?" also answers "is anyone listening?". It renders only in the
  active state with a reported count: a heartbeat error detail keeps the last
  reading, and losing control clears it rather than leaving a stale number under
  a badge that no longer means control. The Android remote ports the wording
  name-for-name (`projectorLabel` in `core/Controls.kt`).

### `control.js` (`window.AnimatorControl`)

`createCommandClient(deps)` is the pure command client; `boot(doc)` is the glue.

- The operator secret lives in **one closure variable**, is cleared from the input
  on capture, and travels only as an `Authorization: Bearer` header — never a URL,
  body, storage, cookie, or log.
- **`403` is handled in one place** (`handleForbidden`), reached from both the
  initial state load and every command, so unlocking with a wrong secret discards
  it and re-prompts instead of leaving an invalid credential in memory behind a
  panel that looks unlocked.
- `start(siteId, restart)` sends `{site_id, restart}` (explicit `null` for the
  global ceremony) because the server compares that value for exact equality with
  the token's scope; `jump(teamId)` sends `{team_id}`;
  `step`/`back`/`reset`/`jumpPending` send **no** body, matching the API's
  `extra="forbid"` models.
- **Jump to next pending** appears only while a ceremony is revealing and its
  projection has no `next_cell`. It sends one server command, not a client-side
  step sequence, so lease, idempotency, persistence, and ambiguity handling
  remain identical to every other mutation. The Android `ControlVisibility`,
  `CommandClient`, `RemoteViewModel`, and `CommandPad` mirror this rule.
- **Every mutating attempt carries a fresh `Idempotency-Key`** (a `randomUUID`,
  or a time-and-random fallback where `crypto.randomUUID` is unavailable). Fresh
  *per attempt*: two deliberate presses of "step" are two commands and must both
  apply — only a repeat of one attempt reuses a key. Reads send none. The key is
  not derived from the secret and grants nothing.
- `stepMany(10)` and `backMany(10)` issue the existing scope-free command ten
  times in strict sequence. They stop on the first response that is not a
  confirmed success; no bulk endpoint or command-bus message exists.
- **`mediaCue(action)` is the one command outside `run()`**, and its three
  differences are the reason a cue is safe where a reveal command is not: it
  sends **no `Idempotency-Key`** (nothing is persisted, so there is no receipt
  ring to match one against, and re-cueing is inherently a no-op); a failure
  **never sets `blocked`** (a cue cannot reveal anything, so locking the pad over
  one would be pure cost); and `hide` is dispatched **even while the pad is
  blocked** — an ambiguous `5xx` with a photograph over the board is exactly when
  the operator needs the board back, and hiding can never double-apply. `show`
  stays suppressed there, since raising an overlay while the ceremony's true
  position is unknown risks showing the wrong team. It expects `204`, so there is
  no projection to apply; only a confirmed cue flips the button's label, and the
  label resets on every applied projection in step with the projector's own
  auto-hide. `controlsForState` gains `mediaVisible`, true whenever the
  projection names a `focused_team_id` — gated on the cursor rather than the
  phase, and deliberately surviving into `done`. The Android `ControlVisibility`,
  `CommandClient`, `RemoteViewModel`, and `CommandPad` mirror all of it.
- **Every mutating attempt carries the controller id** from the wired
  `AnimatorControlOwnership` alongside its fresh `Idempotency-Key`, and `run`
  refuses to fire at all unless the ownership controller reports active — so a
  read-only, lease-lost, or unavailable panel suppresses commands before the
  network is touched. A `409` whose detail names lost ownership marks the lease
  lost (stopping heartbeats and commands) while staying a stated refusal.
- **`isDefinitive(status)` splits the failure model.** Stated refusals
  (`400/403/404/409/422`) changed nothing and re-enable the controls immediately.
  Everything else — network failure and any `5xx`, including the store's `503`,
  which may arrive after a fenced save committed — is ambiguous. The client
  shows a persistent warning, stays `blocked`, and refuses to issue another
  command until the operator selects **Reload state** and that authoritative
  read succeeds. It does not auto-reload, because a fast or cached read could
  erase the warning without making the operator acknowledge the unknown command
  outcome. A batch stops at the ambiguous item. This prevents a failed `step`
  from being replayed into a double reveal in front of an audience.
