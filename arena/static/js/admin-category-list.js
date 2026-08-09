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
  // Slug preview comes from category-slug.js, shared with the legacy category
  // form, so both agree with the server's normalize_slug().
  const slugify = (value) => window.NocaCategorySlug?.slugify(value) ?? value;

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

  const HEX_COLOR = /^#[0-9a-f]{6}$/;

  function applyColor(colorInput, hexInput, color) {
    if (colorInput) colorInput.value = color;
    if (hexInput) {
      hexInput.value = color;
      hexInput.classList.remove("is-invalid");
    }
  }

  /*
   * Wires one swatch + hex + dice group.  The hex field carries no `name`: it is
   * a two-way mirror of the `<input type="color">`, which is the field actually
   * submitted, so a half-typed hex can never reach the server.
   */
  function wireColorGroup(root, prefix) {
    const colorInput = root.querySelector(`#${prefix}_category_color`);
    const hexInput = root.querySelector(`#${prefix}_category_color_hex`);
    const randomBtn = root.querySelector(`#${prefix}_category_color_random`);

    if (colorInput && hexInput) {
      colorInput.addEventListener("input", () => applyColor(null, hexInput, colorInput.value));

      hexInput.addEventListener("input", () => {
        const normalized = "#" + hexInput.value.toLowerCase().replace(/[^0-9a-f]/g, "").slice(0, 6);
        hexInput.value = normalized;
        const valid = HEX_COLOR.test(normalized);
        hexInput.classList.toggle("is-invalid", !valid);
        if (valid) colorInput.value = normalized;
      });

      // An incomplete hex is only ever a display mismatch; restore the real value.
      hexInput.addEventListener("blur", () => {
        if (!HEX_COLOR.test(hexInput.value)) applyColor(null, hexInput, colorInput.value);
      });
    }

    if (randomBtn) {
      randomBtn.addEventListener("click", () => applyColor(colorInput, hexInput, randomCategoryColor()));
    }

    return { colorInput, hexInput };
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
    const { colorInput, hexInput } = wireColorGroup(newModalEl, "new");

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

    // Reset fields and seed a fresh random color each time the modal opens.
    newModalEl.addEventListener("show.bs.modal", () => {
      if (nameInput) nameInput.value = "";
      if (slugInput) {
        slugInput.value = "";
        delete slugInput.dataset.userEdited;
      }
      applyColor(colorInput, hexInput, randomCategoryColor());
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
    const { colorInput, hexInput } = wireColorGroup(editModalEl, "edit");

    document.querySelectorAll("[data-category-edit-button]").forEach((button) => {
      button.addEventListener("click", () => {
        const name = button.getAttribute("data-category-name") || "";
        const slug = button.getAttribute("data-category-slug") || "";
        const color = button.getAttribute("data-category-edit-color") || "#6c757d";
        const action = button.getAttribute("data-category-action") || "";

        if (editForm) editForm.setAttribute("action", action);
        if (nameInput) nameInput.value = name;
        if (slugInput) slugInput.value = slug;
        applyColor(colorInput, hexInput, color);

        editModal.show();
      });
    });

    editModalEl.addEventListener("shown.bs.modal", () => nameInput?.focus());
  }
})();
