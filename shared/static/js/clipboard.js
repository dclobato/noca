//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

/**
 * Copy plain text through the modern Clipboard API or an HTTP-compatible fallback.
 *
 * Exposes `window.NocaClipboard.copyText(text)`, which always returns a Promise.
 */
(function (global) {
    "use strict";

    function copyWithSelection(text) {
        return new global.Promise(function (resolve, reject) {
            var copySource = global.document.createElement("textarea");
            var previousFocus = global.document.activeElement;
            copySource.value = text;
            copySource.className = "position-fixed opacity-0";
            copySource.setAttribute("aria-hidden", "true");
            copySource.setAttribute("readonly", "");
            copySource.setAttribute("tabindex", "-1");
            global.document.body.appendChild(copySource);
            copySource.focus();
            copySource.select();

            try {
                if (!global.document.execCommand("copy")) {
                    throw new Error("The browser rejected the copy command");
                }
                resolve();
            } catch (error) {
                reject(error);
            } finally {
                copySource.remove();
                if (previousFocus && typeof previousFocus.focus === "function") {
                    previousFocus.focus();
                }
            }
        });
    }

    function copyText(text) {
        var clipboard = global.navigator.clipboard;
        if (clipboard && typeof clipboard.writeText === "function") {
            return clipboard.writeText(text);
        }
        return copyWithSelection(text);
    }

    global.NocaClipboard = { copyText: copyText };
}(window));
