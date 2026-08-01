/*
 *  NOCA -- Next Online Contest Administrator
 *  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
 *  This program is distributed in the hope that it will be useful,
 *  but WITHOUT ANY WARRANTY; without even the implied warranty of
 *  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 */

"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const scriptPath = path.resolve(
  __dirname,
  "../../../shared/static/js/clipboard.js",
);
const script = fs.readFileSync(scriptPath, "utf8");

class FakeElement {
  constructor() {
    this.attributes = {};
    this.className = "";
    this.focused = false;
    this.removed = false;
    this.selected = false;
    this.value = "";
  }

  focus() {
    this.focused = true;
  }

  remove() {
    this.removed = true;
  }

  select() {
    this.selected = true;
  }

  setAttribute(name, value) {
    this.attributes[name] = value;
  }
}

function buildFixture(clipboard, copyCommandResult = true) {
  const originalFocus = new FakeElement();
  const createdElements = [];
  let copiedCommand = null;
  const document = {
    activeElement: originalFocus,
    body: {
      appendChild(element) {
        createdElements.push(element);
      },
    },
    createElement(tagName) {
      assert.equal(tagName, "textarea");
      return new FakeElement();
    },
    execCommand(command) {
      copiedCommand = command;
      return copyCommandResult;
    },
  };
  const window = {
    document,
    navigator: { clipboard },
    Promise,
  };
  const context = { window };
  vm.createContext(context);
  vm.runInContext(script, context);

  return {
    api: window.NocaClipboard,
    copiedCommand() {
      return copiedCommand;
    },
    createdElements,
    originalFocus,
  };
}

async function testSecureClipboard() {
  let copiedText = null;
  const fixture = buildFixture({
    writeText(text) {
      copiedText = text;
      return Promise.resolve();
    },
  });

  await fixture.api.copyText("operator-token");

  assert.equal(copiedText, "operator-token");
  assert.equal(fixture.createdElements.length, 0);
}

async function testInsecureContextFallback() {
  const fixture = buildFixture(undefined);

  await fixture.api.copyText("operator-token");

  assert.equal(fixture.copiedCommand(), "copy");
  assert.equal(fixture.createdElements.length, 1);
  assert.equal(fixture.createdElements[0].value, "operator-token");
  assert.equal(fixture.createdElements[0].selected, true);
  assert.equal(fixture.createdElements[0].removed, true);
  assert.equal(fixture.originalFocus.focused, true);
}

async function testRejectedFallback() {
  const fixture = buildFixture(undefined, false);

  await assert.rejects(
    fixture.api.copyText("operator-token"),
    /browser rejected the copy command/,
  );

  assert.equal(fixture.createdElements[0].removed, true);
  assert.equal(fixture.originalFocus.focused, true);
}

Promise.resolve()
  .then(testSecureClipboard)
  .then(testInsecureContextFallback)
  .then(testRejectedFallback)
  .catch((error) => {
    process.nextTick(() => {
      throw error;
    });
  });
