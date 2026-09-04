//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

// One password-confirmation modal shared by every rejudge button on the
// limit-change batch page. A click points the modal's form at the button's
// action and names the scope it covers; the password field itself belongs to
// the form through its `form=` attribute.

"use strict";

const rejudgeModalEl = document.getElementById("limit-rejudge-modal");
const rejudgeForm = rejudgeModalEl?.querySelector("[data-limit-rejudge-form]");
const rejudgeScopeEl = rejudgeModalEl?.querySelector("[data-limit-rejudge-scope]");
const rejudgePasswordEl = rejudgeModalEl?.querySelector("input[name='password']");

document.querySelectorAll("[data-limit-rejudge-button]").forEach((btn) => {
    btn.addEventListener("click", () => {
        rejudgeForm?.setAttribute("action", btn.dataset.action || "");
        if (rejudgeScopeEl) {
            rejudgeScopeEl.textContent = btn.dataset.scope || "";
        }
        if (rejudgePasswordEl) {
            rejudgePasswordEl.value = "";
        }
        bootstrap.Modal.getOrCreateInstance(rejudgeModalEl).show();
    });
});

rejudgeModalEl?.addEventListener("shown.bs.modal", () => {
    rejudgePasswordEl?.focus();
});
