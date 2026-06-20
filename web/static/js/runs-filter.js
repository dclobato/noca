// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

/**
 * Client-side row filtering for the runs list table.
 *
 * Reads filter <select> elements inside #runs-filter-form and toggles the
 * visibility of <tr> rows in the runs table based on their data-* attributes.
 * Runs on initial load and re-runs after every HTMX swap so that active filters
 * are preserved across auto-refreshes.
 */

/**
 * Converts a kebab-case string to camelCase for dataset property access.
 *
 * @param {string} str - kebab-case string (e.g. "problem-id")
 * @returns {string} camelCase string (e.g. "problemId")
 */
function _kebabToCamel(str) {
    return str.replace(/-([a-z])/g, (_, c) => c.toUpperCase());
}

/**
 * Applies the current filter selections to the table rows.
 * Rows whose data attributes do not match all active (non-empty) filter values
 * are hidden via the Bootstrap d-none class.
 */
function applyRunsFilters() {
    const form = document.getElementById("runs-filter-form");
    if (!form) return;

    const selects = form.querySelectorAll("select[data-filter-attr]");
    const tbody = document.querySelector("#runs-list-wrapper table tbody");
    if (!tbody) return;

    tbody.querySelectorAll("tr").forEach(function (row) {
        let visible = true;
        selects.forEach(function (sel) {
            const val = sel.value;
            if (val === "") return;
            const key = _kebabToCamel(sel.dataset.filterAttr);
            if (row.dataset[key] !== val) {
                visible = false;
            }
        });
        row.classList.toggle("d-none", !visible);
    });
}

document.addEventListener("DOMContentLoaded", applyRunsFilters);
document.addEventListener("htmx:afterSwap", applyRunsFilters);
document.addEventListener("change", function (e) {
    if (e.target.closest("#runs-filter-form")) {
        applyRunsFilters();
    }
});
