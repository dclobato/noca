// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

/**
 * contest-clock-utils.js
 *
 * Shared display logic for contest countdown clocks.
 *
 * Exposes a single global object `ContestClockUtils` with two pure functions:
 *
 *   ContestClockUtils.formatDuration(ms, showSeconds) -> string
 *     Formats a duration in milliseconds as a human-readable string
 *     (e.g. "1h 30min", "4min 12s").
 *
 *   ContestClockUtils.countdownText(nowMs, startMs, endMs) -> string
 *     Returns the appropriate countdown string for the current moment
 *     given contest start/end times (all in epoch milliseconds).
 *
 * This file must be loaded before contest-clock.js and contest-login-clock.js.
 */

/* global window */
(function (global) {
  "use strict";

  const SHORT_THRESHOLD_MS = 5 * 60 * 1000;        // show seconds when < 5 min remain
  const COMING_SOON_MS     = 12 * 60 * 60 * 1000;  // "Coming soon" when > 12 h to start

  /**
   * Format a duration (ms) into a human-readable string.
   * Shows seconds only when `showSeconds` is true.
   *
   * Examples:
   *   formatDuration(5400000, false) -> "1h30min"
   *   formatDuration(39900000, false) -> "11h05min"
   *   formatDuration(  75000, true)  -> "1min15s"
   *   formatDuration(      0, true)  -> "0min"
   */
  function formatDuration(ms, showSeconds) {
    const total   = Math.max(0, Math.floor(ms / 1000));
    const hours   = Math.floor(total / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    const seconds = total % 60;
    const parts   = [];
    const mm = hours > 0 ? String(minutes).padStart(2, "0") : String(minutes);
    const ss = String(seconds).padStart(2, "0");
    if (hours   > 0) parts.push(hours + "h");
    if (minutes > 0 || hours > 0) parts.push(mm + "min");
    if (showSeconds) {
      parts.push((parts.length === 0 ? String(seconds) : ss) + "s");
    } else if (parts.length === 0) {
      parts.push("0min");
    }
    return parts.join(" ");
  }

  /**
   * Return the countdown string appropriate for `nowMs` given a contest
   * that starts at `startMs` and ends at `endMs` (all epoch milliseconds).
   *
   * States:
   *   upcoming (> 12 h)  : "Coming soon…"
   *   upcoming (≤ 12 h)  : "Xh Ymin to start… and counting"  (seconds shown < 5 min)
   *   running            : "Xh Ymin until end"                (seconds shown < 5 min)
   *   past               : "The contest is over"
   */
  function countdownText(nowMs, startMs, endMs) {
    if (nowMs < startMs) {
      const remainingMs = startMs - nowMs;
      if (remainingMs >= COMING_SOON_MS) {
        return "Coming soon\u2026";
      }
      return formatDuration(remainingMs, remainingMs < SHORT_THRESHOLD_MS)
           + " to start\u2026 and counting";
    }
    if (nowMs <= endMs) {
      const remainingMs = endMs - nowMs;
      return formatDuration(remainingMs, remainingMs < SHORT_THRESHOLD_MS)
           + " until end";
    }
    return "The contest is over";
  }

  global.ContestClockUtils = { formatDuration: formatDuration, countdownText: countdownText };

}(window));
