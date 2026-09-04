//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

// The single source of truth for how one scoreboard cell reads.
//
// Both the live board (animator-render.js) and the reveal projector
// (ceremony-render.js) draw the same underlying cell with different density
// and chrome, but the *meaning* must not differ between them — an audience
// watching the reveal and a viewer watching the live board have to see the same
// attempt counts. Keeping that meaning in two places is exactly how they drift.
//
// The one rule worth stating explicitly, because getting it wrong is silent:
// `attempts` is already **the number of penalizing attempts made before the
// accepted submission** (see `ProblemResult.attempts` in
// shared/services/scoreboard_projection.py). It is not a total including the
// solve, so a solved cell renders `+attempts` directly when failures exist.
// A first-attempt solve needs no solitary `+`: its balloon and solve minute are
// sufficient. Subtracting one here would underreport every solve.
//
// Exported as UMD: `window.AnimatorCellFormat` / `module.exports`.
(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
  if (root) {
    root.AnimatorCellFormat = api;
  }
})(typeof window !== "undefined" ? window : null, function () {
  "use strict";

  var MINUS = "−"; // true minus sign, not a hyphen
  var EN_DASH = "–"; // "nothing attempted"

  // The two feeds name the same two flags differently: the live snapshot uses
  // `is_pending`/`is_first_balloon`, the reveal projection uses
  // `pending_frozen`/`is_first_solver`. Normalizing here keeps the field-name
  // difference from becoming a behavior difference.
  function isPending(cell) {
    return pendingCountOf(cell) > 0;
  }

  function pendingCountOf(cell) {
    var count = cell && cell.pending_frozen_count;
    if (typeof count === "number" && count > 0) {
      return Math.floor(count);
    }
    return cell && (cell.is_pending || cell.pending_frozen) ? 1 : 0;
  }

  function formatPendingMarks(cell) {
    return "?".repeat(pendingCountOf(cell));
  }

  function isFirst(cell) {
    return !!(cell && (cell.is_first_balloon || cell.is_first_solver));
  }

  function attemptsOf(cell) {
    var value = cell && cell.attempts;
    return typeof value === "number" && value > 0 ? value : 0;
  }

  function penaltyOf(cell) {
    var value = cell && cell.penalty;
    return typeof value === "number" && value > 0 ? value : 0;
  }

  // Which visual state a cell is in. Precedence is solved → pending → attempted
  // → none: a solve is a settled fact, so a cell that was solved before the
  // freeze still reads as solved even when it also holds an unrevealed frozen
  // submission (the projector adds a pending marker on top rather than hiding
  // the solve behind a question mark).
  function cellState(cell) {
    if (!cell) {
      return "none";
    }
    if (cell.solved) {
      return "solved";
    }
    if (isPending(cell)) {
      return "pending";
    }
    return attemptsOf(cell) > 0 ? "attempted" : "none";
  }

  // The primary glyph for a cell, identical on both boards.
  function formatCellText(cell) {
    var attempts = attemptsOf(cell);
    switch (cellState(cell)) {
      case "solved":
        return attempts > 0 ? "+" + attempts : "";
      case "pending":
        return attempts > 0 ? "? " + MINUS + attempts : "?";
      case "attempted":
        return MINUS + attempts;
      default:
        return EN_DASH;
    }
  }

  // The attempt count and the penalty it caused, as ONE complete line.
  //
  // They describe the same failures, so splitting them across two lines makes a
  // cell read as two separate facts — and it was inconsistent: a solved cell
  // showed "+2 (40')" on one line while a pending cell put "−5" and "(100')" on
  // two, so the same information changed shape the moment it was revealed. It
  // also made the projector's pending cell the tallest on any surface.
  //
  // Returned finished rather than as punctuation pieces a renderer recombines,
  // because the pieces are exactly what drifted: both animator renderers used to
  // build `"+" + attempts + " (" + penalty + "')"` inline, and Web's Jinja builds
  // the same sentence a third time in another language. A whole-unit function is
  // something all three can be pinned to by one shared fixture
  // (tests/fixtures/scoreboard_cell_cases.json).
  //
  // The lead's sign follows the cell's state: a solve reports the failures that
  // preceded it with "+", an unsolved cell counts them down with a true minus.
  // `stacked` is the projector's pending cell, where the "?" marks already
  // occupy the line above and must not be repeated here.
  //
  // An empty string means "this cell has no such line" — never a stray "()".
  function formatAttemptLine(cell, options) {
    var stacked = !!(options && options.stacked);
    var attempts = attemptsOf(cell);
    var penalty = penaltyOf(cell);
    var lead;
    if (cellState(cell) === "solved") {
      lead = attempts > 0 ? "+" + attempts : "";
    } else if (stacked) {
      lead = attempts > 0 ? MINUS + attempts : "";
    } else {
      lead = formatCellText(cell);
      if (lead === EN_DASH) {
        return "";
      }
    }
    var tail = penalty > 0 ? "(" + penalty + "')" : "";
    if (!lead) {
      return tail;
    }
    return tail ? lead + " " + tail : lead;
  }

  // How a team is named on screen.
  //
  // The feeds carry both `team_fullname` (the team's real name) and `team_name`
  // (its login). An audience reads the name — a login on a projector means
  // nothing to them — so the full name wins whenever there is one, and the login
  // is only the fallback for a team that has none.
  function teamLabel(team) {
    if (!team) {
      return "";
    }
    var full = typeof team.team_fullname === "string" ? team.team_fullname.trim() : "";
    var login = typeof team.team_name === "string" ? team.team_name.trim() : "";
    return full || login;
  }

  // Screen-reader wording for the same cell.
  function describeCell(cell) {
    var attempts = attemptsOf(cell);
    switch (cellState(cell)) {
      case "solved":
        return attempts > 0 ? "solved after " + attempts + " failed attempts" : "solved";
      case "pending":
        var pending = pendingCountOf(cell);
        return (
          "judgment pending: " +
          pending +
          (pending === 1 ? " unrevealed submission" : " unrevealed submissions") +
          (attempts > 0 ? ", " + attempts + " failed attempts" : "")
        );
      case "attempted":
        return attempts + " failed attempts";
      default:
        return "no attempts";
    }
  }

  return {
    EN_DASH: EN_DASH,
    MINUS: MINUS,
    attemptsOf: attemptsOf,
    cellState: cellState,
    describeCell: describeCell,
    formatAttemptLine: formatAttemptLine,
    formatCellText: formatCellText,
    isFirst: isFirst,
    isPending: isPending,
    penaltyOf: penaltyOf,
    pendingCountOf: pendingCountOf,
    formatPendingMarks: formatPendingMarks,
    teamLabel: teamLabel,
  };
});
