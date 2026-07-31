//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

// Browser-independent contract test for animator-diff.js: the pure snapshot
// comparison that turns two /snapshot payloads into transient CSS classes. It
// verifies id-keyed lookups (never display text) and the precise entering/leaving
// semantics of each of the six transient classes.

"use strict";

const assert = require("assert");
const path = require("path");

const diff = require(path.join(__dirname, "..", "..", "..", "animator", "static", "js", "animator-diff.js"));

function cell(overrides) {
  return Object.assign(
    { problem_id: "p1", solved: false, attempts: 0, is_pending: false, is_first_balloon: false },
    overrides,
  );
}

function snap(standings) {
  return { version: "2026-07-22T00:00:00+00:00", standings: standings };
}

// ── indexSnapshot keys by id, not label ──────────────────────────────────────
(function testIndex() {
  const idx = diff.indexSnapshot(
    snap([{ team_id: "t1", rank: 1, problems: { A: cell({ problem_id: "pX", solved: true }) } }]),
  );
  assert.strictEqual(idx.rankByTeam["t1"], 1);
  assert.strictEqual(idx.cellByKey[diff.cellKey("t1", "pX")].solved, true);
})();

// ── Rank movement: up, down, unchanged, and new team (no prior state) ─────────
(function testRankClasses() {
  const prev = snap([
    { team_id: "t1", rank: 1, problems: {} },
    { team_id: "t2", rank: 2, problems: {} },
    { team_id: "t3", rank: 3, problems: {} },
  ]);
  const next = snap([
    { team_id: "t2", rank: 1, problems: {} }, // up
    { team_id: "t1", rank: 2, problems: {} }, // down
    { team_id: "t3", rank: 3, problems: {} }, // unchanged
    { team_id: "t4", rank: 4, problems: {} }, // new team, no prior rank
  ]);
  const result = diff.diffSnapshots(prev, next);
  assert.deepStrictEqual(result.rowClasses["t2"], [diff.ROW_RANK_UP]);
  assert.deepStrictEqual(result.rowClasses["t1"], [diff.ROW_RANK_DOWN]);
  assert.ok(!("t3" in result.rowClasses), "unchanged rank => no class");
  assert.ok(!("t4" in result.rowClasses), "new team => no transient class");
})();

// ── Cell: newly solved flashes solved, not attempts ───────────────────────────
(function testNewlySolved() {
  const prev = snap([{ team_id: "t1", rank: 1, problems: { A: cell({ attempts: 1 }) } }]);
  const next = snap([{ team_id: "t1", rank: 1, problems: { A: cell({ solved: true, attempts: 2 }) } }]);
  const classes = diff.diffSnapshots(prev, next).cellClasses[diff.cellKey("t1", "p1")];
  assert.deepStrictEqual(classes, [diff.CELL_SOLVED]);
})();

// ── Cell: changed attempts (still unsolved) flashes attempts ─────────────────
(function testChangedAttempts() {
  const prev = snap([{ team_id: "t1", rank: 1, problems: { A: cell({ attempts: 1 }) } }]);
  const next = snap([{ team_id: "t1", rank: 1, problems: { A: cell({ attempts: 2 }) } }]);
  const classes = diff.diffSnapshots(prev, next).cellClasses[diff.cellKey("t1", "p1")];
  assert.deepStrictEqual(classes, [diff.CELL_ATTEMPTS]);
})();

// ── Cell: pending flashes only on entering, never on leaving ──────────────────
(function testPendingEnterLeave() {
  const before = snap([{ team_id: "t1", rank: 1, problems: { A: cell() } }]);
  const enter = snap([{ team_id: "t1", rank: 1, problems: { A: cell({ is_pending: true }) } }]);
  const enterClasses = diff.diffSnapshots(before, enter).cellClasses[diff.cellKey("t1", "p1")];
  assert.ok(enterClasses.indexOf(diff.CELL_PENDING) !== -1, "entering pending flashes");

  // Leaving pending -> solved: the solved flash fires, but not a pending flash.
  const leave = snap([{ team_id: "t1", rank: 1, problems: { A: cell({ solved: true }) } }]);
  const leaveClasses = diff.diffSnapshots(enter, leave).cellClasses[diff.cellKey("t1", "p1")];
  assert.ok(leaveClasses.indexOf(diff.CELL_PENDING) === -1, "leaving pending does not flash pending");
  assert.ok(leaveClasses.indexOf(diff.CELL_SOLVED) !== -1, "leaving pending to solved flashes solved");
})();

// ── Cell: first-balloon flashes on any change (gain or loss) ──────────────────
(function testFirstBalloon() {
  const before = snap([{ team_id: "t1", rank: 1, problems: { A: cell({ solved: true }) } }]);
  const gain = snap([{ team_id: "t1", rank: 1, problems: { A: cell({ solved: true, is_first_balloon: true }) } }]);
  const gainClasses = diff.diffSnapshots(before, gain).cellClasses[diff.cellKey("t1", "p1")] || [];
  assert.ok(gainClasses.indexOf(diff.CELL_FIRST) !== -1, "gaining first-balloon flashes");

  // A recompute that reassigns the marker away must also flash the change.
  const lose = snap([{ team_id: "t1", rank: 1, problems: { A: cell({ solved: true }) } }]);
  const loseClasses = diff.diffSnapshots(gain, lose).cellClasses[diff.cellKey("t1", "p1")] || [];
  assert.ok(loseClasses.indexOf(diff.CELL_FIRST) !== -1, "losing first-balloon also flashes the change");

  // No change in the marker => no first-balloon flash.
  const steady = diff.diffSnapshots(gain, gain).cellClasses[diff.cellKey("t1", "p1")] || [];
  assert.ok(steady.indexOf(diff.CELL_FIRST) === -1, "unchanged first-balloon does not flash");
})();

// ── No prior snapshot => no transient classes at all ──────────────────────────
(function testNoPrior() {
  const next = snap([{ team_id: "t1", rank: 1, problems: { A: cell({ solved: true }) } }]);
  const result = diff.diffSnapshots(null, next);
  assert.deepStrictEqual(result.rowClasses, {});
  assert.deepStrictEqual(result.cellClasses, {});
})();

console.log("animator-diff contract: all assertions passed");
