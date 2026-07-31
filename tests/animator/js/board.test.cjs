//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

// Browser-independent contract test for animator-board.js: applying a /snapshot
// renders standings, animates only after the first snapshot, and — the Phase 08
// regression guard — forwards each accepted snapshot's freeze state to the timer,
// so a contest that freezes while the page is open flips the header live.

"use strict";

const assert = require("assert");
const path = require("path");

const board = require(path.join(__dirname, "..", "..", "..", "animator", "static", "js", "animator-board.js"));

function makeDeps() {
  const calls = { render: [], apply: 0, measure: 0, hidden: [], frozen: [], activity: [], hiddenBy: {} };
  const applier = {
    measureRows() {
      calls.measure += 1;
      return {};
    },
    apply() {
      calls.apply += 1;
    },
  };
  const render = {
    renderStandings(doc, el, problems, standings) {
      calls.render.push(standings);
      return standings.length > 0;
    },
  };
  const diff = {
    diffSnapshots() {
      return { rowClasses: {}, cellClasses: {} };
    },
  };
  const timer = {
    setFrozen(v) {
      calls.frozen.push(v);
    },
  };
  calls.pending = [];
  const pending = {
    render(list) {
      calls.pending.push(list);
    },
  };
  const activity = {
    reconcile(previous, next) {
      calls.activity.push([previous, next]);
    },
  };
  const deps = {
    doc: {},
    render: render,
    diff: diff,
    applier: applier,
    timer: timer,
    pending: pending,
    activity: activity,
    refs: {
      standings: { id: "standings" },
      loading: { id: "loading" },
      empty: { id: "empty" },
      board: { id: "board" },
    },
    setHidden(el, hidden) {
      calls.hidden.push(hidden);
      calls.hiddenBy[el.id] = hidden;
    },
  };
  return { deps: deps, calls: calls };
}

// ── First snapshot renders, does not animate, and syncs freeze state ─────────
(function testFirstSnapshot() {
  const { deps, calls } = makeDeps();
  const b = board.createBoard(deps);
  b.setProblems([{ label: "A" }]);
  b.applySnapshot({ is_frozen: false, standings: [{ team_id: "t1" }] });
  assert.strictEqual(calls.render.length, 1);
  assert.strictEqual(calls.apply, 0, "no animation on the very first snapshot");
  assert.deepStrictEqual(calls.frozen, [false], "freeze state forwarded on first snapshot");
  assert.strictEqual(calls.activity.length, 1);
  assert.strictEqual(calls.activity[0][0], null, "activity receives an empty initial baseline");
})();

// ── A later snapshot that freezes flips the timer to Frozen (regression) ─────
(function testFreezeGoesLive() {
  const { deps, calls } = makeDeps();
  const b = board.createBoard(deps);
  b.applySnapshot({ is_frozen: false, standings: [{ team_id: "t1" }] });
  b.applySnapshot({ is_frozen: true, standings: [{ team_id: "t1" }] });
  assert.strictEqual(calls.apply, 1, "second snapshot animates the delta");
  assert.deepStrictEqual(calls.frozen, [false, true], "freeze transition reaches the timer live");
  assert.strictEqual(calls.activity[1][0].is_frozen, false);
  assert.strictEqual(calls.activity[1][1].is_frozen, true);
})();

// ── Empty/board toggle polarity: rows hide the placeholder, none shows it ────
(function testEmptyTogglePolarity() {
  const withRows = makeDeps();
  board.createBoard(withRows.deps).applySnapshot({ is_frozen: false, standings: [{ team_id: "t1" }] });
  assert.strictEqual(withRows.calls.hiddenBy.empty, true, "with teams the empty placeholder is hidden");
  assert.strictEqual(withRows.calls.hiddenBy.board, false, "with teams the board is shown");

  const noRows = makeDeps();
  board.createBoard(noRows.deps).applySnapshot({ is_frozen: false, standings: [] });
  assert.strictEqual(noRows.calls.hiddenBy.empty, false, "with no teams the empty placeholder is shown");
  assert.strictEqual(noRows.calls.hiddenBy.board, true, "with no teams the board is hidden");
})();

// ── reset() clears the previous snapshot so the next apply does not animate ───
(function testReset() {
  const { deps, calls } = makeDeps();
  const b = board.createBoard(deps);
  b.applySnapshot({ is_frozen: false, standings: [{ team_id: "t1" }] });
  b.reset();
  b.applySnapshot({ is_frozen: false, standings: [{ team_id: "t1" }] });
  assert.strictEqual(calls.apply, 0, "after reset the next snapshot is treated as first");
})();

// ── The pending list is rendered from each snapshot's pending_submissions ─────
(function testPendingRenderedFromSnapshot() {
  const { deps, calls } = makeDeps();
  const b = board.createBoard(deps);
  const list = [{ team_id: "t1", problem_id: "p1", team_name: "alpha", problem_label: "A" }];
  b.applySnapshot({ is_frozen: false, standings: [{ team_id: "t1" }], pending_submissions: list });
  assert.deepStrictEqual(calls.pending, [list], "pending list rendered from the snapshot");

  // A snapshot without the field renders an empty list rather than throwing.
  b.applySnapshot({ is_frozen: false, standings: [{ team_id: "t1" }] });
  assert.deepStrictEqual(calls.pending[1], [], "missing pending_submissions renders an empty list");
})();

console.log("animator-board contract: all assertions passed");
