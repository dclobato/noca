//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

/**
 * Initializes flatpickr date/datetime pickers via data attributes.
 *
 * Supported attributes on <input> elements:
 *   data-fp-date          - date-only picker (YYYY-MM-DD)
 *   data-fp-datetime      - datetime picker (YYYY-MM-DD HH:MM displayed, YYYY-MM-DDTHH:MM submitted)
 *   data-fp-range-end     - CSS selector for the end input; enables the range plugin on this (start) input
 *   data-fp-min-date      - value passed to flatpickr minDate (e.g. "today" or an ISO date string)
 *   data-fp-modal         - set to "true" when the input is inside a Bootstrap modal (uses appendTo:body)
 *
 * Inputs with the `disabled` attribute are skipped entirely.
 * Inputs marked as range-end targets are also skipped (the range plugin manages them).
 */
(function () {
    "use strict";

    const rangeEndSelectors = new Set();

    function buildOptions(el) {
        const isDatetime = el.hasAttribute("data-fp-datetime");
        const rangeEndSelector = el.dataset.fpRangeEnd;
        const minDate = el.dataset.fpMinDate;
        const inModal = el.dataset.fpModal === "true";

        const opts = {
            allowInput: true,
            disableMobile: true,
        };

        if (isDatetime) {
            opts.enableTime = true;
            opts.dateFormat = "Y-m-dTH:i";
            opts.altInput = true;
            opts.altFormat = "Y-m-d H:i";
            opts.time_24hr = true;
        } else {
            opts.dateFormat = "Y-m-d";
        }

        if (minDate) {
            opts.minDate = minDate;
        }

        if (inModal) {
            opts.appendTo = document.body;
        }

        if (rangeEndSelector) {
            const endEl = document.querySelector(rangeEndSelector);
            if (endEl && !endEl.disabled) {
                rangeEndSelectors.add(rangeEndSelector);
                opts.plugins = [new rangePlugin({ input: endEl })];
            }
        }

        return opts;
    }

    document.addEventListener("DOMContentLoaded", function () {
        const dateEls = document.querySelectorAll("[data-fp-date], [data-fp-datetime]");
        dateEls.forEach(function (el) {
            if (el.disabled) return;

            const selector = "#" + el.id;
            if (rangeEndSelectors.has(selector)) return;

            flatpickr(el, buildOptions(el));
        });
    });
})();
