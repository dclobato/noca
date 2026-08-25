/*
 * NOCA -- Next Online Contest Administrator
 * Copyright (c) 2026 The NOCA Authors (see AUTHORS)
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 */

"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const scriptPath = path.resolve(
  __dirname,
  "../../../shared/static/js/confirm-submit.js",
);
const source = fs.readFileSync(scriptPath, "utf8");

function makeHarness() {
  const listeners = [];
  const confirmCalls = [];
  let confirmResult = true;
  const sandbox = {
    document: {
      addEventListener(type, listener) {
        listeners.push([type, listener]);
      },
    },
    window: {
      confirm(message) {
        confirmCalls.push(message);
        return confirmResult;
      },
    },
  };
  vm.createContext(sandbox);
  vm.runInContext(source, sandbox);

  const submitListeners = listeners
    .filter(([type]) => type === "submit")
    .map(([, listener]) => listener);
  assert.equal(
    submitListeners.length,
    1,
    "the script must register exactly one submit listener",
  );

  return {
    confirmCalls,
    setConfirmResult(value) {
      confirmResult = value;
    },
    dispatch(target) {
      let prevented = 0;
      const event = {
        target,
        preventDefault() {
          prevented += 1;
        },
      };
      for (const listener of submitListeners) listener(event);
      return prevented;
    },
  };
}

function makeForm(message) {
  const form = {
    dataset: {},
    closest(selector) {
      return selector === "form[data-confirm]" && form.dataset.confirm
        ? form
        : null;
    },
  };
  if (message !== undefined) form.dataset.confirm = message;
  return form;
}

// A cancelled dialog blocks the submission, asking exactly once, with the
// form's own message.
{
  const harness = makeHarness();
  harness.setConfirmResult(false);
  assert.equal(harness.dispatch(makeForm("Remove this photo?")), 1);
  assert.deepEqual(harness.confirmCalls, ["Remove this photo?"]);
}

// An accepted dialog lets the submission proceed.
{
  const harness = makeHarness();
  harness.setConfirmResult(true);
  assert.equal(harness.dispatch(makeForm("Deactivate this contest?")), 0);
  assert.equal(harness.confirmCalls.length, 1);
}

// A form without data-confirm never triggers the dialog.
{
  const harness = makeHarness();
  harness.setConfirmResult(false);
  assert.equal(harness.dispatch(makeForm(undefined)), 0);
  assert.equal(harness.confirmCalls.length, 0);
}

// A target outside any form is ignored.
{
  const harness = makeHarness();
  assert.equal(harness.dispatch({ closest: () => null }), 0);
  assert.equal(harness.confirmCalls.length, 0);
}

// A form inserted into the document after the listener was registered is still
// intercepted: the delegation lives on the document, not on individual forms.
{
  const harness = makeHarness();
  harness.setConfirmResult(false);
  const lateForm = makeForm("Discard the typed rows?");
  assert.equal(harness.dispatch(lateForm), 1);
  assert.deepEqual(harness.confirmCalls, ["Discard the typed rows?"]);
}
