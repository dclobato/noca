//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"use strict";

const assert = require("assert");
const fs = require("fs");
const path = require("path");
const leaseApi = require(path.join(__dirname, "..", "..", "..", "animator", "static", "js", "control-lease.js"));

const URLS = {
  claim: "/lease/claim",
  heartbeat: "/lease/heartbeat",
  release: "/lease/release",
  takeover: "/lease/takeover",
};

function response(status, body) {
  return { ok: status < 300, status, json: () => Promise.resolve(body || {}) };
}

function eventTarget() {
  const listeners = {};
  return {
    visibilityState: "visible",
    addEventListener(type, listener) {
      listeners[type] = listener;
    },
    removeEventListener(type) {
      delete listeners[type];
    },
    dispatch(type) {
      listeners[type]();
    },
  };
}

function harness(plan) {
  const calls = [];
  const states = [];
  const timers = [];
  const visibility = eventTarget();
  const page = eventTarget();
  const client = leaseApi.createLeaseClient({
    urls: URLS,
    fetchImpl(url, init) {
      calls.push({ url, init });
      const queue = plan[url] || [];
      const next = queue.length > 1 ? queue.shift() : queue[0];
      return next instanceof Error ? Promise.reject(next) : Promise.resolve(next);
    },
    setTimeoutImpl(callback, delay) {
      const timer = { callback, delay, cleared: false };
      timers.push(timer);
      return timer;
    },
    clearTimeoutImpl(timer) {
      timer.cleared = true;
    },
    visibilityTarget: visibility,
    pageTarget: page,
    getVisibility: () => visibility.visibilityState,
    randomUUID: () => "controller-test-id",
    onState: (state) => states.push(state),
  });
  return { client, calls, states, timers, visibility, page };
}

const TIMING = { status: "claimed", lease_ttl_seconds: 45, heartbeat_interval_seconds: 10 };

async function testHeartbeatUsesReturnedSchedule() {
  const h = harness({
    [URLS.claim]: [response(200, TIMING)],
    [URLS.heartbeat]: [response(200, { ...TIMING, status: "renewed" })],
  });
  await h.client.claim("secret");
  assert.strictEqual(h.timers[0].delay, 10000);
  await h.timers[0].callback();
  assert.strictEqual(h.calls[1].url, URLS.heartbeat);
  assert.strictEqual(h.calls[1].init.headers.Authorization, "Bearer secret");
  assert.strictEqual(h.calls[1].init.headers[leaseApi.CONTROLLER_HEADER], "controller-test-id");
  assert.strictEqual(h.timers[1].delay, 10000);
}

async function testVisibilityAndPageShowRenewPromptly() {
  const h = harness({
    [URLS.claim]: [response(200, TIMING)],
    [URLS.heartbeat]: [response(200, { ...TIMING, status: "renewed" })],
  });
  await h.client.claim("secret");
  h.visibility.visibilityState = "hidden";
  h.visibility.dispatch("visibilitychange");
  assert.strictEqual(h.calls.length, 1, "hidden transitions do not renew");
  h.visibility.visibilityState = "visible";
  h.visibility.dispatch("visibilitychange");
  await new Promise((resolve) => setImmediate(resolve));
  h.page.dispatch("pageshow");
  await new Promise((resolve) => setImmediate(resolve));
  assert.strictEqual(h.calls.filter((call) => call.url === URLS.heartbeat).length, 2);
}

async function testPageHideReleasesWithKeepalive() {
  const h = harness({
    [URLS.claim]: [response(200, TIMING)],
    [URLS.release]: [response(200, { ...TIMING, status: "released" })],
  });
  await h.client.claim("secret");
  h.page.dispatch("pagehide");
  await new Promise((resolve) => setImmediate(resolve));
  const release = h.calls.find((call) => call.url === URLS.release);
  assert.ok(release);
  assert.strictEqual(release.init.keepalive, true);
  assert.strictEqual(h.client.isActive(), false);
}

async function testLeaseLossStopsHeartbeat() {
  const h = harness({
    [URLS.claim]: [response(200, TIMING)],
    [URLS.heartbeat]: [response(409, { detail: "This controller no longer owns the ceremony." })],
  });
  await h.client.claim("secret");
  await h.timers[0].callback();
  assert.strictEqual(h.states[h.states.length - 1], "lease-lost");
  assert.strictEqual(h.client.isActive(), false);
  assert.strictEqual(h.client.controllerHeader(), null);
}

async function testTransientBlipRetriesInsideTheLeaseTtl() {
  const h = harness({
    [URLS.claim]: [response(200, TIMING)],
    [URLS.heartbeat]: [
      response(503, { detail: "store unavailable" }),
      response(200, { ...TIMING, status: "renewed" }),
    ],
  });
  await h.client.claim("secret");
  assert.strictEqual(h.timers[0].delay, 10000);

  // First miss: fail closed (no command authority) but stay inside the TTL —
  // retry ~TTL/3 later rather than demanding a manual recovery click.
  await h.timers[0].callback();
  assert.strictEqual(h.states[h.states.length - 1], "pending");
  assert.strictEqual(h.timers[1].delay, 15000, "retry is scheduled inside the lease window");
  assert.strictEqual(h.calls.filter((call) => call.url === URLS.heartbeat).length, 1);

  // The retried renewal succeeds: back to active on the normal cadence.
  await h.timers[1].callback();
  assert.strictEqual(h.states[h.states.length - 1], "active");
  assert.strictEqual(h.timers[2].delay, 10000);
}

async function testConsecutiveBlipsEventuallySurfaceUnavailability() {
  const h = harness({
    [URLS.claim]: [response(200, TIMING)],
    [URLS.heartbeat]: [
      response(503, {}),
      response(503, {}),
      response(503, {}),
    ],
  });
  await h.client.claim("secret");
  await h.timers[0].callback();
  await h.timers[1].callback();
  assert.notStrictEqual(h.states[h.states.length - 1], "unavailable", "one miss inside the budget is not fatal");
  await h.timers[2].callback();
  assert.strictEqual(h.states[h.states.length - 1], "unavailable", "the budget is exhausted honestly");
}

async function testBfcacheRestoreReclaimsAfterPagehideRelease() {
  const h = harness({
    [URLS.claim]: [response(200, TIMING), response(200, TIMING)],
    [URLS.release]: [response(200, { ...TIMING, status: "released" })],
  });
  await h.client.claim("secret");
  h.page.dispatch("pagehide");
  await new Promise((resolve) => setImmediate(resolve));
  assert.strictEqual(h.client.isActive(), false);

  // Restoring from the back-forward cache: the credential is still in
  // memory, so the panel claims again instead of sitting on "released" with
  // no visible way back.
  h.page.dispatch("pageshow");
  await new Promise((resolve) => setImmediate(resolve));
  const claims = h.calls.filter((call) => call.url === URLS.claim);
  assert.strictEqual(claims.length, 2, "pageshow re-claims after a pagehide release");
  assert.strictEqual(h.states[h.states.length - 1], "active");
}

async function testNonJsonSuccessFallsBackToDefaultTimings() {
  const h = harness({
    [URLS.claim]: [{ ok: true, status: 200, json: () => Promise.resolve(null) }],
  });
  await h.client.claim("secret");
  assert.strictEqual(h.states[h.states.length - 1], "active");
  assert.strictEqual(h.client.timings().heartbeatIntervalSeconds, leaseApi.DEFAULT_HEARTBEAT_SECONDS);
}

function testNoPersistentIdentityStorage() {
  const source = fs.readFileSync(
    path.join(__dirname, "..", "..", "..", "animator", "static", "js", "control-lease.js"),
    "utf8",
  );
  const code = source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
  ["localStorage", "sessionStorage", "document.cookie"].forEach((name) => {
    assert.strictEqual(code.includes(name), false, "controller identity must not use " + name);
  });
}

(async function main() {
  await testHeartbeatUsesReturnedSchedule();
  await testVisibilityAndPageShowRenewPromptly();
  await testPageHideReleasesWithKeepalive();
  await testLeaseLossStopsHeartbeat();
  await testTransientBlipRetriesInsideTheLeaseTtl();
  await testConsecutiveBlipsEventuallySurfaceUnavailability();
  await testBfcacheRestoreReclaimsAfterPagehideRelease();
  await testNonJsonSuccessFallsBackToDefaultTimings();
  testNoPersistentIdentityStorage();
  console.log("control lease contract: OK");
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
