/*
 *  NOCA -- Next Online Contest Administrator
 *  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
 *  This program is distributed in the hope that it will be useful,
 *  but WITHOUT ANY WARRANTY; without even the implied warranty of
 *  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 */

(function () {
    "use strict";

    var button = document.getElementById("copy-codes-btn");
    if (!button) return;

    var label = button.querySelector("[data-copy-label]");
    var status = document.querySelector("[data-copy-status]");

    function showResult(message, successful) {
        if (label) {
            label.textContent = message;
        }
        if (status) {
            status.textContent = successful
                ? "Recovery codes copied to your clipboard."
                : "Copy failed. Select and save the codes manually.";
        }
        button.disabled = true;

        setTimeout(function () {
            if (label) {
                label.textContent = "Copy all codes";
            }
            button.disabled = false;
        }, 2000);
    }

    button.addEventListener("click", function () {
        var cells = document.querySelectorAll("#backup-codes-grid .arena-backup-code-cell");
        var codes = Array.from(cells).map(function (el) {
            return el.textContent.trim();
        });
        var text = codes.join("\n");

        button.disabled = true;
        if (label) {
            label.textContent = "Copying...";
        }
        if (status) {
            status.textContent = "";
        }

        window.NocaClipboard.copyText(text).then(function () {
            showResult("Copied", true);
        }).catch(function () {
            showResult("Try copying again", false);
        });
    });
})();
