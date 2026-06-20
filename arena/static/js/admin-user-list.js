/*
 * NOCA -- Next Online Contest Administrator
 * Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 */

(() => {
  "use strict";

  // Bootstrap tooltip init for all tooltip-enabled buttons.
  document.querySelectorAll("[data-bs-toggle='tooltip']").forEach((el) => {
    try {
      new bootstrap.Tooltip(el);
    } catch {
      el.setAttribute("data-bs-toggle", "native-tooltip");
    }
  });

})();
