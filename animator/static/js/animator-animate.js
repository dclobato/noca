//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

// DOM applier for the animator live scoreboard: FLIP rank transitions plus
// transient state-highlight classes. Kept separate from the orchestrator so it
// can be exercised headlessly with a DOM shim and injected timing primitives.
// The FLIP move is a transform-only animation (First-Last-Invert-Play); reduced
// motion neutralizes the transform transition via CSS while the static highlight
// classes remain visible. Exported as UMD: `window.AnimatorAnimate` /
// `module.exports`.
(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
  if (root) {
    root.AnimatorAnimate = api;
  }
})(typeof window !== "undefined" ? window : null, function () {
  "use strict";

  // Lifetime of a transient highlight before its class is removed.
  var HIGHLIGHT_MS = 1600;
  var ROW_MOTION_DURATION_PROPERTY = "--animator-row-motion-duration";
  var ROW_MOTION_EASING_PROPERTY = "--animator-row-motion-easing";

  // Convert one computed CSS time token to the milliseconds Web Animations
  // expects. Invalid values return null so the caller can retain the CSS path.
  function cssTimeMilliseconds(value) {
    var token = String(value || "").trim();
    var multiplier = 0;
    if (token.endsWith("ms")) {
      multiplier = 1;
      token = token.slice(0, -2);
    } else if (token.endsWith("s")) {
      multiplier = 1000;
      token = token.slice(0, -1);
    } else {
      return null;
    }
    var amount = Number(token);
    return Number.isFinite(amount) && amount >= 0 ? amount * multiplier : null;
  }

  // Read the same custom properties that drive the CSS transition. A browser
  // without computed styles returns null and uses createApplier's CSS fallback,
  // keeping CSS as the sole owner of duration and easing on both paths.
  function rowAnimationFromCss(element, getStyle) {
    var readStyle =
      getStyle ||
      (typeof window !== "undefined" && typeof window.getComputedStyle === "function"
        ? window.getComputedStyle.bind(window)
        : null);
    if (!element || !readStyle) {
      return null;
    }
    var style = readStyle(element);
    if (!style || typeof style.getPropertyValue !== "function") {
      return null;
    }
    var duration = cssTimeMilliseconds(style.getPropertyValue(ROW_MOTION_DURATION_PROPERTY));
    var easing = String(style.getPropertyValue(ROW_MOTION_EASING_PROPERTY) || "").trim();
    if (duration === null || !easing) {
      return null;
    }
    return { duration: duration, easing: easing };
  }

  // Create an applier bound to a tbody element and injectable timing hooks so
  // tests can drive it without a real browser.
  //   opts.raf:   requestAnimationFrame-like (cb) -> handle
  //   opts.setTimeout / opts.clearTimeout: timer primitives
  //   opts.highlightMs: highlight lifetime override
  //   opts.rowAnimation: Web Animations timing used instead of CSS transitions
  function createApplier(tbody, opts) {
    var options = opts || {};
    var raf =
      options.raf ||
      (typeof window !== "undefined" && window.requestAnimationFrame
        ? window.requestAnimationFrame.bind(window)
        : function (cb) {
            return setTimeout(cb, 16);
          });
    var setTimer = options.setTimeout || (typeof setTimeout !== "undefined" ? setTimeout : null);
    var clearTimer = options.clearTimeout || (typeof clearTimeout !== "undefined" ? clearTimeout : null);
    var highlightMs = typeof options.highlightMs === "number" ? options.highlightMs : HIGHLIGHT_MS;
    var rowAnimation = options.rowAnimation || null;
    // Reduced motion disables the FLIP move entirely — no inline transform is
    // written — so persisted state highlights show without any displacement.
    var reducedMotion =
      options.reducedMotion ||
      function () {
        return typeof window !== "undefined" && window.matchMedia
          ? window.matchMedia("(prefers-reduced-motion: reduce)").matches
          : false;
      };
    // Reading layout forces the browser to commit the inverted transform before
    // it is cleared, so the release deterministically transitions instead of the
    // two writes coalescing into no animation.
    var forceReflow =
      options.forceReflow ||
      function (el) {
        return el && el.getBoundingClientRect ? el.getBoundingClientRect().top : 0;
      };

    // `will-change` is a hint about imminent work, not a property a row should
    // wear at rest. A blanket stylesheet rule promotes every row of the board to
    // its own compositor layer permanently, which on a large contest is a
    // standing memory cost on the machine driving a projector. It is written
    // here only on rows that are actually moving, and cleared once they stop.
    var MOTION_HINT_FALLBACK_MS = 1200;

    function motionLifetimeMs() {
      if (rowAnimation && typeof rowAnimation.duration === "number") {
        return rowAnimation.duration;
      }
      var fromCss = rowAnimationFromCss(tbody);
      return fromCss && typeof fromCss.duration === "number" ? fromCss.duration : MOTION_HINT_FALLBACK_MS;
    }

    // Promote for the duration of one move. A later update re-promotes before
    // its own motion, so an early clear from an overlapping update costs at most
    // the hint — never the animation.
    function hintMotion(rows) {
      var hinted = [];
      for (var i = 0; i < rows.length; i++) {
        if (rows[i] && rows[i].style) {
          rows[i].style.willChange = "transform";
          hinted.push(rows[i]);
        }
      }
      if (!setTimer || hinted.length === 0) {
        return;
      }
      setTimer(function () {
        for (var j = 0; j < hinted.length; j++) {
          hinted[j].style.willChange = "";
        }
      }, motionLifetimeMs());
    }

    // Track one pending removal timer per (element, class). Reapplying the same
    // class to the same element clears the previous timer first, so a rapid
    // second update never has its stale timeout strip a fresh highlight.
    var pending = [];

    function findPending(el, cls) {
      for (var i = 0; i < pending.length; i++) {
        if (pending[i].el === el && pending[i].cls === cls) {
          return i;
        }
      }
      return -1;
    }

    function flash(el, cls) {
      if (!el || !el.classList) {
        return;
      }
      var idx = findPending(el, cls);
      if (idx !== -1) {
        if (clearTimer) {
          clearTimer(pending[idx].handle);
        }
        pending.splice(idx, 1);
      }
      el.classList.add(cls);
      if (!setTimer) {
        return;
      }
      var record = { el: el, cls: cls, handle: null };
      record.handle = setTimer(function () {
        var at = findPending(el, cls);
        if (at !== -1) {
          pending.splice(at, 1);
        }
        el.classList.remove(cls);
      }, highlightMs);
      pending.push(record);
    }

    function findCell(teamId, problemId) {
      return tbody
        ? tbody.querySelector(
            'tr[data-team-id="' + cssEscape(teamId) + '"] td[data-problem-id="' + cssEscape(problemId) + '"]',
          )
        : null;
    }

    // Snapshot the top offset of every current row by team id (the "First" read
    // of FLIP), before the caller replaces the tbody contents.
    function measureRows() {
      var positions = {};
      var rows = tbody ? tbody.children : [];
      for (var i = 0; i < rows.length; i++) {
        var row = rows[i];
        var teamId = row.getAttribute ? row.getAttribute("data-team-id") : null;
        if (teamId !== null && teamId !== undefined && row.getBoundingClientRect) {
          positions[teamId] = row.getBoundingClientRect().top;
        }
      }
      return positions;
    }

    // Play the FLIP move for rows present before and after the swap: invert to
    // the old position with a transform, then release on the next frame so the
    // browser animates back to the natural (new) position.
    function playFlip(firstTops) {
      // Skip the move for reduced-motion viewers: never write a transform, so
      // there is no displacement or snap — only the static highlights remain.
      if (reducedMotion()) {
        return;
      }
      var rows = tbody ? tbody.children : [];

      // Pass 1 — read only. Every getBoundingClientRect() happens before the
      // first style write, so the browser answers them all from one layout.
      // Interleaving the write below into this loop would invalidate layout on
      // each iteration and force a synchronous recalc for the next row's read:
      // one forced layout per moved row, on the hot path of every rank change.
      var pendingMoves = [];
      for (var i = 0; i < rows.length; i++) {
        var row = rows[i];
        var teamId = row.getAttribute ? row.getAttribute("data-team-id") : null;
        if (teamId === null || firstTops[teamId] === undefined || !row.getBoundingClientRect) {
          continue;
        }
        var delta = firstTops[teamId] - row.getBoundingClientRect().top;
        if (delta) {
          pendingMoves.push({ row: row, delta: delta });
        }
      }
      if (pendingMoves.length === 0) {
        return;
      }

      // Pass 2 — write only. The compositor hint goes on first, before any
      // transform, so the browser can prepare the layers it is about to move.
      var movingRows = [];
      for (var h = 0; h < pendingMoves.length; h++) {
        movingRows.push(pendingMoves[h].row);
      }
      hintMotion(movingRows);

      var moved = [];
      for (var j = 0; j < pendingMoves.length; j++) {
        var move = pendingMoves[j];
        // The reveal projector opts into Web Animations so table-row motion
        // starts reliably even when a browser coalesces style writes around a
        // complete tbody replacement. The live board keeps its established
        // CSS-transition path unless its caller opts in too.
        if (rowAnimation && typeof move.row.animate === "function") {
          move.row.animate(
            [{ transform: "translateY(" + move.delta + "px)" }, { transform: "translateY(0)" }],
            rowAnimation,
          );
          continue;
        }
        move.row.style.transform = "translateY(" + move.delta + "px)";
        move.row.style.transition = "none";
        moved.push(move.row);
      }
      if (moved.length === 0) {
        return;
      }
      // Commit the inverted (First) state before releasing it, so the transition
      // is guaranteed to run rather than being optimized away.
      forceReflow(moved[0]);
      raf(function () {
        moved.forEach(function (row) {
          row.style.transition = "";
          row.style.transform = "";
        });
      });
    }

    // Apply the diff result: paint transient row/cell classes and, given the
    // pre-swap positions, run the FLIP move. Cells are addressed by the stable
    // (team_id, problem_id) hooks the renderer emits.
    function apply(diff, firstTops) {
      if (firstTops) {
        playFlip(firstTops);
      }
      var rowClasses = (diff && diff.rowClasses) || {};
      Object.keys(rowClasses).forEach(function (teamId) {
        var row = tbody
          ? tbody.querySelector('tr[data-team-id="' + cssEscape(teamId) + '"]')
          : null;
        rowClasses[teamId].forEach(function (cls) {
          flash(row, cls);
        });
      });
      var cellClasses = (diff && diff.cellClasses) || {};
      Object.keys(cellClasses).forEach(function (key) {
        var parts = key.split(String.fromCharCode(0));
        var teamId = parts[0];
        var problemId = parts[1];
        var cell = findCell(teamId, problemId);
        cellClasses[key].forEach(function (cls) {
          flash(cell, cls);
        });
      });
    }

    // Flash one cell addressed by its stable (team_id, problem_id) hooks. Used by
    // the snapshot-gated pending flash: the orchestrator only calls this once the
    // freshly applied snapshot confirms the cell is still pending.
    function flashCell(teamId, problemId, cls) {
      flash(findCell(teamId, problemId), cls);
    }

    function cssEscape(value) {
      if (typeof window !== "undefined" && window.CSS && window.CSS.escape) {
        return window.CSS.escape(value);
      }
      // Minimal fallback for the headless shim: escape the attribute quote/backslash.
      return String(value).replace(/["\\]/g, "\\$&");
    }

    return {
      measureRows: measureRows,
      apply: apply,
      flash: flash,
      flashCell: flashCell,
      _pendingCount: function () {
        return pending.length;
      },
    };
  }

  return {
    createApplier: createApplier,
    rowAnimationFromCss: rowAnimationFromCss,
    HIGHLIGHT_MS: HIGHLIGHT_MS,
  };
});
