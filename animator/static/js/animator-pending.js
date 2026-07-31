//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

// Snapshot-driven pending-submission list for the animator scoreboard. The
// server orders the list newest-first and it is authoritative: every /snapshot
// replaces it wholesale, so there is no add/remove/reconcile/dedup logic here.
// Each line reads "<team> waiting problem <letter>" and is written with
// textContent (never raw HTML), so a hostile team name or label stays inert.
// Exported as UMD: `window.AnimatorPending` / `module.exports`.
(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
  if (root) {
    root.AnimatorPending = api;
  }
})(typeof window !== "undefined" ? window : null, function () {
  "use strict";

  // Build a pending-list component. `opts`:
  //   doc:       the document used for element creation
  //   container: the <section> holding the list (hidden when empty)
  //   list:      optional explicit list element; defaults to the container
  function createPendingList(opts) {
    var options = opts || {};
    var doc = options.doc;
    var container = options.container;
    var list = options.list || container;

    function setHidden(hidden) {
      if (!container) {
        return;
      }
      if (hidden) {
        container.setAttribute("hidden", "");
      } else {
        container.removeAttribute("hidden");
      }
    }

    // Rebuild the list DOM from the authoritative, already-ordered array.
    function render(pendingSubmissions) {
      if (!list) {
        return;
      }
      var items = Array.isArray(pendingSubmissions) ? pendingSubmissions : [];
      list.replaceChildren();
      if (items.length === 0) {
        setHidden(true);
        return;
      }
      items.forEach(function (entry) {
        var li = doc.createElement("li");
        li.setAttribute("class", "animator-pending-item");
        if (entry && entry.team_id !== undefined && entry.team_id !== null) {
          li.setAttribute("data-team-id", String(entry.team_id));
        }
        if (entry && entry.problem_id !== undefined && entry.problem_id !== null) {
          li.setAttribute("data-problem-id", String(entry.problem_id));
        }
        var teamName = entry && entry.team_name !== undefined && entry.team_name !== null ? entry.team_name : "";
        var label = entry && entry.problem_label !== undefined && entry.problem_label !== null ? entry.problem_label : "";
        li.textContent = teamName + " waiting problem " + label;
        list.appendChild(li);
      });
      setHidden(false);
    }

    return { render: render };
  }

  return { createPendingList: createPendingList };
});
