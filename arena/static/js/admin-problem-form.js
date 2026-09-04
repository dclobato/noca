/*
 * NOCA -- Next Online Contest Administrator
 * Copyright (c) 2026 The NOCA Authors (see AUTHORS)
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
    // The picker sits outside the form element, so its inputs must name the form
    // they belong to or they are never submitted.
    const formId = picker.getAttribute("data-form-id") || "";
    const pillsEl = document.getElementById("cat-pills");
    const hiddenEl = document.getElementById("cat-hidden");
    const inputEl = document.getElementById("cat-input");
    const dropdownEl = document.getElementById("cat-dropdown");
    const statusEl = document.getElementById("cat-picker-status");

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
        if (formId) input.setAttribute("form", formId);
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
          combo.announce(`${cat.name} removed.`);
          if (inputEl) inputEl.focus();
        });

        pill.appendChild(removeBtn);
        pillsEl.appendChild(pill);
      });
    }

    renderPills();

    // A browser draft (`noca-form-draft.js`) can only persist the hidden
    // `category_ids`; the pills need names and colours too, so those ride in
    // the draft's metadata and rebuild the selection on restore.
    const draftForm = formId ? document.getElementById(formId) : null;
    if (draftForm) {
      draftForm.addEventListener("noca:form-draft-collect", (event) => {
        event.detail.meta.arenaCategories = Array.from(selected.values());
      });
      draftForm.addEventListener("noca:form-draft-restored", (event) => {
        const cats = event.detail && event.detail.meta && event.detail.meta.arenaCategories;
        if (!Array.isArray(cats)) return;
        selected.clear();
        cats.forEach((cat) => {
          if (cat && typeof cat.id === "string" && typeof cat.name === "string") selected.set(cat.id, cat);
        });
        renderPills();
      });
    }

    // ── Dropdown ──────────────────────────────────────────────────────────────
    //
    // The listbox machinery (ARIA state, keyboard navigation, announcements,
    // debounce, cancellation, option rendering) is the shared `.arena-combo`
    // controller, the same one the Source/Author/License comboboxes use. Only
    // what is category-specific lives here.

    // How many categories the last search returned before already-selected ones
    // were filtered out, so "all of them are already added" can be told apart
    // from "there are none".
    let lastMatchCount = 0;

    if (!inputEl || !dropdownEl || !window.ArenaComboListbox) return;

    const combo = window.ArenaComboListbox.createComboListbox({
      input: inputEl,
      listbox: dropdownEl,
      statusEl: statusEl,
      optionIdPrefix: "cat-option",
      outsideClickRoot: picker,
      loadingMessage: "Loading categories…",
      errorMessage: "Could not load categories. Try again.",
      emptyMessage: () => (lastMatchCount ? "All matching categories already added." : "No categories found."),
      announceCount: (count) => `${count} categor${count === 1 ? "y" : "ies"} available.`,
      search: async (query, signal) => {
        if (!searchUrl) return [];
        const response = await fetch(`${searchUrl}?q=${encodeURIComponent(query)}`, { signal });
        if (!response.ok) throw new Error("Category search failed");
        const data = await response.json();
        const cats = data.categories || [];
        lastMatchCount = cats.length;
        return cats.filter((cat) => !selected.has(cat.id));
      },
      renderOption: (cat) => {
        const badge = document.createElement("span");
        badge.className = "badge rounded-pill";
        badge.style.background = cat.color;
        badge.style.color = cat.foreground_color;
        badge.textContent = cat.name;
        return badge;
      },
      onSelect: (cat) => {
        selected.set(cat.id, cat);
        renderPills();
        if (inputEl) {
          inputEl.value = "";
          inputEl.focus();
        }
        combo.close();
        combo.announce(`${cat.name} added.`);
      },
    });
  }

  // Image preview lives in the shared problem-image-preview.js, loaded alongside
  // this script by the form template.
})();
