// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 The NOCA Authors (see AUTHORS)
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const scriptPath = path.resolve(
  __dirname,
  "../../../shared/static/js/submission-status-watcher.js",
);
const script = fs.readFileSync(scriptPath, "utf8");

function flushPromises() {
  return new Promise((resolve) => setImmediate(resolve));
}

// Builds a vm context with controllable fetch/SSE/timers and loads the module.
// `respond` receives (url, requestIndex) and returns a Response-ish object or a
// promise for one, so a test can hold a reply pending mid-flight.
function buildHarness({ respond, withEventSource = true, withAbortController = true } = {}) {
  const requests = [];
  const eventSources = [];
  const intervals = [];
  const timeouts = [];
  const listeners = new Map();
  const errors = [];

  let timerId = 0;
  const liveTimeouts = new Map();
  const liveIntervals = new Map();

  const context = {
    console: { error: (...args) => errors.push(args) },
    Set,
    Map,
    URLSearchParams,
    Error,
    Array,
    fetch: (url, init) => {
      const index = requests.length;
      requests.push({ url, init });
      return Promise.resolve(respond(url, index));
    },
    setInterval: (callback, milliseconds) => {
      timerId += 1;
      intervals.push({ id: timerId, callback, milliseconds });
      liveIntervals.set(timerId, callback);
      return timerId;
    },
    clearInterval: (id) => liveIntervals.delete(id),
    setTimeout: (callback, milliseconds) => {
      timerId += 1;
      timeouts.push({ id: timerId, callback, milliseconds });
      liveTimeouts.set(timerId, callback);
      return timerId;
    },
    clearTimeout: (id) => liveTimeouts.delete(id),
    window: {
      addEventListener: (name, callback) => listeners.set(name, callback),
    },
  };
  if (withEventSource) {
    context.EventSource = class {
      constructor(url) {
        this.url = url;
        this.closed = false;
        this.onopen = null;
        this.onmessage = null;
        eventSources.push(this);
      }
      close() {
        this.closed = true;
      }
    };
  }
  if (withAbortController) {
    context.AbortController = AbortController;
  }

  vm.createContext(context);
  vm.runInContext(script, context);

  return {
    context,
    requests,
    eventSources,
    intervals,
    timeouts,
    listeners,
    errors,
    // Fires a pending debounce timer (the module keeps at most one).
    runDebounce() {
      const pending = timeouts.filter((entry) => liveTimeouts.has(entry.id));
      pending.forEach((entry) => {
        liveTimeouts.delete(entry.id);
        entry.callback();
      });
      return pending.length;
    },
    runPoll() {
      const live = [...liveIntervals.values()];
      live.forEach((callback) => callback());
      return live.length;
    },
    pollIsLive: () => liveIntervals.size > 0,
    watch(options) {
      return context.window.NocaSubmissionStatusWatcher.watch(options);
    },
  };
}

function snapshotResponse(submissions) {
  return { ok: true, status: 200, json: async () => ({ submissions }) };
}

const PENDING_ROW = {
  submission_id: "sub-1",
  is_final: false,
  status: "JUDGING",
  verdict: null,
  max_wall_time_ms: null,
};

const FINAL_ROW = {
  submission_id: "sub-1",
  is_final: true,
  status: "DONE",
  verdict: "AC",
  max_wall_time_ms: 42,
};

function baseOptions(overrides) {
  return Object.assign(
    {
      statusUrl: "/user/submissions/status.json",
      eventsUrl: "/user/submissions/status/events",
      ids: ["sub-1"],
      onSnapshot: () => true,
    },
    overrides,
  );
}

test("an SSE refresh is debounced into a single reconcile", async () => {
  const seen = [];
  const harness = buildHarness({ respond: () => snapshotResponse([PENDING_ROW]) });
  harness.watch(baseOptions({ onSnapshot: (row) => { seen.push(row.status); return true; } }));

  const source = harness.eventSources[0];
  source.onmessage({ data: "refresh" });
  source.onmessage({ data: "refresh" });
  source.onmessage({ data: "refresh" });

  assert.equal(harness.requests.length, 0, "reconcile is deferred by the debounce");
  assert.equal(harness.runDebounce(), 1, "a burst collapses into one pending timer");
  await flushPromises();
  await flushPromises();

  assert.equal(harness.requests.length, 1);
  assert.deepEqual(seen, ["JUDGING"]);
  assert.equal(source.closed, false);
});

test("a non-refresh SSE message never reconciles", async () => {
  const harness = buildHarness({ respond: () => snapshotResponse([PENDING_ROW]) });
  harness.watch(baseOptions());

  harness.eventSources[0].onmessage({ data: "ping" });
  harness.runDebounce();
  await flushPromises();

  assert.equal(harness.requests.length, 0);
});

test("401 and 403 tear down the stream and the poll", async () => {
  for (const status of [401, 403]) {
    const harness = buildHarness({ respond: () => ({ ok: false, status }) });
    harness.watch(baseOptions());

    harness.runPoll();
    await flushPromises();
    await flushPromises();

    assert.equal(harness.eventSources[0].closed, true, `status ${status} closes SSE`);
    assert.equal(harness.pollIsLive(), false, `status ${status} clears the poll`);
    assert.deepEqual(harness.errors, [], `status ${status} is not an error`);
  }
});

test("a non-OK response is logged and retried on the next tick", async () => {
  let calls = 0;
  const harness = buildHarness({
    respond: () => {
      calls += 1;
      return calls === 1 ? { ok: false, status: 500 } : snapshotResponse([PENDING_ROW]);
    },
  });
  harness.watch(baseOptions());

  harness.runPoll();
  await flushPromises();
  await flushPromises();

  assert.equal(harness.errors.length, 1);
  assert.equal(harness.errors[0][0], "Submission status refresh failed");
  assert.equal(harness.pollIsLive(), true, "a server error keeps watching");

  harness.runPoll();
  await flushPromises();
  await flushPromises();

  assert.equal(harness.requests.length, 2);
  assert.equal(harness.errors.length, 1);
});

test("the in-flight guard prevents overlapping reconciles", async () => {
  let release;
  const held = new Promise((resolve) => { release = resolve; });
  const harness = buildHarness({
    respond: (_url, index) => (index === 0 ? held : snapshotResponse([PENDING_ROW])),
  });
  harness.watch(baseOptions());

  harness.runPoll();
  await flushPromises();
  harness.runPoll();
  harness.runPoll();
  await flushPromises();

  assert.equal(harness.requests.length, 1, "no second fetch while one is in flight");

  release(snapshotResponse([PENDING_ROW]));
  await flushPromises();
  await flushPromises();

  harness.runPoll();
  await flushPromises();
  assert.equal(harness.requests.length, 2, "the guard clears once the fetch settles");
});

test("dropping the last id tears down both SSE and the poll", async () => {
  const harness = buildHarness({ respond: () => snapshotResponse([FINAL_ROW]) });
  harness.watch(baseOptions({ onSnapshot: () => false }));

  harness.runPoll();
  await flushPromises();
  await flushPromises();

  assert.equal(harness.eventSources[0].closed, true);
  assert.equal(harness.pollIsLive(), false);
});

test("only an explicit false drops an id", async () => {
  const harness = buildHarness({ respond: () => snapshotResponse([FINAL_ROW]) });
  // A consumer that forgets its `return` must not silently stop updates.
  harness.watch(baseOptions({ onSnapshot: () => undefined }));

  harness.runPoll();
  await flushPromises();
  await flushPromises();

  assert.equal(harness.pollIsLive(), true, "undefined keeps watching");
  assert.equal(harness.eventSources[0].closed, false);
});

test("a shrinking multi-id set keeps watching the remainder", async () => {
  const rows = [
    { submission_id: "sub-1", is_final: true, verdict: "AC" },
    { submission_id: "sub-2", is_final: false, status: "JUDGING" },
  ];
  const harness = buildHarness({ respond: () => snapshotResponse(rows) });
  harness.watch(
    baseOptions({ ids: ["sub-1", "sub-2"], onSnapshot: (row) => !row.is_final }),
  );

  assert.match(harness.eventSources[0].url, /ids=sub-1%2Csub-2/);

  harness.runPoll();
  await flushPromises();
  await flushPromises();

  assert.equal(harness.pollIsLive(), true, "sub-2 is still pending");
  harness.runPoll();
  await flushPromises();
  assert.match(harness.requests[1].url, /ids=sub-2$/, "the finalized id is dropped from ?ids=");
});

test("poll-only mode still reconciles and still resolves", async () => {
  const seen = [];
  const harness = buildHarness({
    respond: () => snapshotResponse([FINAL_ROW]),
    withEventSource: false,
  });
  harness.watch(
    baseOptions({
      onSnapshot: (row) => {
        seen.push(row.verdict);
        return !row.is_final;
      },
    }),
  );

  // connect() reconciles immediately rather than waiting a full interval.
  await flushPromises();
  await flushPromises();

  assert.equal(harness.requests.length, 1);
  assert.deepEqual(seen, ["AC"], "the final verdict is applied without any SSE");
  assert.equal(harness.pollIsLive(), false, "and the watch resolves, so polling stops");
});

test("stop() during an in-flight reconcile suppresses the callback", async () => {
  let release;
  const held = new Promise((resolve) => { release = resolve; });
  const seen = [];
  const harness = buildHarness({ respond: () => held });
  const handle = harness.watch(
    baseOptions({ onSnapshot: (row) => { seen.push(row); return true; } }),
  );

  harness.runPoll();
  await flushPromises();
  assert.equal(harness.requests.length, 1);

  handle.stop();
  release(snapshotResponse([FINAL_ROW]));
  await flushPromises();
  await flushPromises();

  assert.deepEqual(seen, [], "no snapshot callback runs after stop()");
  assert.equal(harness.pollIsLive(), false);
  assert.deepEqual(harness.errors, [], "the aborted fetch is not logged as a failure");

  harness.runPoll();
  await flushPromises();
  assert.equal(harness.requests.length, 1, "no further requests after stop()");
});

test("pagehide during an in-flight reconcile suppresses the callback", async () => {
  let release;
  const held = new Promise((resolve) => { release = resolve; });
  const seen = [];
  const harness = buildHarness({ respond: () => held });
  harness.watch(baseOptions({ onSnapshot: (row) => { seen.push(row); return true; } }));

  harness.runPoll();
  await flushPromises();

  const pagehide = harness.listeners.get("pagehide");
  assert.equal(typeof pagehide, "function", "the module registers a pagehide teardown");
  pagehide();

  release(snapshotResponse([FINAL_ROW]));
  await flushPromises();
  await flushPromises();

  assert.deepEqual(seen, []);
  assert.equal(harness.eventSources[0].closed, true);
  assert.equal(harness.pollIsLive(), false);
});

test("teardown mid-flight is safe without AbortController", async () => {
  let release;
  const held = new Promise((resolve) => { release = resolve; });
  const seen = [];
  const harness = buildHarness({ respond: () => held, withAbortController: false });
  const handle = harness.watch(
    baseOptions({ onSnapshot: (row) => { seen.push(row); return true; } }),
  );

  harness.runPoll();
  await flushPromises();
  assert.equal(harness.requests[0].init.signal, undefined, "no signal is sent");

  handle.stop();
  release(snapshotResponse([FINAL_ROW]));
  await flushPromises();
  await flushPromises();

  assert.deepEqual(seen, [], "the post-await stopped re-check is the sole guard here");
});

test("an empty or malformed watch request does nothing", () => {
  const harness = buildHarness({ respond: () => snapshotResponse([]) });

  const empty = harness.watch(baseOptions({ ids: [] }));
  const noUrl = harness.watch(baseOptions({ statusUrl: "" }));
  const noCallback = harness.watch(baseOptions({ onSnapshot: null }));

  assert.equal(harness.eventSources.length, 0);
  assert.equal(harness.intervals.length, 0);
  assert.equal(harness.requests.length, 0);
  // The returned handle is still safe to call.
  empty.stop();
  noUrl.stop();
  noCallback.stop();
});
