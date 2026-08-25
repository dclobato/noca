//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"use strict";

const assert = require("assert");
const path = require("path");
const ownershipApi = require(
  path.join(__dirname, "..", "..", "..", "animator", "static", "js", "control-ownership.js"),
);

function element() {
  return {
    hidden: false,
    disabled: false,
    textContent: "",
    className: "",
    listeners: {},
    addEventListener(type, listener) {
      this.listeners[type] = listener;
    },
    click() {
      this.listeners.click();
    },
  };
}

function harness() {
  let leaseHandler;
  let active = false;
  let takeoverCalls = 0;
  let claimCalls = 0;
  let confirmation;
  const enabled = [];
  const elements = { label: element(), detail: element(), takeover: element(), retry: element() };
  const lease = {
    setStateHandler(handler) {
      leaseHandler = handler;
    },
    claim() {
      claimCalls += 1;
      return Promise.resolve(null);
    },
    takeover() {
      takeoverCalls += 1;
      return Promise.resolve(null);
    },
    isActive: () => active,
    controllerHeader: () => (active ? "controller-id" : null),
    markLost() {
      active = false;
      leaseHandler("lease-lost");
    },
  };
  const controller = ownershipApi.createOwnershipController({
    lease,
    elements,
    setCommandsEnabled: (value) => enabled.push(value),
    confirmTakeover: (callback) => {
      confirmation = callback;
    },
  });
  return {
    controller,
    elements,
    enabled,
    emit(state) {
      active = state === "active";
      leaseHandler(state);
    },
    confirm: () => confirmation(),
    takeoverCalls: () => takeoverCalls,
    claimCalls: () => claimCalls,
  };
}

async function testReadOnlyAndTakeoverConfirmation() {
  const h = harness();
  h.emit("read-only");
  assert.strictEqual(h.controller.canCommand(), false);
  assert.strictEqual(h.elements.takeover.hidden, false);
  assert.strictEqual(h.elements.retry.hidden, true);
  h.elements.takeover.click();
  assert.strictEqual(h.takeoverCalls(), 0, "takeover waits for explicit confirmation");
  h.confirm();
  assert.strictEqual(h.takeoverCalls(), 1);
  assert.strictEqual(h.controller.state(), "pending");
}

function testLeaseLossFailsClosed() {
  const h = harness();
  h.emit("active");
  assert.strictEqual(h.controller.canCommand(), true);
  h.controller.markLost();
  assert.strictEqual(h.controller.canCommand(), false);
  assert.strictEqual(h.controller.state(), "lease-lost");
  assert.strictEqual(h.elements.takeover.hidden, false);
  assert.strictEqual(h.enabled[h.enabled.length - 1], false);
}

async function testUnavailableHasManualRetryOnly() {
  const h = harness();
  h.emit("unavailable");
  assert.strictEqual(h.controller.canCommand(), false);
  assert.strictEqual(h.elements.takeover.hidden, true);
  assert.strictEqual(h.elements.retry.hidden, false);
  assert.strictEqual(h.claimCalls(), 0, "unavailability does not auto-retry");
  h.elements.retry.click();
  await new Promise((resolve) => setImmediate(resolve));
  assert.strictEqual(h.claimCalls(), 1);
}

(async function main() {
  await testReadOnlyAndTakeoverConfirmation();
  testLeaseLossFailsClosed();
  await testUnavailableHasManualRetryOnly();
  console.log("control ownership contract: OK");
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
