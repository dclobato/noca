//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

/**
 * Render the live contest countdown on the Animator presentation launcher.
 */

/* global ContestClockUtils */
(function () {
  "use strict";

  const clock = document.getElementById("contest-index-clock");
  if (!clock) return;

  const startMs = Number.parseInt(clock.dataset.startMs, 10);
  const endMs = Number.parseInt(clock.dataset.endMs, 10);
  if (!Number.isFinite(startMs) || !Number.isFinite(endMs)) return;

  function render() {
    clock.textContent = ContestClockUtils.countdownText(Date.now(), startMs, endMs);
  }

  render();
  window.setInterval(render, 1_000);
})();
