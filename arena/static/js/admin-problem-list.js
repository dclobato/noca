/*
 * NOCA -- Next Online Contest Administrator
 * Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 */

(() => {
  "use strict";

  // Initialise Bootstrap tooltips
  document.querySelectorAll("[data-bs-toggle='tooltip']").forEach((el) => {
    try {
      new bootstrap.Tooltip(el);
    } catch {
      el.setAttribute("data-bs-toggle", "native-tooltip");
    }
  });

  const modalEl = document.getElementById("problem-toggle-modal");
  if (!modalEl || typeof bootstrap === "undefined") return;

  const modal = new bootstrap.Modal(modalEl);
  const form = modalEl.querySelector("[data-problem-toggle-form]");
  const titleEl = modalEl.querySelector("[data-problem-toggle-modal-title]");
  const bodyEl = modalEl.querySelector("[data-problem-toggle-modal-body]");
  const submitBtn = modalEl.querySelector("[data-problem-toggle-submit]");

  document.querySelectorAll("[data-problem-toggle-button]").forEach((button) => {
    button.addEventListener("click", () => {
      const number = button.getAttribute("data-problem-number") || "?";
      const title = button.getAttribute("data-problem-title") || "this problem";
      const enabled = button.getAttribute("data-problem-enabled") === "true";
      const action = button.getAttribute("data-problem-action") || "";

      if (form) form.setAttribute("action", action);
      if (titleEl) titleEl.textContent = enabled ? "Hide problem" : "Publish problem";
      if (bodyEl) {
        bodyEl.textContent = enabled
          ? `Hide problem #${number} "${title}"? It will no longer be visible to contestants.`
          : `Publish problem #${number} "${title}"? It will become visible to contestants.`;
      }
      if (submitBtn) {
        submitBtn.textContent = enabled ? "Hide" : "Publish";
        submitBtn.className = enabled ? "btn btn-danger" : "btn btn-success";
      }

      modal.show();
    });
  });

  // ── Category filter: counter badge + in-dropdown search ─────────────────────

  const catFilterList = document.getElementById("cat-filter-list");
  const catFilterSearch = document.getElementById("cat-filter-search");
  const catFilterCount = document.getElementById("cat-filter-count");

  function updateCatCount() {
    if (!catFilterList || !catFilterCount) return;
    const checked = catFilterList.querySelectorAll("input[type='checkbox']:checked").length;
    catFilterCount.textContent = String(checked);
    if (checked > 0) {
      catFilterCount.classList.remove("d-none");
    } else {
      catFilterCount.classList.add("d-none");
    }
  }

  if (catFilterList) {
    catFilterList.addEventListener("change", updateCatCount);
  }

  if (catFilterSearch && catFilterList) {
    catFilterSearch.addEventListener("input", () => {
      const q = catFilterSearch.value.trim().toLowerCase();
      catFilterList.querySelectorAll(".arena-cat-filter-item").forEach((item) => {
        const text = item.textContent.trim().toLowerCase();
        item.classList.toggle("d-none", q !== "" && !text.includes(q));
      });
    });
  }
})();
