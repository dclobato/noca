# Phase 22: Enforce one active reveal controller

This optional hardening session prevents two operator panels from driving the
same global or site ceremony. It adds a Valkey-backed controller lease while
leaving any number of read-only projectors connected to the ceremony.

## Status: optional (backlog), not on the required path

This phase is **deferred to the backlog** and isn't required to complete the
Animator module. Implement it after [Phase 21](Phase-21.md) when an event needs
server-enforced single-controller ownership instead of an operational rule that
only one operator opens the panel.

The existing reveal mutation lock isn't controller presence. It exists only
while one command loads, changes, and saves state, then releases immediately.
It prevents overlapping writes but still lets two panels issue sequential
commands. This phase adds a longer-lived ownership lease without weakening that
per-command lock.

## Roadmap relationship

This phase is an operational hardening item discovered after the required
roadmap. It doesn't change the required Phase 00 through Phase 21 completion
criteria or add behavior to the source plan.

## Dependencies

Complete [Phase 21](Phase-21.md) first so the control API, multi-replica reveal
store, recovery behavior, audit boundary, and end-to-end ceremony tests are
stable before controller ownership becomes an additional command gate.

## Session scope

Limit this session to controller-lease keys and atomic operations, authenticated
lease routes, command ownership enforcement, controller-panel heartbeat and
takeover UX, tests, configuration when needed, and route/service documentation.
Don't restrict or enumerate projector connections.

## Required preflight

Complete these checks before editing code:

1. Read `animator/services/reveal_session_store.py`,
   `shared/services/valkey_service/revelation.py`, the control routes and audit
   boundary, `control.js`, and current fake and real-Valkey concurrency tests.
2. Inspect `shared/services/lock_service.py` for reusable ownership-safe
   patterns. Don't extend it blindly: its current lock kinds have no renewal
   operation or command-fencing contract.
3. Verify browser timer throttling, `pagehide`, back-forward cache, and
   `fetch(..., {keepalive: true})` behavior. Treat explicit release as
   best-effort; TTL expiry remains authoritative.
4. Search PyPI for maintained lease or distributed-lock libraries. Record why
   existing Valkey primitives and small Lua scripts are sufficient, or justify
   any dependency.
5. Define the lease TTL and heartbeat interval. Keep the TTL comfortably longer
   than the heartbeat interval and document their failure-detection delay.
6. Define atomic ordering for claim, command acquisition, heartbeat, release,
   expiry, and takeover before writing routes or browser code.

## Ownership contract

The controller lease must satisfy these properties:

- Ownership is isolated by exact `(contest_id, scope)`, where scope is one site
  id or `global`. Controllers for different scopes don't block each other.
- A browser generates a cryptographically random controller id. The id isn't a
  credential: every lease operation and command still requires the operator
  bearer token.
- The controller id stays out of URLs and logs. Send it in a dedicated request
  header and keep it in page memory. A page reload may reclaim through explicit
  takeover if best-effort release didn't complete.
- The first authenticated controller claims an expiring Valkey lease. A second
  controller for the same scope receives `409` and enters read-only mode.
- Only the current owner can renew or release the lease. Compare-and-expire and
  compare-and-delete operations are atomic.
- Valkey unavailability fails closed with `503`; it never grants control based
  on missing or stale local information.
- Every mutating reveal command requires the active controller id. Read-only
  state routes remain available without ownership so a blocked controller can
  observe the ceremony.
- Lease ownership and acquisition of the existing per-command mutation lock are
  checked atomically. A controller that lost ownership can't pass a check and
  race a takeover before acquiring the mutation lock.
- Takeover is explicit and modal-confirmed. It succeeds only when no mutation is
  in flight, replaces the old lease atomically, and fences the former controller
  from every later command and heartbeat.
- A crashed or disconnected controller loses ownership after TTL expiry. An old
  page that reconnects after expiry remains read-only until it claims an empty
  lease or explicitly takes over.
- Controller leases never contain bearer tokens, token digests, client IPs, or
  other credentials.

## Route contract

Add authenticated lease operations under
`/animator/c/{slug}/control/controller-lease`:

- `POST /claim` claims an empty lease for the credential's resolved scope.
- `POST /heartbeat` renews only the caller's active lease.
- `POST /release` releases only the caller's active lease.
- `POST /takeover` replaces another controller after explicit confirmation and
  only when the mutation lock is free.

Use the existing contest, kill-switch, credential, scope, and exactly-once audit
boundaries. Return a small typed response containing lease status and expiry
timing, never holder identity. Use these refusal shapes:

- `409` when another controller owns the scope or the caller has lost ownership.
- `503` with a retry hint when Valkey is unavailable or a command/takeover is
  contended.
- The existing uniform `404` and `403` behavior for gates and credentials.

Require the controller-id header on `start-reveal`, `step`, `back`, `reset`, and
`jump-team`. Missing, malformed, expired, or non-owning ids must not reach the
reveal engine or save/publish state.

## Interface contract

Update the controller panel with these states:

- **Active:** normal ceremony commands are enabled and the lease heartbeat runs.
- **Read-only:** authoritative state remains visible, but every mutating command
  is disabled with “Another controller is active for this ceremony.”
- **Lease lost:** stop heartbeats and commands immediately, retain the last
  projection, and explain that control moved or expired.
- **Unavailable:** fail closed, retain the last projection, and offer a bounded
  retry without claiming that the lease is free.

Show **Take over control…** only in read-only or lease-lost states. Its modal
must warn that the other panel will immediately lose command authority. Don't
offer automatic takeover, and don't change projector pages or SSE contracts.

## Implementation tasks

Implement controller ownership in this order:

1. Add validated controller-lease key construction beside the existing reveal
   state, mutation-lock, and channel builders.
2. Add a focused controller-lease service with typed results and atomic Lua
   operations for claim, owner renewal, owner release, and takeover.
3. Extend mutation-lock acquisition so one atomic operation verifies controller
   ownership and acquires the short command lock. Keep the existing fenced save
   and ownership-safe release.
4. Add typed request-header validation, response models, authenticated lease
   routes, refusal mapping, and exactly-once audit outcomes.
5. Require active ownership in every mutating control route while keeping
   `/control/state` read-only and lease-independent.
6. Generate one random controller id per loaded panel, claim after credential
   validation, renew on a bounded interval, renew promptly after visibility or
   back-forward-cache restoration, and release best-effort on `pagehide`.
7. Add read-only, lease-lost, unavailable, and confirmed-takeover UI states.
   Keep the operator secret and controller id out of the DOM, URLs, storage, and
   logs.
8. Add fake and real-Valkey tests for claim races, same-owner renewal,
   wrong-owner rejection, expiry, release, unavailable Valkey, takeover during
   and after mutation, former-owner fencing, same-scope exclusion,
   cross-scope independence, and multiple Animator replicas.
9. Add browser-independent JavaScript tests for heartbeat scheduling, timer
   throttling recovery, page teardown, read-only controls, takeover
   confirmation, lease loss, and absence of persistent storage.
10. Verify that multiple projectors still receive nudges and reconcile from the
    same authoritative state without acquiring or reading controller leases.
11. Update `animator/docs/ROUTES.md`, `animator/docs/SERVICES.md`,
    `docs/CONFIG.md` when lease timing is configurable, and operational
    documentation that describes controller startup and takeover.

## Validation

Run focused unit, browser-contract, and real-Valkey checks:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest \
  tests/animator/test_controller_lease_service.py \
  tests/animator/test_control_routes.py \
  tests/animator/test_control_concurrency.py \
  tests/animator/test_reveal_templates.py -q
UV_CACHE_DIR=/tmp/uv-cache uv run pytest -m real_valkey \
  tests/animator/test_controller_lease_service.py \
  tests/animator/test_control_concurrency.py -q
node --test tests/animator/js/control.test.cjs
UV_CACHE_DIR=/tmp/uv-cache uv run mypy animator shared
UV_CACHE_DIR=/tmp/uv-cache uv run ruff check animator shared tests/animator
UV_CACHE_DIR=/tmp/uv-cache uv run ruff format --check \
  animator shared tests/animator
uv run djlint animator/template/control.html --check
```

Run a manual multi-window test with two controllers for one site, controllers
for different sites, two Animator replicas, and multiple projectors. Verify
expiry and explicit takeover by closing, suspending, reconnecting, and reviving
the former owner.

## Completion criteria

This phase is complete when exactly one controller can mutate each
contest/scope, takeover and expiry fence the former owner, Valkey outages fail
closed, different scopes remain independent, multiple replicas enforce the same
lease, and any number of projectors continue to converge on authoritative state.

## Next phase

No later phase depends on this optional hardening item. Return to operational
validation and documentation when controller-lease behavior changes.
