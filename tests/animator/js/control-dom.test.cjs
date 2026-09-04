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
  jumpPending: "/c/x/control/jump-pending",
  showMedia: "/c/x/control/show-team-media",
  hideMedia: "/c/x/control/hide-team-media",
  leaseClaim: "/c/x/control/controller-lease/claim",
  leaseHeartbeat: "/c/x/control/controller-lease/heartbeat",
  leaseRelease: "/c/x/control/controller-lease/release",
  leaseTakeover: "/c/x/control/controller-lease/takeover",
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

function jsonResponse(status, body) {
  return {
    ok: status < 300,
    status,
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
  // Attribute names must match control.html exactly, kebab case included.
  const ATTRIBUTE_URLS = {
    meta: URLS.meta,
    state: URLS.state,
    start: URLS.start,
    step: URLS.step,
    back: URLS.back,
    reset: URLS.reset,
    jump: URLS.jump,
    "jump-pending": URLS.jumpPending,
    "show-media": URLS.showMedia,
    "hide-media": URLS.hideMedia,
    "lease-claim": URLS.leaseClaim,
    "lease-heartbeat": URLS.leaseHeartbeat,
    "lease-release": URLS.leaseRelease,
    "lease-takeover": URLS.leaseTakeover,
  };
  Object.entries(ATTRIBUTE_URLS).forEach(([name, url]) => root.setAttribute("data-" + name + "-url", url));

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
    ["control-ownership-status", "span"],
    ["control-ownership-detail", "span"],
    ["control-projectors", "span"],
    ["control-takeover", "button"],
    ["control-ownership-retry", "button"],
    ["control-takeover-modal", "div"],
    ["control-takeover-confirm", "button"],
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
    ["control-jump-pending", "button"],
    ["control-media", "button"],
    ["control-media-team", "span"],
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

const IDLE_PROJECTION = {
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

const SEEKING_PROJECTION = {
  ...IDLE_PROJECTION,
  phase: "revealing",
  focused_team_id: "team-1",
  next_cell: null,
};

const LEASE_TIMINGS = { status: "claimed", lease_ttl_seconds: 45, heartbeat_interval_seconds: 10 };

function makeWindow(calls, responders) {
  const windowListeners = {};
  return {
    window: {
      addEventListener(type, listener) {
        (windowListeners[type] = windowListeners[type] || []).push(listener);
      },
      removeEventListener() {},
      dispatchWindowEvent(type) {
        (windowListeners[type] || []).forEach((listener) => listener({}));
      },
      AnimatorControlLease: require(
        path.join(__dirname, "..", "..", "..", "animator", "static", "js", "control-lease.js"),
      ),
      AnimatorControlOwnership: require(
        path.join(__dirname, "..", "..", "..", "animator", "static", "js", "control-ownership.js"),
      ),
      bootstrap: {
        Modal: {
          getOrCreateInstance() {
            return {
              show() {
                responders.modalShowCount += 1;
              },
              hide() {},
            };
          },
        },
      },
      fetch(url, init) {
        calls.push({ url, init: init || {} });
        const responder = responders.routes[url];
        if (!responder) {
          throw new Error("Unexpected URL: " + url);
        }
        return responder();
      },
    },
    windowListeners,
  };
}

async function withWindow(run) {
  const previousWindow = global.window;
  const calls = [];
  const responders = { modalShowCount: 0, routes: {} };
  const { window: fakeWindow, windowListeners } = makeWindow(calls, responders);
  global.window = fakeWindow;
  try {
    await run({ calls, responders, dispatchWindowEvent: (type) => windowListeners[type] || [] });
  } finally {
    // Close the page the way a real teardown would: pagehide releases the
    // lease and clears the heartbeat timer, so no live timer outlives the test.
    (windowListeners.pagehide || []).forEach((listener) => listener({}));
    await new Promise((resolve) => setImmediate(resolve));
    if (previousWindow === undefined) {
      delete global.window;
    } else {
      global.window = previousWindow;
    }
  }
}

async function main() {
  await withWindow(async ({ calls, responders }) => {
    const { doc, elements } = buildDocument();
    responders.routes[URLS.meta] = () => Promise.resolve(jsonResponse(200, { sites: [] }));
    responders.routes[URLS.state] = () => Promise.resolve(jsonResponse(200, IDLE_PROJECTION));
    responders.routes[URLS.start] = () => Promise.resolve(jsonResponse(200, IDLE_PROJECTION));
    responders.routes[URLS.leaseClaim] = () => Promise.resolve(jsonResponse(200, LEASE_TIMINGS));
    responders.routes[URLS.leaseRelease] = () => Promise.resolve(jsonResponse(200, { ...LEASE_TIMINGS, status: "released" }));

    controlApi.boot(doc);
    elements["control-secret"].value = "operator-secret";
    elements["control-secret-form"].dispatch("submit");
    await new Promise((resolve) => setImmediate(resolve));

    assert.strictEqual(elements["control-start-over"].hidden, false);
    elements["control-start-over"].dispatch("click");
    assert.strictEqual(responders.modalShowCount, 1, "Start over opens its confirmation modal");
    elements["control-confirmation-confirm"].dispatch("click");
    await new Promise((resolve) => setImmediate(resolve));

    const startCall = calls.find((call) => call.url === URLS.start);
    assert.ok(startCall, "confirming Start over sends start-reveal");
    assert.deepStrictEqual(JSON.parse(startCall.init.body), {
      site_id: null,
      restart: true,
    });
  });
  console.log("control DOM contract: idle Start over sends restart=true");

  await withWindow(async ({ calls, responders }) => {
    const { doc, elements } = buildDocument();
    responders.routes[URLS.meta] = () => Promise.resolve(jsonResponse(200, { sites: [] }));
    responders.routes[URLS.state] = () =>
      Promise.resolve(jsonResponse(500, { detail: controlApi.UNUSABLE_STATE_DETAIL }));
    responders.routes[URLS.leaseClaim] = () => Promise.resolve(jsonResponse(200, LEASE_TIMINGS));
    responders.routes[URLS.leaseRelease] = () => Promise.resolve(jsonResponse(200, { ...LEASE_TIMINGS, status: "released" }));

    controlApi.boot(doc);
    elements["control-secret"].value = "operator-secret";
    elements["control-secret-form"].dispatch("submit");
    await new Promise((resolve) => setImmediate(resolve));

    // The stored payload is unreadable, but the credential is fine: the panel
    // must still claim the lease so the documented Rebuild-state recovery is
    // reachable instead of every button staying disabled behind the warning.
    const claimCall = calls.find((call) => call.url === URLS.leaseClaim);
    assert.ok(claimCall, "an unusable state load does not skip the lease claim");
    assert.strictEqual(elements["control-error"].textContent.includes("Rebuild state"), true);
    assert.strictEqual(elements["control-rebuild"].disabled, false, "Rebuild is clickable once in control");
  });
  console.log("control DOM contract: unusable state still claims and enables Rebuild");

  await withWindow(async ({ calls, responders }) => {
    const { doc, elements } = buildDocument();
    responders.routes[URLS.meta] = () => Promise.resolve(jsonResponse(200, { sites: [] }));
    responders.routes[URLS.state] = () => Promise.resolve(jsonResponse(200, SEEKING_PROJECTION));
    responders.routes[URLS.jumpPending] = () => Promise.resolve(jsonResponse(200, SEEKING_PROJECTION));
    responders.routes[URLS.leaseClaim] = () => Promise.resolve(jsonResponse(200, LEASE_TIMINGS));
    responders.routes[URLS.leaseRelease] = () =>
      Promise.resolve(jsonResponse(200, { ...LEASE_TIMINGS, status: "released" }));

    controlApi.boot(doc);
    elements["control-secret"].value = "operator-secret";
    elements["control-secret-form"].dispatch("submit");
    await new Promise((resolve) => setImmediate(resolve));

    assert.strictEqual(elements["control-jump-pending"].hidden, false);
    elements["control-jump-pending"].dispatch("click");
    await new Promise((resolve) => setImmediate(resolve));

    const jumpCall = calls.find((call) => call.url === URLS.jumpPending);
    assert.ok(jumpCall, "the visible control sends jump-pending");
    assert.strictEqual(jumpCall.init.body, undefined);
  });
  console.log("control DOM contract: Jump to next pending sends a bodiless command");

  // ── The media label must track the projector, not every state fetch ────────
  await withWindow(async ({ calls, responders }) => {
    const { doc, elements } = buildDocument();
    responders.routes[URLS.meta] = () => Promise.resolve(jsonResponse(200, { sites: [] }));
    responders.routes[URLS.state] = () => Promise.resolve(jsonResponse(200, SEEKING_PROJECTION));
    responders.routes[URLS.showMedia] = () => Promise.resolve({ ok: true, status: 204 });
    responders.routes[URLS.hideMedia] = () => Promise.resolve({ ok: true, status: 204 });
    responders.routes[URLS.step] = () =>
      Promise.resolve(jsonResponse(200, { ...SEEKING_PROJECTION, revealed_count: 1 }));
    responders.routes[URLS.leaseClaim] = () => Promise.resolve(jsonResponse(200, LEASE_TIMINGS));
    responders.routes[URLS.leaseRelease] = () =>
      Promise.resolve(jsonResponse(200, { ...LEASE_TIMINGS, status: "released" }));

    controlApi.boot(doc);
    elements["control-secret"].value = "operator-secret";
    elements["control-secret-form"].dispatch("submit");
    await new Promise((resolve) => setImmediate(resolve));

    const media = elements["control-media"];
    assert.strictEqual(media.hidden, false, "a focused team makes the cue available");
    assert.strictEqual(media.textContent, "Show media");

    media.dispatch("click");
    await new Promise((resolve) => setImmediate(resolve));
    assert.ok(calls.find((call) => call.url === URLS.showMedia), "the first press shows");
    assert.strictEqual(media.textContent, "Hide media", "a confirmed cue flips the label");

    // An explicit Reload state returns the *same* projection. The projector
    // closes its overlay only when the ceremony moves, so the photograph is
    // still up — and a label that flipped back here would send `show` again on
    // the operator's next press instead of taking it down.
    elements["control-reload"].dispatch("click");
    await new Promise((resolve) => setImmediate(resolve));
    assert.strictEqual(media.textContent, "Hide media", "an unchanged reload must not reset the label");

    media.dispatch("click");
    await new Promise((resolve) => setImmediate(resolve));
    assert.ok(calls.find((call) => call.url === URLS.hideMedia), "the next press hides, not re-shows");
    assert.strictEqual(media.textContent, "Show media");

    // A ceremony that actually moves *does* reset it, in step with the
    // projector's own auto-hide.
    media.dispatch("click");
    await new Promise((resolve) => setImmediate(resolve));
    assert.strictEqual(media.textContent, "Hide media");
    elements["control-step"].dispatch("click");
    await new Promise((resolve) => setImmediate(resolve));
    assert.strictEqual(media.textContent, "Show media", "movement resets the label");
  });
  console.log("control DOM contract: the media label tracks the projector, not every state fetch");
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
