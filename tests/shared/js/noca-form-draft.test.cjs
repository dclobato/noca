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

const scriptPath = path.resolve(__dirname, "../../../shared/static/js/noca-form-draft.js");
const source = fs.readFileSync(scriptPath, "utf8");

// ── Minimal DOM stand-in ────────────────────────────────────────────────────

class FakeEvent {
  constructor(type, init) {
    this.type = type;
    this.bubbles = Boolean(init && init.bubbles);
    this.defaultPrevented = false;
    this.target = null;
  }
  preventDefault() { this.defaultPrevented = true; }
}
class FakeCustomEvent extends FakeEvent {
  constructor(type, init) { super(type, init); this.detail = init && init.detail; }
}

class Element {
  constructor(tag) {
    this.tagName = tag.toUpperCase();
    this.attributes = {};
    this.dataset = {};
    this.children = [];
    this.parentNode = null;
    this.listeners = {};
    this.className = "";
    this.textContent = "";
    this.id = "";
  }
  setAttribute(name, value) {
    this.attributes[name] = String(value);
    if (name.startsWith("data-")) {
      const key = name.slice(5).replace(/-([a-z])/g, (_m, c) => c.toUpperCase());
      this.dataset[key] = String(value);
    }
  }
  getAttribute(name) { return Object.prototype.hasOwnProperty.call(this.attributes, name) ? this.attributes[name] : null; }
  hasAttribute(name) { return Object.prototype.hasOwnProperty.call(this.attributes, name); }
  appendChild(child) { child.parentNode = this; this.children.push(child); return child; }
  removeChild(child) { this.children = this.children.filter((c) => c !== child); child.parentNode = null; }
  insertAdjacentElement(_where, el) { this.parentNode.appendChild(el); this.lastInserted = el; }
  addEventListener(type, listener) { (this.listeners[type] = this.listeners[type] || []).push(listener); }
  dispatchEvent(event) {
    event.target = event.target || this;
    (this.listeners[event.type] || []).forEach((l) => l(event));
    if (event.bubbles && this.bubbleTo) this.bubbleTo.dispatchEvent(event);
    return !event.defaultPrevented;
  }
  querySelectorAll(selector) {
    const out = [];
    const walk = (el) => { el.children.forEach((c) => { if (matches(c, selector)) out.push(c); walk(c); }); };
    walk(this);
    return out;
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  get buttons() { return this.querySelectorAll("button"); }
}

function matches(el, selector) {
  if (selector === "button") return el.tagName === "BUTTON";
  const m = /^(form)?\[([a-z-]+)(?:="([^"]*)")?\]$/.exec(selector);
  if (!m) return false;
  if (m[1] && el.tagName !== "FORM") return false;
  if (!el.hasAttribute(m[2])) return false;
  return m[3] === undefined || el.getAttribute(m[2]) === m[3];
}

function control(form, props) {
  const el = Object.assign(new Element("input"), { type: "text", name: "", value: "", checked: false, disabled: false }, props);
  el.form = form;
  el.bubbleTo = form.document;
  form.elements.push(el);
  return el;
}

function makeHarness(options = {}) {
  const store = new Map();
  let failWrites = false;
  const localStorage = {
    get length() { return store.size; },
    key(i) { return Array.from(store.keys())[i]; },
    getItem(k) { return store.has(k) ? store.get(k) : null; },
    setItem(k, v) { if (failWrites) throw new Error("QuotaExceededError"); store.set(k, String(v)); },
    removeItem(k) { store.delete(k); },
  };
  const timers = [];
  const document = new Element("#document");
  document.createElement = (tag) => new Element(tag);
  document.readyState = "loading";
  document.visibilityState = "visible";
  const window = {
    localStorage,
    setTimeout(fn, ms) { const id = timers.length + 1; timers.push({ id, fn, ms, done: false }); return id; },
    clearTimeout(id) { const t = timers.find((x) => x.id === id); if (t) t.done = true; },
    addEventListener(type, l) { document.addEventListener("window:" + type, l); },
    fetch: options.fetch,
  };
  const sandbox = { window, document, Event: FakeEvent, CustomEvent: FakeCustomEvent, Promise, JSON, Array, Object, Date, isNaN };
  sandbox.window.document = document;
  const body = document.appendChild(new Element("body"));
  if (options.owner) { const el = new Element("div"); el.setAttribute("data-noca-draft-owner", options.owner); body.appendChild(el); }
  if (options.confirmed) { const el = new Element("div"); el.setAttribute("data-noca-draft-confirmed", options.confirmed); body.appendChild(el); }
  if (options.heartbeatUrl) { const el = new Element("div"); el.setAttribute("data-noca-presence", ""); el.setAttribute("data-heartbeat-url", options.heartbeatUrl); body.appendChild(el); }
  const form = new Element("form");
  form.id = "edit-form";
  form.elements = [];
  form.document = document;
  form.bubbleTo = document;
  form.requestSubmitCalls = [];
  form.requestSubmit = (submitter) => { form.requestSubmitCalls.push(submitter); };
  if (options.draftKey) form.setAttribute("data-noca-draft", options.draftKey);
  body.appendChild(form);
  const logout = new Element("form");
  logout.setAttribute("data-noca-draft-clear", "");
  logout.document = document;
  body.appendChild(logout);
  return {
    store, localStorage, form, logout, document, window, timers,
    setFailWrites(v) { failWrites = v; },
    load() {
      vm.createContext(sandbox);
      vm.runInContext(source, sandbox);
      document.readyState = "complete";
      document.dispatchEvent(new FakeEvent("DOMContentLoaded"));
      return sandbox.window.NocaFormDraft;
    },
    runTimers() { timers.filter((t) => !t.done).forEach((t) => { t.done = true; t.fn(); }); },
    input(el) { const ev = new FakeEvent("input", { bubbles: true }); ev.target = el; el.dispatchEvent(ev); },
    submit(submitter) { const ev = new FakeEvent("submit", { bubbles: true }); ev.submitter = submitter; form.dispatchEvent(ev); return ev; },
    notice() { return body.children.find((c) => c.className && c.className.indexOf("noca-form-draft-notice") === 0) || null; },
  };
}

// Values built inside the vm realm carry another realm's prototypes, so
// structural comparisons go through JSON.
const same = (a, b) => assert.equal(JSON.stringify(a), JSON.stringify(b));

const OWNER = "abc123";
const KEY = "noca:form-draft:abc123:arena-problem-definition:p1";

// Serialization: files, disabled, ignored, and unchecked boxes are skipped;
// hidden inputs and checked boxes are kept, in document order.
{
  const h = makeHarness({ owner: OWNER, draftKey: "arena-problem-definition:p1" });
  control(h.form, { name: "title", value: "A" });
  control(h.form, { name: "pdf", type: "file", value: "x.pdf" });
  control(h.form, { name: "gone", value: "no", disabled: true });
  const ignored = control(h.form, { name: "active_tab", type: "hidden", value: "statement" });
  ignored.setAttribute("data-noca-draft-ignore", "");
  control(h.form, { name: "category_ids", type: "hidden", value: "c1" });
  control(h.form, { name: "flag", type: "checkbox", value: "on", checked: false });
  control(h.form, { name: "flag2", type: "checkbox", value: "on", checked: true });
  const api = h.load();
  same(api.serialize(h.form), [["title", "A"], ["category_ids", "c1"], ["flag2", "on"]]);
  assert.equal(api.storageKey(OWNER, "k"), "noca:form-draft:abc123:k");
}

// A debounced write follows input; flush on pagehide writes immediately; the
// payload carries collected metadata and is never removed on submit.
{
  const h = makeHarness({ owner: OWNER, draftKey: "arena-problem-definition:p1" });
  const title = control(h.form, { name: "title", value: "A" });
  h.form.addEventListener("noca:form-draft-collect", (e) => { e.detail.meta.cats = [{ id: "c1" }]; });
  h.load();
  title.value = "B";
  h.input(title);
  assert.equal(h.store.size, 0, "no write before the debounce elapses");
  h.runTimers();
  const saved = JSON.parse(h.store.get(KEY));
  assert.deepEqual(saved.fields, [["title", "B"]]);
  assert.deepEqual(saved.meta, { cats: [{ id: "c1" }] });
  title.value = "C";
  h.input(title);
  h.document.dispatchEvent(new FakeEvent("window:pagehide"));
  assert.deepEqual(JSON.parse(h.store.get(KEY)).fields, [["title", "C"]]);
  h.submit();
  assert.ok(h.store.has(KEY), "submit never clears the draft");
}

// A draft equal to the rendered form is kept silently; a differing draft is
// offered, Restore writes the controls (checkbox off, repeated names in order),
// emits the restored event with its metadata, and Discard deletes it.
{
  const h = makeHarness({ owner: OWNER, draftKey: "arena-problem-definition:p1" });
  control(h.form, { name: "title", value: "A" });
  h.store.set(KEY, JSON.stringify({ v: 1, savedAt: "2026-08-30T10:00:00Z", fields: [["title", "A"]] }));
  h.load();
  assert.equal(h.notice(), null, "an equal draft raises no notice");
  assert.ok(h.store.has(KEY), "an equal draft is kept for a later 422 re-render");
}
{
  const h = makeHarness({ owner: OWNER, draftKey: "arena-problem-definition:p1" });
  const title = control(h.form, { name: "title", value: "A" });
  const box = control(h.form, { name: "flag", type: "checkbox", value: "on", checked: true });
  const id1 = control(h.form, { name: "ids", type: "hidden", value: "x" });
  const id2 = control(h.form, { name: "ids", type: "hidden", value: "y" });
  const events = [];
  h.document.addEventListener("input", (e) => events.push(e.target.name));
  let restored = null;
  h.form.addEventListener("noca:form-draft-restored", (e) => { restored = e.detail; });
  h.store.set(KEY, JSON.stringify({ v: 1, savedAt: "2026-08-30T10:00:00Z", fields: [["title", "B"], ["ids", "p"], ["ids", "q"]], meta: { cats: 1 } }));
  h.load();
  const notice = h.notice();
  assert.ok(notice, "a differing draft is offered");
  assert.match(notice.children[0].textContent, /must be chosen again/);
  const [restoreBtn, discardBtn] = notice.children[1].children;
  assert.equal(restoreBtn.textContent, "Restore draft");
  restoreBtn.listeners.click[0]();
  assert.equal(title.value, "B");
  assert.equal(box.checked, false, "a box absent from the draft is unchecked");
  assert.equal(id1.value, "p"); assert.equal(id2.value, "q");
  assert.deepEqual(events, ["title", "flag", "ids", "ids"]);
  same(restored.meta, { cats: 1 });
  assert.equal(h.notice(), null);
  // Discard path on a fresh harness
  const h2 = makeHarness({ owner: OWNER, draftKey: "arena-problem-definition:p1" });
  control(h2.form, { name: "title", value: "A" });
  h2.store.set(KEY, JSON.stringify({ v: 1, savedAt: "x", fields: [["title", "B"]] }));
  h2.load();
  h2.notice().children[1].children[1].listeners.click[0]();
  assert.equal(h2.store.has(KEY), false, "Discard deletes the draft");
  void discardBtn;
}

// Owner scoping: another account's drafts are purged on load and never
// offered; confirmed keys are deleted; no owner means no draft writes.
{
  const h = makeHarness({ owner: OWNER, draftKey: "arena-problem-definition:p1", confirmed: "arena-problem-definition:p2 other" });
  control(h.form, { name: "title", value: "A" });
  h.store.set("noca:form-draft:zzz:arena-problem-definition:p1", JSON.stringify({ v: 1, savedAt: "x", fields: [["title", "Z"]] }));
  h.store.set("noca:form-draft:abc123:arena-problem-definition:p2", "{}");
  h.store.set("noca-theme", "dark");
  h.load();
  assert.equal(h.notice(), null, "another owner's draft is never offered");
  assert.equal(h.store.has("noca:form-draft:zzz:arena-problem-definition:p1"), false);
  assert.equal(h.store.has("noca:form-draft:abc123:arena-problem-definition:p2"), false, "confirmed key deleted");
  assert.equal(h.store.get("noca-theme"), "dark");
}
{
  const h = makeHarness({ draftKey: "arena-problem-definition:p1" });
  const title = control(h.form, { name: "title", value: "A" });
  h.load();
  title.value = "B"; h.input(title); h.runTimers();
  assert.equal(h.store.size, 0, "no owner, no draft");
}

// A storage failure warns once and never blocks.
{
  const h = makeHarness({ owner: OWNER, draftKey: "arena-problem-definition:p1" });
  const title = control(h.form, { name: "title", value: "A" });
  h.load();
  h.setFailWrites(true);
  title.value = "B"; h.input(title); h.runTimers();
  assert.match(h.notice().children[0].textContent, /could not keep a local draft/);
  const first = h.notice();
  title.value = "C"; h.input(title); h.runTimers();
  assert.equal(h.notice(), first, "the warning is raised once");
}

// Logout clears exactly the NOCA draft keys.
{
  const h = makeHarness({ owner: OWNER });
  h.store.set("noca:form-draft:abc123:a", "{}");
  h.store.set("noca:form-draft:zzz:b", "{}");
  h.store.set("noca-theme", "dark");
  h.load();
  h.logout.dispatchEvent(new FakeEvent("submit"));
  assert.deepEqual(Array.from(h.store.keys()), ["noca-theme"]);
}

// Session probe outcomes.
function probeHarness(responder) {
  const calls = [];
  const h = makeHarness({
    owner: OWNER, draftKey: "arena-problem-definition:p1", heartbeatUrl: "/hb",
    fetch(url, init) { calls.push([url, init]); return responder(); },
  });
  control(h.form, { name: "title", value: "A" });
  h.load();
  h.calls = calls;
  return h;
}
const tick = () => new Promise((r) => setImmediate(r));

(async () => {
  // ok -> exactly one resubmit carrying the original submitter
  {
    const h = probeHarness(() => Promise.resolve({ ok: true, status: 200, type: "basic" }));
    const submitter = { name: "save" };
    const ev = h.submit(submitter);
    assert.equal(ev.defaultPrevented, true);
    assert.equal(h.calls[0][0], "/hb");
    assert.equal(h.calls[0][1].redirect, "manual");
    await tick();
    assert.deepEqual(h.form.requestSubmitCalls, [submitter]);
    // the bypassed resubmit is let through without another probe
    const ev2 = h.submit(submitter);
    assert.equal(ev2.defaultPrevented, false);
    assert.equal(h.calls.length, 1);
  }
  // redirect -> session-expired notice, no submit
  {
    const h = probeHarness(() => Promise.resolve({ ok: false, status: 0, type: "opaqueredirect" }));
    h.submit();
    await tick();
    assert.deepEqual(h.form.requestSubmitCalls, []);
    assert.match(h.notice().children[0].textContent, /session has expired/);
    assert.ok(h.store.has(KEY), "the draft was flushed before the probe");
  }
  // network failure -> unverified notice with Save anyway
  {
    const h = probeHarness(() => Promise.reject(new Error("offline")));
    h.submit();
    await tick();
    assert.deepEqual(h.form.requestSubmitCalls, []);
    const notice = h.notice();
    assert.match(notice.children[0].textContent, /could not be verified/);
    notice.children[1].children[0].listeners.click[0]();
    assert.equal(h.form.requestSubmitCalls.length, 1);
  }
  // a submit already cancelled by the page's own listener is left alone
  {
    const h = probeHarness(() => Promise.resolve({ ok: true }));
    h.form.addEventListener("submit", (e) => e.preventDefault());
    h.submit();
    await tick();
    assert.equal(h.calls.length, 0);
  }
  // no heartbeat configured -> plain submit
  {
    const h = makeHarness({ owner: OWNER, draftKey: "arena-problem-definition:p1" });
    control(h.form, { name: "title", value: "A" });
    h.load();
    assert.equal(h.submit().defaultPrevented, false);
  }
})().catch((error) => { console.error(error); process.exit(1); });
