//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

// Browser-independent contract test for animator-animate.js: FLIP measurement
// and playback plus transient-class application with safe timeout cleanup when
// the same class is reapplied. Uses a tiny DOM shim (rows/cells addressable by
// the stable data-* hooks) and fully injected timing primitives.

"use strict";

const assert = require("assert");
const path = require("path");

const animate = require(path.join(__dirname, "..", "..", "..", "animator", "static", "js", "animator-animate.js"));
const diff = require(path.join(__dirname, "..", "..", "..", "animator", "static", "js", "animator-diff.js"));

// Composite cell keys use the same NUL separator the diff module emits.
const K = diff.cellKey;

// ── Minimal DOM shim ─────────────────────────────────────────────────────────
class El {
  constructor(tag) {
    this.tagName = tag.toUpperCase();
    this.attributes = {};
    this.children = [];
    this.style = {};
    this._top = 0;
    const self = this;
    this.classList = {
      _set: {},
      add(c) {
        self.classList._set[c] = true;
      },
      remove(c) {
        delete self.classList._set[c];
      },
      contains(c) {
        return Boolean(self.classList._set[c]);
      },
    };
  }
  setAttribute(name, value) {
    this.attributes[name] = String(value);
  }
  getAttribute(name) {
    return Object.prototype.hasOwnProperty.call(this.attributes, name) ? this.attributes[name] : null;
  }
  getBoundingClientRect() {
    return { top: this._top };
  }
  _walk(cb) {
    this.children.forEach((c) => {
      cb(c);
      c._walk(cb);
    });
  }
  _matches(tag, attr, value) {
    return this.tagName === tag.toUpperCase() && this.getAttribute(attr) === value;
  }
  querySelector(selector) {
    // Supports 'tr[data-team-id="X"]' and
    // 'tr[data-team-id="X"] td[data-problem-id="Y"]'.
    const parts = selector.split(/\s+/);
    const parse = (token) => {
      const m = token.match(/^(\w+)\[([\w-]+)="(.*)"\]$/);
      return { tag: m[1], attr: m[2], value: m[3].replace(/\\(.)/g, "$1") };
    };
    const first = parse(parts[0]);
    let match = null;
    this._walk((n) => {
      if (!match && n._matches(first.tag, first.attr, first.value)) {
        match = n;
      }
    });
    if (!match || parts.length === 1) {
      return match;
    }
    const second = parse(parts[1]);
    let inner = null;
    match._walk((n) => {
      if (!inner && n._matches(second.tag, second.attr, second.value)) {
        inner = n;
      }
    });
    return inner;
  }
}

function makeRow(teamId, top, problemIds) {
  const tr = new El("tr");
  tr.setAttribute("data-team-id", teamId);
  tr._top = top;
  (problemIds || []).forEach((pid) => {
    const td = new El("td");
    td.setAttribute("data-problem-id", pid);
    tr.children.push(td);
  });
  return tr;
}

// A controllable timer scheduler.
function makeTimers() {
  let nextId = 1;
  const timers = {};
  return {
    setTimeout(cb) {
      const id = nextId++;
      timers[id] = cb;
      return id;
    },
    clearTimeout(id) {
      delete timers[id];
    },
    pending() {
      return Object.keys(timers).length;
    },
    flush() {
      Object.keys(timers).forEach((id) => {
        const cb = timers[id];
        delete timers[id];
        cb();
      });
    },
  };
}

// ── CSS timing tokens are the Web Animations timing source ───────────────────
(function testRowAnimationFromCss() {
  const tokens = {
    "--animator-row-motion-duration": "3s",
    "--animator-row-motion-easing": "cubic-bezier(0.22, 1, 0.36, 1)",
  };
  const timing = animate.rowAnimationFromCss(new El("tbody"), () => ({
    getPropertyValue(name) {
      return tokens[name] || "";
    },
  }));

  assert.deepStrictEqual(timing, {
    duration: 3000,
    easing: "cubic-bezier(0.22, 1, 0.36, 1)",
  });
})();

(function testRowAnimationFromCssAcceptsMillisecondsAndRejectsMissingTokens() {
  const element = new El("tbody");
  const milliseconds = animate.rowAnimationFromCss(element, () => ({
    getPropertyValue(name) {
      return name === "--animator-row-motion-duration" ? "450ms" : "ease";
    },
  }));
  const missing = animate.rowAnimationFromCss(element, () => ({
    getPropertyValue() {
      return "";
    },
  }));

  assert.deepStrictEqual(milliseconds, { duration: 450, easing: "ease" });
  assert.strictEqual(missing, null);
})();

// ── measureRows captures every row's top by team id ──────────────────────────
(function testMeasure() {
  const tbody = new El("tbody");
  tbody.children = [makeRow("t1", 10, []), makeRow("t2", 40, [])];
  const applier = animate.createApplier(tbody, { setTimeout() {}, clearTimeout() {}, raf() {} });
  const tops = applier.measureRows();
  assert.strictEqual(tops["t1"], 10);
  assert.strictEqual(tops["t2"], 40);
})();

// ── FLIP: a moved row is inverted then released on the next frame ─────────────
(function testFlip() {
  const tbody = new El("tbody");
  const row = makeRow("t1", 10, []);
  tbody.children = [row];
  let rafCb = null;
  const applier = animate.createApplier(tbody, {
    setTimeout() {},
    clearTimeout() {},
    raf(cb) {
      rafCb = cb;
    },
  });
  const first = applier.measureRows(); // top = 10
  row._top = 60; // the row moved down after re-render
  applier.apply({ rowClasses: {}, cellClasses: {} }, first);
  // Inverted back toward the old position (delta = 10 - 60 = -50).
  assert.strictEqual(row.style.transform, "translateY(-50px)");
  assert.strictEqual(row.style.transition, "none");
  assert.ok(rafCb, "a frame was scheduled");
  rafCb();
  assert.strictEqual(row.style.transform, "");
  assert.strictEqual(row.style.transition, "");
})();

// ── Web Animations: projector rows traverse the full distance directly ───────
(function testFlipWithWebAnimations() {
  const tbody = new El("tbody");
  const row = makeRow("t1", 10, []);
  tbody.children = [row];
  let frames = null;
  let timing = null;
  let rafCalled = false;
  row.animate = (nextFrames, nextTiming) => {
    frames = nextFrames;
    timing = nextTiming;
  };
  const rowAnimation = {
    duration: 1500,
    easing: "cubic-bezier(0.22, 1, 0.36, 1)",
  };
  const applier = animate.createApplier(tbody, {
    rowAnimation,
    raf() {
      rafCalled = true;
    },
  });
  const first = applier.measureRows();
  row._top = 60;
  applier.apply({ rowClasses: {}, cellClasses: {} }, first);

  assert.deepStrictEqual(frames, [
    { transform: "translateY(-50px)" },
    { transform: "translateY(0)" },
  ]);
  assert.strictEqual(timing, rowAnimation);
  assert.strictEqual(rafCalled, false, "Web Animations does not depend on a later style-write frame");
  assert.strictEqual(row.style.transform, undefined, "the CSS-transition fallback was not used");
})();

// ── FLIP is skipped entirely under reduced motion (no transform written) ─────
(function testFlipReducedMotion() {
  const tbody = new El("tbody");
  const row = makeRow("t1", 10, []);
  tbody.children = [row];
  let rafCalled = false;
  const applier = animate.createApplier(tbody, {
    setTimeout() {},
    clearTimeout() {},
    raf() {
      rafCalled = true;
    },
    reducedMotion: () => true,
  });
  const first = applier.measureRows();
  row._top = 60; // the row "moved"
  applier.apply({ rowClasses: {}, cellClasses: {} }, first);
  assert.strictEqual(row.style.transform, undefined, "no transform written under reduced motion");
  assert.strictEqual(rafCalled, false, "no frame scheduled under reduced motion");
})();

// ── FLIP commits the inverted state before releasing it (deterministic play) ──
(function testFlipForcesReflow() {
  const tbody = new El("tbody");
  const row = makeRow("t1", 10, []);
  tbody.children = [row];
  let reflowed = 0;
  let rafCb = null;
  const applier = animate.createApplier(tbody, {
    setTimeout() {},
    clearTimeout() {},
    raf(cb) {
      rafCb = cb;
    },
    forceReflow() {
      reflowed += 1;
    },
  });
  const first = applier.measureRows();
  row._top = 60;
  applier.apply({ rowClasses: {}, cellClasses: {} }, first);
  assert.strictEqual(row.style.transform, "translateY(-50px)");
  assert.strictEqual(reflowed, 1, "inverted state is committed before release");
  rafCb();
  assert.strictEqual(row.style.transform, "");
})();

// ── Transient classes applied to the right row and cell, all six classes ─────
(function testClasses() {
  const tbody = new El("tbody");
  tbody.children = [makeRow("t1", 0, ["p1"]), makeRow("t2", 0, ["p1", "p2"])];
  const timers = makeTimers();
  const applier = animate.createApplier(tbody, {
    setTimeout: timers.setTimeout,
    clearTimeout: timers.clearTimeout,
    raf() {},
  });
  const diff = {
    rowClasses: { t1: ["animator-row--rank-up"], t2: ["animator-row--rank-down"] },
    cellClasses: {
      [K("t1", "p1")]: ["animator-cell--flash-solved"],
      [K("t2", "p1")]: ["animator-cell--flash-attempts", "animator-cell--flash-first"],
      [K("t2", "p2")]: ["animator-cell--flash-pending"],
    },
  };
  applier.apply(diff, null);
  const row1 = tbody.querySelector('tr[data-team-id="t1"]');
  const row2 = tbody.querySelector('tr[data-team-id="t2"]');
  assert.ok(row1.classList.contains("animator-row--rank-up"));
  assert.ok(row2.classList.contains("animator-row--rank-down"));
  const c1 = tbody.querySelector('tr[data-team-id="t1"] td[data-problem-id="p1"]');
  const c2 = tbody.querySelector('tr[data-team-id="t2"] td[data-problem-id="p1"]');
  const c3 = tbody.querySelector('tr[data-team-id="t2"] td[data-problem-id="p2"]');
  assert.ok(c1.classList.contains("animator-cell--flash-solved"));
  assert.ok(c2.classList.contains("animator-cell--flash-attempts"));
  assert.ok(c2.classList.contains("animator-cell--flash-first"));
  assert.ok(c3.classList.contains("animator-cell--flash-pending"));

  // After the highlight lifetime the classes are removed.
  timers.flush();
  assert.ok(!c1.classList.contains("animator-cell--flash-solved"));
  assert.ok(!row1.classList.contains("animator-row--rank-up"));
})();

// ── Reapplying the same class clears the prior timeout (no stale removal) ─────
(function testReapplyCleanup() {
  const tbody = new El("tbody");
  const row = makeRow("t1", 0, ["p1"]);
  tbody.children = [row];
  const timers = makeTimers();
  const applier = animate.createApplier(tbody, {
    setTimeout: timers.setTimeout,
    clearTimeout: timers.clearTimeout,
    raf() {},
  });
  const cellDiff = { rowClasses: {}, cellClasses: { [K("t1", "p1")]: ["animator-cell--flash-solved"] } };
  applier.apply(cellDiff, null);
  assert.strictEqual(timers.pending(), 1);
  // Reapply before the first timer fires: exactly one timer stays pending, and
  // the class is still present (the first timer was cancelled, not left to strip
  // the fresh highlight).
  applier.apply(cellDiff, null);
  assert.strictEqual(timers.pending(), 1, "reapply cancels the stale timer");
  const cell = tbody.querySelector('tr[data-team-id="t1"] td[data-problem-id="p1"]');
  assert.ok(cell.classList.contains("animator-cell--flash-solved"));
  timers.flush();
  assert.ok(!cell.classList.contains("animator-cell--flash-solved"));
  assert.strictEqual(applier._pendingCount(), 0);
})();

// ── flashCell addresses the cell by its (team_id, problem_id) hooks ──────────
(function testFlashCell() {
  const tbody = new El("tbody");
  tbody.children = [makeRow("t1", 0, ["p1", "p2"])];
  const timers = makeTimers();
  const applier = animate.createApplier(tbody, {
    setTimeout: timers.setTimeout,
    clearTimeout: timers.clearTimeout,
    raf() {},
  });
  applier.flashCell("t1", "p2", "animator-cell--flash-pending");
  const cell = tbody.querySelector('tr[data-team-id="t1"] td[data-problem-id="p2"]');
  assert.ok(cell.classList.contains("animator-cell--flash-pending"), "flashCell flashes the target cell");
  // A missing cell is a no-op rather than a throw.
  applier.flashCell("nope", "p2", "animator-cell--flash-pending");
  timers.flush();
  assert.ok(!cell.classList.contains("animator-cell--flash-pending"), "flash clears after its lifetime");
})();

console.log("animator-animate contract: all assertions passed");
