/*
 *  NOCA -- Next Online Contest Administrator
 *  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
 *  This program is distributed in the hope that it will be useful,
 *  but WITHOUT ANY WARRANTY; without even the implied warranty of
 *  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 */

(function () {
    "use strict";

    var btn = document.getElementById("copy-codes-btn");
    if (!btn) return;

    btn.addEventListener("click", function () {
        var cells = document.querySelectorAll("#backup-codes-grid .arena-backup-code-cell");
        var codes = Array.from(cells).map(function (el) {
            return el.textContent.trim();
        });
        var text = codes.join("\n");

        navigator.clipboard.writeText(text).then(function () {
            var original = btn.innerHTML;
            btn.textContent = "Copied!";
            btn.disabled = true;
            setTimeout(function () {
                btn.innerHTML = original;
                btn.disabled = false;
            }, 2000);
        }).catch(function () {
            btn.textContent = "Copy failed";
            setTimeout(function () {
                btn.textContent = "Copy all codes";
            }, 2000);
        });
    });
})();
