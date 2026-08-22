// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 The NOCA Authors (see AUTHORS)
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

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

  // ── Render ──────────────────────────────────────────────────────────────────

  function render() {
    const nowMs = Date.now() + offsetMs;
    el.textContent = ContestClockUtils.countdownText(nowMs, startMs, endMs);
    if (!phaseEl) return;
    const phase = ContestClockUtils.contestPhase(nowMs, startMs, endMs, freezeMs, blindMs);
    const label = ContestClockUtils.phaseLabel(phase);
    phaseEl.textContent = label;
    phaseEl.dataset.phase = phase;
    phaseEl.hidden = label === "";
  }

  // ── Server sync ─────────────────────────────────────────────────────────────

  function applyClockData(data, measuredAtMs) {
    offsetMs = data.server_now_ms - measuredAtMs;
    startMs  = data.start_ms;
    endMs    = data.end_ms;
    freezeMs = typeof data.freeze_ms === "number" ? data.freeze_ms : null;
    blindMs  = typeof data.blind_ms  === "number" ? data.blind_ms  : null;

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
