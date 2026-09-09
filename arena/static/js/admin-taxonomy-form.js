/*
 * NOCA -- Next Online Contest Administrator
 * Copyright (c) 2026 The NOCA Authors (see AUTHORS)
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 */

/*
 * The standalone create/edit form for Arena's flat taxonomies -- categories and
 * collections.  Both forms are identical, so the fields are found by data
 * attribute rather than by a taxonomy-specific id.
 *
 * Requires shared/static/js/slugify.js and arena/static/js/taxonomy-slug.js.
 */
(() => {
  "use strict";

  const nameInput = document.querySelector("[data-taxonomy-name-input]");
  const slugInput = document.querySelector("[data-taxonomy-slug-input]");
  const colorInput = document.querySelector("[data-taxonomy-color-input]");
  const colorText = colorInput ? colorInput.parentElement.querySelector(".arena-monospace") : null;

  // Slug preview comes from taxonomy-slug.js, shared with the list modals, so
  // both agree with the server's normalize_slug().
  const slugify = (value) => window.NocaTaxonomySlug?.slugify(value) ?? value;

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
