//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

// Landing page behavior: theme switching and the one authored entrance.
// The page is fully readable and navigable with this file blocked.
(function landingPage() {
    "use strict";

    var STORAGE_KEY = "noca-landing-theme";
    var root = document.documentElement;

    /**
     * Read the theme the visitor explicitly chose, if any.
     *
     * @returns {string|null} "light", "dark", or null when following the system.
     */
    function storedTheme() {
        try {
            var value = window.localStorage.getItem(STORAGE_KEY);
            return value === "light" || value === "dark" ? value : null;
        } catch (error) {
            return null;
        }
    }

    /**
     * Report the theme currently painted, whether chosen or inherited.
     *
     * @returns {string} "light" or "dark".
     */
    function activeTheme() {
        var chosen = root.getAttribute("data-theme");

        if (chosen === "light" || chosen === "dark") {
            return chosen;
        }

        return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
    }

    /**
     * Paint a theme and remember it for the next visit.
     *
     * @param {string} theme Either "light" or "dark".
     * @param {HTMLElement} toggle The control whose label and state follow the theme.
     */
    function applyTheme(theme, toggle) {
        root.setAttribute("data-theme", theme);

        try {
            window.localStorage.setItem(STORAGE_KEY, theme);
        } catch (error) {
            // A blocked storage quota must not break the toggle itself.
        }

        var goingTo = theme === "dark" ? "light" : "dark";
        toggle.setAttribute("aria-pressed", theme === "dark" ? "true" : "false");
        toggle.setAttribute("aria-label", "Switch to " + goingTo + " theme");
        toggle.querySelector(".theme-toggle__label").textContent = goingTo === "dark" ? "Dark" : "Light";
    }

    /**
     * Wire the header theme control.
     */
    function setUpThemeToggle() {
        var toggle = document.querySelector("[data-theme-toggle]");

        if (!toggle) {
            return;
        }

        toggle.hidden = false;
        applyTheme(storedTheme() || activeTheme(), toggle);

        toggle.addEventListener("click", function onToggle() {
            applyTheme(activeTheme() === "dark" ? "light" : "dark", toggle);
        });
    }

    /**
     * Raise the instance tiles out of the hero, once, on load.
     *
     * The tiles sit at the top of the page and are the reason to be here, so the
     * entrance is tied to load rather than to scrolling into view: nothing may
     * depend on an observer firing to become visible. Visitors who ask for
     * reduced motion never see it.
     */
    function setUpEntrance() {
        var tiles = document.querySelectorAll("[data-entrance]");

        if (!tiles.length || window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
            return;
        }

        root.classList.add("has-entrance");

        window.requestAnimationFrame(function afterLayout() {
            window.requestAnimationFrame(function play() {
                tiles.forEach(function reveal(tile) {
                    tile.classList.add("is-entered");
                });
            });
        });
    }

    setUpThemeToggle();
    setUpEntrance();
})();
