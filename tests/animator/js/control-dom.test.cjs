//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

// Focused DOM contract for the reveal controller. It boots the real control.js
// glue, unlocks into a stored idle ceremony, and clicks through Start over so
// the restart flag is pinned at the interaction boundary rather than only in
// the command-client and server tests.

"use strict";

const assert = require("assert");
const path = require("path");

const controlApi = require(
  path.join(__dirname, "..", "..", "..", "animator", "static", "js", "control.js"),
);

const URLS = {
  meta: "/c/x/meta",
  state: "/c/x/control/state",
  start: "/c/x/control/start-reveal",
  step: "/c/x/control/step",
  back: "/c/x/control/back",
  reset: "/c/x/control/reset",
  jump: "/c/x/control/jump-team",
};

class Element {
  constructor(tagName) {
    this.tagName = (tagName || "div").toUpperCase();
    this.attributes = {};
    this.children = [];
    this.listeners = {};
    this.hidden = false;
    this.disabled = false;
    this.textContent = "";
    this.value = "";
  }

  setAttribute(name, value) {
    this.attributes[name] = String(value);
  }

  getAttribute(name) {
    return Object.prototype.hasOwnProperty.call(this.attributes, name)
      ? this.attributes[name]
      : null;
  }

  appendChild(child) {
    this.children.push(child);
    return child;
  }

  removeChild(child) {
    this.children = this.children.filter((candidate) => candidate !== child);
    return child;
  }

  get firstChild() {
    return this.children[0] || null;
  }

  addEventListener(type, listener) {
    this.listeners[type] = this.listeners[type] || [];
    this.listeners[type].push(listener);
  }

  dispatch(type) {
    const event = { target: this, preventDefault() {} };
    (this.listeners[type] || []).forEach((listener) => listener(event));
  }
}

function jsonResponse(body) {
  return {
    ok: true,
    status: 200,
    json: () => Promise.resolve(body),
  };
}

function buildDocument() {
  const elements = {};
  function add(id, tagName) {
    const element = new Element(tagName);
    elements[id] = element;
    return element;
  }

  const root = add("control-app");
  root.setAttribute("data-initial-scope", "global");
  Object.entries(URLS).forEach(([name, url]) => root.setAttribute("data-" + name + "-url", url));

  [
    ["control-secret-form", "form"],
    ["control-secret", "input"],
    ["control-panel", "section"],
    ["control-status", "span"],
    ["control-error", "div"],
    ["control-scope", "select"],
    ["control-scope-label", "span"],
    ["control-jump-row", "div"],
    ["control-jump-team", "select"],
    ["control-confirmation-modal", "div"],
    ["control-confirmation-modal-label", "h2"],
    ["control-confirmation-message", "p"],
    ["control-confirmation-counts", "p"],
    ["control-state-phase", "dd"],
    ["control-state-scope", "dd"],
    ["control-state-revealed", "dd"],
    ["control-state-focus", "dd"],
    ["control-start", "button"],
    ["control-start-over", "button"],
    ["control-rebuild", "button"],
    ["control-reset", "button"],
    ["control-confirmation-confirm", "button"],
    ["control-reload", "button"],
    ["control-step", "button"],
    ["control-step-ten", "button"],
    ["control-back", "button"],
    ["control-back-ten", "button"],
    ["control-jump", "button"],
  ].forEach(([id, tagName]) => add(id, tagName));

  return {
    elements,
    doc: {
      getElementById: (id) => elements[id] || null,
      createElement: (tagName) => new Element(tagName),
      addEventListener() {},
    },
  };
}

async function main() {
  const { doc, elements } = buildDocument();
  const calls = [];
  let modalShowCount = 0;
  const idleProjection = {
    contest_id: "c1",
    scope: "global",
    site_id: null,
    site_name: null,
    phase: "idle",
    focused_team_id: null,
    revealed_count: 0,
    frozen_count: 7,
    medal_cutoffs: null,
    teams: [],
  };
  const previousWindow = global.window;
  global.window = {
    bootstrap: {
      Modal: {
        getOrCreateInstance() {
          return {
            show() {
              modalShowCount += 1;
            },
            hide() {},
          };
        },
      },
    },
    fetch(url, init) {
      calls.push({ url, init: init || {} });
      if (url === URLS.meta) {
        return Promise.resolve(jsonResponse({ sites: [] }));
      }
      if (url === URLS.state || url === URLS.start) {
        return Promise.resolve(jsonResponse(idleProjection));
      }
      throw new Error("Unexpected URL: " + url);
    },
  };

  try {
    controlApi.boot(doc);
    elements["control-secret"].value = "operator-secret";
    elements["control-secret-form"].dispatch("submit");
    await new Promise((resolve) => setImmediate(resolve));

    assert.strictEqual(elements["control-start-over"].hidden, false);
    elements["control-start-over"].dispatch("click");
    assert.strictEqual(modalShowCount, 1, "Start over opens its confirmation modal");
    elements["control-confirmation-confirm"].dispatch("click");
    await new Promise((resolve) => setImmediate(resolve));

    const startCall = calls.find((call) => call.url === URLS.start);
    assert.ok(startCall, "confirming Start over sends start-reveal");
    assert.deepStrictEqual(JSON.parse(startCall.init.body), {
      site_id: null,
      restart: true,
    });
  } finally {
    if (previousWindow === undefined) {
      delete global.window;
    } else {
      global.window = previousWindow;
    }
  }

  console.log("control DOM contract: idle Start over sends restart=true");
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
