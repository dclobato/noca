/*
 * NOCA -- Next Online Contest Administrator
 * Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 */

(() => {
  "use strict";

  // ── Problem authorship ─────────────────────────────────────────────────────

  const authorIsOwnerInput = document.getElementById("author_is_owner");
  const authorField = document.getElementById("author-field");
  const authorInput = document.getElementById("author");

  function syncAuthorField() {
    if (!authorIsOwnerInput || !authorField || !authorInput) return;

    const authorIsOwner = authorIsOwnerInput.checked;
    authorField.hidden = authorIsOwner;
    authorInput.disabled = authorIsOwner;
    authorInput.required = !authorIsOwner;
    if (authorIsOwner) authorInput.value = "";
  }

  if (authorIsOwnerInput) {
    authorIsOwnerInput.addEventListener("change", syncAuthorField);
    syncAuthorField();
  }

  // ── Category autocomplete tag-picker ────────────────────────────────────────

  const picker = document.getElementById("cat-picker");

  if (picker) {
    const searchUrl = picker.getAttribute("data-search-url") || "";
    const pillsEl = document.getElementById("cat-pills");
    const hiddenEl = document.getElementById("cat-hidden");
    const inputEl = document.getElementById("cat-input");
    const dropdownEl = document.getElementById("cat-dropdown");

    /** @type {Map<string, {id: string, name: string, color: string, foreground_color: string}>} */
    const selected = new Map();

    // Populate initial selection from embedded JSON
    const jsonScript = document.getElementById("cat-selected-data");
    if (jsonScript) {
      try {
        /** @type {Array<{id: string, name: string, color: string, foreground_color: string}>} */
        const cats = JSON.parse(jsonScript.textContent || "[]");
        cats.forEach((cat) => selected.set(cat.id, cat));
      } catch {
        // Malformed JSON — start with no pre-selection
      }
    }

    function renderPills() {
      if (!pillsEl || !hiddenEl) return;
      pillsEl.innerHTML = "";
      hiddenEl.innerHTML = "";

      selected.forEach((cat) => {
        // Hidden form input so category_ids is submitted
        const input = document.createElement("input");
        input.type = "hidden";
        input.name = "category_ids";
        input.value = cat.id;
        hiddenEl.appendChild(input);

        // Visible pill
        const pill = document.createElement("span");
        pill.className = "badge rounded-pill d-inline-flex align-items-center me-1 mb-1";
        pill.style.background = cat.color;
        pill.style.color = cat.foreground_color;
        pill.textContent = cat.name;

        const removeBtn = document.createElement("button");
        removeBtn.type = "button";
        removeBtn.className = "arena-cat-pill-remove ms-1";
        removeBtn.setAttribute("aria-label", `Remove ${cat.name}`);
        removeBtn.textContent = "×";
        removeBtn.addEventListener("click", () => {
          selected.delete(cat.id);
          renderPills();
          if (inputEl) inputEl.focus();
        });

        pill.appendChild(removeBtn);
        pillsEl.appendChild(pill);
      });
    }

    renderPills();

    // ── Dropdown helpers ──────────────────────────────────────────────────────

    function closeDropdown() {
      if (dropdownEl) {
        dropdownEl.classList.remove("open");
        dropdownEl.innerHTML = "";
      }
    }

    /**
     * @param {Array<{id: string, name: string, color: string, foreground_color: string}>} cats
     */
    function renderDropdown(cats) {
      if (!dropdownEl) return;
      dropdownEl.innerHTML = "";

      const unselected = cats.filter((c) => !selected.has(c.id));

      if (!unselected.length) {
        const empty = document.createElement("div");
        empty.className = "arena-cat-dropdown-empty";
        empty.textContent = cats.length ? "All matching categories already added." : "No categories found.";
        dropdownEl.appendChild(empty);
      } else {
        unselected.forEach((cat) => {
          const item = document.createElement("button");
          item.type = "button";
          item.className = "arena-cat-dropdown-item";

          const badge = document.createElement("span");
          badge.className = "badge rounded-pill";
          badge.style.background = cat.color;
          badge.style.color = cat.foreground_color;
          badge.textContent = cat.name;

          item.appendChild(badge);
          item.addEventListener("click", () => {
            selected.set(cat.id, cat);
            renderPills();
            if (inputEl) {
              inputEl.value = "";
              inputEl.focus();
            }
            closeDropdown();
          });

          dropdownEl.appendChild(item);
        });
      }

      dropdownEl.classList.add("open");
    }

    // ── Search with debounce ──────────────────────────────────────────────────

    let searchTimer = null;

    async function doSearch(q) {
      if (!searchUrl) return;
      try {
        const resp = await fetch(`${searchUrl}?q=${encodeURIComponent(q)}`);
        if (!resp.ok) return;
        const data = await resp.json();
        renderDropdown(data.categories || []);
      } catch {
        // Network error — silently ignore
      }
    }

    if (inputEl) {
      inputEl.addEventListener("input", () => {
        clearTimeout(searchTimer);
        const q = inputEl.value.trim();
        if (!q) {
          closeDropdown();
          return;
        }
        searchTimer = setTimeout(() => doSearch(q), 250);
      });

      inputEl.addEventListener("keydown", (e) => {
        if (e.key === "Escape") {
          closeDropdown();
          return;
        }
        if (e.key === "ArrowDown" && dropdownEl && dropdownEl.classList.contains("open")) {
          const first = dropdownEl.querySelector(".arena-cat-dropdown-item");
          if (first) {
            e.preventDefault();
            first.focus();
          }
        }
      });
    }

    // Keyboard navigation within the dropdown
    if (dropdownEl) {
      dropdownEl.addEventListener("keydown", (e) => {
        const items = Array.from(dropdownEl.querySelectorAll(".arena-cat-dropdown-item"));
        const idx = items.indexOf(document.activeElement);
        if (e.key === "ArrowDown" && idx < items.length - 1) {
          e.preventDefault();
          items[idx + 1].focus();
        } else if (e.key === "ArrowUp") {
          e.preventDefault();
          if (idx <= 0) {
            if (inputEl) inputEl.focus();
          } else {
            items[idx - 1].focus();
          }
        } else if (e.key === "Escape") {
          closeDropdown();
          if (inputEl) inputEl.focus();
        }
      });
    }

    // Close dropdown when clicking outside the picker
    document.addEventListener("click", (e) => {
      if (!picker.contains(e.target)) {
        closeDropdown();
      }
    });
  }

  // ── Image preview ───────────────────────────────────────────────────────────

  const imageInput = document.getElementById("image");
  if (imageInput) {
    imageInput.addEventListener("change", () => {
      const file = imageInput.files && imageInput.files[0];
      if (!file) return;

      const existingPreview = document.getElementById("image-preview");
      if (existingPreview) existingPreview.remove();

      const reader = new FileReader();
      reader.onload = (e) => {
        const img = document.createElement("img");
        img.id = "image-preview";
        img.src = e.target.result;
        img.alt = "Image preview";
        img.className = "img-thumbnail mt-2 arena-problem-image-preview";
        imageInput.insertAdjacentElement("afterend", img);
      };
      reader.readAsDataURL(file);
    });
  }
})();
