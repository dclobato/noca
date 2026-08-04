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

const animatorScript = fs.readFileSync(
  path.resolve(__dirname, "../../../web/static/js/animator-settings.js"),
  "utf8",
);
const backupCodesScript = fs.readFileSync(
  path.resolve(__dirname, "../../../arena/static/js/backup_codes.js"),
  "utf8",
);

class FakeElement {
  constructor(id = "", selectors = []) {
    this.id = id;
    this.disabled = false;
    this.innerHTML = "<span>copy</span> Copy";
    this.textContent = "";
    this.clickHandler = null;
    this.selectors = selectors;
    this.children = [];
  }

  addEventListener(eventName, handler) {
    if (eventName === "click") this.clickHandler = handler;
  }

  closest(selector) {
    return selector === `#${this.id}` ? this : null;
  }

  querySelector(selector) {
    return this.children.find((child) => child.selectors.includes(selector)) || null;
  }
}

async function flushPromises() {
  await Promise.resolve();
  await Promise.resolve();
}

async function testAnimatorConsumer() {
  const button = new FakeElement("copy-secret-btn");
  const secret = new FakeElement("new-secret-value");
  let copiedText = null;
  let documentClickHandler;
  secret.textContent = "  operator-token  ";

  const context = {
    document: {
      addEventListener(eventName, handler) {
        if (eventName === "click") documentClickHandler = handler;
      },
      getElementById(id) {
        return id === secret.id ? secret : null;
      },
    },
    Element: FakeElement,
    Promise,
    setTimeout() {},
    window: {
      NocaClipboard: {
        copyText(text) {
          copiedText = text;
          return Promise.resolve();
        },
      },
    },
  };
  vm.createContext(context);
  vm.runInContext(animatorScript, context);

  documentClickHandler({ target: button });
  await flushPromises();

  assert.equal(copiedText, "operator-token");
  assert.equal(button.textContent, "Copied!");
}

async function testArenaConsumer() {
  const button = new FakeElement("copy-codes-btn");
  const label = new FakeElement("", ["[data-copy-label]"]);
  const status = new FakeElement("", ["[data-copy-status]"]);
  const codeElements = [new FakeElement(), new FakeElement()];
  let copiedText = null;
  label.textContent = "Copy all codes";
  button.children.push(label);
  codeElements[0].textContent = "  AAA111  ";
  codeElements[1].textContent = "  BBB222  ";

  const context = {
    Array,
    document: {
      getElementById(id) {
        return id === button.id ? button : null;
      },
      querySelector(selector) {
        return status.selectors.includes(selector) ? status : null;
      },
      querySelectorAll() {
        return codeElements;
      },
    },
    Promise,
    setTimeout() {},
    window: {
      NocaClipboard: {
        copyText(text) {
          copiedText = text;
          return Promise.resolve();
        },
      },
    },
  };
  vm.createContext(context);
  vm.runInContext(backupCodesScript, context);

  button.clickHandler();
  await flushPromises();

  assert.equal(copiedText, "AAA111\nBBB222");
  assert.equal(label.textContent, "Copied");
  assert.equal(status.textContent, "Recovery codes copied to your clipboard.");
  assert.equal(button.disabled, true);
}

Promise.resolve()
  .then(testAnimatorConsumer)
  .then(testArenaConsumer)
  .catch((error) => {
    process.nextTick(() => {
      throw error;
    });
  });
