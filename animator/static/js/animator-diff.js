//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

// Pure snapshot-comparison helpers for the animator live scoreboard. No DOM: the
// diff is computed from two /snapshot payloads and returns the transient CSS
// classes the applier paints for one update. Row state is keyed by `team_id` and
// cell state by `(team_id, problem_id)` — never by display text — so a rename or
// reorder can never be mistaken for a data change. Exported as a UMD module:
// `window.AnimatorDiff` in the browser, `module.exports` in Node.
(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
  if (root) {
    root.AnimatorDiff = api;
  }
})(typeof window !== "undefined" ? window : null, function () {
  "use strict";

  // Transient row-level classes (rank movement).
  var ROW_RANK_UP = "animator-row--rank-up";
  var ROW_RANK_DOWN = "animator-row--rank-down";
  // Transient cell-level classes (problem-cell changes).
  var CELL_SOLVED = "animator-cell--flash-solved";
  var CELL_ATTEMPTS = "animator-cell--flash-attempts";
  var CELL_PENDING = "animator-cell--flash-pending";
  var CELL_FIRST = "animator-cell--flash-first";

  // NUL separator: neither a team_id nor a problem_id can contain it, so the
  // composite key is unambiguous regardless of the id contents.
  var KEY_SEP = String.fromCharCode(0);

  function cellKey(teamId, problemId) {
    return String(teamId) + KEY_SEP + String(problemId);
  }

  // Build lookup maps for one snapshot. `rankByTeam` maps team_id -> rank and
  // `cellByKey` maps (team_id, problem_id) -> the cell record, so the diff reads
  // both sides by stable identity instead of array position.
  function indexSnapshot(snapshot) {
    var rankByTeam = {};
    var cellByKey = {};
    var standings = snapshot && Array.isArray(snapshot.standings) ? snapshot.standings : [];
    standings.forEach(function (row) {
      var teamId = String(row.team_id);
      rankByTeam[teamId] = row.rank;
      var cells = row.problems || {};
      Object.keys(cells).forEach(function (label) {
        var cell = cells[label];
        if (cell && cell.problem_id !== undefined && cell.problem_id !== null) {
          cellByKey[cellKey(teamId, cell.problem_id)] = cell;
        }
      });
    });
    return { rankByTeam: rankByTeam, cellByKey: cellByKey };
  }

  // Classes for one cell transition. `pending` highlights only when a cell
  // *enters* the pending state (false -> true); leaving pending is already
  // conveyed by newly-solved / changed-attempts, so it is not flashed here.
  // `first-balloon` highlights on any change of the marker (gaining it on a
  // first solve, or losing it if a recompute reassigns it), which is the
  // Phase 08 "first-balloon changes" requirement.
  function cellClasses(prevCell, nextCell) {
    // No next cell, or no prior state to have changed from (first render, a team
    // or problem appearing for the first time): nothing transient to flash.
    if (!nextCell || !prevCell) {
      return [];
    }
    var classes = [];
    var wasSolved = Boolean(prevCell && prevCell.solved);
    if (nextCell.solved && !wasSolved) {
      classes.push(CELL_SOLVED);
    } else if (!nextCell.solved) {
      var prevAttempts = prevCell ? prevCell.attempts || 0 : 0;
      if ((nextCell.attempts || 0) !== prevAttempts) {
        classes.push(CELL_ATTEMPTS);
      }
    }
    var wasPending = Boolean(prevCell && prevCell.is_pending);
    if (nextCell.is_pending && !wasPending) {
      classes.push(CELL_PENDING);
    }
    var wasFirst = Boolean(prevCell && prevCell.is_first_balloon);
    if (Boolean(nextCell.is_first_balloon) !== wasFirst) {
      classes.push(CELL_FIRST);
    }
    return classes;
  }

  // Compare two snapshots and return the transient classes for this update:
  //   rowClasses:  { team_id: [row classes] }        (rank movement)
  //   cellClasses: { "team_id\0problem_id": [classes] } (cell changes)
  // A team or cell absent from `prev` (first render, a new team) produces no
  // transient class: there is no prior state to have changed from.
  function diffSnapshots(prev, next) {
    var before = indexSnapshot(prev || {});
    var after = indexSnapshot(next || {});
    var rowClasses = {};
    var cellClassMap = {};

    Object.keys(after.rankByTeam).forEach(function (teamId) {
      var newRank = after.rankByTeam[teamId];
      var oldRank = before.rankByTeam[teamId];
      if (oldRank === undefined) {
        return;
      }
      if (newRank < oldRank) {
        rowClasses[teamId] = [ROW_RANK_UP];
      } else if (newRank > oldRank) {
        rowClasses[teamId] = [ROW_RANK_DOWN];
      }
    });

    Object.keys(after.cellByKey).forEach(function (key) {
      var classes = cellClasses(before.cellByKey[key], after.cellByKey[key]);
      if (classes.length > 0) {
        cellClassMap[key] = classes;
      }
    });

    return { rowClasses: rowClasses, cellClasses: cellClassMap };
  }

  return {
    ROW_RANK_UP: ROW_RANK_UP,
    ROW_RANK_DOWN: ROW_RANK_DOWN,
    CELL_SOLVED: CELL_SOLVED,
    CELL_ATTEMPTS: CELL_ATTEMPTS,
    CELL_PENDING: CELL_PENDING,
    CELL_FIRST: CELL_FIRST,
    cellKey: cellKey,
    indexSnapshot: indexSnapshot,
    cellClasses: cellClasses,
    diffSnapshots: diffSnapshots,
  };
});
