// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

/**
 * scoreboard.js
 *
 * Team photo modal: populates #teamPhotoModal with the clicked team's
 * full photo and name when any .team-link anchor is activated.
 */
document.querySelectorAll('.team-link').forEach(function (el) {
  el.addEventListener('click', function () {
    var teamId = this.dataset.teamId;
    var teamName = this.dataset.teamName;
    document.getElementById('teamPhotoModalLabel').textContent = teamName;
    var img = document.getElementById('teamPhotoImg');
    img.src = '/user/' + teamId + '/photo';
    img.alt = teamName;
  });
});

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

/**
 * Back-to-top floating button: smooth-scrolls the window to the top.
 */
(function () {
  var backToTopBtn = document.getElementById('scoreboard-back-to-top');
  if (!backToTopBtn) return;
  backToTopBtn.addEventListener('click', function () {
    window.scrollTo({ top: 0, behavior: 'smooth' });
  });
})();
