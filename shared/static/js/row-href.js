//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"use strict";

(function () {
    // Elements that should NOT trigger row navigation when clicked.
    // .arena-favorite-icon — star/bookmark icons used on the Arena problem list.
    // [data-no-row-link]   — explicit opt-out for any element inside a row.
    var INTERACTIVE = "a, button, input, select, textarea, label, .arena-favorite-icon, [data-no-row-link]";

    function _navigate(row, event) {
        var href = row.getAttribute("data-href");
        if (!href) return;
        if (event.ctrlKey || event.metaKey || event.shiftKey || event.button === 1) {
            window.open(href, "_blank", "noopener");
        } else {
            window.location.href = href;
        }
    }

    function _onClick(event) {
        var row = event.target.closest("tr[data-href]");
        if (!row) return;
        if (event.target.closest(INTERACTIVE)) return;
        event.preventDefault();
        _navigate(row, event);
    }

    function _onKeydown(event) {
        if (event.key !== "Enter" && event.key !== " ") return;
        var row = event.target.closest("tr[data-href]");
        if (!row) return;
        if (event.target.closest(INTERACTIVE)) return;
        event.preventDefault();
        _navigate(row, event);
    }

    function decorate(root) {
        (root || document).querySelectorAll("tr[data-href]").forEach(function (row) {
            row.setAttribute("tabindex", "0");
            row.setAttribute("role", "link");
            row.classList.add("noca-clickable-row");
        });
    }

    function init() {
        decorate(document);
        document.addEventListener("click", _onClick);
        document.addEventListener("keydown", _onKeydown);
        // Rows swapped in by HTMX need the same accessibility attributes.
        document.body.addEventListener("htmx:afterSwap", function (event) {
            decorate(event.target);
        });
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
}());
