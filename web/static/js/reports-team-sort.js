// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

/**
 * Client-side reordering for the "Runs by Team and Problem" report table.
 *
 * The table body (#team-problem-body) holds one <tr> per team, each carrying
 *   - data-total:    integer count of judged runs (busiest-first default)
 *   - data-ac-count: integer count of accepted runs
 *   - data-name:     lower-cased team display name
 *
 * Header buttons (.noca-team-sort) declare a data-sort-key of "name", "total"
 * or "ac". "name" sorts ascending A→Z; the numeric keys sort descending
 * (highest first). The "ac" (effectiveness) key ranks by the Wilson score
 * lower bound of the acceptance rate, so a small sample such as 1/1 does not
 * outrank a well-supported 40/50. The active header shows an arrow; the rest
 * show the idle unfold icon.
 */
document.addEventListener("DOMContentLoaded", function () {
    "use strict";

    var body = document.getElementById("team-problem-body");
    if (!body) return;

    var buttons = Array.prototype.slice.call(
        document.querySelectorAll(".noca-team-sort")
    );
    if (buttons.length === 0) return;

    // Wilson score lower bound (95%) of the acceptance rate. Regresses small
    // samples toward zero so effectiveness reflects sample size.
    function wilsonScore(accepted, total) {
        if (total <= 0) return 0;
        var z = 1.96;
        var p = accepted / total;
        var z2 = z * z;
        var denom = 1 + z2 / total;
        var center = p + z2 / (2 * total);
        var margin = z * Math.sqrt((p * (1 - p) + z2 / (4 * total)) / total);
        return (center - margin) / denom;
    }

    function scoreFor(key, row) {
        if (key === "ac") {
            return wilsonScore(
                parseFloat(row.dataset.acCount || "0"),
                parseFloat(row.dataset.total || "0")
            );
        }
        return parseFloat(row.dataset.total || "0");
    }

    function comparatorFor(key) {
        if (key === "name") {
            return function (a, b) {
                return (a.dataset.name || "").localeCompare(b.dataset.name || "");
            };
        }
        return function (a, b) {
            return scoreFor(key, b) - scoreFor(key, a);
        };
    }

    function setIcon(button, active) {
        var icon = button.querySelector(".noca-sort-icon");
        if (!icon) return;
        if (active) {
            icon.textContent =
                button.dataset.sortKey === "name" ? "arrow_upward" : "arrow_downward";
            icon.classList.remove("noca-sort-icon--idle");
            icon.classList.add("noca-sort-icon--active");
        } else {
            icon.textContent = "unfold_more";
            icon.classList.remove("noca-sort-icon--active");
            icon.classList.add("noca-sort-icon--idle");
        }
    }

    function sortBy(key) {
        var rows = Array.prototype.slice.call(body.querySelectorAll("tr"));
        rows.sort(comparatorFor(key));
        rows.forEach(function (row) {
            body.appendChild(row);
        });
        buttons.forEach(function (button) {
            setIcon(button, button.dataset.sortKey === key);
        });
    }

    buttons.forEach(function (button) {
        button.addEventListener("click", function () {
            sortBy(button.dataset.sortKey);
        });
    });
});
