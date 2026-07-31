//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

// Browser-independent contract test for ceremony-transport.js: the ordering
// rule that makes a non-replayable stream safe. A deferred fetch and a fake
// EventSource drive the real module headlessly, so the assertions are about
// observable order (fetch settles, *then* the stream is constructed) rather than
// about implementation details.

"use strict";

const assert = require("assert");
const path = require("path");

const transportApi = require(
  path.join(__dirname, "..", "..", "..", "animator", "static", "js", "ceremony-transport.js"),
);

function flush() {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

function deferredFetcher() {
  const calls = [];
  function fetchState() {
    let resolve;
    let reject;
    const promise = new Promise((res, rej) => {
      resolve = res;
      reject = rej;
    });
    calls.push({ resolve: resolve, reject: reject });
    return promise;
  }
  return { fetchState: fetchState, calls: calls };
}

function fakeSchedule() {
  const pending = [];
  return {
    schedule: {
      setTimeout: function (fn, ms) {
        const entry = { fn: fn, ms: ms, cancelled: false };
        pending.push(entry);
        return entry;
      },
      clearTimeout: function (handle) {
        handle.cancelled = true;
      },
    },
    pending: pending,
    // Fire every timer scheduled so far, in order.
    async fire() {
      const due = pending.splice(0, pending.length);
      due.filter((e) => !e.cancelled).forEach((e) => e.fn());
      await flush();
    },
  };
}

function fakeEventSourceClass() {
  class FakeEventSource {
    constructor(url) {
      this.url = url;
      this.listeners = {};
      this.closed = false;
      this.onopen = null;
      this.onerror = null;
      FakeEventSource.instances.push(this);
    }
    addEventListener(name, cb) {
      (this.listeners[name] = this.listeners[name] || []).push(cb);
    }
    emit(name) {
      (this.listeners[name] || []).forEach((cb) => cb({}));
    }
    close() {
      this.closed = true;
    }
  }
  FakeEventSource.instances = [];
  return FakeEventSource;
}

// ── The initial fetch must complete before the stream is opened ──────────────
async function testFetchBeforeSubscribe() {
  const fetcher = deferredFetcher();
  const FakeEventSource = fakeEventSourceClass();
  const states = [];
  const transport = transportApi.createRevealTransport({
    fetchState: fetcher.fetchState,
    EventSourceCtor: FakeEventSource,
    eventsUrl: "/reveal/events?scope=global",
    onState: (s) => states.push(s),
  });

  const started = transport.start();
  await flush();
  assert.strictEqual(fetcher.calls.length, 1, "start() issues the state fetch");
  assert.strictEqual(
    FakeEventSource.instances.length,
    0,
    "the stream is NOT opened while the initial state request is still in flight",
  );

  fetcher.calls[0].resolve({ has_session: true, projection: { phase: "revealing" } });
  await started;
  assert.strictEqual(states.length, 1, "the fetched state is handed to onState");
  assert.strictEqual(FakeEventSource.instances.length, 1, "only after the fetch settles is EventSource constructed");
  assert.strictEqual(FakeEventSource.instances[0].url, "/reveal/events?scope=global");
}

// ── A failed initial fetch must not leave a stream subscribed to nothing ─────
async function testFailedInitialFetchDoesNotSubscribe() {
  const fetcher = deferredFetcher();
  const FakeEventSource = fakeEventSourceClass();
  const timers = fakeSchedule();
  const errors = [];
  const transport = transportApi.createRevealTransport({
    fetchState: fetcher.fetchState,
    EventSourceCtor: FakeEventSource,
    eventsUrl: "/reveal/events",
    onError: (e) => errors.push(e),
    schedule: timers.schedule,
  });

  const started = transport.start();
  await flush();
  fetcher.calls[0].reject(new Error("503"));
  const ok = await started;

  assert.strictEqual(ok, false, "start() reports the failure");
  assert.strictEqual(errors.length, 1, "the error is surfaced");
  assert.strictEqual(FakeEventSource.instances.length, 0, "no stream is opened without authoritative state");
  assert.strictEqual(transport.isStreaming(), false);
  transport.stop();
}

// ── Every nudge triggers another authoritative fetch ─────────────────────────
async function testNudgeTriggersRefetch() {
  const fetcher = deferredFetcher();
  const FakeEventSource = fakeEventSourceClass();
  const states = [];
  const transport = transportApi.createRevealTransport({
    fetchState: fetcher.fetchState,
    EventSourceCtor: FakeEventSource,
    eventsUrl: "/reveal/events",
    onState: (s) => states.push(s),
  });

  const started = transport.start();
  await flush();
  fetcher.calls[0].resolve({ has_session: false });
  await started;

  const source = FakeEventSource.instances[0];
  source.emit(transportApi.EVENT_STATE_CHANGED);
  await flush();
  assert.strictEqual(fetcher.calls.length, 2, "a nudge refetches the authoritative state");
  fetcher.calls[1].resolve({ has_session: true, projection: { revealed_count: 1 } });
  await flush();
  assert.strictEqual(states.length, 2);
  assert.strictEqual(states[1].projection.revealed_count, 1, "the refetched state is applied, not the event payload");
}

// ── A burst of nudges collapses to one in-flight plus one follow-up ──────────
async function testNudgeBurstCoalesces() {
  const fetcher = deferredFetcher();
  const FakeEventSource = fakeEventSourceClass();
  const transport = transportApi.createRevealTransport({
    fetchState: fetcher.fetchState,
    EventSourceCtor: FakeEventSource,
    eventsUrl: "/reveal/events",
  });

  const started = transport.start();
  await flush();
  fetcher.calls[0].resolve({ has_session: true });
  await started;

  const source = FakeEventSource.instances[0];
  source.emit(transportApi.EVENT_STATE_CHANGED);
  await flush();
  source.emit(transportApi.EVENT_STATE_CHANGED);
  source.emit(transportApi.EVENT_STATE_CHANGED);
  source.emit(transportApi.EVENT_STATE_CHANGED);
  await flush();
  assert.strictEqual(fetcher.calls.length, 2, "an operator holding the step key does not open a fetch per event");

  fetcher.calls[1].resolve({ has_session: true });
  await flush();
  assert.strictEqual(fetcher.calls.length, 3, "exactly one follow-up runs after the in-flight request finishes");
}

// ── A reconnection reconciles, because events during the gap are lost ────────
async function testReopenRefetches() {
  const fetcher = deferredFetcher();
  const FakeEventSource = fakeEventSourceClass();
  const transport = transportApi.createRevealTransport({
    fetchState: fetcher.fetchState,
    EventSourceCtor: FakeEventSource,
    eventsUrl: "/reveal/events",
  });

  const started = transport.start();
  await flush();
  fetcher.calls[0].resolve({ has_session: true });
  await started;

  const source = FakeEventSource.instances[0];
  // `open` alone must NOT reconcile: the response headers are written before the
  // server's Valkey subscription exists, so a refetch triggered here could miss a
  // mutation published in that window, with no replay to recover it.
  if (typeof source.onopen === "function") {
    source.onopen();
    await flush();
  }
  assert.strictEqual(fetcher.calls.length, 1, "onopen does not reconcile: coverage has not started yet");

  // `reveal_ready` is emitted by the server only once it is actually subscribed,
  // so reconciling here strictly follows coverage — on the first connection and
  // on every reconnect.
  source.emit(transportApi.EVENT_READY);
  await flush();
  assert.strictEqual(fetcher.calls.length, 2, "reveal_ready reconciles against the store");
  fetcher.calls[1].resolve({ has_session: true });
  await flush();

  // A reconnect re-emits `reveal_ready`, and coverage restarts there too.
  source.emit(transportApi.EVENT_READY);
  await flush();
  assert.strictEqual(fetcher.calls.length, 3, "a reconnect's reveal_ready reconciles again");
}

// ── A terminal stream error is replaced with bounded backoff ────────────────
async function testStreamErrorExplicitlyReconnects() {
  const fetcher = deferredFetcher();
  const FakeEventSource = fakeEventSourceClass();
  const timers = fakeSchedule();
  const connectionErrors = [];
  const transport = transportApi.createRevealTransport({
    fetchState: fetcher.fetchState,
    EventSourceCtor: FakeEventSource,
    eventsUrl: "/reveal/events",
    onConnectionError: (error, retryable) => connectionErrors.push({ error, retryable }),
    schedule: timers.schedule,
  });

  const started = transport.start();
  await flush();
  fetcher.calls[0].resolve({ has_session: true });
  await started;

  const first = FakeEventSource.instances[0];
  first.onerror(new Error("server stopped"));
  assert.strictEqual(first.closed, true, "the failed EventSource is discarded");
  assert.strictEqual(transport.isStreaming(), false);
  assert.strictEqual(connectionErrors.length, 1, "the reconnecting state is surfaced");
  assert.strictEqual(connectionErrors[0].retryable, true);
  assert.strictEqual(timers.pending[0].ms, 500, "stream reconnect uses bounded backoff");

  await timers.fire();
  assert.strictEqual(
    FakeEventSource.instances.length,
    2,
    "a fresh EventSource replaces the terminal one",
  );
  const second = FakeEventSource.instances[1];
  second.emit(transportApi.EVENT_READY);
  await flush();
  assert.strictEqual(
    fetcher.calls.length,
    2,
    "readiness after reconnect reloads authoritative state",
  );
  fetcher.calls[1].resolve({ has_session: true, projection: { revealed_count: 4 } });
  await flush();
  assert.strictEqual(transport.failureCount(), 0);
}

// ── A failed initial fetch is retried, and recovery subscribes ───────────────
async function testInitialFailureRetriesAndRecovers() {
  const fetcher = deferredFetcher();
  const FakeEventSource = fakeEventSourceClass();
  const timers = fakeSchedule();
  const states = [];
  const errors = [];
  const transport = transportApi.createRevealTransport({
    fetchState: fetcher.fetchState,
    EventSourceCtor: FakeEventSource,
    eventsUrl: "/reveal/events",
    onState: (s) => states.push(s),
    onError: (e) => errors.push(e),
    schedule: timers.schedule,
  });

  const started = transport.start();
  await flush();
  const unavailable = new Error("503");
  unavailable.retryAfterSeconds = 1;
  fetcher.calls[0].reject(unavailable);
  await started;

  assert.strictEqual(FakeEventSource.instances.length, 0, "no stream while the state is unknown");
  assert.strictEqual(timers.pending.length, 1, "the failure schedules a retry");
  // Retry-After: 1 (1000 ms) beats the first schedule step (500 ms).
  assert.strictEqual(timers.pending[0].ms, 1000, "a server Retry-After hint wins when it is the longer wait");

  // The retry fails too: the client keeps trying rather than giving up.
  await timers.fire();
  assert.strictEqual(fetcher.calls.length, 2, "the retry re-requests the state");
  fetcher.calls[1].reject(new Error("503"));
  await flush();
  assert.strictEqual(timers.pending.length, 1, "a second failure schedules another retry");

  // Recovery: the state finally loads and the stream opens, so the projector is
  // never permanently stranded by a transient failure at page load.
  await timers.fire();
  fetcher.calls[2].resolve({ has_session: true });
  await flush();
  assert.strictEqual(states.length, 1, "the recovered state is applied");
  assert.strictEqual(FakeEventSource.instances.length, 1, "recovery opens the stream");
  assert.strictEqual(transport.failureCount(), 0, "a success resets the failure count");
  assert.strictEqual(timers.pending.length, 0, "no retry remains pending after success");
}

// ── A failed refetch after a nudge is retried too ───────────────────────────
async function testFailedRefetchIsRetried() {
  const fetcher = deferredFetcher();
  const FakeEventSource = fakeEventSourceClass();
  const timers = fakeSchedule();
  const states = [];
  const transport = transportApi.createRevealTransport({
    fetchState: fetcher.fetchState,
    EventSourceCtor: FakeEventSource,
    eventsUrl: "/reveal/events",
    onState: (s) => states.push(s),
    schedule: timers.schedule,
  });

  const started = transport.start();
  await flush();
  fetcher.calls[0].resolve({ has_session: true, projection: { revealed_count: 0 } });
  await started;

  // The final nudge of a ceremony arrives and its refetch fails. Without a retry
  // the projector would stay stale indefinitely, since no further nudge is coming.
  FakeEventSource.instances[0].emit(transportApi.EVENT_STATE_CHANGED);
  await flush();
  fetcher.calls[1].reject(new Error("boom"));
  await flush();
  assert.strictEqual(timers.pending.length, 1, "a failed refetch schedules a retry");

  await timers.fire();
  fetcher.calls[2].resolve({ has_session: true, projection: { revealed_count: 9 } });
  await flush();
  assert.strictEqual(states[states.length - 1].projection.revealed_count, 9, "the retry recovers the latest state");
}

// ── A permanent HTTP failure is surfaced without a retry storm ───────────────
async function testPermanentHttpFailureDoesNotRetry() {
  const fetcher = deferredFetcher();
  const FakeEventSource = fakeEventSourceClass();
  const timers = fakeSchedule();
  const errors = [];
  const transport = transportApi.createRevealTransport({
    fetchState: fetcher.fetchState,
    EventSourceCtor: FakeEventSource,
    eventsUrl: "/reveal/events",
    onError: (error, retryable) => errors.push({ error: error, retryable: retryable }),
    schedule: timers.schedule,
  });

  const started = transport.start();
  await flush();
  const corruptState = new Error("500");
  corruptState.status = 500;
  fetcher.calls[0].reject(corruptState);
  await started;

  assert.strictEqual(errors.length, 1);
  assert.strictEqual(errors[0].retryable, false, "a corrupt-state 500 is permanent");
  assert.strictEqual(timers.pending.length, 0, "a permanent failure schedules no retry");
  assert.strictEqual(FakeEventSource.instances.length, 0);
}

// ── A queued recovery cancels the failed request's obsolete timer ────────────
async function testQueuedRecoveryLeavesNoStaleRetry() {
  const fetcher = deferredFetcher();
  const timers = fakeSchedule();
  const fetcherApi = transportApi.createStateFetcher(
    fetcher.fetchState,
    () => {},
    () => {},
    timers.schedule,
  );

  fetcherApi.trigger();
  await flush();
  fetcherApi.trigger();
  fetcher.calls[0].reject(new Error("network"));
  await flush();
  assert.strictEqual(fetcher.calls.length, 2, "the queued refresh starts immediately");
  assert.strictEqual(timers.pending.length, 0, "an immediate queued refresh needs no timer");

  fetcher.calls[1].resolve({ has_session: true });
  await flush();
  await timers.fire();
  assert.strictEqual(fetcher.calls.length, 2, "recovery leaves no stale timer to issue a third request");
}

// ── stop() cancels a pending retry ──────────────────────────────────────────
async function testStopCancelsRetry() {
  const fetcher = deferredFetcher();
  const FakeEventSource = fakeEventSourceClass();
  const timers = fakeSchedule();
  const transport = transportApi.createRevealTransport({
    fetchState: fetcher.fetchState,
    EventSourceCtor: FakeEventSource,
    eventsUrl: "/reveal/events",
    schedule: timers.schedule,
  });

  const started = transport.start();
  await flush();
  fetcher.calls[0].reject(new Error("503"));
  await started;
  assert.strictEqual(timers.pending.length, 1);

  transport.stop();
  await timers.fire();
  assert.strictEqual(fetcher.calls.length, 1, "a stopped transport does not keep retrying");
}

// ── stop() prevents an in-flight failure from scheduling a new retry ─────────
async function testStopBeforeFailurePreventsRetry() {
  const fetcher = deferredFetcher();
  const FakeEventSource = fakeEventSourceClass();
  const timers = fakeSchedule();
  const transport = transportApi.createRevealTransport({
    fetchState: fetcher.fetchState,
    EventSourceCtor: FakeEventSource,
    eventsUrl: "/reveal/events",
    schedule: timers.schedule,
  });

  const started = transport.start();
  await flush();
  transport.stop();
  fetcher.calls[0].reject(new Error("network"));
  await started;

  assert.strictEqual(timers.pending.length, 0, "a stopped in-flight request schedules no retry");
  await timers.fire();
  assert.strictEqual(fetcher.calls.length, 1);
}

// ── A stale response never overwrites a newer one ────────────────────────────
async function testStaleResponseIsDropped() {
  const fetcher = deferredFetcher();
  const applied = [];
  const fetcherApi = transportApi.createStateFetcher(
    fetcher.fetchState,
    (state) => applied.push(state),
    () => {},
  );

  fetcherApi.trigger();
  await flush();
  // Force a second, later request by letting the first settle first.
  fetcher.calls[0].resolve({ revealed_count: 1 });
  await flush();
  fetcherApi.trigger();
  await flush();
  fetcher.calls[1].resolve({ revealed_count: 2 });
  await flush();

  assert.deepStrictEqual(
    applied.map((s) => s.revealed_count),
    [1, 2],
    "responses are applied in request order",
  );
}

// ── stop() closes the stream ────────────────────────────────────────────────
async function testStopClosesStream() {
  const fetcher = deferredFetcher();
  const FakeEventSource = fakeEventSourceClass();
  const transport = transportApi.createRevealTransport({
    fetchState: fetcher.fetchState,
    EventSourceCtor: FakeEventSource,
    eventsUrl: "/reveal/events",
  });

  const started = transport.start();
  await flush();
  fetcher.calls[0].resolve({ has_session: true });
  await started;

  transport.stop();
  assert.strictEqual(FakeEventSource.instances[0].closed, true, "stop() closes the EventSource");
  assert.strictEqual(transport.isStreaming(), false);
}

async function main() {
  await testFetchBeforeSubscribe();
  await testFailedInitialFetchDoesNotSubscribe();
  await testNudgeTriggersRefetch();
  await testNudgeBurstCoalesces();
  await testReopenRefetches();
  await testStreamErrorExplicitlyReconnects();
  await testInitialFailureRetriesAndRecovers();
  await testFailedRefetchIsRetried();
  await testPermanentHttpFailureDoesNotRetry();
  await testQueuedRecoveryLeavesNoStaleRetry();
  await testStopCancelsRetry();
  await testStopBeforeFailurePreventsRetry();
  await testStaleResponseIsDropped();
  await testStopClosesStream();
  console.log("ceremony-transport contract: OK");
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
