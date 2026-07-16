// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

// Paints uptime heatmap cells from their data-uptime attribute:
// 100% uptime -> full green (hue 120), <=70% -> full red (hue 0),
// linear gradient in between. Exact values stay available as text
// in each cell's title tooltip, so color is never the only encoding.
(function () {
  "use strict";

  const FLOOR_PCT = 70;
  const CEIL_PCT = 100;

  function paintCell(cell) {
    const pct = parseFloat(cell.dataset.uptime);
    if (Number.isNaN(pct)) return;
    const t = Math.min(1, Math.max(0, (pct - FLOOR_PCT) / (CEIL_PCT - FLOOR_PCT)));
    const hue = 120 * t;
    const dark = document.documentElement.getAttribute("data-bs-theme") === "dark";
    const lightness = dark ? 38 : 42;
    cell.style.backgroundColor = "hsl(" + hue + " 65% " + lightness + "%)";
  }

  function paintAll() {
    document.querySelectorAll(".healthmon-slot[data-uptime]").forEach(paintCell);
  }

  document.addEventListener("DOMContentLoaded", function () {
    paintAll();
    // Repaint when the shared theme toggle flips data-bs-theme.
    new MutationObserver(paintAll).observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-bs-theme"],
    });
  });
})();
