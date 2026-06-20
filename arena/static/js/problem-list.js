/*
 * NOCA -- Next Online Contest Administrator
 * Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 */

/**
 * Public problem list interactions.
 *
 * Handles the category multi-select dropdown and category color swatches.
 */

(() => {
  "use strict";

  const catFilterList   = document.getElementById("cat-filter-list");
  const catFilterSearch = document.getElementById("cat-filter-search");
  const catFilterCount  = document.getElementById("cat-filter-count");

  document.querySelectorAll("[data-category-color]").forEach((el) => {
    const color = el.getAttribute("data-category-color") || "#6c757d";
    el.style.backgroundColor = color;
  });

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
        const label = item.textContent.trim().toLowerCase();
        item.classList.toggle("d-none", q !== "" && !label.includes(q));
      });
    });
  }
})();
