// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

/**
 * contest-login-clock.js
 *
 * Displays a live countdown on the contest login page (/c/{slug}/login).
 *
 * Behaviour:
 *   - Upcoming contest : "Xh Ymin to start... and counting"
 *   - Running contest  : "Xh Ymin until end"
 *   - Past contest     : "The contest is over"
 *   - When < 5 minutes remain (either phase), seconds are included.
 *
 * Time source: contest start/end timestamps are baked into data-* attributes
 * by the server at render time. Date.now() is used directly — no server sync
 * is attempted because the login page has no authenticated session to call the
 * clock endpoint. Clock drift is acceptable here; a user watching the login
 * page for 12 hours is not a realistic scenario.
 *
 * Compare with contest-clock.js, which serves the authenticated navbar clock
 * and adds SSE + polling to stay continuously in sync with the server clock.
 *
 * Display logic (formatDuration, countdownText) lives in contest-clock-utils.js,
 * which must be loaded before this file.
 *
 * Expected markup:
 *   <div id="login-countdown"
 *        data-start-ms="<epoch_ms>"
 *        data-end-ms="<epoch_ms>">
 *   </div>
 */

/* global ContestClockUtils */
(function () {
  "use strict";

  const el = document.getElementById("login-countdown");
  if (!el) return;

  const startMs = parseInt(el.dataset.startMs, 10);
  const endMs   = parseInt(el.dataset.endMs,   10);
  if (isNaN(startMs) || isNaN(endMs)) return;

  // ── Render ──────────────────────────────────────────────────────────────────

  function render() {
    el.textContent = ContestClockUtils.countdownText(Date.now(), startMs, endMs);
  }

  // ── Bootstrap ────────────────────────────────────────────────────────────────

  render();
  setInterval(render, 1_000);
})();
