/*
 * NOCA -- Next Online Contest Administrator
 * Copyright (c) 2026 The NOCA Authors (see AUTHORS)
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 */

/*
 * New / edit / delete modals for Arena's flat, colored taxonomies -- categories
 * and collections.  Both list pages have identical widgets, so they share this
 * file and differ only in the DOM hooks' values: the page supplies its own
 * endpoints through data attributes and its own delete wording through
 * `data-taxonomy-delete-count-one` / `-many` on the delete modal (`{count}` is
 * substituted).  Nothing here names one taxonomy.
 *
 * Requires shared/static/js/slugify.js and arena/static/js/taxonomy-slug.js.
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

  // ── Shared utilities ──────────────────────────────────────────────────────
  // Slug preview comes from taxonomy-slug.js, shared with the standalone forms,
  // so every surface agrees with the server's normalize_slug().
  const slugify = (value) => window.NocaTaxonomySlug?.slugify(value) ?? value;

  function randomBadgeColor() {
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
    const colorInput = root.querySelector(`#${prefix}_taxonomy_color`);
    const hexInput = root.querySelector(`#${prefix}_taxonomy_color_hex`);
    const randomBtn = root.querySelector(`#${prefix}_taxonomy_color_random`);

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
      randomBtn.addEventListener("click", () => applyColor(colorInput, hexInput, randomBadgeColor()));
    }

    return { colorInput, hexInput };
  }

  // ── Delete modal ─────────────────────────────────────────────────
  const deleteModalEl = document.getElementById("taxonomy-delete-modal");
  if (deleteModalEl) {
    const deleteModal = new bootstrap.Modal(deleteModalEl);
    const deleteForm = deleteModalEl.querySelector("[data-taxonomy-delete-form]");
    const nameEl = deleteModalEl.querySelector("[data-taxonomy-delete-name]");
    const countEl = deleteModalEl.querySelector("[data-taxonomy-delete-count]");
    const pageInput = deleteForm ? deleteForm.querySelector("input[name='page']") : null;
    const perPageInput = deleteForm ? deleteForm.querySelector("input[name='per_page']") : null;

    document.querySelectorAll("[data-taxonomy-delete-button]").forEach((button) => {
      button.addEventListener("click", () => {
        const name = button.getAttribute("data-taxonomy-name") || "this entry";
        const count = Number(button.getAttribute("data-taxonomy-count") || "0");
        const action = button.getAttribute("data-taxonomy-action") || "";

        if (deleteForm) deleteForm.setAttribute("action", action);
        if (nameEl) nameEl.textContent = name;
        if (countEl) {
          // Wording is the one thing that genuinely differs between taxonomies:
          // a category unlinks, a collection unfiles.  The page owns the phrasing.
          const template =
            count === 1
              ? deleteModalEl.getAttribute("data-taxonomy-delete-count-one") || ""
              : deleteModalEl.getAttribute("data-taxonomy-delete-count-many") || "";
          countEl.textContent = template.replace("{count}", String(count));
        }
        if (pageInput) pageInput.value = button.getAttribute("data-taxonomy-page") || "1";
        if (perPageInput) perPageInput.value = button.getAttribute("data-taxonomy-per-page") || "25";

        deleteModal.show();
      });
    });
  }

  // ── New modal ────────────────────────────────────────────────────
  const newModalEl = document.getElementById("taxonomy-new-modal");
  if (newModalEl) {
    const nameInput = newModalEl.querySelector("#new_taxonomy_name");
    const slugInput = newModalEl.querySelector("#new_taxonomy_slug");
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
      applyColor(colorInput, hexInput, randomBadgeColor());
    });

    newModalEl.addEventListener("shown.bs.modal", () => nameInput?.focus());
  }

  // ── Edit modal ───────────────────────────────────────────────────
  const editModalEl = document.getElementById("taxonomy-edit-modal");
  if (editModalEl) {
    const editModal = new bootstrap.Modal(editModalEl);
    const editForm = editModalEl.querySelector("[data-taxonomy-edit-form]");
    const nameInput = editModalEl.querySelector("#edit_taxonomy_name");
    const slugInput = editModalEl.querySelector("#edit_taxonomy_slug");
    const { colorInput, hexInput } = wireColorGroup(editModalEl, "edit");

    document.querySelectorAll("[data-taxonomy-edit-button]").forEach((button) => {
      button.addEventListener("click", () => {
        const name = button.getAttribute("data-taxonomy-name") || "";
        const slug = button.getAttribute("data-taxonomy-slug") || "";
        const color = button.getAttribute("data-taxonomy-edit-color") || "#6c757d";
        const action = button.getAttribute("data-taxonomy-action") || "";

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
