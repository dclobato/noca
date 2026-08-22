// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 The NOCA Authors (see AUTHORS)
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

/**
 * contest-nav.js
 *
 * Brings the current section into view in the contest navigation band.
 *
 * The band scrolls horizontally rather than collapsing, so on a narrow screen
 * the section you are actually on can start off-screen -- which is precisely
 * when knowing where you are matters most. Scrolling is instant and only ever
 * moves the band itself, never the page.
 */
(function () {
  "use strict";

  const track = document.querySelector(".noca-contest-nav-track");
  if (!track) return;

  const current = track.querySelector(".noca-contest-nav-item.is-current");
  if (!current) return;

  // Nothing to do when the band is not scrollable, which is the common case.
  if (track.scrollWidth <= track.clientWidth) return;

  const offset =
    current.offsetLeft -
    track.offsetLeft -
    (track.clientWidth - current.offsetWidth) / 2;
  track.scrollLeft = Math.max(0, offset);
})();
