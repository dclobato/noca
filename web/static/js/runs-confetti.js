// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

// Runs-page confetti: listens for `runs:accepted-visible` and fires a celebration
// via the shared NocaConfetti burst (see shared/static/js/confetti-celebrate.js).
// This file owns only the runs-specific event wiring and dedup key.

(function () {
  if (typeof window.NocaConfetti === 'undefined') return;

  // Backwards-compatible alias retained for manual testing from the console.
  window.testRunsConfetti = function (options) {
    window.NocaConfetti.burst(options);
  };

  document.addEventListener('runs:accepted-visible', function (evt) {
    var d = evt.detail;
    if (!d || !d.teamId || !d.problemId || !d.verdict) return;

    var key = d.submissionId || (d.teamId + ':' + d.problemId + ':' + d.verdict);
    window.NocaConfetti.celebrate(key);
  });
})();
