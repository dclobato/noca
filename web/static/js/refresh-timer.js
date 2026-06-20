// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

(function () {
  var timerEl = document.getElementById('refresh-timer');
  if (!timerEl) return;

  var wrapperId = timerEl.getAttribute('data-htmx-wrapper');
  var seconds = 60;

  setInterval(function () {
    seconds -= 1;
    timerEl.textContent = seconds;
    if (seconds <= 0) seconds = 60;
  }, 1000);

  if (wrapperId) {
    document.addEventListener('htmx:afterSwap', function (evt) {
      if (evt.detail && evt.detail.target && evt.detail.target.id === wrapperId) {
        seconds = 60;
        timerEl.textContent = seconds;
      }
    });
  }
})();
