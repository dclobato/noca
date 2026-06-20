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

  // Stop words removed before building a slug so that prepositions and articles
  // do not inflate URL length.  Mirrors the Python set in admin_category_service.py.
  const SLUG_STOP_WORDS = new Set([
    // Portuguese articles
    "a", "o", "as", "os", "um", "uma",
    // Portuguese prepositions & contractions
    "de", "do", "da", "dos", "das",
    "em", "no", "na", "nos", "nas",
    "por", "para", "com",
    "pelo", "pela", "pelos", "pelas",
    // Portuguese conjunctions / pronouns
    "e", "ou", "se",
    // English articles / prepositions / conjunctions
    "the", "an", "and", "or",
    "of", "in", "on", "for", "to", "from", "with", "by", "at",
    // English copula
    "is", "are",
  ]);

  const slugify = (value) => {
    const normalized = value
      .toLowerCase()
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "");
    const words = normalized
      .replace(/[^a-z0-9]+/g, " ")
      .trim()
      .split(" ")
      .filter((w) => w && !SLUG_STOP_WORDS.has(w));
    return words.join("-");
  };

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
