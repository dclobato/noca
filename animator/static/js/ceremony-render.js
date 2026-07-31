//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

// Pure DOM rendering for the reveal projection. Every function takes an explicit
// `doc`, so the renderer is exercised headlessly by a Node contract test rather
// than only in a browser. All server-provided text is written with textContent
// and every attribute through setAttribute, so a hostile team name stays inert.
//
// Two rules are load-bearing and easy to get wrong:
//
//   1. Column order is computed, never inherited. `problemLabels` takes the union
//      of every team's problem keys and follows live-scoreboard metadata order
//      when available, with a natural-order fallback. Rows are drawn against that
//      one list, so a team missing a key still lines up with the header.
//   2. Team names are rendered as Bootstrap modal *data-API* triggers
//      (data-bs-toggle/data-bs-target), not as plain buttons opened
//      programmatically. Bootstrap 5.3 restores focus to the trigger on `hidden`
//      only through that data API, so a projector operator returns to the same
//      team button after closing the modal instead of the top of the document.
//
// Exported as UMD: `window.CeremonyRender` / `module.exports`.
(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
  if (root) {
    root.CeremonyRender = api;
  }
})(typeof window !== "undefined" ? window : null, function () {
  "use strict";

  // The cell formatter is shared with the live board so the two cannot drift.
  var format =
    typeof module !== "undefined" && module.exports
      ? require("./cell-format.js")
      : (typeof window !== "undefined" ? window : {}).AnimatorCellFormat;
  var scoreboardRender =
    typeof module !== "undefined" && module.exports
      ? require("./animator-render.js")
      : (typeof window !== "undefined" ? window : {}).AnimatorRender;
  var keyedRows =
    typeof module !== "undefined" && module.exports
      ? require("./animator-keyed-rows.js")
      : (typeof window !== "undefined" ? window : {}).AnimatorKeyedRows;

  var MODAL_SELECTOR = "#ceremony-team-modal";
  var MEDAL_BANDS = ["gold", "silver", "bronze"];

  // Natural label order: length first, then lexicographic. "B" < "Z" < "AA".
  function compareLabels(a, b) {
    if (a.length !== b.length) {
      return a.length - b.length;
    }
    return a < b ? -1 : a > b ? 1 : 0;
  }

  // The authoritative column list for a projection: the union of every team's
  // problem keys, ordered like the live scoreboard when metadata is available,
  // so no team's missing cell can shorten or misalign the board.
  function problemLabels(projection, headerProblems) {
    var teams = (projection && projection.teams) || [];
    var seen = {};
    var labels = [];
    teams.forEach(function (team) {
      Object.keys((team && team.problems) || {}).forEach(function (label) {
        if (!Object.prototype.hasOwnProperty.call(seen, label)) {
          seen[label] = true;
          labels.push(label);
        }
      });
    });
    labels.sort(compareLabels);

    // The metadata feed is the same source the live scoreboard uses, so prefer
    // its configured problem order when it is available. Any projection-only
    // label is appended in natural order as a defensive fallback.
    var configured = Array.isArray(headerProblems) ? headerProblems : [];
    if (!configured.length) {
      return labels;
    }
    var ordered = [];
    configured.forEach(function (problem) {
      var label = problem && String(problem.label);
      if (seen[label] && ordered.indexOf(label) === -1) {
        ordered.push(label);
      }
    });
    labels.forEach(function (label) {
      if (ordered.indexOf(label) === -1) {
        ordered.push(label);
      }
    });
    return ordered;
  }

  function setText(el, text) {
    el.textContent = text === null || text === undefined ? "" : String(text);
    return el;
  }

  function cell(doc, tag, className, text) {
    var el = doc.createElement(tag);
    if (className) {
      el.setAttribute("class", className);
    }
    return setText(el, text);
  }

  // The oversized medal watermark served by the animator's own
  // /assets/medal/{band} route.
  function medalImage(doc, medalBase, medal) {
    var img = doc.createElement("img");
    img.setAttribute("class", "ceremony-medal-watermark");
    if (medalBase) {
      img.setAttribute("src", medalBase + "/" + encodeURIComponent(medal));
    }
    img.setAttribute("alt", medal + " medal");
    return img;
  }

  // Build the header row: the four fixed columns plus one per problem label.
  function renderHeader(doc, headerRow, labels, headerProblems) {
    while (headerRow.firstChild) {
      headerRow.removeChild(headerRow.firstChild);
    }
    var columnClasses = [
      "animator-col-rank ceremony-col-rank",
      "animator-col-team ceremony-col-team",
      "animator-col-solved ceremony-col-solved",
      "animator-col-time ceremony-col-time",
    ];
    ["#", "Team", "Solved", "Time"].forEach(function (title, index) {
      var th = cell(doc, "th", columnClasses[index], title);
      th.setAttribute("scope", "col");
      headerRow.appendChild(th);
    });
    var problemsByLabel = {};
    (headerProblems || []).forEach(function (problem) {
      problemsByLabel[String(problem.label)] = problem;
    });
    labels.forEach(function (label) {
      var problem = problemsByLabel[label];
      var th = cell(doc, "th", "animator-problem-col", problem ? "" : label);
      th.setAttribute("scope", "col");
      if (problem && scoreboardRender && scoreboardRender.createBalloonImage) {
        th.appendChild(scoreboardRender.createBalloonImage(doc, problem));
      }
      headerRow.appendChild(th);
    });
  }

  // One problem cell. The glyph and its wording come from the shared formatter,
  // so the projector and the live board can never disagree about what a cell
  // means — in particular about `attempts`, which is already the count of
  // failures *before* the solve and must not be adjusted here.
  function renderProblemCell(doc, view, isNext, problem) {
    var fallbackProblem = {
      label: (view && view.label) || "",
      problemId: (view && view.problem_id) || "",
      color: null,
      balloonBase: null,
      starBase: null,
    };
    var td = scoreboardRender.createProblemCell(doc, problem || fallbackProblem);
    td.classList.add("ceremony-cell");
    scoreboardRender.fillProblemCell(doc, td, problem || fallbackProblem, view, {
      stackPending: true,
    });

    var state = format.cellState(view);
    // A cell solved before the freeze can still hold an unrevealed frozen
    // submission. The solve is a settled fact, so it keeps its glyph and the
    // pending marker is added on top rather than replacing it.
    if (format.isPending(view)) {
      td.classList.add("ceremony-cell--pending");
    }

    // The cell the next step will resolve. The class drives a continuous glow,
    // so an audience knows where to look before anything changes.
    if (isNext) {
      td.classList.add("ceremony-cell--next");
    }

    td.setAttribute("data-problem-id", (view && view.problem_id) || "");
    td.setAttribute("data-cell-state", state);
    if (isNext) {
      td.setAttribute("data-next", "true");
    }
    return td;
  }

  // The team-name trigger. data-bs-toggle/data-bs-target are what make Bootstrap
  // open the shared modal *and* return focus here when it closes.
  function renderTeamCell(doc, team, medalBase) {
    var th = doc.createElement("th");
    th.setAttribute("scope", "row");
    th.setAttribute("class", "animator-col-team ceremony-cell-team");
    var button = doc.createElement("button");
    button.setAttribute("type", "button");
    button.setAttribute("class", "ceremony-team-name animator-team-primary");
    button.setAttribute("data-bs-toggle", "modal");
    button.setAttribute("data-bs-target", MODAL_SELECTOR);
    button.setAttribute("data-team-id", team.team_id);
    // The audience reads the team's name, never its login. The title carries the
    // same text so a truncated long name is still readable on hover, and the
    // modal reuses it as its heading.
    var label = format.teamLabel(team);
    button.setAttribute("title", label);
    setText(button, label);
    th.appendChild(button);
    if (team.site_name) {
      th.appendChild(
        cell(doc, "span", "ceremony-team-site animator-team-secondary", team.site_name),
      );
    }
    if (team.medal) {
      th.appendChild(medalImage(doc, medalBase, team.medal));
    }
    return th;
  }

  function renderRow(doc, team, labels, options) {
    var tr = doc.createElement("tr");
    var classes = ["ceremony-row"];
    if (options.focusedTeamId && team.team_id === options.focusedTeamId) {
      classes.push("ceremony-row--focused");
    }
    if (options.bandEnd) {
      classes.push("ceremony-row--band-end");
    }
    tr.setAttribute("class", classes.join(" "));
    tr.setAttribute("data-team-id", String(team.team_id));
    if (team.medal) {
      tr.setAttribute("data-medal", team.medal);
    }

    tr.appendChild(
      cell(doc, "td", "animator-col-rank ceremony-cell-rank", team.current_rank),
    );
    tr.appendChild(renderTeamCell(doc, team, options.medalBase));
    tr.appendChild(
      cell(doc, "td", "animator-col-solved ceremony-cell-solved", team.solved),
    );
    tr.appendChild(
      cell(doc, "td", "animator-col-time ceremony-cell-time", team.penalty),
    );
    labels.forEach(function (label) {
      var view = (team.problems || {})[label];
      // Matched on the server-supplied cell, by problem id rather than label, so
      // a relabelled column cannot move the highlight to the wrong problem.
      var isNext =
        !!options.nextCell &&
        options.nextCell.team_id === team.team_id &&
        !!view &&
        options.nextCell.problem_id === view.problem_id;
      tr.appendChild(renderProblemCell(doc, view, isNext, options.problemsByLabel[label]));
    });
    return tr;
  }

  // Refresh a surviving team's row without replacing the <tr> itself. Keeping
  // that stable element is essential for FLIP: the browser retains the row that
  // was painted at the old rank, then AnimatorAnimate moves that same row to its
  // new server position.
  function updateRow(doc, tr, team, labels, options) {
    var next = renderRow(doc, team, labels, options);
    tr.setAttribute("class", next.getAttribute("class"));
    tr.setAttribute("data-team-id", next.getAttribute("data-team-id"));
    var medal = next.getAttribute("data-medal");
    if (medal) {
      tr.setAttribute("data-medal", medal);
    } else {
      tr.removeAttribute("data-medal");
    }
    tr.replaceChildren.apply(tr, Array.prototype.slice.call(next.children));
    return tr;
  }

  // Whether this row is the last of its medal band (used for the heavier rule).
  function isBandEnd(teams, index) {
    var medal = teams[index].medal;
    if (!medal || MEDAL_BANDS.indexOf(medal) === -1) {
      return false;
    }
    var next = teams[index + 1];
    return !next || next.medal !== medal;
  }

  // Draw the whole projection in authoritative server order. Surviving rows
  // are reconciled by team id and re-appended into their new rank instead of
  // being destroyed, preserving the element identity FLIP needs to animate.
  function renderStandings(doc, tbody, headerRow, projection, options) {
    var opts = options || {};
    var teams = (projection && projection.teams) || [];
    var labels = problemLabels(projection, opts.headerProblems);
    var problemsByLabel = {};
    (opts.headerProblems || []).forEach(function (problem) {
      problemsByLabel[String(problem.label)] = problem;
    });
    renderHeader(doc, headerRow, labels, opts.headerProblems);
    keyedRows.reconcile(tbody, teams, {
      key: function (team) {
        return team.team_id;
      },
      build: function (team, index) {
        return renderRow(doc, team, labels, rowOptions(team, index));
      },
      update: function (row, team, index) {
        updateRow(doc, row, team, labels, rowOptions(team, index));
      },
    });

    function rowOptions(_team, index) {
      var rowOptions = {
        focusedTeamId: projection ? projection.focused_team_id : null,
        nextCell: (projection && projection.next_cell) || null,
        medalBase: opts.medalBase || null,
        problemsByLabel: problemsByLabel,
        bandEnd: isBandEnd(teams, index),
      };
      return rowOptions;
    }
    return labels;
  }

  // Header summary: phase plus revealed/frozen progress.
  function renderProgress(projection) {
    if (!projection) {
      return "";
    }
    return projection.revealed_count + " of " + projection.frozen_count + " revealed";
  }

  return {
    MODAL_SELECTOR: MODAL_SELECTOR,
    compareLabels: compareLabels,
    problemLabels: problemLabels,
    renderHeader: renderHeader,
    renderProblemCell: renderProblemCell,
    renderProgress: renderProgress,
    updateRow: updateRow,
    renderStandings: renderStandings,
  };
});
