// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

function setText(selector, value) {
  const el = document.querySelector(selector);
  if (el) el.textContent = value || "";
}

function setupDenialReasonModal() {
  const modal = document.getElementById("class-denial-modal");
  if (!modal) return;
  modal.addEventListener("show.bs.modal", (event) => {
    const button = event.relatedTarget;
    const reason = button?.getAttribute("data-denial-reason") || "No reason provided.";
    setText("[data-class-denial-reason]", reason);
  });
}

function setupOpenClassModal() {
  const modal = document.getElementById("open-class-modal");
  if (!modal) return;
  const bsModal = new bootstrap.Modal(modal);
  document.querySelectorAll("[data-open-class-row]").forEach((row) => {
    row.addEventListener("click", () => {
      setText("[data-open-class-name]", row.dataset.name);
      setText("[data-open-class-description]", row.dataset.description || "No description.");
      setText("[data-open-class-starts]", row.dataset.starts);
      setText("[data-open-class-finishes]", row.dataset.finishes);
      setText("[data-open-class-teacher]", row.dataset.teacher);
      setText("[data-open-class-affiliation]", row.dataset.affiliation);
      setText("[data-open-class-member-count]", row.dataset.memberCount);
      const form = document.querySelector("[data-open-class-form]");
      if (form) form.setAttribute("action", row.dataset.requestUrl || "");
      bsModal.show();
    });
  });
}

function setupMembershipModals() {
  const denyModal = document.getElementById("deny-request-modal");
  const denyForm = document.querySelector("[data-deny-request-form]");
  const denyBsModal = denyModal ? new bootstrap.Modal(denyModal) : null;
  document.querySelectorAll("[data-deny-request-button]").forEach((button) => {
    button.addEventListener("click", () => {
      if (!denyForm || !denyBsModal) return;
      denyForm.setAttribute("action", button.dataset.action || "");
      setText("[data-deny-request-user]", button.dataset.userName);
      denyBsModal.show();
    });
  });

  const removeModal = document.getElementById("remove-member-modal");
  const removeForm = document.querySelector("[data-remove-member-form]");
  const removeBsModal = removeModal ? new bootstrap.Modal(removeModal) : null;
  document.querySelectorAll("[data-remove-member-button]").forEach((button) => {
    button.addEventListener("click", () => {
      if (!removeForm || !removeBsModal) return;
      removeForm.setAttribute("action", button.dataset.action || "");
      setText("[data-remove-member-user]", button.dataset.userName);
      removeBsModal.show();
    });
  });
}

function setupTeacherAutocomplete() {
  const root = document.querySelector("[data-teacher-autocomplete]");
  if (!root) return;
  const searchInput = root.querySelector("[data-teacher-search]");
  const teacherIdInput = root.querySelector("[data-teacher-id]");
  const suggestions = root.querySelector("[data-teacher-suggestions]");
  const searchUrl = root.dataset.searchUrl;
  if (!searchInput || !teacherIdInput || !suggestions || !searchUrl) return;

  let debounce = null;
  let abortController = null;

  function hideSuggestions() {
    suggestions.classList.add("d-none");
    suggestions.innerHTML = "";
  }

  function renderSuggestions(rows) {
    suggestions.innerHTML = "";
    if (!rows.length) {
      hideSuggestions();
      return;
    }
    rows.forEach((row) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "arena-autocomplete-option";
      button.textContent = row.label;
      button.addEventListener("click", () => {
        teacherIdInput.value = row.id;
        searchInput.value = row.label;
        hideSuggestions();
      });
      suggestions.appendChild(button);
    });
    suggestions.classList.remove("d-none");
  }

  async function fetchTeachers(query) {
    if (abortController) abortController.abort();
    abortController = new AbortController();
    const url = `${searchUrl}?q=${encodeURIComponent(query)}`;
    const response = await fetch(url, { signal: abortController.signal });
    const data = await response.json();
    renderSuggestions(Array.isArray(data.teachers) ? data.teachers : []);
  }

  searchInput.addEventListener("input", () => {
    teacherIdInput.value = "";
    const query = searchInput.value.trim();
    if (debounce) window.clearTimeout(debounce);
    if (!query) {
      hideSuggestions();
      return;
    }
    debounce = window.setTimeout(() => {
      fetchTeachers(query).catch((err) => {
        if (err.name !== "AbortError") hideSuggestions();
      });
    }, 180);
  });

  document.addEventListener("click", (event) => {
    if (!root.contains(event.target)) hideSuggestions();
  });
}

function setupStudentAutocomplete() {
  const root = document.querySelector("[data-student-autocomplete]");
  if (!root) return;
  const form = root.closest("form");
  const searchInput = root.querySelector("[data-student-search]");
  const idList = form ? form.querySelector("[data-student-id-list]") : null;
  const pendingList = form ? form.querySelector("[data-student-pending-list]") : null;
  const suggestions = root.querySelector("[data-student-suggestions]");
  const searchUrl = root.dataset.searchUrl;
  if (!form || !searchInput || !idList || !pendingList || !suggestions || !searchUrl) return;

  let debounce = null;
  let abortController = null;
  const pendingStudents = new Map();

  function hideSuggestions() {
    suggestions.classList.add("d-none");
    suggestions.innerHTML = "";
  }

  function renderPendingStudents() {
    idList.innerHTML = "";
    pendingList.innerHTML = "";
    pendingStudents.forEach((student) => {
      const input = document.createElement("input");
      input.type = "hidden";
      input.name = "student_ids";
      input.value = student.id;
      idList.appendChild(input);

      const pill = document.createElement("span");
      pill.className = "badge bg-secondary d-inline-flex align-items-center gap-1";
      pill.textContent = student.label;

      const removeButton = document.createElement("button");
      removeButton.type = "button";
      removeButton.className = "btn-close btn-close-white";
      removeButton.setAttribute("aria-label", `Remove ${student.label}`);
      removeButton.addEventListener("click", () => {
        pendingStudents.delete(student.id);
        renderPendingStudents();
        searchInput.focus();
      });

      pill.appendChild(removeButton);
      pendingList.appendChild(pill);
    });
  }

  function addPendingStudent(row) {
    if (!row.id) return;
    pendingStudents.set(row.id, { id: row.id, label: row.label || row.id });
    renderPendingStudents();
    searchInput.value = "";
    searchInput.focus();
    hideSuggestions();
  }

  function renderSuggestions(rows) {
    suggestions.innerHTML = "";
    const availableRows = rows.filter((row) => row.id && !pendingStudents.has(row.id));
    if (!availableRows.length) {
      hideSuggestions();
      return;
    }
    availableRows.forEach((row) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "arena-autocomplete-option";
      button.textContent = row.label;
      button.addEventListener("click", () => addPendingStudent(row));
      suggestions.appendChild(button);
    });
    suggestions.classList.remove("d-none");
  }

  async function fetchStudents(query) {
    if (abortController) abortController.abort();
    abortController = new AbortController();
    const url = `${searchUrl}?q=${encodeURIComponent(query)}`;
    const response = await fetch(url, { signal: abortController.signal });
    const data = await response.json();
    renderSuggestions(Array.isArray(data.students) ? data.students : []);
  }

  searchInput.addEventListener("input", () => {
    const query = searchInput.value.trim();
    if (debounce) window.clearTimeout(debounce);
    if (!query) {
      hideSuggestions();
      return;
    }
    debounce = window.setTimeout(() => {
      fetchStudents(query).catch((err) => {
        if (err.name !== "AbortError") hideSuggestions();
      });
    }, 180);
  });

  document.addEventListener("click", (event) => {
    if (!root.contains(event.target)) hideSuggestions();
  });
}

document.addEventListener("DOMContentLoaded", () => {
  setupDenialReasonModal();
  setupOpenClassModal();
  setupMembershipModals();
  setupTeacherAutocomplete();
  setupStudentAutocomplete();
});
