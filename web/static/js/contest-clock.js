// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
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
 * Time source: prefer SSE from `data-clock-url`; fall back to polling every
 * 30 seconds when SSE is unavailable or repeatedly fails.
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

  const clockUrl = el.dataset.clockUrl;
  if (!clockUrl) return;

  // Mutable state ─────────────────────────────────────────────────────────────
  let offsetMs       = 0;      // serverNow - clientNow at last sync
  let startMs        = 0;      // contest start (epoch ms)
  let endMs          = 0;      // contest end   (epoch ms)
  let lastState      = null;   // "upcoming" | "running" | "past"
  let eventSource    = null;   // EventSource when SSE is active
  let pollingStarted = false;
  let sseFailures    = 0;

  // ── Render ──────────────────────────────────────────────────────────────────

  function render() {
    el.textContent = ContestClockUtils.countdownText(Date.now() + offsetMs, startMs, endMs);
  }

  // ── Server sync ─────────────────────────────────────────────────────────────

  function applyClockData(data, measuredAtMs) {
    offsetMs = data.server_now_ms - measuredAtMs;
    startMs  = data.start_ms;
    endMs    = data.end_ms;

    const newState = data.state;
    if (newState !== lastState) {
      lastState = newState;
    }

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

  function fallbackToPolling() {
    if (eventSource) {
      eventSource.close();
      eventSource = null;
    }
    startPolling();
  }

  function startSse() {
    if (!("EventSource" in window)) {
      startPolling();
      return;
    }

    eventSource = new window.EventSource(clockUrl, { withCredentials: true });
    let sawFirstMessage = false;

    eventSource.onmessage = function (event) {
      let data;
      try {
        data = JSON.parse(event.data);
      } catch (_) {
        sseFailures += 1;
        if (sseFailures >= 3) fallbackToPolling();
        return;
      }
      sawFirstMessage = true;
      sseFailures = 0;
      applyClockData(data, Date.now());
    };

    eventSource.onerror = function () {
      sseFailures += 1;
      if (!sawFirstMessage || sseFailures >= 3) {
        fallbackToPolling();
      }
    };

    setInterval(render, TICK_INTERVAL_MS);
  }

  // ── Bootstrap ────────────────────────────────────────────────────────────────

  startSse();
})();
