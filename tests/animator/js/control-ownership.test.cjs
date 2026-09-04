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
  const elements = {
    label: element(),
    detail: element(),
    projectors: element(),
    takeover: element(),
    retry: element(),
  };
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
    emit(state, detail) {
      active = state === "active";
      leaseHandler(state, detail);
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

function testProjectorReadoutFollowsTheLeasePayload() {
  const h = harness();
  const el = h.elements.projectors;
  // Active without a reported count renders nothing rather than a guess.
  h.emit("active", {});
  assert.strictEqual(el.hidden, true);

  h.emit("active", { projector_count: 3 });
  assert.strictEqual(el.hidden, false);
  assert.strictEqual(el.textContent, "3 projectors connected");
  assert.strictEqual(el.className, "control-projectors");

  h.emit("active", { projector_count: 1 });
  assert.strictEqual(el.textContent, "1 projector connected");

  // An empty hall is a warning the operator must see...
  h.emit("active", { projector_count: 0 });
  assert.strictEqual(el.textContent, "No projectors connected");
  assert.strictEqual(el.className, "control-projectors is-empty");

  // ...and an outage is a different fact, never rendered as zero.
  h.emit("active", { projector_count: null });
  assert.strictEqual(el.textContent, "Projector count unavailable");
  assert.strictEqual(el.className, "control-projectors is-unknown");

  // A heartbeat error detail carries no count and keeps the last reading
  // while still active; losing control clears it under the badge.
  h.emit("active", { projector_count: 2 });
  h.emit("active", { status: 503, detail: "blip" });
  assert.strictEqual(el.textContent, "2 projectors connected");
  h.controller.markLost();
  assert.strictEqual(el.hidden, true);
  assert.strictEqual(h.controller.projectorCount(), undefined);
  h.emit("active", { projector_count: 2 });
  assert.strictEqual(el.hidden, false);
}

(async function main() {
  await testReadOnlyAndTakeoverConfirmation();
  testLeaseLossFailsClosed();
  await testUnavailableHasManualRetryOnly();
  testProjectorReadoutFollowsTheLeasePayload();
  console.log("control ownership contract: OK");
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
