//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

// One password-confirmation modal shared by every Clear IP lock button on the
// enrolled-users page. During a running contest every team can be bound at
// once, so a modal per row would put hundreds of dialogs in the document; a
// click instead points this single modal's form at the button's action and
// names the team and address it covers. The password field belongs to the form
// through its `form=` attribute, exactly as the limit-batch rejudge modal does.

"use strict";

const clearLockModalEl = document.getElementById("clear-ip-lock-modal");
const clearLockForm = clearLockModalEl?.querySelector("[data-clear-ip-lock-form]");
const clearLockTeamEl = clearLockModalEl?.querySelector("[data-clear-ip-lock-team]");
const clearLockAddressEl = clearLockModalEl?.querySelector("[data-clear-ip-lock-address]");
const clearLockPasswordEl = clearLockModalEl?.querySelector("input[name='password']");

document.querySelectorAll("[data-clear-ip-lock-button]").forEach((btn) => {
    btn.addEventListener("click", () => {
        clearLockForm?.setAttribute("action", btn.dataset.action || "");
        if (clearLockTeamEl) {
            clearLockTeamEl.textContent = btn.dataset.team || "";
        }
        if (clearLockAddressEl) {
            clearLockAddressEl.textContent = btn.dataset.address || "";
        }
        if (clearLockPasswordEl) {
            clearLockPasswordEl.value = "";
        }
        bootstrap.Modal.getOrCreateInstance(clearLockModalEl).show();
    });
});

clearLockModalEl?.addEventListener("shown.bs.modal", () => {
    clearLockPasswordEl?.focus();
});
