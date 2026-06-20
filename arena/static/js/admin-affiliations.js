/*
 * NOCA -- Next Online Contest Administrator
 * Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 */

/**
 * Admin affiliation list page JS.
 *
 * Responsibilities:
 *   1. Bootstrap tooltip init.
 *   2. Country → subdivision dynamic update for the filter form and both modals.
 *   3. New-affiliation modal: reset fields on open.
 *   4. Edit-affiliation modal: preload row data on open.
 *   5. Delete-affiliation modal: preload name on open.
 *
 * Logo management is handled separately by admin-affiliations-logo.js.
 */

(() => {
    "use strict";

    const SUBDIVISIONS_URL = "/user/profile/subdivisions";

    // ── Utilities ─────────────────────────────────────────────────────────────

    /**
     * Fetch subdivisions for a country and populate a <select> element.
     *
     * @param {string} countryCode - ISO 3166-1 alpha-2 code.
     * @param {HTMLSelectElement} selectEl - Subdivision select to populate.
     * @param {string} [selectedValue=""] - Pre-selected subdivision code.
     */
    async function loadSubdivisions(countryCode, selectEl, selectedValue = "") {
        selectEl.innerHTML = '<option value="">— Subdivision —</option>';
        if (!countryCode) {
            selectEl.disabled = true;
            return;
        }
        try {
            const res = await fetch(`${SUBDIVISIONS_URL}?country_code=${encodeURIComponent(countryCode)}`);
            if (!res.ok) throw new Error("fetch failed");
            const data = await res.json();
            const subdivisions = data.subdivisions || [];
            subdivisions.forEach((s) => {
                const opt = document.createElement("option");
                opt.value = s.code;
                opt.textContent = s.name;
                if (s.code === selectedValue) opt.selected = true;
                selectEl.appendChild(opt);
            });
            selectEl.disabled = subdivisions.length === 0;
        } catch {
            selectEl.disabled = true;
        }
    }

    /**
     * Wire country → subdivision live-update for a form scope.
     *
     * @param {HTMLElement} scope         - Form or modal element containing the selects.
     * @param {string}      countrySelId  - ID of the country <select>.
     * @param {string}      subdivSelId   - ID of the subdivision <select>.
     * @param {string}      [initialSub]  - Pre-selected subdivision value on load.
     */
    function wireCountrySubdivision(scope, countrySelId, subdivSelId, initialSub = "") {
        const countrySelect = scope.querySelector(`#${countrySelId}`) || document.getElementById(countrySelId);
        const subdivSelect = scope.querySelector(`#${subdivSelId}`) || document.getElementById(subdivSelId);
        if (!countrySelect || !subdivSelect) return;

        countrySelect.addEventListener("change", () => {
            loadSubdivisions(countrySelect.value, subdivSelect);
        });

        // Load subdivisions for the current country value immediately.
        if (countrySelect.value) {
            loadSubdivisions(countrySelect.value, subdivSelect, initialSub);
        } else {
            subdivSelect.disabled = true;
        }
    }

    // ── Bootstrap tooltips ────────────────────────────────────────────────────
    document.querySelectorAll("[data-bs-toggle='tooltip']").forEach((el) => {
        try {
            new bootstrap.Tooltip(el);
        } catch {
            el.setAttribute("data-bs-toggle", "native-tooltip");
        }
    });

    // ── Filter form: country → subdivision ───────────────────────────────────
    wireCountrySubdivision(
        document,
        "filter-country-code",
        "filter-subdivision-code",
    );
    // Auto-submit filter when country changes.
    const filterCountry = document.getElementById("filter-country-code");
    const filterSubdiv = document.getElementById("filter-subdivision-code");
    const filterForm = document.getElementById("affiliation-filter-form");
    if (filterCountry && filterForm) {
        filterCountry.addEventListener("change", () => filterForm.submit());
    }
    if (filterSubdiv && filterForm) {
        filterSubdiv.addEventListener("change", () => filterForm.submit());
    }

    // ── New affiliation modal ─────────────────────────────────────────────────
    const newModalEl = document.getElementById("affiliation-new-modal");
    if (newModalEl) {
        const newSubdivSelect = newModalEl.querySelector("#new-subdivision-code");

        newModalEl.addEventListener("show.bs.modal", () => {
            newModalEl.querySelectorAll("input[type='text'], input[type='url']").forEach((el) => {
                el.value = "";
            });
            const countrySelect = newModalEl.querySelector("#new-country-code");
            if (countrySelect) countrySelect.value = "";
            if (newSubdivSelect) {
                newSubdivSelect.innerHTML = '<option value="">— Subdivision —</option>';
                newSubdivSelect.disabled = true;
            }
        });

        newModalEl.addEventListener("shown.bs.modal", () => {
            newModalEl.querySelector("input[type='text']")?.focus();
        });

        wireCountrySubdivision(newModalEl, "new-country-code", "new-subdivision-code");
    }

    // ── Edit affiliation modal ────────────────────────────────────────────────
    const editModalEl = document.getElementById("affiliation-edit-modal");
    if (editModalEl) {
        const editForm = editModalEl.querySelector("[data-affiliation-edit-form]");

        document.querySelectorAll("[data-affiliation-edit-button]").forEach((button) => {
            button.addEventListener("click", () => {
                const action = button.getAttribute("data-action") || "";
                const name = button.getAttribute("data-name") || "";
                const url = button.getAttribute("data-url") || "";
                const country = button.getAttribute("data-country") || "";
                const subdiv = button.getAttribute("data-subdiv") || "";
                const excludeFromRanking = button.getAttribute("data-exclude-from-ranking") === "true";

                if (editForm) editForm.setAttribute("action", action);

                const nameInput = editModalEl.querySelector("#edit-name");
                const urlInput = editModalEl.querySelector("#edit-url");
                const countrySelect = editModalEl.querySelector("#edit-country-code");
                const excludeCheckbox = editModalEl.querySelector("#edit-exclude-from-ranking");

                if (nameInput) nameInput.value = name;
                if (urlInput) urlInput.value = url;
                if (countrySelect) countrySelect.value = country;
                if (excludeCheckbox) excludeCheckbox.checked = excludeFromRanking;

                // Load subdivisions for the current country
                const subdivSelect = editModalEl.querySelector("#edit-subdivision-code");
                if (subdivSelect) {
                    subdivSelect.innerHTML = '<option value="">— Subdivision —</option>';
                    if (country) {
                        loadSubdivisions(country, subdivSelect, subdiv);
                    } else {
                        subdivSelect.disabled = true;
                    }
                }

                bootstrap.Modal.getOrCreateInstance(editModalEl).show();
            });
        });

        // Country → subdivision in edit modal
        const editCountrySelect = editModalEl.querySelector("#edit-country-code");
        const editSubdivSelect = editModalEl.querySelector("#edit-subdivision-code");
        if (editCountrySelect && editSubdivSelect) {
            editCountrySelect.addEventListener("change", () => {
                loadSubdivisions(editCountrySelect.value, editSubdivSelect);
            });
        }
    }

    // ── Delete affiliation modal ──────────────────────────────────────────────
    const deleteModalEl = document.getElementById("affiliation-delete-modal");
    if (deleteModalEl) {
        const deleteForm = deleteModalEl.querySelector("[data-affiliation-delete-form]");
        const nameEl = deleteModalEl.querySelector("[data-affiliation-delete-name]");

        document.querySelectorAll("[data-affiliation-delete-button]").forEach((button) => {
            button.addEventListener("click", () => {
                const action = button.getAttribute("data-action") || "";
                const name = button.getAttribute("data-name") || "this affiliation";

                if (deleteForm) deleteForm.setAttribute("action", action);
                if (nameEl) nameEl.textContent = name;

                bootstrap.Modal.getOrCreateInstance(deleteModalEl).show();
            });
        });
    }
})();
