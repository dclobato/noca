//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

// Shared team-cell rendering for the live scoreboard and reveal ceremony.
// The live renderer reconciles rows in place, so syncTeamCell preserves the
// exact modal-trigger button node across refreshes. Bootstrap stores that node
// as the modal's relatedTarget and returns focus to it when the dialog closes.
// Exported as UMD: `window.AnimatorTeamCell` / `module.exports`.
(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
  if (root) {
    root.AnimatorTeamCell = api;
  }
})(typeof window !== "undefined" ? window : null, function () {
  "use strict";

  var format =
    typeof module !== "undefined" && module.exports
      ? require("./cell-format.js")
      : (typeof window !== "undefined" ? window : {}).AnimatorCellFormat;
  var MODAL_SELECTOR = "#team-media-modal";
  var ABSENT_LABEL = "No sign of life since the contest started";

  function findTrigger(th) {
    for (var i = 0; i < th.children.length; i++) {
      var child = th.children[i];
      if (child.getAttribute("data-team-media-trigger") !== null) {
        return child;
      }
    }
    return null;
  }

  function createTrigger(doc) {
    var button = doc.createElement("button");
    button.setAttribute("type", "button");
    button.setAttribute("class", "team-media-trigger animator-team-primary");
    button.setAttribute("data-team-media-trigger", "");
    button.setAttribute("data-bs-toggle", "modal");
    button.setAttribute("data-bs-target", MODAL_SELECTOR);
    return button;
  }

  function appendSite(doc, th, team, className) {
    if (!team.site_name) {
      return;
    }
    var site = doc.createElement("span");
    site.setAttribute("class", className || "animator-team-secondary");
    site.textContent = team.site_name;
    th.appendChild(site);
  }

  // The team never signed in since the contest started. The glyph goes *inside*
  // the trigger so it stays on the name line -- the trigger is a block element,
  // so a sibling span would drop to a line of its own -- and so it joins the
  // button's accessible name instead of being announced as a stray image.
  function appendAbsentMarker(doc, button, team) {
    if (!team.absent) {
      return;
    }
    var marker = doc.createElement("i");
    marker.setAttribute("class", "material-symbols-outlined animator-team-absent-icon");
    marker.setAttribute("role", "img");
    marker.setAttribute("aria-label", ABSENT_LABEL);
    marker.setAttribute("title", ABSENT_LABEL);
    marker.textContent = "person_off";
    button.appendChild(marker);
  }

  function appendMedal(doc, th, team, options) {
    if (typeof options.createMedalImage !== "function") {
      return;
    }
    var medal = options.createMedalImage(doc, options.medalBase, team.medal);
    if (medal) {
      th.appendChild(medal);
    }
  }

  // Refresh the team cell while preserving its trigger object. Other children
  // are presentation-only and can be rebuilt without disrupting focus.
  function syncTeamCell(doc, th, team, options) {
    var opts = options || {};
    var button = findTrigger(th) || createTrigger(doc);
    var label = format.teamLabel(team);
    button.setAttribute("data-team-id", team.team_id);
    button.setAttribute("title", label);
    button.textContent = label;
    if (team.absent) {
      th.setAttribute("data-team-absent", "");
    } else {
      th.removeAttribute("data-team-absent");
    }

    // Remove and re-append the same button synchronously. Event handlers and
    // Bootstrap's relatedTarget retain the object identity across live updates.
    while (th.children.length) {
      th.removeChild(th.children[0]);
    }
    th.appendChild(button);
    appendAbsentMarker(doc, button, team);
    appendSite(doc, th, team, opts.siteClass);
    appendMedal(doc, th, team, opts);
    return button;
  }

  function createTeamCell(doc, team, options) {
    var th = doc.createElement("th");
    th.setAttribute("scope", "row");
    th.setAttribute("class", "animator-col-team");
    syncTeamCell(doc, th, team, options);
    return th;
  }

  return {
    MODAL_SELECTOR: MODAL_SELECTOR,
    createTeamCell: createTeamCell,
    syncTeamCell: syncTeamCell,
  };
});
