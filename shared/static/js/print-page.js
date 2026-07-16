// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

// Binds any [data-print-page] control to the browser's print dialog. Used by the
// standalone print-friendly problem pages in the web and arena modules.
document.addEventListener('DOMContentLoaded', function () {
  document.querySelectorAll('[data-print-page]').forEach(function (el) {
    el.addEventListener('click', function () {
      window.print();
    });
  });
});
