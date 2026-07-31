//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

// Pure DOM-rendering helpers for the animator live scoreboard. Every function
// takes an explicit `doc` (a `document`) so the renderer can be exercised by a
// browser-independent DOM test. All server-provided labels are written with
// textContent (never raw HTML insertion), and problem cells/rows carry stable
// data-* hooks (data-team-id, data-problem-id) for later phases. Exported as a
// UMD module: `window.AnimatorRender` in the browser, `module.exports` in Node.
(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
  if (root) {
    root.AnimatorRender = api;
  }
})(typeof window !== "undefined" ? window : null, function () {
  "use strict";

  // Cell glyphs/wording are shared with the reveal projector so the live board
  // and the ceremony can never report different attempt counts.
  var format =
    typeof module !== "undefined" && module.exports
      ? require("./cell-format.js")
      : (typeof window !== "undefined" ? window : {}).AnimatorCellFormat;
  var keyedRows =
    typeof module !== "undefined" && module.exports
      ? require("./animator-keyed-rows.js")
      : (typeof window !== "undefined" ? window : {}).AnimatorKeyedRows;

  var HEX_COLOR = /^[0-9a-fA-F]{3,8}$/;

  function pad2(value) {
    return value < 10 ? "0" + value : String(value);
  }

  // Format a non-negative duration (milliseconds) as HH:MM:SS.
  function formatDuration(ms) {
    var totalSeconds = Math.max(0, Math.floor(ms / 1000));
    var hours = Math.floor(totalSeconds / 3600);
    var minutes = Math.floor((totalSeconds % 3600) / 60);
    var seconds = totalSeconds % 60;
    return pad2(hours) + ":" + pad2(minutes) + ":" + pad2(seconds);
  }

  // Pure timer projection covering all contest and public-scoreboard states.
  // Once the contest ends, two independent badges remain visible: Frozen or
  // Final for scoreboard visibility, plus Ended for the contest lifecycle.
  // `running` reports whether the caller should keep ticking (false once the
  // contest has ended or the timing is unknown, so no interval is scheduled).
  function computeTimerView(startMs, endMs, frozen, nowMs) {
    var invalid = startMs === null || endMs === null || isNaN(startMs) || isNaN(endMs);
    if (invalid) {
      return { state: "unknown", label: "Contest", text: "--:--:--", running: false };
    }
    if (nowMs < startMs) {
      return { state: "scheduled", label: "Starts in", text: formatDuration(startMs - nowMs), running: true };
    }
    if (nowMs >= endMs) {
      // Pin the final elapsed duration; the contest is over, so stop ticking.
      return {
        state: frozen ? "frozen" : "final",
        label: frozen ? "Frozen" : "Final",
        ended: true,
        text: formatDuration(endMs - startMs),
        running: false,
      };
    }
    var elapsed = formatDuration(nowMs - startMs);
    if (frozen) {
      return { state: "frozen", label: "Frozen", ended: false, text: elapsed, running: true };
    }
    return { state: "running", label: "Running", ended: false, text: elapsed, running: true };
  }

  // The public final scoreboard is identifiable from the two authoritative
  // feeds: the contest has ended, and the snapshot is no longer frozen. An
  // ended but unreleased contest remains frozen, so it must keep the live
  // connection machinery available for a later release/reconciliation.
  function isReleasedFinal(meta, snapshot, nowMs) {
    var endMs = Date.parse(meta && meta.end_time);
    return (
      !isNaN(endMs) &&
      nowMs > endMs &&
      snapshot &&
      snapshot.is_frozen === false
    );
  }

  // Normalize the /meta problem objects into the shape the renderer needs. The
  // optional `assets` carries the balloon/star image mount bases so each problem
  // object can build its own <img> src without threading extra parameters
  // through the render/board call chain.
  function extractProblems(meta, assets) {
    var opts = assets || {};
    var list = meta && Array.isArray(meta.problems) ? meta.problems : [];
    return list.map(function (problem) {
      return {
        label: String(problem.label),
        color: problem.balloon_color,
        problemId: problem.problem_id,
        balloonBase: opts.balloonBase || null,
        starBase: opts.starBase || null,
      };
    });
  }

  // Build the src for a balloon/star asset served by the animator's own /assets
  // route. Returns null when the base or color is unusable so the caller can
  // omit src entirely. The letter segment is added only for a real ASCII letter
  // (the route renders its first letter), and both segments are URL-encoded so a
  // hostile label can never break out of the path.
  function assetSrc(base, color, label) {
    var safeColor = typeof color === "string" && HEX_COLOR.test(color) ? color : null;
    if (!base || !safeColor) {
      return null;
    }
    var src = base + "/" + encodeURIComponent(safeColor);
    if (typeof label === "string" && /^[A-Za-z]/.test(label)) {
      src += "/" + encodeURIComponent(label);
    }
    return src;
  }

  function createAssetImage(doc, base, problem, className, altPrefix) {
    var img = doc.createElement("img");
    img.setAttribute("class", className);
    var src = assetSrc(base, problem.color, problem.label);
    if (src) {
      img.setAttribute("src", src);
    }
    // alt is set via setAttribute (an attribute, never parsed as markup), so a
    // hostile label stays inert.
    img.setAttribute("alt", altPrefix + problem.label);
    return img;
  }

  // The balloon artwork (letter baked in) for a problem column header.
  function createBalloonImage(doc, problem) {
    return createAssetImage(doc, problem.balloonBase, problem, "animator-balloon", "Problem ");
  }

  // The star artwork (letter baked in) marking the first solver of a problem.
  function createStarImage(doc, problem) {
    return createAssetImage(
      doc,
      problem.starBase,
      problem,
      "animator-balloon animator-cell-star",
      "First to solve ",
    );
  }

  // Compact solved-cell artwork, matching the Web scoreboard's 23 × 35 layout.
  // A first solve uses the star route; every other solve uses the balloon route.
  function createSolvedImage(doc, problem, first) {
    var image = doc.createElement("img");
    image.setAttribute(
      "class",
      "animator-cell-result-balloon mb-1" + (first ? " animator-cell-star" : ""),
    );
    var src = assetSrc(first ? problem.starBase : problem.balloonBase, problem.color, null);
    if (src) {
      image.setAttribute("src", src);
    }
    image.setAttribute("alt", (first ? "First to solve " : "Solved ") + problem.label);
    image.setAttribute("width", "23");
    image.setAttribute("height", "35");
    return image;
  }

  // Rebuild the per-problem header columns after the four fixed leading <th>.
  // Each column shows the balloon artwork with the problem letter inside it.
  function renderHeader(doc, headerEl, problems) {
    if (!headerEl) {
      return;
    }
    while (headerEl.children.length > 4) {
      headerEl.removeChild(headerEl.lastChild);
    }
    problems.forEach(function (problem) {
      var th = doc.createElement("th");
      th.setAttribute("scope", "col");
      th.setAttribute("class", "animator-problem-col");
      if (problem.problemId !== undefined && problem.problemId !== null) {
        th.setAttribute("data-problem-id", String(problem.problemId));
      }
      th.appendChild(createBalloonImage(doc, problem));
      headerEl.appendChild(th);
    });
  }

  function appendHidden(doc, cell, text) {
    var span = doc.createElement("span");
    span.setAttribute("class", "visually-hidden");
    span.textContent = " " + text;
    cell.appendChild(span);
  }

  // Data-driven state classes fillProblemCell owns. They are cleared and re-added
  // on every update; transient `animator-cell--flash-*` highlight classes are NOT
  // in this list, so they survive an in-place re-render for their full lifetime.
  var DATA_CELL_STATE_CLASSES = [
    "animator-cell--solved",
    "animator-cell--attempted",
    "animator-cell--pending",
    "animator-cell--first",
  ];

  function createProblemCell(doc, problem) {
    var td = doc.createElement("td");
    td.setAttribute("class", "animator-cell");
    if (problem.problemId !== undefined && problem.problemId !== null) {
      td.setAttribute("data-problem-id", String(problem.problemId));
    }
    return td;
  }

  function textBlock(doc, className, text) {
    var block = doc.createElement("div");
    block.setAttribute("class", className);
    block.textContent = text;
    return block;
  }

  // Populate a (possibly reused) problem cell from its data, preserving any
  // transient flash class already on it. On a first solve the cell also carries a
  // balloon in the problem's color, so "got the balloon" reads at a glance.
  function fillProblemCell(doc, td, problem, cellData, options) {
    DATA_CELL_STATE_CLASSES.forEach(function (cls) {
      td.classList.remove(cls);
    });
    td.replaceChildren();
    var inner = doc.createElement("div");
    inner.setAttribute("class", "noca-problem-cell-inner");
    td.appendChild(inner);
    if (!cellData) {
      appendHidden(doc, td, "no attempts");
      return;
    }
    // Glyph, state, and wording come from the shared formatter, which is also
    // what the reveal projector draws from — one owner for what a cell means.
    var state = format.cellState(cellData);
    var attempts = format.attemptsOf(cellData);
    var penalty = format.penaltyOf(cellData);
    var stackPending = !!(options && options.stackPending && format.isPending(cellData));
    if (stackPending) {
      inner.appendChild(
        textBlock(
          doc,
          "text-warning-emphasis small fw-semibold",
          format.formatPendingMarks(cellData),
        ),
      );
    }

    if (state === "solved") {
      td.classList.add("animator-cell--solved");
      var first = format.isFirst(cellData);
      if (first) {
        td.classList.add("animator-cell--first");
      }
      var resultImage = createSolvedImage(doc, problem, first);
      if (resultImage.getAttribute("src")) {
        inner.appendChild(resultImage);
      }
      if (cellData.solved_at_minutes !== null && cellData.solved_at_minutes !== undefined) {
        inner.appendChild(
          textBlock(doc, "fw-semibold text-success small", cellData.solved_at_minutes + "'"),
        );
      }
      if (attempts > 0) {
        inner.appendChild(
          textBlock(doc, "text-danger small", "+" + attempts + " (" + penalty + "')"),
        );
      }
      if (first) {
        appendHidden(doc, td, "first solve");
      }
      appendHidden(doc, td, "solved");
    } else if (state === "pending") {
      td.classList.add("animator-cell--pending");
      if (stackPending) {
        if (attempts > 0) {
          inner.appendChild(
            textBlock(doc, "text-warning-emphasis small fw-semibold", format.MINUS + attempts),
          );
        }
      } else {
        inner.appendChild(
          textBlock(doc, "text-warning-emphasis small fw-semibold", format.formatCellText(cellData)),
        );
      }
      if (penalty > 0) {
        inner.appendChild(textBlock(doc, "text-warning-emphasis small", "(" + penalty + "')"));
      }
      appendHidden(doc, td, format.describeCell(cellData));
    } else if (state === "attempted") {
      td.classList.add("animator-cell--attempted");
      inner.appendChild(
        textBlock(doc, "text-danger small fw-semibold", format.formatCellText(cellData)),
      );
      if (penalty > 0) {
        inner.appendChild(textBlock(doc, "text-danger small", "(" + penalty + "')"));
      }
      appendHidden(doc, td, format.describeCell(cellData));
    } else {
      appendHidden(doc, td, "no attempts");
    }
  }

  function buildProblemCell(doc, problem, cellData) {
    var td = createProblemCell(doc, problem);
    fillProblemCell(doc, td, problem, cellData);
    return td;
  }

  // Fill the team header cell. Rebuilt each update (it is never a flash target),
  // so a rename would still be reflected.
  function fillTeamCell(doc, th, standing) {
    th.replaceChildren();
    // First line: full name (prominent). Second line: site (muted). The login is
    // only a fallback when no full name exists.
    var primary = doc.createElement("span");
    primary.setAttribute("class", "animator-team-primary");
    primary.textContent = format.teamLabel(standing);
    th.appendChild(primary);
    if (standing.site_name) {
      var secondary = doc.createElement("span");
      secondary.setAttribute("class", "animator-team-secondary");
      secondary.textContent = standing.site_name;
      th.appendChild(secondary);
    }
  }

  function buildRow(doc, problems, standing) {
    var tr = doc.createElement("tr");
    tr.setAttribute("data-team-id", String(standing.team_id));

    var rank = doc.createElement("td");
    rank.setAttribute("class", "animator-col-rank");
    tr.appendChild(rank);

    var team = doc.createElement("th");
    team.setAttribute("scope", "row");
    team.setAttribute("class", "animator-col-team");
    tr.appendChild(team);

    var solved = doc.createElement("td");
    solved.setAttribute("class", "animator-col-solved");
    tr.appendChild(solved);

    var time = doc.createElement("td");
    time.setAttribute("class", "animator-col-time");
    tr.appendChild(time);

    problems.forEach(function (problem) {
      tr.appendChild(createProblemCell(doc, problem));
    });
    updateRow(doc, tr, problems, standing);
    return tr;
  }

  // Update an existing row in place: text and data-driven cell classes change,
  // but the row/cell elements — and any transient flash classes on them — are
  // kept, so a highlight survives subsequent refreshes.
  function updateRow(doc, tr, problems, standing) {
    tr.children[0].textContent = String(standing.rank);
    fillTeamCell(doc, tr.children[1], standing);
    tr.children[2].textContent = String(standing.problems_solved);
    tr.children[3].textContent = String(standing.total_time);
    var cells = standing.problems || {};
    var byProblemId = {};
    for (var i = 4; i < tr.children.length; i++) {
      var existing = tr.children[i];
      byProblemId[existing.getAttribute("data-problem-id")] = existing;
    }
    problems.forEach(function (problem) {
      var key = problem.problemId === undefined || problem.problemId === null ? null : String(problem.problemId);
      var td = key !== null ? byProblemId[key] : null;
      if (!td) {
        td = createProblemCell(doc, problem);
        tr.appendChild(td);
      }
      fillProblemCell(doc, td, problem, cells[problem.label]);
    });
  }

  // Reconcile the standings body in place, keyed by `team_id`. Surviving rows are
  // reused (moved into server order via re-append) and updated; new teams are
  // built; departed teams are removed. Because elements persist across refreshes,
  // transient highlight classes live their full lifetime instead of being wiped
  // by a full rebuild. Returns true when at least one row is present.
  function renderStandings(doc, tbodyEl, problems, standings) {
    if (!tbodyEl) {
      return Array.isArray(standings) && standings.length > 0;
    }
    var rows = Array.isArray(standings) ? standings : [];
    keyedRows.reconcile(tbodyEl, rows, {
      key: function (standing) {
        return standing.team_id;
      },
      build: function (standing) {
        return buildRow(doc, problems, standing);
      },
      update: function (tr, standing) {
        updateRow(doc, tr, problems, standing);
      },
    });
    return rows.length > 0;
  }

  return {
    formatDuration: formatDuration,
    computeTimerView: computeTimerView,
    isReleasedFinal: isReleasedFinal,
    extractProblems: extractProblems,
    createBalloonImage: createBalloonImage,
    createSolvedImage: createSolvedImage,
    createStarImage: createStarImage,
    renderHeader: renderHeader,
    createProblemCell: createProblemCell,
    fillProblemCell: fillProblemCell,
    buildProblemCell: buildProblemCell,
    buildRow: buildRow,
    updateRow: updateRow,
    renderStandings: renderStandings,
  };
});
