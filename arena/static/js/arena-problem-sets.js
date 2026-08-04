// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 The NOCA Authors (see AUTHORS)
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

function initTooltips() {
  document.querySelectorAll("[data-bs-toggle='tooltip']").forEach((el) => {
    try {
      new bootstrap.Tooltip(el);
    } catch {
      el.setAttribute("data-bs-toggle", "native-tooltip");
    }
  });
}

function setText(selector, value) {
  const el = document.querySelector(selector);
  if (el) el.textContent = value || "";
}

function setupProblemSetDeleteModal() {
  const modal = document.getElementById("problem-set-delete-modal");
  const form = document.querySelector("[data-problem-set-delete-form]");
  if (!modal || !form || typeof bootstrap === "undefined") return;
  const bsModal = new bootstrap.Modal(modal);
  let deleteTrigger = null;
  document.querySelectorAll("[data-problem-set-delete-button]").forEach((button) => {
    button.addEventListener("click", () => {
      deleteTrigger = button;
      form.setAttribute("action", button.dataset.action || "");
      setText("[data-problem-set-delete-name]", button.dataset.name || "this problem set");
      const pageInput = document.querySelector("[data-problem-set-delete-page]");
      const sortInput = document.querySelector("[data-problem-set-delete-sort]");
      const directionInput = document.querySelector("[data-problem-set-delete-direction]");
      if (pageInput) pageInput.value = button.dataset.page || "1";
      if (sortInput) sortInput.value = button.dataset.sort || "deadline";
      if (directionInput) directionInput.value = button.dataset.direction || "desc";
      bsModal.show(button);
    });
  });
  modal.addEventListener("hidden.bs.modal", () => {
    if (deleteTrigger?.isConnected) deleteTrigger.focus();
    deleteTrigger = null;
  });
}

function setupProblemRemoveModal() {
  const modal = document.getElementById("problem-remove-modal");
  const form = document.querySelector("[data-problem-remove-form]");
  if (!modal || !form || typeof bootstrap === "undefined") return;
  const bsModal = new bootstrap.Modal(modal);
  let removeTrigger = null;
  document.querySelectorAll("[data-problem-remove-button]").forEach((button) => {
    button.addEventListener("click", () => {
      removeTrigger = button;
      form.setAttribute("action", button.dataset.action || "");
      setText("[data-problem-remove-name]", button.dataset.name || "this problem");
      bsModal.show(button);
    });
  });
  modal.addEventListener("hidden.bs.modal", () => {
    if (removeTrigger?.isConnected) removeTrigger.focus();
    removeTrigger = null;
  });
}

function setupProblemAutocomplete() {
  const root = document.querySelector("[data-problem-autocomplete]");
  if (!root) return;
  const searchInput = root.querySelector("[data-problem-search]");
  const form = root.closest("form");
  const refList = form ? form.querySelector("[data-problem-ref-list]") : null;
  const pendingList = form ? form.querySelector("[data-problem-pending-list]") : null;
  const suggestions = root.querySelector("[data-problem-suggestions]");
  const suggestionsStatus = form ? form.querySelector("[data-problem-suggestions-status]") : null;
  const searchUrl = root.dataset.searchUrl;
  if (!searchInput || !refList || !pendingList || !suggestions || !searchUrl) return;

  let debounce = null;
  let abortController = null;
  let activeSuggestion = -1;
  const pendingProblems = new Map();

  function setSuggestionsStatus(value) {
    if (suggestionsStatus) suggestionsStatus.textContent = value;
  }

  function hideSuggestions() {
    suggestions.classList.add("d-none");
    suggestions.innerHTML = "";
    activeSuggestion = -1;
    searchInput.setAttribute("aria-expanded", "false");
    searchInput.removeAttribute("aria-activedescendant");
    setSuggestionsStatus("");
  }

  function renderPendingProblems() {
    refList.innerHTML = "";
    pendingList.innerHTML = "";
    pendingProblems.forEach((problem) => {
      const input = document.createElement("input");
      input.type = "hidden";
      input.name = "problem_refs";
      input.value = problem.ref;
      refList.appendChild(input);

      const pill = document.createElement("span");
      pill.className = "badge bg-secondary d-inline-flex align-items-center gap-1 arena-problem-set-chip";
      pill.textContent = problem.label;

      const removeButton = document.createElement("button");
      removeButton.type = "button";
      removeButton.className = "btn-close btn-close-white";
      removeButton.setAttribute("aria-label", `Remove ${problem.label}`);
      removeButton.addEventListener("click", () => {
        pendingProblems.delete(problem.ref);
        renderPendingProblems();
        searchInput.focus();
      });

      pill.appendChild(removeButton);
      pendingList.appendChild(pill);
    });
  }

  function addPendingProblem(row) {
    const ref = row.ref || row.id || "";
    const label = row.label || ref;
    if (!ref) return;
    pendingProblems.set(ref, { ref, label });
    renderPendingProblems();
    searchInput.value = "";
    searchInput.focus();
    hideSuggestions();
  }

  function renderSuggestions(rows) {
    suggestions.innerHTML = "";
    activeSuggestion = -1;
    const availableRows = rows.filter((row) => !pendingProblems.has(row.ref || row.id || ""));
    if (!availableRows.length) {
      hideSuggestions();
      setSuggestionsStatus("No matching problems found.");
      return;
    }
    availableRows.forEach((row, index) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "arena-autocomplete-option";
      button.id = `problem-set-suggestion-${index}`;
      button.setAttribute("role", "option");
      button.setAttribute("aria-selected", "false");
      button.textContent = row.label;
      button.addEventListener("click", () => addPendingProblem(row));
      suggestions.appendChild(button);
    });
    suggestions.classList.remove("d-none");
    searchInput.setAttribute("aria-expanded", "true");
    setSuggestionsStatus(`${availableRows.length} problem suggestion${availableRows.length === 1 ? "" : "s"} available.`);
  }

  function setActiveSuggestion(index) {
    const options = [...suggestions.querySelectorAll("[role='option']")];
    if (!options.length) return;
    activeSuggestion = (index + options.length) % options.length;
    options.forEach((option, optionIndex) => {
      const isActive = optionIndex === activeSuggestion;
      option.setAttribute("aria-selected", String(isActive));
    });
    searchInput.setAttribute("aria-activedescendant", options[activeSuggestion].id);
    options[activeSuggestion].scrollIntoView({ block: "nearest" });
  }

  async function fetchProblems(query) {
    if (abortController) abortController.abort();
    abortController = new AbortController();
    const url = `${searchUrl}?q=${encodeURIComponent(query)}`;
    const response = await fetch(url, { signal: abortController.signal });
    const data = await response.json();
    renderSuggestions(Array.isArray(data.problems) ? data.problems : []);
  }

  searchInput.addEventListener("input", () => {
    const query = searchInput.value.trim();
    if (debounce) window.clearTimeout(debounce);
    if (!query) {
      hideSuggestions();
      return;
    }
    debounce = window.setTimeout(() => {
      fetchProblems(query).catch((err) => {
        if (err.name !== "AbortError") {
          hideSuggestions();
          setSuggestionsStatus("Problem suggestions are unavailable.");
        }
      });
    }, 180);
  });

  searchInput.addEventListener("keydown", (event) => {
    const options = suggestions.querySelectorAll("[role='option']");
    if (event.key === "Escape") {
      hideSuggestions();
      return;
    }
    if (event.key === "ArrowDown" && options.length) {
      event.preventDefault();
      setActiveSuggestion(activeSuggestion + 1);
    } else if (event.key === "ArrowUp" && options.length) {
      event.preventDefault();
      setActiveSuggestion(activeSuggestion - 1);
    } else if (event.key === "Enter" && activeSuggestion >= 0 && options[activeSuggestion]) {
      event.preventDefault();
      options[activeSuggestion].click();
    }
  });

  if (form) {
    form.addEventListener("submit", () => {
      const manualRef = searchInput.value.trim();
      const hasPendingProblem = refList.querySelector("input[name='problem_refs']") !== null;
      if (!hasPendingProblem && manualRef) {
        const input = document.createElement("input");
        input.type = "hidden";
        input.name = "problem_refs";
        input.value = manualRef;
        refList.appendChild(input);
      }
    });
  }

  document.addEventListener("click", (event) => {
    if (!root.contains(event.target)) hideSuggestions();
  });
}

document.addEventListener("DOMContentLoaded", () => {
  initTooltips();
  setupProblemSetDeleteModal();
  setupProblemRemoveModal();
  setupProblemAutocomplete();
});
