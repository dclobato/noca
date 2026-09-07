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

const root = path.resolve(__dirname, "../../..");
const source = fs.readFileSync(
  path.join(root, "web/static/js/contest-timing-timeline.js"), "utf8");

function element(attributes = {}) {
  const classes = new Set();
  return {
    attributes,
    classList: {
      add(...names) { names.forEach((name) => classes.add(name)); },
      remove(...names) { names.forEach((name) => classes.delete(name)); },
    },
    dataset: {},
    hidden: false,
    style: {},
    getAttribute(name) { return this.attributes[name] ?? null; },
    setAttribute(name, value) { this.attributes[name] = String(value); },
  };
}

function boot(includeMarker = true) {
  const listeners = new Map();
  const elements = {
    "timing-timeline-wrapper": element({
      "data-duration-minutes": "300",
      "data-freeze-minutes": "240",
      "data-blind-minutes": "270",
    }),
    "timeline-seg-fallback": element(),
    "timeline-seg-live": element(),
    "timeline-seg-frozen": element(),
    "timeline-seg-silence": element(),
  };
  if (includeMarker) elements["timeline-now-marker"] = element();

  const document = {
    getElementById(id) { return elements[id] ?? null; },
    addEventListener(type, listener) {
      const typeListeners = listeners.get(type) ?? [];
      typeListeners.push(listener);
      listeners.set(type, typeListeners);
    },
    dispatch(type, detail = undefined) {
      for (const listener of listeners.get(type) ?? []) listener({ detail });
    },
  };

  vm.runInNewContext(source, { document, Number, isNaN, parseInt });
  document.dispatch("DOMContentLoaded");
  return { document, marker: elements["timeline-now-marker"] };
}

function tick(app, nowMs, startMs = 1_000, endMs = 11_000) {
  app.document.dispatch("noca:contest-clock-tick", { nowMs, startMs, endMs });
}

{
  const app = boot();
  // Before the start there is no position to mark yet.
  tick(app, 0);
  assert.equal(app.marker.hidden, true);
  assert.equal(app.marker.style.left, undefined);

  tick(app, 1_000);
  assert.equal(app.marker.hidden, false);
  assert.equal(app.marker.dataset.edge, "start");
  assert.equal(app.marker.style.left, "0.0000%");
  assert.equal(
    app.marker.attributes["aria-label"],
    "Current contest position: 0% elapsed",
  );

  tick(app, 6_000);
  assert.equal(app.marker.style.left, "50.0000%");
  assert.equal(app.marker.dataset.edge, "inside");
  assert.equal(
    app.marker.attributes["aria-label"],
    "Current contest position: 50% elapsed",
  );

  tick(app, 11_000);
  assert.equal(app.marker.style.left, "100.0000%");
  assert.equal(
    app.marker.attributes["aria-label"],
    "Current contest position: 100% elapsed",
  );

  // Past the end it goes away again: a finished contest has no "now" on its
  // own timeline, and clamping left it pinned to the closing edge for good.
  tick(app, 12_000);
  assert.equal(app.marker.hidden, true);
}

{
  const app = boot();
  tick(app, 6_000, 10_000, 10_000);
  assert.equal(app.marker.hidden, true);
  assert.equal(app.marker.style.left, undefined);
}

{
  const app = boot(false);
  tick(app, 6_000);
  assert.equal(app.marker, undefined);
}
