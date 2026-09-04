//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

// Browser-independent contract test for the shared `.arena-combo` controller
// and the Source/Author/License suggestion combobox built on it.
//
// The behavior these lock down is asynchronous, so a source-text assertion
// cannot see it: closing the listbox (blur, Escape, a selection) must also
// cancel the queued debounce and abort the in-flight request. Without that, a
// response that lands after the close reopens a listbox the user already
// dismissed or walked away from.

"use strict";

const assert = require("assert");
const path = require("path");

const listboxApi = require(path.join(__dirname, "..", "..", "..", "arena", "static", "js", "arena-combo-listbox.js"));
const suggestApi = require(path.join(__dirname, "..", "..", "..", "arena", "static", "js", "arena-suggest-combobox.js"));

// ── Minimal DOM shim ────────────────────────────────────────────────────────

function makeEl(tag) {
  const classes = new Set();
  const listeners = new Map();
  return {
    tagName: tag,
    children: [],
    attributes: {},
    listeners,
    textContent: "",
    innerHTML: null,
    value: "",
    id: "",
    className: "",
    style: {},
    focusCount: 0,
    dispatched: [],
    classList: {
      add: (name) => classes.add(name),
      remove: (name) => classes.delete(name),
      contains: (name) => classes.has(name),
      toggle: (name, force) => (force ? classes.add(name) : classes.delete(name)),
    },
    setAttribute(name, value) {
      this.attributes[name] = String(value);
    },
    getAttribute(name) {
      return Object.prototype.hasOwnProperty.call(this.attributes, name) ? this.attributes[name] : null;
    },
    appendChild(child) {
      this.children.push(child);
      return child;
    },
    replaceChildren(...nodes) {
      this.children = nodes;
    },
    addEventListener(type, handler) {
      if (!listeners.has(type)) listeners.set(type, []);
      listeners.get(type).push(handler);
    },
    dispatchEvent(event) {
      this.dispatched.push(event.type);
      (listeners.get(event.type) || []).forEach((handler) => handler(event));
      return true;
    },
    focus() {
      this.focusCount += 1;
    },
    fire(type, event) {
      (listeners.get(type) || []).forEach((handler) => handler(event || { preventDefault() {} }));
    },
    click() {
      this.fire("click");
    },
  };
}

const doc = { createElement: (tag) => makeEl(tag) };

function makeHarness(options) {
  const input = makeEl("input");
  input.id = "source";
  const listbox = makeEl("div");
  const statusEl = makeEl("div");
  return Object.assign({ input, listbox, statusEl }, options);
}

/** Resolve after `count` turns of the microtask/macrotask queue. */
function settle(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms === undefined ? 5 : ms));
}

function optionTexts(listbox) {
  return listbox.children.map((child) => child.textContent);
}

// ── 1. Blur during the debounce cancels the queued request ──────────────────

async function testBlurDuringDebounce() {
  const harness = makeHarness();
  let searches = 0;
  const combo = listboxApi.createComboListbox({
    input: harness.input,
    listbox: harness.listbox,
    statusEl: harness.statusEl,
    doc,
    minLength: 2,
    debounceMs: 20,
    closeOnBlur: true,
    search: async () => {
      searches += 1;
      return ["late result"];
    },
  });

  harness.input.value = "local 2023";
  harness.input.fire("input");
  harness.input.fire("blur");
  await settle(60);

  assert.strictEqual(searches, 0, "a blur during the debounce must cancel the queued search");
  assert.strictEqual(combo.isOpen(), false, "the listbox must stay closed after a blur");
  assert.strictEqual(harness.input.getAttribute("aria-expanded"), "false");
}

// ── 2. Escape while loading discards the in-flight response ─────────────────

async function testEscapeWhileLoading() {
  const harness = makeHarness();
  let aborted = false;
  let releaseResponse;
  const responseReady = new Promise((resolve) => {
    releaseResponse = resolve;
  });

  const combo = listboxApi.createComboListbox({
    input: harness.input,
    listbox: harness.listbox,
    statusEl: harness.statusEl,
    doc,
    minLength: 2,
    debounceMs: 1,
    search: async (query, signal) => {
      signal.addEventListener("abort", () => {
        aborted = true;
      });
      await responseReady;
      return ["late result"];
    },
  });

  harness.input.value = "cutigi carlos";
  harness.input.fire("input");
  await settle(10);
  assert.ok(combo.isOpen(), "the loading state should be visible while the request is in flight");

  let escapePrevented = false;
  harness.input.fire("keydown", {
    key: "Escape",
    preventDefault() {
      escapePrevented = true;
    },
  });
  assert.ok(escapePrevented, "Escape must be consumed while the listbox is open");
  assert.strictEqual(combo.isOpen(), false, "Escape must close the listbox");
  assert.ok(aborted, "Escape must abort the in-flight request");

  releaseResponse();
  await settle(20);

  assert.strictEqual(combo.isOpen(), false, "a response arriving after Escape must not reopen the listbox");
  assert.deepStrictEqual(optionTexts(harness.listbox), [], "a discarded response must render nothing");
}

// ── 3. A superseded response never paints over a newer one ──────────────────

async function testSupersededResponseIgnored() {
  const harness = makeHarness();
  const releases = [];
  const combo = listboxApi.createComboListbox({
    input: harness.input,
    listbox: harness.listbox,
    statusEl: harness.statusEl,
    doc,
    minLength: 2,
    debounceMs: 1,
    search: (query) =>
      new Promise((resolve) => {
        releases.push(() => resolve([`result for ${query}`]));
      }),
  });

  harness.input.value = "loc";
  harness.input.fire("input");
  await settle(10);
  harness.input.value = "local 2023";
  harness.input.fire("input");
  await settle(10);

  // The first request settles last; it must lose.
  releases[0]();
  await settle(10);
  releases[1]();
  await settle(10);

  assert.ok(combo.isOpen());
  assert.deepStrictEqual(optionTexts(harness.listbox), ["result for local 2023"]);
}

// ── 4. Keyboard navigation and selection ────────────────────────────────────

async function testKeyboardSelection() {
  const harness = makeHarness();
  const picked = [];
  const combo = listboxApi.createComboListbox({
    input: harness.input,
    listbox: harness.listbox,
    statusEl: harness.statusEl,
    doc,
    minLength: 2,
    debounceMs: 1,
    search: async () => ["first", "second"],
    onSelect: (item) => picked.push(item),
  });

  harness.input.value = "fa";
  harness.input.fire("input");
  await settle(10);

  assert.strictEqual(combo.optionCount(), 2);
  // The server ranking is the only filter: nothing may be dropped for failing
  // to contain the typed text.
  assert.deepStrictEqual(optionTexts(harness.listbox), ["first", "second"]);

  let enterPreventedWhileIdle = false;
  harness.input.fire("keydown", {
    key: "Enter",
    preventDefault() {
      enterPreventedWhileIdle = true;
    },
  });
  assert.ok(!enterPreventedWhileIdle, "Enter must still submit the form while no option is highlighted");
  assert.deepStrictEqual(picked, []);

  harness.input.fire("keydown", { key: "ArrowDown", preventDefault() {} });
  assert.strictEqual(combo.activeIndex(), 0);
  assert.strictEqual(harness.input.getAttribute("aria-activedescendant"), "combo-option-0");
  harness.input.fire("keydown", { key: "ArrowDown", preventDefault() {} });
  assert.strictEqual(combo.activeIndex(), 1);
  harness.input.fire("keydown", { key: "Enter", preventDefault() {} });
  assert.deepStrictEqual(picked, ["second"]);
}

// ── 5. The suggestion combobox notifies the form on a pick ──────────────────

async function testSuggestComboboxSelection() {
  const harness = makeHarness();
  const requested = [];
  const combo = suggestApi.createSuggestCombobox({
    input: harness.input,
    listbox: harness.listbox,
    statusEl: harness.statusEl,
    doc,
    debounceMs: 1,
    url: "/admin/problems/suggestions",
    field: "source",
    fetchImpl: async (url) => {
      requested.push(url);
      return {
        ok: true,
        json: async () => ({ suggestions: ["VI Maratona de Programação InterIF - 2023 / Fase Local", 42] }),
      };
    },
  });

  harness.input.value = "Local 2023";
  harness.input.fire("input");
  await settle(10);

  assert.deepStrictEqual(requested, ["/admin/problems/suggestions?field=source&q=Local+2023"]);
  assert.deepStrictEqual(
    optionTexts(harness.listbox),
    ["VI Maratona de Programação InterIF - 2023 / Fase Local"],
    "non-string payload entries are dropped and the ranked match is kept verbatim"
  );

  harness.listbox.children[0].click();

  assert.strictEqual(harness.input.value, "VI Maratona de Programação InterIF - 2023 / Fase Local");
  assert.deepStrictEqual(
    harness.input.dispatched,
    ["input", "change"],
    "a pick must notify form validation and the unsaved-changes guard"
  );
  assert.strictEqual(combo.isOpen(), false, "picking closes the listbox");

  // The synthetic `input` event must not restart the search it just satisfied.
  await settle(10);
  assert.strictEqual(requested.length, 1);
  assert.strictEqual(combo.isOpen(), false);
}

// ── 6. A term below the minimum defers, and says so ─────────────────────────

async function testShortTermDefersWithHint() {
  const harness = makeHarness();
  const requested = [];
  const combo = suggestApi.createSuggestCombobox({
    input: harness.input,
    listbox: harness.listbox,
    statusEl: harness.statusEl,
    doc,
    debounceMs: 1,
    url: "/admin/problems/suggestions",
    field: "source",
    fetchImpl: async (url) => {
      requested.push(url);
      return { ok: true, json: async () => ({ suggestions: ["never requested"] }) };
    },
  });

  // The server declines a term with fewer than three letters or digits, since
  // no branch can answer it without a sequential scan. The client must not
  // send it, and must say why rather than showing an empty result.
  for (const query of ["2024 L", "2024 Lo", "ga", "---", "²²²"]) {
    harness.input.value = query;
    harness.input.fire("input");
    await settle(10);
    assert.deepStrictEqual(requested, [], `"${query}" must not be requested`);
    assert.ok(combo.isOpen(), `"${query}" must explain itself rather than close silently`);
    assert.deepStrictEqual(optionTexts(harness.listbox), ["Type at least 3 letters or digits per word."]);
  }

  // The moment every term qualifies, the search runs.
  harness.input.value = "2024 Loc";
  harness.input.fire("input");
  await settle(10);
  assert.deepStrictEqual(requested, ["/admin/problems/suggestions?field=source&q=2024+Loc"]);

  // An emptied field closes rather than nagging.
  harness.input.value = "";
  harness.input.fire("input");
  assert.strictEqual(combo.isOpen(), false);
}

// ── 7. Below the minimum length nothing is requested ────────────────────────

async function testMinimumQueryLength() {
  const harness = makeHarness();
  let searches = 0;
  const combo = listboxApi.createComboListbox({
    input: harness.input,
    listbox: harness.listbox,
    doc,
    minLength: 2,
    debounceMs: 1,
    search: async () => {
      searches += 1;
      return ["never"];
    },
  });

  harness.input.value = "l";
  harness.input.fire("input");
  await settle(10);

  assert.strictEqual(searches, 0);
  assert.strictEqual(combo.isOpen(), false);
}

async function main() {
  await testBlurDuringDebounce();
  await testEscapeWhileLoading();
  await testSupersededResponseIgnored();
  await testKeyboardSelection();
  await testSuggestComboboxSelection();
  await testShortTermDefersWithHint();
  await testMinimumQueryLength();
  console.log("arena combo listbox contract: all checks passed");
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
