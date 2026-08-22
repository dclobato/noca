// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 The NOCA Authors (see AUTHORS)
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

/**
 * Own-row scroll: when the viewer is browsing as a team, scroll their own row
 * into view once on load. The row's highlight itself is a persistent CSS
 * class (.noca-own-team-row) applied server-side, not a fading flash — so no
 * hash/highlight-row.js involvement is needed here, only the scroll.
 */
document.addEventListener('DOMContentLoaded', function () {
  var marker = document.querySelector('[data-own-team-id]');
  if (!marker) return;
  var teamId = marker.getAttribute('data-own-team-id');
  if (!teamId) return;
  var row = document.getElementById(teamId);
  if (!row) return;
  row.scrollIntoView({ behavior: 'smooth', block: 'center' });
});
