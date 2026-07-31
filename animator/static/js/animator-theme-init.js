//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

// Early theme initializer. Loaded synchronously in <head> before the
// stylesheets so the stored theme is applied with no light flash. The shared
// theme-toggle.js runs at DOMContentLoaded and cannot replace this earlier pass.
(function () {
  "use strict";
  var theme = localStorage.getItem("noca-theme") || "light";
  document.documentElement.setAttribute("data-bs-theme", theme);
})();
