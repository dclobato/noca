/*
 * NOCA -- Next Online Contest Administrator
 * Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 */

(() => {
  "use strict";

  const nameInput = document.getElementById("category_name");
  const slugInput = document.getElementById("category_slug");
  const colorInput = document.getElementById("category_color");
  const colorText = colorInput ? colorInput.parentElement.querySelector(".arena-monospace") : null;

  // Slug preview comes from category-slug.js, shared with the category list
  // modals, so both agree with the server's normalize_slug().
  const slugify = (value) => window.NocaCategorySlug?.slugify(value) ?? value;

  if (nameInput && slugInput) {
    nameInput.addEventListener("input", () => {
      if (slugInput.dataset.userEdited) return;
      slugInput.value = slugify(nameInput.value);
    });

    slugInput.addEventListener("input", () => {
      slugInput.dataset.userEdited = "1";
      slugInput.value = slugify(slugInput.value);
    });
  }

  if (colorInput && colorText) {
    colorInput.addEventListener("input", () => {
      colorText.textContent = colorInput.value;
    });
  }
})();
