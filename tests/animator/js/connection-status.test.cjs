//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"use strict";

const assert = require("assert");
const path = require("path");
const connection = require(
  path.join(
    __dirname,
    "..",
    "..",
    "..",
    "animator",
    "static",
    "js",
    "animator-connection-status.js",
  ),
);

function fakeElement() {
  const attributes = {};
  return {
    textContent: "",
    attributes: attributes,
    setAttribute: (name, value) => {
      attributes[name] = value;
    },
    removeAttribute: (name) => {
      delete attributes[name];
    },
  };
}

function testFormatting() {
  assert.strictEqual(connection.formatElapsed(-1), "00:00");
  assert.strictEqual(connection.formatElapsed(999), "00:00");
  assert.strictEqual(connection.formatElapsed(1000), "00:01");
  assert.strictEqual(connection.formatElapsed(61000), "01:01");
  assert.strictEqual(connection.formatElapsed(3665000), "61:05");
}

function testContinuousOutageAndRecovery() {
  let now = 100000;
  let nextHandle = 0;
  const intervals = new Map();
  const container = fakeElement();
  const label = fakeElement();
  const timer = fakeElement();
  timer.setAttribute("hidden", "");

  const status = connection.createConnectionStatus({
    container: container,
    label: label,
    timer: timer,
    now: () => now,
    setInterval: (callback, delay) => {
      assert.strictEqual(delay, 1000);
      const handle = ++nextHandle;
      intervals.set(handle, callback);
      return handle;
    },
    clearInterval: (handle) => intervals.delete(handle),
  });

  status.setStatus("reconnecting");
  assert.strictEqual(label.textContent, "Reconnecting…");
  assert.strictEqual(timer.textContent, "00:00");
  assert.ok(!("hidden" in timer.attributes));
  assert.strictEqual(intervals.size, 1);

  now += 65000;
  intervals.values().next().value();
  assert.strictEqual(timer.textContent, "01:05");
  assert.strictEqual(timer.attributes["aria-label"], "Connection disruption duration: 01:05");

  status.setStatus("polling");
  assert.strictEqual(label.textContent, "Polling");
  assert.strictEqual(timer.textContent, "01:05", "Polling preserves the original outage start");
  assert.strictEqual(intervals.size, 1, "a state transition does not create a second ticker");

  now += 1000;
  status.setStatus("polling");
  assert.strictEqual(status._state().startedAt, 100000, "repeated degraded state does not reset");
  assert.strictEqual(intervals.size, 1);

  status.setStatus("live");
  assert.strictEqual(label.textContent, "Live");
  assert.ok("hidden" in timer.attributes);
  assert.strictEqual(intervals.size, 0);
  assert.strictEqual(status._state().startedAt, null);

  now += 10000;
  status.setStatus("reconnecting");
  assert.strictEqual(timer.textContent, "00:00", "a later outage starts a fresh duration");
}

testFormatting();
testContinuousOutageAndRecovery();
console.log("animator connection-status contract: all assertions passed");
