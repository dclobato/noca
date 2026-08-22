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

// The consumer is meaningless without the shared engine it delegates to, so
// both are loaded into one context exactly as the page loads them.
const corePath = path.resolve(
  __dirname,
  "../../../shared/static/js/submission-status-watcher.js",
);
const coreScript = fs.readFileSync(corePath, "utf8");
const scriptPath = path.resolve(
  __dirname,
  "../../../arena/static/js/submission-detail-live.js",
);
const script = fs.readFileSync(scriptPath, "utf8");

// Timers the core scheduled (its reconcile debounce). Drained by flushPromises
// so tests read as "let everything settle", as they did before the extraction.
let pendingTimeouts = [];

function flushPromises() {
  const due = pendingTimeouts;
  pendingTimeouts = [];
  due.forEach((entry) => entry.callback());
  return new Promise((resolve) => setImmediate(resolve));
}

function makeElement(initialClasses = []) {
  const classes = new Set(initialClasses);
  return {
    children: [],
    className: "",
    classList: {
      add: (...names) => names.forEach((name) => classes.add(name)),
      contains: (name) => classes.has(name),
      remove: (...names) => names.forEach((name) => classes.delete(name)),
    },
    removed: false,
    replacedWith: null,
    textContent: "",
    remove() {
      this.removed = true;
    },
    replaceChildren(...children) {
      this.children = children;
    },
    replaceWith(element) {
      this.replacedWith = element;
    },
  };
}

function buildHarness({ confetti = true, root = true, snapshots = [] } = {}) {
  const celebrations = [];
  const requests = [];
  const eventSources = [];
  const intervals = [];
  const listeners = new Map();
  const summary = makeElement(["arena-submission-verdict-summary", "is-pending"]);
  const verdictCode = makeElement();
  const verdictLabel = makeElement();
  const verdictStatus = makeElement();
  const wallTime = makeElement();
  const currentResultCard = makeElement();
  const refreshedResultCard = makeElement();
  verdictLabel.textContent = "Pending judgment";
  verdictStatus.textContent = "Current status: QUEUED";
  wallTime.textContent = "\u2014";

  const selectors = new Map([
    ["[data-live-verdict-summary]", summary],
    ["[data-live-verdict-code]", verdictCode],
    ["[data-live-verdict-label]", verdictLabel],
    ["[data-live-verdict-status]", verdictStatus],
    ["[data-live-wall-time]", wallTime],
  ]);
  const rootElement = {
    dataset: {
      submissionId: "submission-1",
      statusUrl: "/user/submissions/status.json",
      eventsUrl: "/user/submissions/status/events",
    },
    querySelector: (selector) => selectors.get(selector) || null,
  };
  let snapshotIndex = 0;
  let timerId = 0;
  const liveIntervals = new Map();
  pendingTimeouts = [];

  class MockEventSource {
    constructor(url) {
      this.url = url;
      this.closed = false;
      this.onmessage = null;
      eventSources.push(this);
    }

    close() {
      this.closed = true;
    }
  }

  const context = vm.createContext({
    DOMParser: class {
      parseFromString() {
        return {
          querySelector: (selector) => (
            selector === "[data-submission-result-card]" ? refreshedResultCard : null
          ),
        };
      }
    },
    URLSearchParams,
    console,
    AbortController,
    clearInterval: (id) => liveIntervals.delete(id),
    setTimeout: (callback) => {
      timerId += 1;
      pendingTimeouts.push({ id: timerId, callback });
      return timerId;
    },
    clearTimeout: (id) => {
      pendingTimeouts = pendingTimeouts.filter((entry) => entry.id !== id);
    },
    document: {
      createElement: () => makeElement(),
      querySelector: (selector) => {
        if (selector === "[data-submission-result-card]") return currentResultCard;
        return root ? rootElement : null;
      },
    },
    EventSource: MockEventSource,
    fetch: async (url, options) => {
      requests.push({ url, options });
      if (url === "http://test/submissions/submission-1") {
        return {
          ok: true,
          status: 200,
          text: async () => "<section data-submission-result-card></section>",
        };
      }
      const snapshot = snapshots[Math.min(snapshotIndex, snapshots.length - 1)];
      snapshotIndex += 1;
      return {
        ok: true,
        status: 200,
        json: async () => ({ submissions: snapshot ? [snapshot] : [] }),
      };
    },
    setInterval: (callback, milliseconds) => {
      timerId += 1;
      intervals.push({ id: timerId, callback, milliseconds });
      liveIntervals.set(timerId, callback);
      return timerId;
    },
    window: {
      ...(confetti ? {
        NocaConfetti: {
          celebrate: (key) => celebrations.push(key),
        },
      } : {}),
      addEventListener: (name, callback) => listeners.set(name, callback),
      location: { href: "http://test/submissions/submission-1" },
    },
  });

  vm.runInContext(coreScript, context);
  vm.runInContext(script, context);
  return {
    pollIsLive: () => liveIntervals.size > 0,
    celebrations,
    eventSources,
    intervals,
    listeners,
    requests,
    currentResultCard,
    refreshedResultCard,
    summary,
    verdictCode,
    verdictLabel,
    verdictStatus,
    wallTime,
  };
}

test("renders an intermediate snapshot when the SSE connection opens", async () => {
  const harness = buildHarness({
    snapshots: [{
      submission_id: "submission-1",
      is_final: false,
      status: "JUDGING",
      verdict: null,
      max_wall_time_ms: null,
    }],
  });
  const source = harness.eventSources[0];

  source.onopen();
  await flushPromises();
  await flushPromises();

  assert.equal(harness.eventSources.length, 1);
  assert.equal(harness.requests.length, 1);
  assert.equal(harness.verdictStatus.textContent, "Current status: JUDGING");
  assert.equal(harness.verdictLabel.textContent, "Pending judgment");
  assert.deepEqual(harness.celebrations, []);
  assert.equal(source.closed, false);
});

test("polling renders intermediate status changes", async () => {
  const harness = buildHarness({
    snapshots: [{
      submission_id: "submission-1",
      is_final: false,
      status: "DISPATCHED",
      verdict: null,
      max_wall_time_ms: null,
    }],
  });

  assert.equal(harness.intervals[0].milliseconds, 2500);
  harness.intervals[0].callback();
  await flushPromises();
  await flushPromises();

  assert.equal(harness.requests.length, 1);
  assert.equal(harness.verdictStatus.textContent, "Current status: DISPATCHED");
  assert.deepEqual(harness.celebrations, []);
});

test("live status does not depend on the optional confetti helper", async () => {
  const harness = buildHarness({
    confetti: false,
    snapshots: [{
      submission_id: "submission-1",
      is_final: false,
      status: "JUDGING",
      verdict: null,
      max_wall_time_ms: null,
    }],
  });

  harness.eventSources[0].onopen();
  await flushPromises();
  await flushPromises();

  assert.equal(harness.verdictStatus.textContent, "Current status: JUDGING");
  assert.deepEqual(harness.celebrations, []);
});

test("an SSE-confirmed AC updates the verdict and celebrates", async () => {
  const harness = buildHarness({
    snapshots: [{
      submission_id: "submission-1",
      is_final: true,
      status: "DONE",
      verdict: "AC",
      verdict_label: "Accepted",
      verdict_badge_class: "text-bg-success",
      max_wall_time_ms: 42,
    }],
  });
  const source = harness.eventSources[0];

  source.onmessage({ data: "refresh" });
  await flushPromises();
  await flushPromises();

  assert.deepEqual(harness.celebrations, ["submission-1"]);
  assert.equal(harness.verdictLabel.textContent, "Accepted");
  assert.equal(harness.wallTime.textContent, "42 ms");
  assert.equal(harness.verdictStatus.removed, true);
  assert.equal(harness.summary.classList.contains("is-accepted"), true);
  assert.equal(harness.verdictCode.children[0].textContent, "AC");
  assert.equal(harness.currentResultCard.replacedWith, harness.refreshedResultCard);
  assert.equal(source.closed, true);
});

test("a polled AC resolves and celebrates without SSE", async () => {
  // The snapshot endpoint is the sole data source: a final verdict seen by the
  // fallback poll resolves the watch on its own. Previously an `sseRefreshObserved`
  // gate required a verdict `refresh` frame first, so a page whose SSE never
  // delivered one rendered the verdict but polled forever and never celebrated.
  const accepted = {
    submission_id: "submission-1",
    is_final: true,
    status: "DONE",
    verdict: "AC",
    verdict_label: "Accepted",
    verdict_badge_class: "text-bg-success",
    max_wall_time_ms: 42,
  };
  const harness = buildHarness({ snapshots: [accepted, accepted] });
  const source = harness.eventSources[0];

  harness.intervals[0].callback();
  await flushPromises();
  await flushPromises();

  assert.equal(harness.verdictLabel.textContent, "Accepted");
  assert.deepEqual(harness.celebrations, ["submission-1"]);
  assert.equal(source.closed, true);
  assert.equal(harness.pollIsLive(), false, "the poll stops once the verdict is final");
});

test("an SSE-confirmed non-AC verdict never celebrates", async () => {
  const harness = buildHarness({
    snapshots: [{
      submission_id: "submission-1",
      is_final: true,
      status: "DONE",
      verdict: "WA",
      verdict_label: "Wrong Answer",
      verdict_badge_class: "text-bg-danger",
      max_wall_time_ms: 18,
    }],
  });
  const source = harness.eventSources[0];

  source.onmessage({ data: "refresh" });
  await flushPromises();
  await flushPromises();

  assert.equal(harness.verdictLabel.textContent, "Wrong Answer");
  assert.equal(harness.summary.classList.contains("is-rejected"), true);
  assert.deepEqual(harness.celebrations, []);
  assert.equal(source.closed, true);
});

test("does nothing when the owner-gated live marker is absent", () => {
  const harness = buildHarness({ root: false });

  assert.equal(harness.eventSources.length, 0);
  assert.equal(harness.intervals.length, 0);
  assert.equal(harness.requests.length, 0);
  assert.deepEqual(harness.celebrations, []);
});
