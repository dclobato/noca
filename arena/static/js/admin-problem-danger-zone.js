//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"use strict";

const deleteModalEl = document.getElementById("problem-delete-modal");
const deleteForm = deleteModalEl?.querySelector("[data-problem-delete-form]");
const deleteNumberEl = deleteModalEl?.querySelector("[data-problem-delete-number]");

document.querySelectorAll("[data-problem-delete-button]").forEach((btn) => {
    btn.addEventListener("click", () => {
        deleteForm?.setAttribute("action", btn.dataset.action || "");
        if (deleteNumberEl) {
            deleteNumberEl.textContent = `#${btn.dataset.problemNumber}`;
        }
        bootstrap.Modal.getOrCreateInstance(deleteModalEl).show();
    });
});

const rejudgeModalEl = document.getElementById("problem-rejudge-modal");
const rejudgeForm = rejudgeModalEl?.querySelector("[data-problem-rejudge-form]");
const rejudgeNumberEl = rejudgeModalEl?.querySelector("[data-problem-rejudge-number]");

document.querySelectorAll("[data-problem-rejudge-button]").forEach((btn) => {
    btn.addEventListener("click", () => {
        rejudgeForm?.setAttribute("action", btn.dataset.action || "");
        if (rejudgeNumberEl) {
            rejudgeNumberEl.textContent = `#${btn.dataset.problemNumber}`;
        }
        bootstrap.Modal.getOrCreateInstance(rejudgeModalEl).show();
    });
});
