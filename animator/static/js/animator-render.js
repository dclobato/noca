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
  var teamCell =
    typeof module !== "undefined" && module.exports
      ? require("./animator-team-cell.js")
      : (typeof window !== "undefined" ? window : {}).AnimatorTeamCell;

  var HEX_COLOR = /^[0-9a-fA-F]{3,8}$/;
  var MEDAL_BANDS = ["gold", "silver", "bronze"];
  var SHEET_ID = "animator-problem-colors";

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

  // A hidden window as a person would say it: "45 min", "1 h", "1 h 20 min".
  function formatHiddenWindow(ms) {
    var minutes = Math.floor(ms / 60000);
    if (minutes < 60) {
      return minutes + " min";
    }
    var hours = Math.floor(minutes / 60);
    var rest = minutes % 60;
    return rest === 0 ? hours + " h" : hours + " h " + rest + " min";
  }

  // "Frozen · last 45 min hidden" -- the question a frozen board actually raises
  // is "how much am I not seeing?", and this answers it outright instead of
  // leaving the reader to subtract a freeze time from an elapsed clock.
  //
  // The window is measured to NOW while the contest runs and to the END once it
  // is over, because those are genuinely different amounts: at 03:15 on a board
  // frozen at 02:40, only 35 minutes are hidden, not the 60 the contest rules
  // set aside. Stating the planned window during the contest would overstate it.
  // It also means the figure depends on `end_time` only after the end, by which
  // point the end can no longer move.
  //
  // Degrades to a bare "Frozen" whenever the window is unknown, nonsensical, or
  // still under a minute: a wrong or absurd number here is worse than none,
  // because a room reads it as authoritative.
  function frozenLabel(startMs, endMs, freezeMs, nowMs) {
    var unusable =
      freezeMs === null ||
      freezeMs === undefined ||
      isNaN(freezeMs) ||
      freezeMs < startMs ||
      endMs === null ||
      endMs === undefined ||
      isNaN(endMs);
    if (unusable) {
      return "Frozen";
    }
    var hidden = Math.min(nowMs, endMs) - freezeMs;
    if (hidden < 60000) {
      return "Frozen";
    }
    return "Frozen · last " + formatHiddenWindow(hidden) + " hidden";
  }

  // Pure timer projection covering all contest and public-scoreboard states.
  // Once the contest ends, two independent badges remain visible: Frozen or
  // Final for scoreboard visibility, plus Ended for the contest lifecycle.
  // `running` reports whether the caller should keep ticking (false once the
  // contest has ended or the timing is unknown, so no interval is scheduled).
  // `freezeMs` is optional: omitted, the frozen states just read "Frozen".
  function computeTimerView(startMs, endMs, frozen, nowMs, freezeMs) {
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
        label: frozen ? frozenLabel(startMs, endMs, freezeMs, nowMs) : "Final",
        ended: true,
        text: formatDuration(endMs - startMs),
        running: false,
      };
    }
    var elapsed = formatDuration(nowMs - startMs);
    if (frozen) {
      return {
        state: "frozen",
        label: frozenLabel(startMs, endMs, freezeMs, nowMs),
        ended: false,
        text: elapsed,
        running: true,
      };
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
  // optional `assets` carries the balloon image mount base so each problem object
  // can build its own header artwork src without threading extra parameters
  // through the render/board call chain. There is no star base: the first-solve
  // mark is a glyph coloured by renderProblemColors, not a served asset.
  function extractProblems(meta, assets) {
    var opts = assets || {};
    var list = meta && Array.isArray(meta.problems) ? meta.problems : [];
    return list.map(function (problem) {
      return {
        label: String(problem.label),
        color: problem.balloon_color,
        problemId: problem.problem_id,
        balloonBase: opts.balloonBase || null,
      };
    });
  }

  // Build the src for a balloon asset served by the animator's own /assets
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

  // The star marking the first solver of a problem, drawn INSIDE the solve-minute
  // line so it costs no row height. An ordinary solve carries no artwork at all:
  // a problem's balloon identifies its column from the header, and repeating it in
  // every solved cell is what made each row 80px tall.
  //
  // A text glyph rather than the /assets/star artwork: at one line tall that SVG
  // showed more white backing disc and outline than colour. The glyph takes the
  // problem's colour from `--noca-cell-balloon`, which renderProblemColors sets
  // per column, so this element needs no per-cell wiring at all.
  //
  // Decorative on purpose: fillProblemCell already appends a visually-hidden
  // "first solve" span, and two announcements for one fact is worse than none.
  function createFirstMark(doc) {
    var mark = doc.createElement("span");
    mark.setAttribute("class", "noca-cell-first-mark animator-cell-star");
    mark.setAttribute("aria-hidden", "true");
    mark.textContent = "★";
    return mark;
  }

  // Give each problem column its balloon colour, as one stylesheet keyed on
  // column position rather than a per-cell attribute.
  //
  // It cannot be a class: a problem's colour is free-form hex (the admin form
  // offers a native colour picker beside the palette), so the value space is
  // every colour there is. The colour is validated against HEX_COLOR before it
  // reaches the sheet — a stylesheet, unlike the /assets/balloon route that
  // answers 400, would execute whatever it is handed — and a column whose
  // colour does not validate is simply left out, falling back to no bar.
  //
  // Four fixed columns (rank, team, solved, time) precede the problems on both
  // animator surfaces, hence the +5.
  function problemColorRules(problems) {
    var rules = [];
    (problems || []).forEach(function (problem, index) {
      var color = problem && problem.color;
      if (typeof color !== "string" || !HEX_COLOR.test(color)) {
        return;
      }
      rules.push(
        ".animator-scoreboard .animator-cell:nth-child(" +
          (index + 5) +
          "){--noca-cell-balloon:#" +
          color +
          "}",
      );
    });
    return rules.join("\n");
  }

  // The sheet is created here rather than shipped in the page: both animator
  // templates assert that the served HTML carries nothing inline, and an empty
  // <style> element in the markup would break that contract to no purpose.
  function ensureProblemColorSheet(doc) {
    var existing = doc.getElementById ? doc.getElementById(SHEET_ID) : null;
    if (existing) {
      return existing;
    }
    var sheet = doc.createElement("style");
    sheet.setAttribute("id", SHEET_ID);
    if (doc.head && doc.head.appendChild) {
      doc.head.appendChild(sheet);
    }
    return sheet;
  }

  // Replaced wholesale on every /meta, never appended to, so a problem set that
  // shrinks cannot leave a departed column still coloured.
  function renderProblemColors(doc, problems) {
    var sheet = ensureProblemColorSheet(doc);
    if (sheet) {
      sheet.textContent = problemColorRules(problems);
    }
    return sheet;
  }

  // The oversized medal watermark served by the animator's own /assets/medal/{band}
  // route. Shared with the reveal ceremony rather than duplicated there, so the
  // live board and the projector draw the same artwork from the same markup.
  function createMedalImage(doc, medalBase, medal) {
    if (!medalBase || MEDAL_BANDS.indexOf(medal) === -1) {
      return null;
    }
    var img = doc.createElement("img");
    img.setAttribute("class", "animator-medal-watermark");
    img.setAttribute("src", medalBase + "/" + encodeURIComponent(medal));
    img.setAttribute("alt", medal + " medal");
    return img;
  }

  // Keep a row's `data-medal` in step with the server's band. Set *and* remove,
  // never set-only: a team that drops off the podium between refreshes must lose
  // the attribute rather than keep a stale band.
  function syncMedalAttribute(tr, medal) {
    if (medal && MEDAL_BANDS.indexOf(medal) !== -1) {
      tr.setAttribute("data-medal", medal);
    } else {
      tr.removeAttribute("data-medal");
    }
  }

  // Whether this row is the last of its medal band (used for the heavier rule).
  function isBandEnd(rows, index) {
    var row = rows[index];
    var medal = row && row.medal;
    if (!medal || MEDAL_BANDS.indexOf(medal) === -1) {
      return false;
    }
    var next = rows[index + 1];
    return !next || next.medal !== medal;
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
    "noca-cell-balloon-edge",
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

  // An inline run inside one of the cell's lines, so a line can hold both the
  // first-solve star and its text without the two stacking.
  function textSpan(doc, className, text) {
    var span = doc.createElement("span");
    span.setAttribute("class", className);
    span.textContent = text;
    return span;
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
    var stackPending = !!(options && options.stackPending && format.isPending(cellData));
    if (stackPending) {
      inner.appendChild(
        textBlock(doc, "fw-semibold", format.formatPendingMarks(cellData)),
      );
    }

    if (state === "solved") {
      td.classList.add("animator-cell--solved");
      td.classList.add("noca-cell-balloon-edge");
      var first = format.isFirst(cellData);
      if (first) {
        td.classList.add("animator-cell--first");
      }
      if (cellData.solved_at_minutes !== null && cellData.solved_at_minutes !== undefined) {
        // The star rides INSIDE the minute line rather than above it, so marking a
        // first solve adds no row height. An ordinary solve gets no artwork at all.
        var minuteLine = doc.createElement("div");
        minuteLine.setAttribute("class", "fw-semibold");
        if (first) {
          minuteLine.appendChild(createFirstMark(doc));
        }
        minuteLine.appendChild(
          textSpan(doc, "animator-cell-minutes", cellData.solved_at_minutes + "'"),
        );
        inner.appendChild(minuteLine);
      }
      var solvedNote = format.formatAttemptLine(cellData);
      if (solvedNote) {
        inner.appendChild(textBlock(doc, "noca-cell-note", solvedNote));
      }
      if (first) {
        appendHidden(doc, td, "first solve");
      }
      appendHidden(doc, td, "solved");
    } else if (state === "pending") {
      td.classList.add("animator-cell--pending");
      // Stacked (the projector) puts the "?" marks on their own line with the
      // attempts and penalty beneath; unstacked (the live board) is one line.
      // Either way the attempts and the penalty stay together on one line.
      var pendingLine = format.formatAttemptLine(cellData, { stacked: stackPending });
      if (pendingLine) {
        inner.appendChild(
          textBlock(
            doc,
            stackPending
              ? "noca-cell-note"
              : "fw-semibold",
            pendingLine,
          ),
        );
      }
      appendHidden(doc, td, format.describeCell(cellData));
    } else if (state === "attempted") {
      td.classList.add("animator-cell--attempted");
      inner.appendChild(
        textBlock(doc, "fw-semibold", format.formatAttemptLine(cellData)),
      );
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

  // Refresh the team header cell while retaining its modal trigger. The helper
  // rebuilds only presentation siblings, so renames still appear immediately.
  function fillTeamCell(doc, th, standing, medalBase) {
    teamCell.syncTeamCell(doc, th, standing, {
      medalBase: medalBase,
      createMedalImage: createMedalImage,
    });
  }

  function buildRow(doc, problems, standing, options) {
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
    updateRow(doc, tr, problems, standing, options);
    return tr;
  }

  // Update an existing row in place: text and data-driven cell classes change,
  // but the row/cell elements — and any transient flash classes on them — are
  // kept, so a highlight survives subsequent refreshes.
  function updateRow(doc, tr, problems, standing, options) {
    var opts = options || {};
    tr.children[0].textContent = String(standing.rank);
    fillTeamCell(doc, tr.children[1], standing, opts.medalBase);
    // classList rather than setAttribute("class"): the live board parks transient
    // flash classes on this same row, and rewriting the attribute would wipe them.
    syncMedalAttribute(tr, standing.medal);
    if (opts.bandEnd) {
      tr.classList.add("animator-row--band-end");
    } else {
      tr.classList.remove("animator-row--band-end");
    }
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
  function renderStandings(doc, tbodyEl, problems, standings, options) {
    if (!tbodyEl) {
      return Array.isArray(standings) && standings.length > 0;
    }
    var opts = options || {};
    var rows = Array.isArray(standings) ? standings : [];

    function rowOptions(index) {
      return { medalBase: opts.medalBase || null, bandEnd: isBandEnd(rows, index) };
    }

    keyedRows.reconcile(tbodyEl, rows, {
      key: function (standing) {
        return standing.team_id;
      },
      build: function (standing, index) {
        return buildRow(doc, problems, standing, rowOptions(index));
      },
      update: function (tr, standing, index) {
        updateRow(doc, tr, problems, standing, rowOptions(index));
      },
    });
    return rows.length > 0;
  }

  return {
    formatDuration: formatDuration,
    computeTimerView: computeTimerView,
    formatHiddenWindow: formatHiddenWindow,
    frozenLabel: frozenLabel,
    isReleasedFinal: isReleasedFinal,
    extractProblems: extractProblems,
    createBalloonImage: createBalloonImage,
    createFirstMark: createFirstMark,
    problemColorRules: problemColorRules,
    renderProblemColors: renderProblemColors,
    createMedalImage: createMedalImage,
    syncMedalAttribute: syncMedalAttribute,
    isBandEnd: isBandEnd,
    renderHeader: renderHeader,
    createProblemCell: createProblemCell,
    fillProblemCell: fillProblemCell,
    buildProblemCell: buildProblemCell,
    buildRow: buildRow,
    updateRow: updateRow,
    renderStandings: renderStandings,
  };
});
