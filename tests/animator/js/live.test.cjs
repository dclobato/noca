//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

// Browser-independent integration test for animator-live.js: the RefreshCoordinator
// (burst coalescing + stale-version rejection) and the connection controller
// (named SSE listeners, Live/Reconnecting/Polling machine, reconnect
// reconciliation, poll start/stop, unsupported EventSource, foreground refresh).
// A fake EventSource and deferred fetches drive the real wiring headlessly.

"use strict";

const assert = require("assert");
const path = require("path");

const live = require(path.join(__dirname, "..", "..", "..", "animator", "static", "js", "animator-live.js"));

function flush() {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

function deferredFetcher() {
  const calls = [];
  function fetchSnapshot() {
    let resolve;
    const promise = new Promise((r) => {
      resolve = r;
    });
    calls.push({ resolve: resolve });
    return promise;
  }
  return { fetchSnapshot: fetchSnapshot, calls: calls };
}

class FakeEventSource {
  constructor(url) {
    this.url = url;
    this.listeners = {};
    this.closed = false;
    this.readyState = FakeEventSource.CONNECTING;
    this.onopen = null;
    this.onerror = null;
    FakeEventSource.instances.push(this);
  }
  addEventListener(name, cb) {
    (this.listeners[name] = this.listeners[name] || []).push(cb);
  }
  emit(name, detail) {
    (this.listeners[name] || []).forEach((cb) => cb(detail || {}));
  }
  close() {
    this.closed = true;
    this.readyState = FakeEventSource.CLOSED;
  }
}
FakeEventSource.CONNECTING = 0;
FakeEventSource.OPEN = 1;
FakeEventSource.CLOSED = 2;

// ── Coordinator: coalesce a burst to one in-flight + one queued ──────────────
async function testCoalescing() {
  const fetcher = deferredFetcher();
  const applied = [];
  const coord = live.createRefreshCoordinator(fetcher.fetchSnapshot, (s) => applied.push(s));

  assert.strictEqual(coord.trigger(), 1, "first trigger schedules request generation 1");
  await flush();
  assert.strictEqual(fetcher.calls.length, 1, "one fetch in flight");

  // A burst while one is in flight collapses to a single queued follow-up.
  assert.strictEqual(coord.trigger(), 2, "trigger during a fetch targets the queued generation");
  assert.strictEqual(coord.trigger(), 2, "a burst shares the queued generation");
  assert.strictEqual(coord.trigger(), 2, "a burst shares the queued generation");
  await flush();
  assert.strictEqual(fetcher.calls.length, 1, "burst does not open more fetches");

  fetcher.calls[0].resolve({ version: "2026-07-22T00:00:01+00:00" });
  await flush();
  assert.strictEqual(applied.length, 1);
  assert.strictEqual(fetcher.calls.length, 2, "queued follow-up runs once");

  fetcher.calls[1].resolve({ version: "2026-07-22T00:00:02+00:00" });
  await flush();
  assert.strictEqual(applied.length, 2);
  assert.strictEqual(fetcher.calls.length, 2, "no further fetch after queue drains");
}

// ── Coordinator: reject a response older than the last applied version ───────
async function testStaleRejection() {
  const fetcher = deferredFetcher();
  const applied = [];
  const coord = live.createRefreshCoordinator(fetcher.fetchSnapshot, (s) => applied.push(s));

  coord.trigger();
  await flush();
  coord.trigger(); // queued
  await flush();

  // First request completes with a newer version; second (queued) completes with
  // an older version and must be rejected — the board never regresses.
  fetcher.calls[0].resolve({ version: "2026-07-22T10:00:00+00:00" });
  await flush();
  fetcher.calls[1].resolve({ version: "2026-07-22T09:00:00+00:00" });
  await flush();

  assert.strictEqual(applied.length, 1, "stale older snapshot rejected");
  assert.strictEqual(applied[0].version, "2026-07-22T10:00:00+00:00");
}

// ── Shared snapshot gate: one notion of "newer" across every fetch path ──────
function testSharedSnapshotGate() {
  const gate = live.createSnapshotGate();

  assert.strictEqual(gate.accept({ version: "2026-07-22T10:00:00+00:00" }), true, "first snapshot applies");
  assert.strictEqual(
    gate.accept({ version: "2026-07-22T09:00:00+00:00" }),
    false,
    "an older snapshot is refused whichever path fetched it",
  );
  assert.strictEqual(
    gate.accept({ version: "2026-07-22T10:00:00+00:00" }),
    true,
    "an equal version is the same snapshot, not a regression",
  );
  assert.strictEqual(gate.accept({}), true, "a snapshot with no version carries nothing to order it by");
  assert.strictEqual(gate.accept(null), true, "a null snapshot is left to the caller");
  assert.strictEqual(
    gate.accept({ version: "2026-07-22T11:00:00+00:00" }),
    true,
    "a newer snapshot still applies after an unversioned one",
  );
}

// A poll outside the coordinator -- the absence watch, the release watch, the
// start re-check -- must not be able to roll the board back over a snapshot the
// live transport already applied. Sharing the gate is what prevents it.
async function testCoordinatorSharesTheGateWithDirectPolls() {
  const fetcher = deferredFetcher();
  const applied = [];
  const gate = live.createSnapshotGate();
  const coord = live.createRefreshCoordinator(fetcher.fetchSnapshot, (s) => applied.push(s), gate);

  coord.trigger();
  await flush();
  fetcher.calls[0].resolve({ version: "2026-07-22T10:00:00+00:00" });
  await flush();
  assert.strictEqual(applied.length, 1, "the transport applied its snapshot");

  // A direct poll's response, delayed until after the newer one landed.
  assert.strictEqual(
    gate.accept({ version: "2026-07-22T09:30:00+00:00" }),
    false,
    "a slow direct poll cannot regress what the transport applied",
  );

  // ...and the reverse: a direct poll that lands first is respected by the
  // coordinator, which must not re-apply an older snapshot over it.
  assert.strictEqual(gate.accept({ version: "2026-07-22T12:00:00+00:00" }), true);
  coord.trigger();
  await flush();
  fetcher.calls[1].resolve({ version: "2026-07-22T11:00:00+00:00" });
  await flush();
  assert.strictEqual(applied.length, 1, "the coordinator honours a newer direct poll");
}

// ── Controller: named listeners; only scoreboard_refresh fetches ─────────────
function testNamedListeners() {
  FakeEventSource.instances = [];
  let refreshes = 0;
  const controller = live.createConnectionController({
    EventSourceCtor: FakeEventSource,
    eventsUrl: "/events",
    coordinator: { trigger: () => (refreshes += 1) },
    startPolling: () => {},
    stopPolling: () => {},
    setStatus: () => {},
  });
  controller.connect();
  const es = FakeEventSource.instances[0];
  assert.ok(es.listeners["scoreboard_refresh"], "named scoreboard_refresh listener");
  assert.ok(es.listeners["verdict"], "named verdict listener");
  assert.ok(es.listeners["timer_tick"], "named timer_tick listener");

  assert.ok(es.listeners["submission"], "named submission listener");

  es.emit("scoreboard_refresh");
  assert.strictEqual(refreshes, 1, "refresh event triggers a fetch");
  es.emit("verdict");
  es.emit("timer_tick");
  assert.strictEqual(refreshes, 1, "verdict and timer_tick never fetch");
}

// ── Controller: a submission nudge parses, calls onSubmission, then refetches ──
function testSubmissionListener() {
  FakeEventSource.instances = [];
  let refreshes = 0;
  const received = [];
  const controller = live.createConnectionController({
    EventSourceCtor: FakeEventSource,
    eventsUrl: "/events",
    coordinator: { trigger: () => (refreshes += 1) },
    startPolling: () => {},
    stopPolling: () => {},
    setStatus: () => {},
    onSubmission: (data, refreshSequence) => received.push({ data: data, refreshSequence: refreshSequence }),
  });
  controller.connect();
  const es = FakeEventSource.instances[0];

  const payload = { submission_id: "s1", team_id: "t1", problem_id: "p1" };
  es.emit("submission", { data: JSON.stringify(payload) });
  assert.deepStrictEqual(
    received,
    [{ data: payload, refreshSequence: 1 }],
    "onSubmission receives the payload and its scheduled refresh generation",
  );
  assert.strictEqual(refreshes, 1, "a submission nudge triggers the authoritative refetch");

  // A malformed payload is swallowed: no onSubmission call and no refetch.
  es.emit("submission", { data: "not-json" });
  assert.strictEqual(received.length, 1, "malformed submission payload is ignored");
  assert.strictEqual(refreshes, 1, "malformed submission does not refetch");
}

// ── Verdict detail is parsed for observers without triggering a fetch ────────
function testVerdictListener() {
  FakeEventSource.instances = [];
  let refreshes = 0;
  const received = [];
  const controller = live.createConnectionController({
    EventSourceCtor: FakeEventSource,
    eventsUrl: "/events",
    coordinator: { trigger: () => (refreshes += 1) },
    startPolling: () => {},
    stopPolling: () => {},
    setStatus: () => {},
    onVerdict: (data) => received.push(data),
  });
  controller.connect();
  const es = FakeEventSource.instances[0];
  es.emit("verdict", { data: JSON.stringify({ judgment_id: "j1", verdict: "AC" }) });
  es.emit("verdict", { data: "not-json" });
  assert.deepStrictEqual(received, [{ judgment_id: "j1", verdict: "AC" }]);
  assert.strictEqual(refreshes, 0, "verdict detail leaves fetching to scoreboard_refresh");
}

// ── Pending flash waits for the refresh scheduled after its submission ───────
async function testPendingFlashWaitsForScheduledRefresh() {
  const fetcher = deferredFetcher();
  const flashes = [];
  const flashQueue = live.createPendingFlashQueue({
    cellKey: (teamId, problemId) => teamId + ":" + problemId,
    flashCell: (teamId, problemId) => flashes.push([teamId, problemId]),
  });
  const coordinator = live.createRefreshCoordinator(fetcher.fetchSnapshot, (snapshot, refreshSequence) => {
    flashQueue.apply(snapshot, refreshSequence);
  });

  coordinator.trigger(); // generation 1 is already in flight before the event
  await flush();
  const submissionRefresh = coordinator.trigger(); // queued generation 2
  flashQueue.enqueue({ team_id: "t1", problem_id: "p1" }, submissionRefresh);

  fetcher.calls[0].resolve({ version: "2026-07-22T00:00:01+00:00", pending_submissions: [] });
  await flush();
  assert.deepStrictEqual(flashes, [], "older in-flight snapshot cannot consume the newer flash");
  assert.strictEqual(flashQueue._pendingCount(), 1, "flash waits for its scheduled generation");

  fetcher.calls[1].resolve({
    version: "2026-07-22T00:00:02+00:00",
    pending_submissions: [{ team_id: "t1", problem_id: "p1" }],
  });
  await flush();
  assert.deepStrictEqual(flashes, [["t1", "p1"]], "scheduled refresh flashes the pending cell once");
  assert.strictEqual(flashQueue._pendingCount(), 0);

  const resolvedRefresh = coordinator.trigger();
  await flush();
  flashQueue.enqueue({ team_id: "t2", problem_id: "p2" }, resolvedRefresh);
  fetcher.calls[2].resolve({ version: "2026-07-22T00:00:03+00:00", pending_submissions: [] });
  await flush();
  assert.deepStrictEqual(flashes, [["t1", "p1"]], "resolved cells never receive a pending flash");
  assert.strictEqual(flashQueue._pendingCount(), 0);
}

// ── Controller: error escalation and reconnect reconciliation ────────────────
function testStateMachine() {
  FakeEventSource.instances = [];
  let pollStarts = 0;
  let pollStops = 0;
  let refreshes = 0;
  const statuses = [];
  const controller = live.createConnectionController({
    EventSourceCtor: FakeEventSource,
    eventsUrl: "/events",
    coordinator: { trigger: () => (refreshes += 1) },
    startPolling: () => (pollStarts += 1),
    stopPolling: () => (pollStops += 1),
    setStatus: (s) => statuses.push(s),
  });
  controller.connect();
  const es = FakeEventSource.instances[0];

  // First open: Live, reset, reconcile immediately.
  es.onopen();
  assert.strictEqual(statuses[statuses.length - 1], live.STATUS_LIVE);
  assert.strictEqual(refreshes, 1, "open reconciles with an immediate refresh");

  // First error -> Reconnecting, no polling yet.
  es.onerror();
  assert.strictEqual(statuses[statuses.length - 1], live.STATUS_RECONNECTING);
  assert.strictEqual(pollStarts, 0);

  // Second consecutive error -> Polling (exactly one interval).
  es.onerror();
  assert.strictEqual(statuses[statuses.length - 1], live.STATUS_POLLING);
  assert.strictEqual(pollStarts, 1, "polling starts once at threshold");

  // Recovery open: stop polling, back to Live, reset failures, reconcile again.
  es.onopen();
  assert.strictEqual(pollStops, 1, "recovery stops polling");
  assert.strictEqual(statuses[statuses.length - 1], live.STATUS_LIVE);
  assert.strictEqual(refreshes, 2, "reconnect reconciles the missed window");

  // A single error after recovery is only Reconnecting again (failures reset).
  es.onerror();
  assert.strictEqual(statuses[statuses.length - 1], live.STATUS_RECONNECTING);
  assert.strictEqual(pollStarts, 1, "failure counter reset on recovery");
}

// ── Controller: terminal EventSource is replaced while polling ───────────────
function testTerminalFailureRecovery() {
  FakeEventSource.instances = [];
  let pollOnce = null;
  let pollStarts = 0;
  let pollStops = 0;
  let refreshes = 0;
  const statuses = [];
  const controller = live.createConnectionController({
    EventSourceCtor: FakeEventSource,
    eventsUrl: "/events",
    coordinator: { trigger: () => (refreshes += 1) },
    startPolling: (callback) => {
      pollStarts += 1;
      pollOnce = callback;
    },
    stopPolling: () => (pollStops += 1),
    setStatus: (s) => statuses.push(s),
  });
  controller.connect();
  const first = FakeEventSource.instances[0];

  // A terminal response can close EventSource after only one error. The browser
  // will not retry that object, so the controller must enter polling immediately.
  first.readyState = FakeEventSource.CLOSED;
  first.onerror();
  assert.strictEqual(statuses[statuses.length - 1], live.STATUS_POLLING);
  assert.strictEqual(pollStarts, 1, "terminal failure starts one poll interval");
  assert.ok(pollOnce, "poll interval receives the controller recovery callback");

  // The next poll refreshes the snapshot and replaces the terminal EventSource.
  pollOnce();
  assert.strictEqual(refreshes, 1, "poll tick refreshes the authoritative snapshot");
  assert.strictEqual(FakeEventSource.instances.length, 2, "poll tick opens a fresh EventSource");
  assert.ok(first.closed, "terminal EventSource is discarded");

  // Starting the module again lets the fresh source open and restore live mode.
  const second = FakeEventSource.instances[1];
  second.readyState = FakeEventSource.OPEN;
  second.onopen();
  assert.strictEqual(pollStops, 1, "successful replacement stops polling");
  assert.strictEqual(statuses[statuses.length - 1], live.STATUS_LIVE);
  assert.strictEqual(refreshes, 2, "recovered stream reconciles immediately");

  // A delayed callback from the discarded source cannot degrade the new stream.
  first.onerror();
  assert.strictEqual(statuses[statuses.length - 1], live.STATUS_LIVE);
}

// ── Controller: unsupported EventSource polls from the start ─────────────────
function testUnsupported() {
  let pollStarts = 0;
  let refreshes = 0;
  const statuses = [];
  const controller = live.createConnectionController({
    EventSourceCtor: null,
    eventsUrl: "/events",
    coordinator: { trigger: () => (refreshes += 1) },
    startPolling: () => (pollStarts += 1),
    stopPolling: () => {},
    setStatus: (s) => statuses.push(s),
  });
  controller.connect();
  assert.strictEqual(pollStarts, 1, "no EventSource => poll immediately");
  assert.strictEqual(statuses[statuses.length - 1], live.STATUS_POLLING);
  assert.strictEqual(refreshes, 1, "unsupported path still fetches an initial snapshot");
}

// ── Controller: foreground refresh and teardown ──────────────────────────────
function testForegroundAndClose() {
  FakeEventSource.instances = [];
  let refreshes = 0;
  let pollStops = 0;
  const controller = live.createConnectionController({
    EventSourceCtor: FakeEventSource,
    eventsUrl: "/events",
    coordinator: { trigger: () => (refreshes += 1) },
    startPolling: () => {},
    stopPolling: () => (pollStops += 1),
    setStatus: () => {},
  });
  controller.connect();
  const es = FakeEventSource.instances[0];

  controller.refreshNow();
  assert.strictEqual(refreshes, 1, "foreground refresh triggers one fetch");

  controller.close();
  assert.ok(es.closed, "close() closes the EventSource");
  // An error delivered after teardown must not escalate to polling.
  es.onerror();
  assert.strictEqual(controller._state().closed, true);
}

// ── Controller: reopen after a back/forward-cache restore ────────────────────
function testReopenAfterBfcache() {
  FakeEventSource.instances = [];
  let refreshes = 0;
  const controller = live.createConnectionController({
    EventSourceCtor: FakeEventSource,
    eventsUrl: "/events",
    coordinator: { trigger: () => (refreshes += 1) },
    startPolling: () => {},
    stopPolling: () => {},
    setStatus: () => {},
  });
  controller.connect();
  const first = FakeEventSource.instances[0];

  // A real unload closes; but a persisted pagehide must not — simulate the
  // restore path directly through reopen(), which the page wires to
  // pageshow.persisted. A fresh EventSource is opened and its open reconciles.
  controller.reopen();
  assert.ok(first.closed, "reopen closes the stale EventSource");
  assert.strictEqual(FakeEventSource.instances.length, 2, "a fresh EventSource is opened");
  assert.strictEqual(controller._state().closed, false, "controller is live again after reopen");

  const second = FakeEventSource.instances[1];
  second.onopen();
  assert.strictEqual(refreshes, 1, "reopened stream reconciles on open");
}

async function main() {
  await testCoalescing();
  await testStaleRejection();
  testSharedSnapshotGate();
  await testCoordinatorSharesTheGateWithDirectPolls();
  testNamedListeners();
  testSubmissionListener();
  testVerdictListener();
  await testPendingFlashWaitsForScheduledRefresh();
  testStateMachine();
  testTerminalFailureRecovery();
  testUnsupported();
  testForegroundAndClose();
  testReopenAfterBfcache();
  console.log("animator-live contract: all assertions passed");
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
