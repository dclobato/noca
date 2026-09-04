//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

// Contract test for the shared username-availability probe and the two surfaces
// built on it: the user's own profile field and the admin rename modal.
//
// What it locks down is that both surfaces answer the same question the same
// way. They drifted once already -- the admin modal coloured its verdict green
// or red while the profile field wrote every non-error verdict into a muted
// help slot, so "available" was indistinguishable from static help text. Two
// visual languages for one question is a bug, and only a test that renders both
// can see it.
//
// It also covers the stale-response guard, which no rendering test would catch:
// typing "ana" then "anna" fires two probes, and without the token the slower
// first answer can land last and label the second handle with the first's
// verdict.

"use strict";

const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const JS_DIR = path.join(__dirname, "..", "..", "..", "arena", "static", "js");
const SHARED = path.join(JS_DIR, "arena-username-availability.js");
const PROFILE = path.join(JS_DIR, "profile-username.js");

// ── Minimal DOM shim ────────────────────────────────────────────────────────

function makeEl(id) {
  const classes = new Set();
  const listeners = new Map();
  return {
    id,
    dataset: {},
    attributes: {},
    value: "",
    disabled: false,
    textContent: "",
    listeners,
    classList: {
      add: (n) => classes.add(n),
      remove: (n) => classes.delete(n),
      contains: (n) => classes.has(n),
      toggle: (n, force) => (force ? classes.add(n) : classes.delete(n)),
    },
    setAttribute(name, value) {
      this.attributes[name] = String(value);
    },
    getAttribute(name) {
      return Object.prototype.hasOwnProperty.call(this.attributes, name) ? this.attributes[name] : null;
    },
    addEventListener(type, handler) {
      if (!listeners.has(type)) listeners.set(type, []);
      listeners.get(type).push(handler);
    },
    focus() {},
    click() {
      return this.fire("click");
    },
    fire(type, event) {
      const evt = Object.assign({ preventDefault() {}, type }, event || {});
      return Promise.all((listeners.get(type) || []).map((h) => h(evt)));
    },
  };
}

function settle(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms === undefined ? 20 : ms));
}

/**
 * Build a context holding the given elements and a scripted fetch.
 *
 * @param {Map} els Elements by id.
 * @param {Function} fetchImpl Called as (url, init).
 */
function makeContext(els, fetchImpl) {
  const declarative = [];
  for (const el of els.values()) {
    if (el.dataset && el.dataset.usernameCheckUrl) declarative.push(el);
  }
  const context = {
    console,
    setTimeout,
    clearTimeout,
    Promise,
    JSON,
    Object,
    String,
    Boolean,
    Error,
    encodeURIComponent,
    document: {
      readyState: "complete",
      getElementById: (id) => els.get(id) || null,
      addEventListener() {},
      querySelectorAll: () => declarative,
      querySelector: () => null,
    },
    window: { setTimeout, clearTimeout, addEventListener() {} },
    fetch: fetchImpl,
  };
  context.globalThis = context;
  context.window.ArenaUsernameAvailability = undefined;
  vm.createContext(context);
  return context;
}

function runShared(context) {
  vm.runInContext(fs.readFileSync(SHARED, "utf8"), context, { filename: SHARED });
  // The page scripts reach the module through `window`; mirror what a browser
  // does, where `window` and the global object are the same thing.
  context.ArenaUsernameAvailability = context.window.ArenaUsernameAvailability;
}

// ── 1. The profile field colours both verdicts ──────────────────────────────

async function testProfileFieldColoursBothVerdicts() {
  const els = new Map();
  for (const id of [
    "profile-username-input",
    "profile-username-save",
    "profile-username-error",
    "profile-username-help",
    "profile-username-rules",
  ]) {
    els.set(id, makeEl(id));
  }
  const input = els.get("profile-username-input");
  const help = els.get("profile-username-help");
  const error = els.get("profile-username-error");
  input.dataset.currentUsername = "tucano-alegre-100";
  input.dataset.checkUrl = "/user/profile/username/available";
  input.dataset.updateUrl = "/user/profile/username";

  let answer = { username: "onca-pintada-1", available: true, error: null };
  const context = makeContext(els, async () => ({ ok: true, status: 200, json: async () => answer }));
  runShared(context);
  vm.runInContext(fs.readFileSync(PROFILE, "utf8"), context, { filename: PROFILE });

  input.value = "onca-pintada-1";
  await input.fire("input");
  await settle(400);

  assert.ok(help.classList.contains("text-success"), "an available handle must read as available, not as help text");
  assert.ok(input.classList.contains("is-valid"));
  assert.ok(String(help.textContent).includes("available"));

  answer = { username: "onca-pintada-2", available: false, error: "That username is already taken." };
  input.value = "onca-pintada-2";
  await input.fire("input");
  await settle(400);

  assert.ok(input.classList.contains("is-invalid"), "a taken handle must read as invalid");
  assert.strictEqual(input.getAttribute("aria-invalid"), "true");
  assert.ok(String(error.textContent).includes("already taken"));
  assert.ok(!help.classList.contains("text-success"), "the success styling must not survive a later refusal");
}

// ── 2. The admin modal, bound declaratively, colours the same way ───────────

async function testAdminModalColoursTheSameWay() {
  const els = new Map();
  const input = makeEl("admin-new-username");
  const status = makeEl("admin-username-status");
  input.dataset.usernameCheckUrl = "/admin/users/u1/username/available";
  input.dataset.usernameCurrent = "tucano-alegre-200";
  input.dataset.usernameStatus = "admin-username-status";
  els.set("admin-new-username", input);
  els.set("admin-username-status", status);

  let answer = { username: "onca-pintada-3", available: true, error: null };
  const context = makeContext(els, async () => ({ ok: true, status: 200, json: async () => answer }));
  runShared(context);

  input.value = "onca-pintada-3";
  await input.fire("input");
  await settle(400);

  assert.ok(status.classList.contains("text-success"), "the admin modal must colour an available handle");
  assert.ok(input.classList.contains("is-valid"));

  answer = { username: "onca-pintada-4", available: false, error: "That username is already taken." };
  input.value = "onca-pintada-4";
  await input.fire("input");
  await settle(400);

  assert.ok(status.classList.contains("text-danger"), "the admin modal must colour a taken handle");
  assert.ok(input.classList.contains("is-invalid"));
  assert.ok(String(status.textContent).includes("already taken"));
}

// ── 3. The target's own handle is never reported as taken ──────────────────

async function testOwnHandleIsNotAConflict() {
  const els = new Map();
  const input = makeEl("admin-new-username");
  const status = makeEl("admin-username-status");
  input.dataset.usernameCheckUrl = "/admin/users/u1/username/available";
  input.dataset.usernameCurrent = "tucano-alegre-300";
  input.dataset.usernameStatus = "admin-username-status";
  els.set("admin-new-username", input);
  els.set("admin-username-status", status);

  let probed = 0;
  const context = makeContext(els, async () => {
    probed += 1;
    return { ok: true, status: 200, json: async () => ({ available: false, error: "taken" }) };
  });
  runShared(context);

  // The modal is pre-filled with the handle the account already holds.
  input.value = "tucano-alegre-300";
  await input.fire("input");
  await settle(400);

  assert.strictEqual(probed, 0, "the handle already held must not even be probed");
  assert.strictEqual(status.textContent, "");
  assert.ok(!input.classList.contains("is-invalid"));
}

// ── 4. A slow answer never overwrites a newer one ──────────────────────────

async function testStaleResponseIsDiscarded() {
  const els = new Map();
  const input = makeEl("admin-new-username");
  const status = makeEl("admin-username-status");
  input.dataset.usernameCheckUrl = "/admin/users/u1/username/available";
  input.dataset.usernameCurrent = "tucano-alegre-400";
  input.dataset.usernameStatus = "admin-username-status";
  els.set("admin-new-username", input);
  els.set("admin-username-status", status);

  let call = 0;
  const context = makeContext(els, async (url) => {
    call += 1;
    const slow = call === 1;
    const taken = url.includes("ana") && !url.includes("anna");
    await settle(slow ? 120 : 0);
    return {
      ok: true,
      status: 200,
      json: async () => ({
        username: taken ? "ana" : "anna",
        available: !taken,
        error: taken ? "That username is already taken." : null,
      }),
    };
  });
  runShared(context);

  input.value = "ana";
  await input.fire("input");
  await settle(310);
  input.value = "anna";
  await input.fire("input");
  await settle(500);

  assert.ok(
    status.classList.contains("text-success"),
    "the newer verdict must win even when the older answer lands last",
  );
  assert.ok(String(status.textContent).includes("anna"));
}

// ── 5. A failed probe is not a verdict ─────────────────────────────────────

async function testNetworkFailureIsNotAVerdict() {
  const els = new Map();
  const input = makeEl("admin-new-username");
  const status = makeEl("admin-username-status");
  input.dataset.usernameCheckUrl = "/admin/users/u1/username/available";
  input.dataset.usernameCurrent = "tucano-alegre-500";
  input.dataset.usernameStatus = "admin-username-status";
  els.set("admin-new-username", input);
  els.set("admin-username-status", status);

  const context = makeContext(els, async () => {
    throw new Error("network down");
  });
  runShared(context);

  input.value = "onca-pintada-9";
  await input.fire("input");
  await settle(400);

  assert.ok(
    !input.classList.contains("is-invalid"),
    "a dropped connection must not talk someone out of a handle they can have",
  );
  assert.strictEqual(status.textContent, "");
}

async function main() {
  await testProfileFieldColoursBothVerdicts();
  await testAdminModalColoursTheSameWay();
  await testOwnHandleIsNotAConflict();
  await testStaleResponseIsDiscarded();
  await testNetworkFailureIsNotAVerdict();
  console.log("arena username availability contract: all checks passed");
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
