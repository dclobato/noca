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
