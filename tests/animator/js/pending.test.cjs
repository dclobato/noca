//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

// Browser-independent contract test for animator-pending.js: the snapshot-driven
// pending list renders each authoritative (already-ordered) entry, hides itself
// when empty, and writes team/label text via textContent so a hostile team name
// can never be parsed as markup.

"use strict";

const assert = require("assert");
const path = require("path");

const pending = require(path.join(__dirname, "..", "..", "..", "animator", "static", "js", "animator-pending.js"));

// ── Minimal DOM shim ─────────────────────────────────────────────────────────
class El {
  constructor(tag) {
    this.tagName = tag.toUpperCase();
    this.attributes = {};
    this.children = [];
    this._text = "";
  }
  setAttribute(name, value) {
    this.attributes[name] = String(value);
  }
  getAttribute(name) {
    return Object.prototype.hasOwnProperty.call(this.attributes, name) ? this.attributes[name] : null;
  }
  removeAttribute(name) {
    delete this.attributes[name];
  }
  get hidden() {
    return Object.prototype.hasOwnProperty.call(this.attributes, "hidden");
  }
  set textContent(value) {
    // Assigning textContent stores the raw string; children are discarded. This
    // mirrors the browser: no HTML parsing ever occurs.
    this._text = String(value);
    this.children = [];
  }
  get textContent() {
    if (this.children.length) {
      return this.children.map((c) => c.textContent).join("");
    }
    return this._text;
  }
  replaceChildren() {
    this.children = [];
  }
  appendChild(child) {
    this.children.push(child);
    return child;
  }
}

const doc = {
  createElement(tag) {
    return new El(tag);
  },
};

function makeList() {
  const container = new El("section");
  const list = new El("ul");
  const component = pending.createPendingList({ doc, container, list });
  return { container, list, component };
}

// ── Renders entries in the given (authoritative) order, container shown ──────
(function testRenderOrderAndText() {
  const { container, list, component } = makeList();
  component.render([
    { team_id: "t2", problem_id: "p2", team_name: "bravo", problem_label: "B" },
    { team_id: "t1", problem_id: "p1", team_name: "alpha", problem_label: "A" },
  ]);
  assert.strictEqual(container.hidden, false, "container is shown when there are entries");
  assert.strictEqual(list.children.length, 2);
  assert.strictEqual(list.children[0].textContent, "bravo waiting problem B");
  assert.strictEqual(list.children[1].textContent, "alpha waiting problem A");
  // Data hooks are attached for the snapshot-gated flash addressing.
  assert.strictEqual(list.children[0].getAttribute("data-team-id"), "t2");
  assert.strictEqual(list.children[0].getAttribute("data-problem-id"), "p2");
})();

// ── An empty (or missing) list hides the container ───────────────────────────
(function testEmptyHides() {
  const { container, list, component } = makeList();
  component.render([{ team_id: "t1", problem_id: "p1", team_name: "alpha", problem_label: "A" }]);
  assert.strictEqual(container.hidden, false);
  component.render([]);
  assert.strictEqual(container.hidden, true, "empty list hides the container");
  assert.strictEqual(list.children.length, 0, "empty list clears the DOM");
  component.render(undefined);
  assert.strictEqual(container.hidden, true, "missing list also hides the container");
})();

// ── A hostile team name is written as text, never parsed as markup ───────────
(function testXssSafeText() {
  const { list, component } = makeList();
  const evil = '<img src=x onerror="alert(1)">';
  component.render([{ team_id: "t1", problem_id: "p1", team_name: evil, problem_label: "A" }]);
  // The whole string is the textContent of a single <li>; no child elements were
  // created from the markup, so nothing could execute.
  assert.strictEqual(list.children.length, 1);
  assert.strictEqual(list.children[0].tagName, "LI");
  assert.strictEqual(list.children[0].textContent, evil + " waiting problem A");
  assert.strictEqual(list.children[0].children.length, 0, "no markup was parsed into child nodes");
})();

console.log("animator-pending contract: all assertions passed");
