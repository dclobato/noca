/*
 * NOCA -- Next Online Contest Administrator
 * Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 */

(() => {
  "use strict";

  if (typeof bootstrap === "undefined") return;

  // ── Bootstrap tooltips ────────────────────────────────────────────────────
  document.querySelectorAll("[data-bs-toggle='tooltip']").forEach((el) => {
    try {
      new bootstrap.Tooltip(el);
    } catch {
      el.setAttribute("data-bs-toggle", "native-tooltip");
    }
  });

  // ── Category color swatches ───────────────────────────────────────────────
  document.querySelectorAll("[data-category-color]").forEach((el) => {
    const color = el.getAttribute("data-category-color") || "#6c757d";
    el.style.backgroundColor = color;
  });

  // ── Shared utilities ──────────────────────────────────────────────────────
  const slugify = (value) =>
    value
      .toLowerCase()
      .normalize("NFD")
      .replace(/[̀-ͯ]/g, "")
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "");

  function randomCategoryColor() {
    const hueToRgb = (p, q, t) => {
      let tt = t;
      if (tt < 0) tt += 1;
      if (tt > 1) tt -= 1;
      if (tt < 1 / 6) return p + (q - p) * 6 * tt;
      if (tt < 1 / 2) return q;
      if (tt < 2 / 3) return p + (q - p) * (2 / 3 - tt) * 6;
      return p;
    };
    const h = Math.random();
    const s = 0.55 + Math.random() * 0.2;
    const l = 0.4 + Math.random() * 0.18;
    const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
    const p = 2 * l - q;
    const r = hueToRgb(p, q, h + 1 / 3);
    const g = hueToRgb(p, q, h);
    const b = hueToRgb(p, q, h - 1 / 3);
    return "#" + [r, g, b].map((x) => Math.round(x * 255).toString(16).padStart(2, "0")).join("");
  }

  function applyColor(colorInput, colorText, color) {
    if (colorInput) colorInput.value = color;
    if (colorText) colorText.textContent = color;
  }

  // ── Delete category modal ─────────────────────────────────────────────────
  const deleteModalEl = document.getElementById("category-delete-modal");
  if (deleteModalEl) {
    const deleteModal = new bootstrap.Modal(deleteModalEl);
    const deleteForm = deleteModalEl.querySelector("[data-category-delete-form]");
    const nameEl = deleteModalEl.querySelector("[data-category-delete-name]");
    const countEl = deleteModalEl.querySelector("[data-category-delete-count]");
    const pageInput = deleteForm ? deleteForm.querySelector("input[name='page']") : null;
    const perPageInput = deleteForm ? deleteForm.querySelector("input[name='per_page']") : null;

    document.querySelectorAll("[data-category-delete-button]").forEach((button) => {
      button.addEventListener("click", () => {
        const name = button.getAttribute("data-category-name") || "this category";
        const count = Number(button.getAttribute("data-category-count") || "0");
        const action = button.getAttribute("data-category-action") || "";

        if (deleteForm) deleteForm.setAttribute("action", action);
        if (nameEl) nameEl.textContent = name;
        if (countEl) {
          countEl.textContent =
            count === 1
              ? "Its link to 1 problem will be removed. The problem remains available."
              : `Its links to ${count} problems will be removed. The problems remain available.`;
        }
        if (pageInput) pageInput.value = button.getAttribute("data-category-page") || "1";
        if (perPageInput) perPageInput.value = button.getAttribute("data-category-per-page") || "25";

        deleteModal.show();
      });
    });
  }

  // ── New category modal ────────────────────────────────────────────────────
  const newModalEl = document.getElementById("category-new-modal");
  if (newModalEl) {
    const nameInput = newModalEl.querySelector("#new_category_name");
    const slugInput = newModalEl.querySelector("#new_category_slug");
    const colorInput = newModalEl.querySelector("#new_category_color");
    const colorText = newModalEl.querySelector("#new_category_color_text");
    const randomBtn = newModalEl.querySelector("#new_category_color_random");

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

    if (randomBtn) {
      randomBtn.addEventListener("click", () => applyColor(colorInput, colorText, randomCategoryColor()));
    }

    // Reset fields and seed a fresh random color each time the modal opens.
    newModalEl.addEventListener("show.bs.modal", () => {
      if (nameInput) nameInput.value = "";
      if (slugInput) {
        slugInput.value = "";
        delete slugInput.dataset.userEdited;
      }
      applyColor(colorInput, colorText, randomCategoryColor());
    });

    newModalEl.addEventListener("shown.bs.modal", () => nameInput?.focus());
  }

  // ── Edit category modal ───────────────────────────────────────────────────
  const editModalEl = document.getElementById("category-edit-modal");
  if (editModalEl) {
    const editModal = new bootstrap.Modal(editModalEl);
    const editForm = editModalEl.querySelector("[data-category-edit-form]");
    const nameInput = editModalEl.querySelector("#edit_category_name");
    const slugInput = editModalEl.querySelector("#edit_category_slug");
    const colorInput = editModalEl.querySelector("#edit_category_color");
    const colorText = editModalEl.querySelector("#edit_category_color_text");
    const randomBtn = editModalEl.querySelector("#edit_category_color_random");

    if (colorInput && colorText) {
      colorInput.addEventListener("input", () => {
        colorText.textContent = colorInput.value;
      });
    }

    if (randomBtn) {
      randomBtn.addEventListener("click", () => applyColor(colorInput, colorText, randomCategoryColor()));
    }

    document.querySelectorAll("[data-category-edit-button]").forEach((button) => {
      button.addEventListener("click", () => {
        const name = button.getAttribute("data-category-name") || "";
        const slug = button.getAttribute("data-category-slug") || "";
        const color = button.getAttribute("data-category-edit-color") || "#6c757d";
        const action = button.getAttribute("data-category-action") || "";

        if (editForm) editForm.setAttribute("action", action);
        if (nameInput) nameInput.value = name;
        if (slugInput) slugInput.value = slug;
        applyColor(colorInput, colorText, color);

        editModal.show();
      });
    });

    editModalEl.addEventListener("shown.bs.modal", () => nameInput?.focus());
  }
})();
