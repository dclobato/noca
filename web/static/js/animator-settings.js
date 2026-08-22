/*
 *  NOCA -- Next Online Contest Administrator
 *  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
 *  This program is distributed in the hope that it will be useful,
 *  but WITHOUT ANY WARRANTY; without even the implied warranty of
 *  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 */

(function () {
    "use strict";

    var accessForm = document.getElementById("animator-access-form");
    var enabledToggle = document.getElementById("animator_enabled");
    var disableModalElement = document.getElementById("animator-disable-confirm-modal");
    var confirmDisableButton = document.getElementById("confirm-animator-disable-btn");
    var disableModal = disableModalElement ? new bootstrap.Modal(disableModalElement) : null;
    var disableConfirmed = false;
    if (accessForm && enabledToggle && disableModal) {
        accessForm.addEventListener("submit", function (event) {
            if (!enabledToggle.checked && !disableConfirmed) {
                event.preventDefault();
                disableModal.show();
            }
        });
    }
    if (accessForm && confirmDisableButton && disableModal) {
        confirmDisableButton.addEventListener("click", function () {
            disableConfirmed = true;
            confirmDisableButton.disabled = true;
            disableModal.hide();
            accessForm.requestSubmit();
        });
    }
    if (disableModalElement && enabledToggle) {
        disableModalElement.addEventListener("hidden.bs.modal", function () {
            if (!disableConfirmed) enabledToggle.checked = true;
        });
    }

    document.addEventListener("click", function (event) {
        var target = event.target;
        if (!(target instanceof Element)) return;
        var btn = target.closest("#copy-secret-btn");
        if (!btn) return;
        var value = document.getElementById("new-secret-value");
        if (!value) return;
        var text = value.textContent.trim();
        window.NocaClipboard.copyText(text).then(function () {
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
