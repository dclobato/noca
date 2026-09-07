/*
 *  NOCA -- Next Online Contest Administrator
 *  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
 *  This program is distributed in the hope that it will be useful,
 *  but WITHOUT ANY WARRANTY; without even the implied warranty of
 *  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 *
 *  Keeps the Auto-Limit timing note in step with the language selector.
 *
 *  The per-run ceiling is the same for every language; the ceiling for all runs
 *  of one test case is that times the language's repetition count, so it moves
 *  when the selection does. The server renders both for the initially selected
 *  language, so the note is already correct without this script -- it only stops
 *  the second number going stale after a change.
 *
 *  Listens on `document` rather than on the select itself: the whole panel is
 *  swapped out by HTMX while a profiling run is active, which would discard a
 *  listener bound to an element.
 */
(function () {
    "use strict";

    var NOTE_ID = "profiling-timing-note";
    var SELECT_ID = "profiling_language_id";

    /* Trailing zeros read as false precision on a value like "10.0 s". */
    function formatSeconds(value) {
        return String(Number(value.toFixed(3)));
    }

    function update() {
        var note = document.getElementById(NOTE_ID);
        var select = document.getElementById(SELECT_ID);
        if (!note || !select) {
            return;
        }

        var cap = parseFloat(note.getAttribute("data-profiling-cap-seconds"));
        var option = select.options[select.selectedIndex];
        if (!option || !isFinite(cap)) {
            return;
        }

        var repetitions = parseInt(option.getAttribute("data-repetitions"), 10);
        if (!isFinite(repetitions) || repetitions < 1) {
            repetitions = 1;
        }

        var singleRun = note.querySelector("[data-profiling-single-run]");
        var repetitionCount = note.querySelector("[data-profiling-repetitions]");
        var total = note.querySelector("[data-profiling-total]");

        if (singleRun) {
            singleRun.textContent = formatSeconds(cap) + " s";
        }
        if (repetitionCount) {
            repetitionCount.textContent = String(repetitions);
        }
        if (total) {
            total.textContent = formatSeconds(cap * repetitions) + " s";
        }
    }

    document.addEventListener("change", function (event) {
        if (event.target && event.target.id === SELECT_ID) {
            update();
        }
    });

    document.addEventListener("DOMContentLoaded", update);
    /* An HTMX swap replaces the panel with server-rendered markup that is already
       correct, but re-running costs nothing and covers a swap that kept an older
       selection. */
    document.body.addEventListener("htmx:afterSwap", update);
})();
