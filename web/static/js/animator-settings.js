/*
 *  NOCA -- Next Online Contest Administrator
 *  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
 *  This program is distributed in the hope that it will be useful,
 *  but WITHOUT ANY WARRANTY; without even the implied warranty of
 *  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 */

(function () {
    "use strict";

    document.addEventListener("click", function (event) {
        var target = event.target;
        if (!(target instanceof Element)) return;
        var btn = target.closest("#copy-secret-btn");
        if (!btn) return;
        var value = document.getElementById("new-secret-value");
        if (!value) return;
        var text = value.textContent.trim();
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
                btn.textContent = "Copy";
            }, 2000);
        });
    });
})();
