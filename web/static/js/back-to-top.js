// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 The NOCA Authors (see AUTHORS)
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

/**
 * Back-to-top floating button: smooth-scrolls the window to the top.
 * Wires every `.noca-back-to-top-btn` element present on the page, so one
 * script serves every page that includes the button markup.
 */
document.querySelectorAll('.noca-back-to-top-btn').forEach(function (btn) {
  btn.addEventListener('click', function () {
    window.scrollTo({ top: 0, behavior: 'smooth' });
  });
});
