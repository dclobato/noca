//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

/**
 * contest-clock.js
 *
 * Displays a live countdown in the navbar for authenticated contest pages.
 *
 * Behaviour:
 *   - Upcoming contest : "Xh Ymin to start... and counting"
 *   - Running contest  : "Xh Ymin until end"
 *   - Past contest     : "The contest is over"
 *   - When < 5 minutes remain (either phase), seconds are included.
 *
 * Also paints the contest phase (running / frozen / silence) next to the
 * countdown. The moment a scoreboard freezes is the moment the contest's rules
 * change, and the chrome used to say nothing about it.
 *
 * The countdown additionally carries a `data-urgency` hook naming how much time
 * is left (normal / warning / critical / ended), which the stylesheet colours.
 * It is driven independently of `data-phase` because the two answer different
 * questions, and it is recomputed on the one-second tick rather than on the
 * 60-second resync, so the clock changes colour on the second it should.
 *
 * Time source: poll `data-clock-url` every 60 seconds to stay synchronized
 * with the server while rendering the countdown locally every second.
 *
 * Display logic (formatDuration, countdownText) lives in contest-clock-utils.js,
 * which must be loaded before this file.
 */

/* global ContestClockUtils */
(function () {
  "use strict";

  const RESYNC_INTERVAL_MS = 60_000;  // re-fetch server clock every 60 s
  const TICK_INTERVAL_MS   =  1_000;  // repaint every second

  const el = document.getElementById("contest-countdown");
  if (!el) return;

  const phaseEl = document.getElementById("contest-phase");

  const clockUrl = el.dataset.clockUrl;
  if (!clockUrl) return;

  // Mutable state ─────────────────────────────────────────────────────────────
  let offsetMs       = 0;      // serverNow - clientNow at last sync
  let startMs        = 0;      // contest start (epoch ms)
  let endMs          = 0;      // contest end   (epoch ms)
  let freezeMs       = null;   // scoreboard stops updating (epoch ms)
  let blindMs        = null;   // verdicts stop reaching teams (epoch ms)
  let pollingStarted = false;
  let synced         = false;  // a valid payload has been applied at least once

  // ── Render ──────────────────────────────────────────────────────────────────

  function render() {
    // Until a payload lands, start and end are both 0 -- a window that ended in
    // 1970. Rendering that sentinel would announce "The contest is over", now in
    // the spent-clock colour, to everyone whose first sync failed. The bar keeps
    // saying "Updating..." instead, and the resync loop below is still running.
    if (!synced) return;
    const nowMs = Date.now() + offsetMs;
    el.textContent = ContestClockUtils.countdownText(nowMs, startMs, endMs);
    el.dataset.urgency = ContestClockUtils.countdownUrgency(nowMs, startMs, endMs);
    if (!phaseEl) return;
    const phase = ContestClockUtils.contestPhase(nowMs, startMs, endMs, freezeMs, blindMs);
    const label = ContestClockUtils.phaseLabel(phase);
    phaseEl.textContent = label;
    phaseEl.dataset.phase = phase;
    phaseEl.hidden = label === "";
  }

  // ── Server sync ─────────────────────────────────────────────────────────────

  function isFiniteNumber(value) {
    return typeof value === "number" && Number.isFinite(value);
  }

  function applyClockData(data, measuredAtMs) {
    // A malformed payload would turn every arithmetic result into NaN, and every
    // comparison against NaN is false -- which reads as a running contest with an
    // unformattable countdown. Keep the last good sync instead.
    if (!data || !isFiniteNumber(data.server_now_ms)
        || !isFiniteNumber(data.start_ms) || !isFiniteNumber(data.end_ms)) {
      return;
    }
    offsetMs = data.server_now_ms - measuredAtMs;
    startMs  = data.start_ms;
    endMs    = data.end_ms;
    freezeMs = isFiniteNumber(data.freeze_ms) ? data.freeze_ms : null;
    blindMs  = isFiniteNumber(data.blind_ms)  ? data.blind_ms  : null;
    synced   = true;

    render();
  }

  async function sync() {
    try {
      const beforeMs = Date.now();
      const resp     = await fetch(clockUrl, { credentials: "same-origin" });
      const afterMs  = Date.now();
      if (!resp.ok) return;
      const data = await resp.json();
      // Use midpoint of request round-trip to estimate the moment server read
      // its clock, reducing one-sided latency bias.
      const midMs = Math.floor((beforeMs + afterMs) / 2);
      applyClockData(data, midMs);
    } catch (_) {
      // Network error — keep using last known offset.
    }
  }

  function startPolling() {
    if (pollingStarted) return;
    pollingStarted = true;
    sync().then(() => setInterval(render, TICK_INTERVAL_MS));
    setInterval(sync, RESYNC_INTERVAL_MS);
  }

  // ── Bootstrap ────────────────────────────────────────────────────────────────

  startPolling();
})();
