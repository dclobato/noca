// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

// admin-problems-edit.js — Category autocomplete + balloon color picker + create-mode TC rows

// ---------------------------------------------------------------------------
// Category autocomplete with pending state
// ---------------------------------------------------------------------------

function notifyProblemEditChanged() {
  document.dispatchEvent(new CustomEvent("noca:problem-edit-changed"));
}

function updateCategoryInput() {
  const chips = document.querySelectorAll("#category-chips .category-chip");
  const names = Array.from(chips).map(c => c.dataset.name).filter(Boolean);
  const input = document.getElementById("category_names_input");
  if (input) input.value = names.join(",");
  notifyProblemEditChanged();
}

function addChip(name) {
  const container = document.getElementById("category-chips");
  if (!container) return;
  const norm = name.trim().toLowerCase();
  if (!norm) return;
  // Avoid duplicates
  const existing = Array.from(container.querySelectorAll(".category-chip")).map(c => c.dataset.name);
  if (existing.includes(norm)) return;

  const span = document.createElement("span");
  span.className = "badge bg-secondary d-flex align-items-center gap-1 category-chip";
  span.dataset.name = norm;
  span.textContent = norm + " ";

  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "btn-close btn-close-white noca-chip-close";
  btn.onclick = function() { removeChip(this); };
  span.appendChild(btn);
  container.appendChild(span);
  updateCategoryInput();
}

function removeChip(btn) {
  btn.closest(".category-chip").remove();
  updateCategoryInput();
}

function addCategories() {
  const input = document.getElementById("category-input");
  if (!input) return;
  const parts = input.value.split(",");
  parts.forEach(p => addChip(p.trim()));
  input.value = "";
  hideSuggestions();
}

// ---------------------------------------------------------------------------
// Balloon color picker
// ---------------------------------------------------------------------------

const BALLOON_PREDEFINED = [
  "#FF0000","#800000","#FFA500","#FFD700","#FFFF00","#808000",
  "#00FF00","#008000","#00FFFF","#008080","#0000FF","#000080",
  "#FF00FF","#800080","#FFFFFF","#C0C0C0","#808080","#000000",
];

function selectBalloonColor(hex) {
  hex = hex.toUpperCase();
  const colorValue = document.getElementById("color-value");
  if (colorValue) colorValue.value = hex;

  // Highlight matching predefined button
  document.querySelectorAll(".noca-balloon-opt[data-color]").forEach(btn => {
    btn.classList.toggle("noca-balloon-selected", btn.dataset.color.toUpperCase() === hex);
  });

  const isPredefined = BALLOON_PREDEFINED.includes(hex);

  // Custom button highlight
  const customBtn = document.getElementById("custom-color-btn");
  if (customBtn) customBtn.classList.toggle("noca-balloon-selected", !isPredefined);

  // Always update the preview balloon
  const preview = document.getElementById("balloon-preview");
  if (preview) {
    const src = preview.src;
    preview.src = src.substring(0, src.lastIndexOf("/") + 1) + hex.replace("#", "");
  }

  // Keep custom input in sync
  const customInput = document.getElementById("color-custom-input");
  if (customInput) customInput.value = hex;
  notifyProblemEditChanged();
}

// ---------------------------------------------------------------------------
// Debounce + AbortController for autocomplete requests
let _catDebounceTimer = null;
let _catAbortController = null;

function scheduleCategorySuggestions() {
  clearTimeout(_catDebounceTimer);
  _catDebounceTimer = setTimeout(() => {
    const input = document.getElementById("category-input");
    if (!input) return;
    fetchCategorySuggestions(input.value.trim());
  }, 180);
}

function fetchCategorySuggestions(query) {
  const input = document.getElementById("category-input");
  if (!input) return;
  const url = input.dataset.autocompleteUrl;
  if (!url) return;

  if (_catAbortController) _catAbortController.abort();
  _catAbortController = new AbortController();

  const params = query ? `?q=${encodeURIComponent(query)}` : "";
  fetch(url + params, { signal: _catAbortController.signal })
    .then(r => r.json())
    .then(data => renderCategorySuggestions(data.categories || []))
    .catch(() => {});
}

function renderCategorySuggestions(cats) {
  const menu = document.getElementById("category-suggestions");
  if (!menu) return;
  menu.innerHTML = "";
  if (!cats.length) {
    menu.classList.add("d-none");
    return;
  }
  cats.forEach(cat => {
    const item = document.createElement("button");
    item.type = "button";
    item.className = "dropdown-item";
    item.textContent = cat.name;
    item.onclick = function() {
      addChip(cat.name);
      document.getElementById("category-input").value = "";
      hideSuggestions();
    };
    menu.appendChild(item);
  });
  menu.classList.remove("d-none");
}

function hideSuggestions() {
  const menu = document.getElementById("category-suggestions");
  if (menu) menu.classList.add("d-none");
}

// Wire up autocomplete input
document.addEventListener("DOMContentLoaded", function() {
  const catInput = document.getElementById("category-input");
  if (catInput) {
    catInput.addEventListener("input", scheduleCategorySuggestions);
    catInput.addEventListener("keydown", function(e) {
      if (e.key === "Enter") {
        e.preventDefault();
        addCategories();
      }
    });
    catInput.addEventListener("focus", function() {
      fetchCategorySuggestions(this.value.trim());
    });
  }

  // Close suggestions on outside click
  document.addEventListener("click", function(e) {
    const wrapper = document.getElementById("category-autocomplete-wrapper");
    if (wrapper && !wrapper.contains(e.target)) hideSuggestions();
  });

  // Balloon color picker
  const balloonPicker = document.getElementById("balloon-color-picker");
  if (balloonPicker) {
    balloonPicker.addEventListener("click", function(e) {
      const btn = e.target.closest(".noca-balloon-opt[data-color]");
      if (btn && !btn.disabled) selectBalloonColor(btn.dataset.color);
    });
    const customInput = document.getElementById("color-custom-input");
    if (customInput) {
      customInput.addEventListener("input", function() { selectBalloonColor(this.value); });
    }
    const initialColor = document.getElementById("color-value");
    if (initialColor && initialColor.value) selectBalloonColor(initialColor.value);
  }

  // Activate tab from server-rendered data-active-tab or ?tab= query param
  const _tabNav = document.getElementById("problem-edit-tabs");
  const _activeTabInput = document.getElementById("active-tab-input");
  const _tabParam = new URLSearchParams(window.location.search).get("tab")
    || (_tabNav && _tabNav.dataset.activeTab);
  if (_tabParam && _tabParam !== "content") {
    const _tabBtn = document.querySelector(`[data-bs-target="#tab-${_tabParam}"]`);
    if (_tabBtn && typeof bootstrap !== "undefined") {
      bootstrap.Tab.getOrCreateInstance(_tabBtn).show();
    }
  }

  if (_tabNav && _activeTabInput) {
    _tabNav.addEventListener("shown.bs.tab", function(e) {
      const target = e.target && e.target.getAttribute("data-bs-target");
      _activeTabInput.value = target === "#tab-limits" ? "limits" : "content";
    });
  }

  // Edit-form submit: serialize category chips
  const form = document.getElementById("edit-form");
  if (form) {
    form.addEventListener("submit", function(e) {
      updateCategoryInput();

      // Create-mode: validate at least 1 test case
      const isCreate = !document.getElementById("problem-id-hint");
      if (!isCreate) return;

      const rows = document.querySelectorAll("#tc-add-rows > div").length;
      const zipInput = document.getElementById("testcases_zip_input");
      const hasZip = zipInput && zipInput.files && zipInput.files.length > 0;
      if (rows === 0 && !hasZip) {
        e.preventDefault();
        const warn = document.getElementById("tc-count-warning");
        if (warn) warn.classList.remove("d-none");
        const addRows = document.getElementById("tc-add-rows");
        if (addRows) addRows.scrollIntoView({ behavior: "smooth" });
      }
    });
  }
});

// ---------------------------------------------------------------------------
// Create-mode dynamic test case rows
// ---------------------------------------------------------------------------

let tcRowCount = 0;

function addTcRow() {
  const container = document.getElementById("tc-add-rows");
  if (!container) return;
  const i = tcRowCount++;
  const div = document.createElement("div");
  div.className = "border rounded p-3 mb-3 position-relative";
  div.innerHTML = `
    <button type="button" class="btn-close position-absolute top-0 end-0 m-2"
            onclick="this.closest('div').remove(); updateTcCount()"></button>
    <div class="row g-2">
      <div class="col-6">
        <label class="form-label small">Input</label>
        <textarea name="tc_in_${i}" rows="4"
                  class="form-control font-monospace form-control-sm"
                  placeholder="Input data"></textarea>
      </div>
      <div class="col-6">
        <label class="form-label small">Expected output</label>
        <textarea name="tc_out_${i}" rows="4"
                  class="form-control font-monospace form-control-sm"
                  placeholder="Expected output"></textarea>
      </div>
      <div class="col-12">
        <label class="form-label small">Explanation (optional)</label>
        <textarea name="tc_explanation_${i}" rows="2"
                  maxlength="1024"
                  class="form-control form-control-sm"
                  placeholder="Explain why this test case produces its expected output…"></textarea>
        <div class="form-text">Shown to contestants below the sample. Up to 1024 characters.</div>
      </div>
    </div>
    <div class="form-check mt-2">
      <input type="checkbox" name="tc_is_sample_${i}" id="tc_sample_${i}" class="form-check-input">
      <label class="form-check-label small" for="tc_sample_${i}">Sample (visible to contestants)</label>
    </div>`;
  container.appendChild(div);
  updateTcCount();
}

function updateTcCount() {
  const rows = document.querySelectorAll("#tc-add-rows > div").length;
  const warn = document.getElementById("tc-count-warning");
  const zipInput = document.getElementById("testcases_zip_input");
  const hasZip = zipInput && zipInput.files && zipInput.files.length > 0;
  if (warn) warn.classList.toggle("d-none", rows > 0 || hasZip);
}

// ---------------------------------------------------------------------------
// Create-mode single-TC ZIP upload rows
// ---------------------------------------------------------------------------

let tcZipRowCount = 0;

function addTcZipRow() {
  const container = document.getElementById("tc-add-rows");
  if (!container) return;
  const i = tcZipRowCount++;
  const div = document.createElement("div");
  div.className = "border rounded p-3 mb-3 position-relative";
  div.innerHTML = `
    <button type="button" class="btn-close position-absolute top-0 end-0 m-2"
            onclick="this.closest('div').remove(); updateTcCount()"></button>
    <div class="d-flex flex-wrap align-items-center gap-2">
      <label class="form-label mb-0 text-nowrap">Test case ZIP:</label>
      <input type="file" name="tc_zip_${i}" accept=".zip" required
             class="form-control form-control-sm">
    </div>
    <div class="form-check mt-2">
      <input type="checkbox" name="tc_zip_is_sample_${i}" id="tc_zip_sample_${i}" class="form-check-input">
      <label class="form-check-label small" for="tc_zip_sample_${i}">Sample (visible to contestants)</label>
    </div>`;
  container.appendChild(div);
  updateTcCount();
}
