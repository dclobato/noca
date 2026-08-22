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
  "../../../arena/static/js/profile-submissions-live.js",
);
const script = fs.readFileSync(scriptPath, "utf8");

let pendingTimeouts = [];

function flushPromises() {
  const due = pendingTimeouts;
  pendingTimeouts = [];
  due.forEach((entry) => entry.callback());
  return new Promise((resolve) => setImmediate(resolve));
}

// Minimal stand-in for the div the script uses to entity-escape text.
function makeEscapingDiv() {
  return {
    textContent: "",
    get innerHTML() {
      return String(this.textContent)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;");
    },
  };
}

function makeRow(submissionId, { final = false } = {}) {
  const badge = { innerHTML: "" };
  const runtime = { innerHTML: "" };
  return {
    dataset: { submissionId, final: final ? "true" : "false" },
    badge,
    runtime,
    querySelector: (selector) => {
      if (selector === ".js-verdict-badge") return badge;
      if (selector === ".js-submission-runtime") return runtime;
      return null;
    },
  };
}

function buildHarness({ rows, snapshots, confetti = true, section = true } = {}) {
  const celebrations = [];
  const requests = [];
  const eventSources = [];
  const intervals = [];
  const listeners = new Map();
  let snapshotIndex = 0;
  let timerId = 0;
  const liveIntervals = new Map();
  pendingTimeouts = [];

  const sectionElement = {
    dataset: {
      statusUrl: "/user/submissions/status.json",
      eventsUrl: "/user/submissions/status/events",
    },
  };

  const context = vm.createContext({
    URLSearchParams,
    console,
    AbortController,
    document: {
      createElement: () => makeEscapingDiv(),
      querySelector: (selector) => {
        if (selector === ".arena-progress-list[data-status-url]") {
          return section ? sectionElement : null;
        }
        return null;
      },
      querySelectorAll: (selector) => (
        selector === "tr[data-submission-id]" ? rows : []
      ),
    },
    EventSource: class {
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
    },
    fetch: async (url, options) => {
      requests.push({ url, options });
      const snapshot = snapshots[Math.min(snapshotIndex, snapshots.length - 1)];
      snapshotIndex += 1;
      return {
        ok: true,
        status: 200,
        json: async () => ({ submissions: snapshot || [] }),
      };
    },
    setInterval: (callback, milliseconds) => {
      timerId += 1;
      intervals.push({ id: timerId, callback, milliseconds });
      liveIntervals.set(timerId, callback);
      return timerId;
    },
    clearInterval: (id) => liveIntervals.delete(id),
    setTimeout: (callback) => {
      timerId += 1;
      pendingTimeouts.push({ id: timerId, callback });
      return timerId;
    },
    clearTimeout: (id) => {
      pendingTimeouts = pendingTimeouts.filter((entry) => entry.id !== id);
    },
    window: {
      ...(confetti ? {
        NocaConfetti: { celebrate: (key) => celebrations.push(key) },
      } : {}),
      addEventListener: (name, callback) => listeners.set(name, callback),
    },
  });

  vm.runInContext(coreScript, context);
  vm.runInContext(script, context);

  return {
    celebrations,
    eventSources,
    intervals,
    listeners,
    requests,
    pollIsLive: () => liveIntervals.size > 0,
  };
}

test("an intermediate snapshot updates the row's badge and runtime", async () => {
  const row = makeRow("sub-1");
  const harness = buildHarness({
    rows: [row],
    snapshots: [[{
      submission_id: "sub-1",
      is_final: false,
      status: "JUDGING",
      verdict: null,
      max_wall_time_ms: null,
    }]],
  });

  harness.eventSources[0].onopen();
  await flushPromises();
  await flushPromises();

  assert.equal(
    row.badge.innerHTML,
    '<span class="badge bg-secondary font-monospace">JUDGING</span>',
  );
  assert.equal(row.runtime.innerHTML, "&mdash;");
  assert.equal(row.dataset.final, "false", "a pending row stays watched");
  assert.deepEqual(harness.celebrations, []);
  assert.equal(harness.pollIsLive(), true);
});

test("a final AC marks the row, celebrates, and tears down", async () => {
  const row = makeRow("sub-1");
  const harness = buildHarness({
    rows: [row],
    snapshots: [[{
      submission_id: "sub-1",
      is_final: true,
      status: "DONE",
      verdict: "AC",
      verdict_label: "Accepted",
      verdict_badge_class: "text-bg-success",
      max_wall_time_ms: 42,
    }]],
  });

  assert.equal(harness.intervals[0].milliseconds, 2500);
  harness.intervals[0].callback();
  await flushPromises();
  await flushPromises();

  assert.equal(
    row.badge.innerHTML,
    '<span class="badge text-bg-success">Accepted</span>',
  );
  assert.equal(row.runtime.innerHTML, "42 ms");
  assert.equal(row.dataset.final, "true");
  assert.deepEqual(harness.celebrations, ["sub-1"]);
  assert.equal(harness.eventSources[0].closed, true);
  assert.equal(harness.pollIsLive(), false, "the last watched row resolved");
});

test("a final non-AC verdict never celebrates", async () => {
  const row = makeRow("sub-1");
  const harness = buildHarness({
    rows: [row],
    snapshots: [[{
      submission_id: "sub-1",
      is_final: true,
      status: "DONE",
      verdict: "WA",
      verdict_label: "Wrong Answer",
      verdict_badge_class: "text-bg-danger",
      max_wall_time_ms: 18,
    }]],
  });

  harness.intervals[0].callback();
  await flushPromises();
  await flushPromises();

  assert.equal(
    row.badge.innerHTML,
    '<span class="badge text-bg-danger">Wrong Answer</span>',
  );
  assert.equal(row.dataset.final, "true");
  assert.deepEqual(harness.celebrations, []);
  assert.equal(harness.eventSources[0].closed, true);
});

test("live updates do not depend on the optional confetti helper", async () => {
  const row = makeRow("sub-1");
  const harness = buildHarness({
    confetti: false,
    rows: [row],
    snapshots: [[{
      submission_id: "sub-1",
      is_final: true,
      status: "DONE",
      verdict: "AC",
      verdict_label: "Accepted",
      verdict_badge_class: "text-bg-success",
      max_wall_time_ms: 42,
    }]],
  });

  harness.intervals[0].callback();
  await flushPromises();
  await flushPromises();

  assert.equal(row.dataset.final, "true");
  assert.deepEqual(harness.celebrations, []);
  assert.equal(harness.eventSources[0].closed, true);
});

test("only finalized rows leave the watch set", async () => {
  const finished = makeRow("sub-1");
  const pending = makeRow("sub-2");
  const harness = buildHarness({
    rows: [finished, pending],
    snapshots: [[
      { submission_id: "sub-1", is_final: true, verdict: "AC", max_wall_time_ms: 7 },
      { submission_id: "sub-2", is_final: false, status: "QUEUED", max_wall_time_ms: null },
    ]],
  });

  assert.match(harness.eventSources[0].url, /ids=sub-1%2Csub-2/);

  harness.intervals[0].callback();
  await flushPromises();
  await flushPromises();

  assert.equal(finished.dataset.final, "true");
  assert.equal(pending.dataset.final, "false");
  assert.deepEqual(harness.celebrations, ["sub-1"]);
  assert.equal(harness.pollIsLive(), true, "sub-2 is still pending");

  harness.intervals[0].callback();
  await flushPromises();
  assert.match(harness.requests[1].url, /ids=sub-2$/);
});

test("rows already final at render are never watched", () => {
  const harness = buildHarness({
    rows: [makeRow("sub-1", { final: true })],
    snapshots: [[]],
  });

  assert.equal(harness.eventSources.length, 0);
  assert.equal(harness.intervals.length, 0);
  assert.equal(harness.requests.length, 0);
});

test("does nothing when the submissions section is absent", () => {
  const harness = buildHarness({ section: false, rows: [makeRow("sub-1")], snapshots: [[]] });

  assert.equal(harness.eventSources.length, 0);
  assert.equal(harness.intervals.length, 0);
  assert.equal(harness.requests.length, 0);
});
