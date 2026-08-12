//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

// Applies the stored theme before first paint. Loaded synchronously in <head>
// so the page never flashes the wrong surface. Keep this file tiny.
(function applyStoredTheme() {
    "use strict";

    var stored = null;

    try {
        stored = window.localStorage.getItem("noca-landing-theme");
    } catch (error) {
        stored = null;
    }

    if (stored === "light" || stored === "dark") {
        document.documentElement.setAttribute("data-theme", stored);
    }
})();
