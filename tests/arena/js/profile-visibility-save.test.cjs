//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

// Contract test for the age-shield half of `profile-personal-security.js`.
//
// This file exists because of a bug it now locks out. The save handler read a
// variable the module never declared, which threw `ReferenceError` *after* the
// POST had already committed -- so the flag was saved and the page reported
// "shielded is not defined" beside the Save button. A source-text assertion
// could not have seen it and neither could the Python suite, since the failure
// lives entirely in the browser.
//
// The page scripts are classic global IIFEs with no build step and no exports,
// so the module is evaluated inside a `vm` context holding a DOM stand-in
// rather than `require`d.

"use strict";

const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const SCRIPT = path.join(__dirname, "..", "..", "..", "arena", "static", "js", "profile-personal-security.js");

// ── Minimal DOM shim ────────────────────────────────────────────────────────

function makeEl(id, tag) {
  const classes = new Set();
  const listeners = new Map();
  return {
    id,
    tagName: tag || "div",
    dataset: {},
    attributes: {},
    value: "",
    checked: false,
    disabled: false,
    textContent: "",
    parentElement: null,
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
    querySelector() {
      return null;
    },
    focus() {},
    fire(type, event) {
      const evt = Object.assign({ preventDefault() {}, type }, event || {});
      return Promise.all((listeners.get(type) || []).map((h) => h(evt)));
    },
  };
}

const IDS = [
  "personal-security-tab-pane",
  "profile-name-input",
  "profile-name-error",
  "profile-date-of-birth-input",
  "profile-date-of-birth-error",
  "profile-ranking-visible-input",
  "profile-public-profile-input",
  "profile-full-name-public-input",
  "profile-language-select",
  "profile-prefered-language-select",
  "profile-country-select",
  "profile-subdivision-select",
  "personal-data-form",
];

/**
 * Evaluate the page script against a fresh DOM stub.
 *
 * @param {object} responseBody What the personal-data POST answers with.
 * @param {boolean} shieldedAtLoad The server-rendered `data-shielded` value.
 */
function loadScript(responseBody, shieldedAtLoad) {
  const els = new Map();
  for (const id of IDS) {
    els.set(id, makeEl(id, id.endsWith("form") ? "form" : "input"));
  }
  const pane = els.get("personal-security-tab-pane");
  pane.dataset.personalDataUrl = "/user/profile/personal-data";
  pane.dataset.subdivisionsUrl = "/user/profile/subdivisions";
  pane.dataset.detectUrl = "/user/profile/location/detect";

  els.get("profile-public-profile-input").dataset.shielded = shieldedAtLoad ? "1" : "0";
  els.get("profile-name-input").value = "Fulano de Tal";
  els.get("profile-date-of-birth-input").value = "1990-06-15";

  const requests = [];
  const context = {
    console,
    setTimeout,
    clearTimeout,
    Promise,
    JSON,
    Object,
    Array,
    String,
    Boolean,
    Error,
    URL,
    document: {
      getElementById: (id) => els.get(id) || null,
      addEventListener() {},
      querySelectorAll: () => [],
      querySelector: () => null,
      createElement: (tag) => makeEl("", tag),
    },
    window: {
      addEventListener() {},
      location: { assign() {} },
      bootstrap: undefined,
      setTimeout,
      clearTimeout,
    },
    fetch: async (url, init) => {
      requests.push({ url, init });
      return {
        ok: true,
        status: 200,
        json: async () => responseBody,
      };
    },
  };
  context.globalThis = context;
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(SCRIPT, "utf8"), context, { filename: SCRIPT });
  return { els, requests };
}

// ── 1. The regression: a successful save must not throw ─────────────────────

async function testSaveDoesNotThrowOnTheShieldSync() {
  const { els, requests } = loadScript(
    {
      name: "Fulano de Tal",
      date_of_birth: "1990-06-15",
      ranking_visible: true,
      public_profile: false,
      full_name_public: true,
      age_shielded: false,
    },
    false,
  );

  els.get("profile-full-name-public-input").checked = true;
  await els.get("personal-data-form").fire("submit");

  assert.strictEqual(requests.length, 1, "the save must reach the server exactly once");
  const sent = JSON.parse(requests[0].init.body);
  assert.strictEqual(sent.full_name_public, true, "the opt-in must be sent to the server");

  // The bug rendered the ReferenceError message beside the Save button while the
  // POST had already committed. Asserting on the error slot is what pins it: a
  // thrown ReferenceError lands there, so an empty slot proves none was thrown.
  const nameError = els.get("profile-name-error");
  assert.ok(
    !String(nameError.textContent).includes("is not defined"),
    `a successful save must not surface a ReferenceError, got: ${nameError.textContent}`,
  );
  assert.strictEqual(
    els.get("profile-full-name-public-input").checked,
    true,
    "the checkbox must stay checked after the server confirms it",
  );
}

// ── 2. The shield is re-read from the response, not the page-load snapshot ──

async function testShieldIsRederivedFromTheResponse() {
  const { els } = loadScript(
    {
      name: "Fulano de Tal",
      date_of_birth: "2012-06-15",
      ranking_visible: true,
      public_profile: false,
      full_name_public: false,
      age_shielded: true,
    },
    false,
  );

  await els.get("personal-data-form").fire("submit");

  assert.strictEqual(
    els.get("profile-public-profile-input").disabled,
    true,
    "a save that shields the account must disable the public-profile opt-in",
  );
  assert.strictEqual(
    els.get("profile-full-name-public-input").disabled,
    true,
    "a save that shields the account must disable the full-name opt-in",
  );
  assert.strictEqual(els.get("profile-public-profile-input").dataset.shielded, "1");
}

// ── 3. Leaving the shield re-enables both opt-ins without a reload ──────────

async function testLeavingTheShieldReEnablesBothOptIns() {
  const { els } = loadScript(
    {
      name: "Fulano de Tal",
      date_of_birth: "1990-06-15",
      ranking_visible: true,
      public_profile: false,
      full_name_public: false,
      age_shielded: false,
    },
    true,
  );

  els.get("profile-ranking-visible-input").checked = true;
  await els.get("personal-data-form").fire("submit");

  assert.strictEqual(
    els.get("profile-full-name-public-input").disabled,
    false,
    "an account that is no longer shielded must regain the full-name opt-in",
  );
  assert.strictEqual(els.get("profile-public-profile-input").dataset.shielded, "0");
}

// ── 4. Re-enabling ranking visibility must not hand back a shielded control ─

async function testRankingToggleRespectsTheShield() {
  const { els } = loadScript({ age_shielded: true }, true);
  const ranking = els.get("profile-ranking-visible-input");
  const publicProfile = els.get("profile-public-profile-input");

  ranking.checked = false;
  await ranking.fire("change");
  assert.strictEqual(publicProfile.disabled, true, "hiding from the ranking disables the public profile");

  ranking.checked = true;
  await ranking.fire("change");
  assert.strictEqual(
    publicProfile.disabled,
    true,
    "re-enabling ranking visibility must not hand the checkbox back to a shielded account",
  );
}

async function main() {
  await testSaveDoesNotThrowOnTheShieldSync();
  await testShieldIsRederivedFromTheResponse();
  await testLeavingTheShieldReEnablesBothOptIns();
  await testRankingToggleRespectsTheShield();
  console.log("arena profile visibility save contract: all checks passed");
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
