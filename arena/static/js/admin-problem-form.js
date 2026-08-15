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

  // ── Problem metadata suggestions ───────────────────────────────────────────

  document.querySelectorAll("input[data-suggestions-url][data-suggestions-field]").forEach((input) => {
    const suggestionsUrl = input.getAttribute("data-suggestions-url") || "";
    const field = input.getAttribute("data-suggestions-field") || "";
    const datalistId = input.getAttribute("list") || "";
    const datalist = datalistId ? document.getElementById(datalistId) : null;

    if (!suggestionsUrl || !field || !datalist) return;

    let timer = null;
    let controller = null;

    function clearSuggestions() {
      datalist.replaceChildren();
    }

    async function loadSuggestions(query) {
      controller = new AbortController();
      const requestController = controller;
      const params = new URLSearchParams({ field, q: query });

      try {
        const response = await fetch(`${suggestionsUrl}?${params}`, { signal: requestController.signal });
        if (!response.ok) {
          if (requestController === controller) clearSuggestions();
          return;
        }

        const data = await response.json();
        if (requestController.signal.aborted || requestController !== controller || !Array.isArray(data.suggestions)) {
          return;
        }

        clearSuggestions();
        data.suggestions.forEach((suggestion) => {
          if (typeof suggestion !== "string") return;
          const option = document.createElement("option");
          option.value = suggestion;
          datalist.appendChild(option);
        });
      } catch (error) {
        if (error && typeof error === "object" && error.name === "AbortError") return;
        if (requestController === controller) clearSuggestions();
      }
    }

    input.addEventListener("input", () => {
      clearTimeout(timer);
      if (controller) {
        controller.abort();
        controller = null;
      }

      const query = input.value.trim();
      clearSuggestions();
      if (query.length < 2) {
        return;
      }

      timer = setTimeout(() => {
        void loadSuggestions(query);
      }, 250);
    });
  });

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
          announcePicker(`${cat.name} removed.`);
          if (inputEl) inputEl.focus();
        });

        pill.appendChild(removeBtn);
        pillsEl.appendChild(pill);
      });
    }

    renderPills();

    // ── Dropdown helpers ──────────────────────────────────────────────────────

    let activeOptionIndex = -1;

    function announcePicker(message) {
      if (!statusEl) return;
      statusEl.textContent = "";
      window.setTimeout(() => {
        statusEl.textContent = message;
      }, 0);
    }

    function setExpanded(expanded) {
      if (inputEl) inputEl.setAttribute("aria-expanded", expanded ? "true" : "false");
    }

    function closeDropdown() {
      if (!dropdownEl) return;
      dropdownEl.classList.remove("open");
      dropdownEl.replaceChildren();
      activeOptionIndex = -1;
      setExpanded(false);
      if (inputEl) inputEl.setAttribute("aria-activedescendant", "");
    }

    function optionItems() {
      if (!dropdownEl) return [];
      return Array.from(dropdownEl.querySelectorAll("[role='option']:not([aria-disabled='true'])"));
    }

    function setActiveOption(index) {
      const items = optionItems();
      if (!items.length || !inputEl) return;
      activeOptionIndex = Math.max(0, Math.min(index, items.length - 1));
      items.forEach((item, itemIndex) => {
        const active = itemIndex === activeOptionIndex;
        item.classList.toggle("is-active", active);
        item.setAttribute("aria-selected", active ? "true" : "false");
      });
      const activeItem = items[activeOptionIndex];
      inputEl.setAttribute("aria-activedescendant", activeItem.id);
      activeItem.scrollIntoView({ block: "nearest" });
    }

    function showDropdownMessage(message, options) {
      if (!dropdownEl) return;
      dropdownEl.replaceChildren();
      const item = document.createElement("div");
      item.className = "arena-cat-dropdown-empty";
      item.setAttribute("role", "option");
      item.setAttribute("aria-disabled", "true");
      item.textContent = message;
      dropdownEl.appendChild(item);
      dropdownEl.classList.add("open");
      activeOptionIndex = -1;
      setExpanded(true);
      if (inputEl) inputEl.setAttribute("aria-activedescendant", "");
      if (!options || options.announce !== false) announcePicker(message);
    }

    function chooseCategory(cat) {
      selected.set(cat.id, cat);
      renderPills();
      if (inputEl) {
        inputEl.value = "";
        inputEl.focus();
      }
      closeDropdown();
      announcePicker(`${cat.name} added.`);
    }

    /**
     * @param {Array<{id: string, name: string, color: string, foreground_color: string}>} cats
     */
    function renderDropdown(cats) {
      if (!dropdownEl) return;
      dropdownEl.replaceChildren();

      const unselected = cats.filter((c) => !selected.has(c.id));

      if (!unselected.length) {
        showDropdownMessage(cats.length ? "All matching categories already added." : "No categories found.");
        return;
      } else {
        unselected.forEach((cat, index) => {
          const item = document.createElement("button");
          item.type = "button";
          item.className = "arena-cat-dropdown-item";
          item.id = `cat-option-${index}`;
          item.tabIndex = -1;
          item.setAttribute("role", "option");
          item.setAttribute("aria-selected", "false");

          const badge = document.createElement("span");
          badge.className = "badge rounded-pill";
          badge.style.background = cat.color;
          badge.style.color = cat.foreground_color;
          badge.textContent = cat.name;

          item.appendChild(badge);
          item.addEventListener("pointerdown", (event) => event.preventDefault());
          item.addEventListener("click", () => chooseCategory(cat));

          dropdownEl.appendChild(item);
        });
      }

      dropdownEl.classList.add("open");
      activeOptionIndex = -1;
      setExpanded(true);
      if (inputEl) inputEl.setAttribute("aria-activedescendant", "");
      announcePicker(`${unselected.length} categor${unselected.length === 1 ? "y" : "ies"} available.`);
    }

    // ── Search with debounce ──────────────────────────────────────────────────

    let searchTimer = null;
    let searchController = null;

    async function doSearch(q) {
      if (!searchUrl) return;
      if (searchController) searchController.abort();
      searchController = new AbortController();
      const requestController = searchController;
      showDropdownMessage("Loading categories…", { announce: false });
      announcePicker("Loading categories.");
      try {
        const resp = await fetch(`${searchUrl}?q=${encodeURIComponent(q)}`, {
          signal: requestController.signal
        });
        if (!resp.ok) throw new Error("Category search failed");
        const data = await resp.json();
        if (requestController !== searchController) return;
        renderDropdown(data.categories || []);
      } catch (error) {
        if (error && typeof error === "object" && error.name === "AbortError") return;
        if (requestController === searchController) {
          showDropdownMessage("Could not load categories. Try again.");
        }
      }
    }

    if (inputEl) {
      inputEl.addEventListener("input", () => {
        clearTimeout(searchTimer);
        if (searchController) {
          searchController.abort();
          searchController = null;
        }
        const q = inputEl.value.trim();
        if (!q) {
          closeDropdown();
          return;
        }
        searchTimer = setTimeout(() => doSearch(q), 250);
      });

      inputEl.addEventListener("keydown", (e) => {
        if (e.key === "Escape") {
          e.preventDefault();
          closeDropdown();
          return;
        }
        const items = optionItems();
        if ((e.key === "ArrowDown" || e.key === "ArrowUp") && items.length) {
          e.preventDefault();
          const step = e.key === "ArrowDown" ? 1 : -1;
          const start = activeOptionIndex < 0 ? (step > 0 ? 0 : items.length - 1) : activeOptionIndex + step;
          setActiveOption(start);
        } else if (e.key === "Enter" && activeOptionIndex >= 0 && items[activeOptionIndex]) {
          e.preventDefault();
          items[activeOptionIndex].click();
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

  // Image preview lives in the shared problem-image-preview.js, loaded alongside
  // this script by the form template.
})();
