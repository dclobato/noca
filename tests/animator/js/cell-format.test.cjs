//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

// Drives cell-format.js with the shared fixture that also drives Web's template
// test. The formatter returns complete lines rather than punctuation pieces a
// renderer recombines, so a case here pins an actual rendered string on both
// sides rather than a fragment that could still be assembled differently.

const assert = require("assert");
const fs = require("fs");
const path = require("path");

const format = require(path.join(__dirname, "..", "..", "..", "animator", "static", "js", "cell-format.js"));
const fixture = JSON.parse(
  fs.readFileSync(path.join(__dirname, "..", "..", "fixtures", "scoreboard_cell_cases.json"), "utf8"),
);

(function testEveryFixtureCase() {
  assert.ok(fixture.cases.length > 0, "the fixture must carry cases");
  fixture.cases.forEach((testCase) => {
    const cell = testCase.cell;
    assert.strictEqual(format.cellState(cell), testCase.state, testCase.name + ": state");
    assert.strictEqual(
      format.formatAttemptLine(cell),
      testCase.attempt_line,
      testCase.name + ": attempts and penalty on one line",
    );
    if (testCase.minute) {
      assert.strictEqual(cell.solved_at_minutes + "'", testCase.minute, testCase.name + ": minute");
    }
  });
})();

// ── The attempts and the penalty they caused are never split across lines ────
(function testAttemptAndPenaltyStayTogether() {
  // Solved: the "+" reports failures that preceded the accepted submission.
  assert.strictEqual(
    format.formatAttemptLine({ solved: true, attempts: 2, penalty: 40 }),
    "+2 (40')",
  );
  // Unsolved: a true minus, counting down.
  assert.strictEqual(
    format.formatAttemptLine({ solved: false, attempts: 3, penalty: 60 }),
    "−3 (60')",
  );
  // Pending on the live board: one line, marks included.
  assert.strictEqual(
    format.formatAttemptLine({ solved: false, attempts: 1, penalty: 20, is_pending: true }),
    "? −1 (20')",
  );
  // Pending on the projector: the "?" marks occupy the line above, so `stacked`
  // omits them here rather than repeating them.
  assert.strictEqual(
    format.formatAttemptLine(
      { solved: false, attempts: 5, penalty: 100, pending_frozen_count: 3 },
      { stacked: true },
    ),
    "−5 (100')",
  );
  assert.strictEqual(format.formatPendingMarks({ pending_frozen_count: 3 }), "???");
})();

// ── No line at all, rather than an empty shell ───────────────────────────────
(function testEmptyRatherThanStrayPunctuation() {
  // A first-attempt solve has nothing to report: no solitary "+", no "()".
  assert.strictEqual(format.formatAttemptLine({ solved: true, attempts: 0, penalty: 0 }), "");
  // Nothing attempted: the renderer draws no line, so the formatter says so
  // rather than handing back an en dash to be typeset.
  assert.strictEqual(format.formatAttemptLine({ solved: false, attempts: 0, penalty: 0 }), "");
  assert.strictEqual(format.formatAttemptLine({}, { stacked: true }), "");
})();

// ── Field-name aliases between the two feeds are not behavior differences ────
(function testFeedAliasesAgree() {
  assert.strictEqual(
    format.formatAttemptLine({ solved: false, attempts: 2, penalty: 40, is_pending: true }),
    format.formatAttemptLine({ solved: false, attempts: 2, penalty: 40, pending_frozen: true }),
  );
  assert.strictEqual(format.isFirst({ is_first_balloon: true }), format.isFirst({ is_first_solver: true }));
})();

console.log("cell-format.test.cjs ok");
