//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

// Board controller: applies one authoritative /snapshot to the DOM and keeps
// derived UI in sync. Rendering, diffing, animation, hide-toggling, and the
// timer are all injected so the whole flow is exercised headlessly. Crucially it
// forwards each accepted snapshot's freeze state to the timer, so a contest that
// freezes while the page is open flips the header from Running to Frozen live.
// Exported as UMD: `window.AnimatorBoard` / `module.exports`.
(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
  if (root) {
    root.AnimatorBoard = api;
  }
})(typeof window !== "undefined" ? window : null, function () {
  "use strict";

  // Build a board bound to its collaborators. `deps`:
  //   doc:      the document used for element creation
  //   render:   AnimatorRender (renderStandings)
  //   diff:     AnimatorDiff (diffSnapshots)
  //   applier:  AnimatorAnimate applier (measureRows / apply)
  //   timer:    object exposing setFrozen(bool) (optional)
  //   pending:  object exposing render(pendingSubmissions) (optional)
  //   activity: object exposing reconcile(previous, next) (optional)
  //   refs:     { standings, loading, empty, board }
  //   setHidden(el, hidden): visibility toggle
  function createBoard(deps) {
    var problems = [];
    var previous = null;

    function setProblems(next) {
      problems = next;
    }

    // Render in authoritative server order, animate the delta from the previously
    // applied snapshot, and sync the timer's freeze state. The board cells hold
    // no focusable content, so replacing the tbody cannot move focus.
    function applySnapshot(snapshot) {
      var firstTops = deps.applier.measureRows();
      var hasRows = deps.render.renderStandings(deps.doc, deps.refs.standings, problems, snapshot.standings);
      deps.setHidden(deps.refs.loading, true);
      // Empty placeholder is hidden when there ARE rows; board is hidden when
      // there are none.
      deps.setHidden(deps.refs.empty, hasRows);
      deps.setHidden(deps.refs.board, !hasRows);
      if (previous) {
        deps.applier.apply(deps.diff.diffSnapshots(previous, snapshot), firstTops);
      }
      if (deps.activity) {
        deps.activity.reconcile(previous, snapshot);
      }
      previous = snapshot;
      if (deps.timer) {
        deps.timer.setFrozen(snapshot.is_frozen);
      }
      if (deps.pending) {
        deps.pending.render(snapshot.pending_submissions || []);
      }
    }

    function reset() {
      previous = null;
    }

    return {
      setProblems: setProblems,
      applySnapshot: applySnapshot,
      reset: reset,
      _previous: function () {
        return previous;
      },
    };
  }

  return { createBoard: createBoard };
});
